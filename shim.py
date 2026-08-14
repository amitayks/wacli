#!/usr/bin/env python3
"""Token-gated send shim in front of wacli. vex's guard has already
authorized the requester; this only accepts calls bearing the shared
GERMANICUS_WACLI_SEND_TOKEN and execs `wacli send`."""
import os, json, base64, tempfile, subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("GERMANICUS_WACLI_SEND_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

class H(BaseHTTPRequestHandler):
    def _ok(self):
        return bool(TOKEN) and self.headers.get("Authorization", "") == "Bearer " + TOKEN
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not found"})
    def do_POST(self):
        if not self._ok():
            return self._send(401, {"error": "unauthorized"})
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        to = body.get("to")
        if not to:
            return self._send(400, {"error": "missing 'to'"})
        try:
            if self.path == "/send":
                cmd = ["wacli", "send", "text", "--to", to, "--message", body.get("text", ""), "--json"]
            elif self.path == "/send-file":
                data = base64.b64decode(body.get("fileBase64", ""))
                tf = tempfile.NamedTemporaryFile(delete=False, suffix="_" + body.get("filename", "file.bin"))
                tf.write(data); tf.close()
                cmd = ["wacli", "send", "file", "--to", to, "--file", tf.name, "--caption", body.get("caption", ""), "--json"]
            else:
                return self._send(404, {"error": "not found"})
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            return self._send(200 if r.returncode == 0 else 502, {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr})
        except Exception as e:
            return self._send(500, {"error": str(e)})

if __name__ == "__main__":
    print(f"[shim] listening on :{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
