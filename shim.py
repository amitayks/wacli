#!/usr/bin/env python3
"""wacli bridge shim v2 — send + tracked-contact read lanes.

Lanes:
  SEND (unchanged contract): POST /send {to,text} | /send-file {to,fileBase64,
    filename,caption} — bearer-gated, execs `wacli send` (delegated to the
    running sync --follow via its socket).
  READ (new): wacli sync posts every live message to /hook (localhost only,
    HMAC-signed). The allowlist filter runs BEFORE any write: messages whose
    chat JID is on the allowlist append to the tracked store
    (/data/config/tracked.jsonl); everything else is discarded in memory.
    GET /messages?since=<seq> serves tracked rows. POST /allowlist replaces
    the allowlist atomically (and prunes tracked rows that fell off it).
  PRIVACY PURGE: a background thread deletes non-allowlisted chat/message rows
    from wacli's own store (wacli.db, WAL) so nothing outside the tracked
    list persists on this volume. Fail-closed: purge errors are logged
    (content-free) and retried next cycle; they never crash the shim.

Logs NEVER contain message content or non-tracked identifiers — counts and
tracked JIDs only.
"""
import os, json, base64, tempfile, subprocess, threading, time, sqlite3, hmac, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("GERMANICUS_WACLI_SEND_TOKEN", "")
HOOK_SECRET = os.environ.get("WACLI_WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))
CONF_DIR = os.environ.get("SHIM_CONF_DIR", "/data/config")
ALLOWLIST_PATH = os.path.join(CONF_DIR, "allowlist.json")
TRACKED_PATH = os.path.join(CONF_DIR, "tracked.jsonl")
WACLI_DB = os.environ.get("WACLI_DB", "/data/store/wacli.db")
PURGE_INTERVAL = int(os.environ.get("PURGE_INTERVAL_SEC", "600"))

_lock = threading.Lock()          # guards allowlist + tracked store
_seq = 0                          # monotonically increasing row id


def _load_allowlist():
    try:
        with open(ALLOWLIST_PATH) as f:
            return set(json.load(f).get("jids", []))
    except Exception:
        return set()


def _bare(jid):
    """15551234567@s.whatsapp.net -> 15551234567 (match on bare number too)."""
    return jid.split("@", 1)[0].split(":", 1)[0] if jid else ""


ALLOW = _load_allowlist()


def _allowed(chat_jid):
    return chat_jid in ALLOW or _bare(chat_jid) in {_bare(j) for j in ALLOW}


def _init_seq():
    global _seq
    try:
        with open(TRACKED_PATH) as f:
            for line in f:
                try:
                    _seq = max(_seq, json.loads(line).get("seq", 0))
                except Exception:
                    pass
    except FileNotFoundError:
        pass


def _append_tracked(evt):
    global _seq
    with _lock:
        _seq += 1
        row = {
            "seq": _seq,
            "chat": evt.get("Chat"),
            "id": evt.get("ID"),
            "sender": evt.get("SenderJID"),
            "ts": evt.get("Timestamp"),
            "fromMe": bool(evt.get("FromMe")),
            "text": evt.get("Text", ""),
            "chatName": evt.get("ChatName", ""),
            "media": (evt.get("Media") or {}).get("Type") if evt.get("Media") else None,
        }
        with open(TRACKED_PATH, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _read_tracked(since):
    rows = []
    try:
        with _lock:
            with open(TRACKED_PATH) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if r.get("seq", 0) > since:
                            rows.append(r)
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return rows


def _set_allowlist(jids):
    global ALLOW
    with _lock:
        tmp = ALLOWLIST_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"jids": sorted(set(jids))}, f)
        os.replace(tmp, ALLOWLIST_PATH)
        ALLOW = set(jids)
        # prune tracked rows that fell off the list
        kept, dropped = [], 0
        try:
            with open(TRACKED_PATH) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if _allowed(r.get("chat", "")):
                            kept.append(line)
                        else:
                            dropped += 1
                    except Exception:
                        dropped += 1
            with open(TRACKED_PATH + ".tmp", "w") as f:
                f.writelines(kept)
            os.replace(TRACKED_PATH + ".tmp", TRACKED_PATH)
        except FileNotFoundError:
            pass
    print(f"[shim] allowlist set: {len(ALLOW)} jids, pruned {dropped} rows", flush=True)


def _purge_wacli_store():
    """Delete non-allowlisted chat content from wacli's own db. Local only."""
    if not os.path.exists(WACLI_DB):
        return
    try:
        con = sqlite3.connect(WACLI_DB, timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        cur = con.execute("SELECT DISTINCT chat_jid FROM messages")
        targets = [r[0] for r in cur.fetchall() if r[0] and not _allowed(r[0])]
        total = 0
        for jid in targets:
            c = con.execute("DELETE FROM messages WHERE chat_jid=?", (jid,))
            total += c.rowcount
            con.commit()
        # FTS + chats rows best-effort; schema-dependent, fail silently per table
        for stmt, args in [
            ("DELETE FROM chats WHERE jid NOT IN (SELECT DISTINCT chat_jid FROM messages)", ()),
        ]:
            try:
                con.execute(stmt, args); con.commit()
            except sqlite3.Error:
                pass
        con.close()
        if total:
            print(f"[shim] purge: removed {total} non-tracked message rows ({len(targets)} chats)", flush=True)
    except Exception as e:
        print(f"[shim] purge error (retry next cycle): {type(e).__name__}", flush=True)


def _purge_loop():
    while True:
        time.sleep(PURGE_INTERVAL)
        _purge_wacli_store()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # default request logging leaks paths/ips; silence
        pass

    def _ok(self):
        return bool(TOKEN) and self.headers.get("Authorization", "") == "Bearer " + TOKEN

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n) if n else b"{}"

    def do_GET(self):
        if self.path == "/health":
            paired = subprocess.run(["wacli", "auth", "status"], capture_output=True).returncode == 0
            return self._send(200, {"ok": True, "paired": paired, "tracked": len(ALLOW), "seq": _seq})
        if self.path.startswith("/messages"):
            if not self._ok():
                return self._send(401, {"error": "unauthorized"})
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            since = int(q.get("since", ["0"])[0])
            rows = _read_tracked(since)
            return self._send(200, {"rows": rows, "seq": _seq})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        raw = self._body()
        # webhook lane: signed by wacli sync, localhost — content NEVER logged
        if self.path == "/hook":
            sig = self.headers.get("X-Wacli-Signature", "")
            want = "sha256=" + hmac.new(HOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
            if not HOOK_SECRET or not hmac.compare_digest(sig, want):
                return self._send(401, {"error": "bad signature"})
            try:
                evt = json.loads(raw)
            except Exception:
                return self._send(400, {"error": "bad json"})
            if evt.get("EventType"):          # receipts/presence: not stored
                return self._send(200, {"ok": True})
            if _allowed(evt.get("Chat", "")):
                _append_tracked(evt)
            # no match → falls through unwritten, by law
            return self._send(200, {"ok": True})

        if not self._ok():
            return self._send(401, {"error": "unauthorized"})
        body = json.loads(raw or b"{}")

        if self.path == "/allowlist":
            jids = body.get("jids")
            if not isinstance(jids, list):
                return self._send(400, {"error": "missing 'jids' list"})
            _set_allowlist(jids)
            _purge_wacli_store()
            return self._send(200, {"ok": True, "tracked": len(ALLOW)})

        to = body.get("to")
        if not to:
            return self._send(400, {"error": "missing 'to'"})
        try:
            if self.path == "/send":
                cmd = ["wacli", "send", "text", "--to", to, "--message", body.get("text", ""), "--json"]
            elif self.path == "/send-file":
                data = base64.b64decode(body.get("fileBase64", ""))
                tf = tempfile.NamedTemporaryFile(delete=False, suffix="_" + body.get("filename", "file.bin"))
                tf.write(data)
                tf.close()
                cmd = ["wacli", "send", "file", "--to", to, "--file", tf.name, "--caption", body.get("caption", ""), "--json"]
            else:
                return self._send(404, {"error": "not found"})
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            return self._send(200 if r.returncode == 0 else 502, {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr})
        except Exception as e:
            return self._send(500, {"error": str(e)})


if __name__ == "__main__":
    os.makedirs(CONF_DIR, exist_ok=True)
    _init_seq()
    threading.Thread(target=_purge_loop, daemon=True).start()
    print(f"[shim] v2 listening on :{PORT} (tracked jids: {len(ALLOW)})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
