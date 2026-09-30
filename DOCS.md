# wacli bridge — full system docs (personal-CRM WhatsApp)

One page, everything. The code this describes lives in THIS directory and in
repo `amitayks/wacli` (main) — the repo is what Railway builds; this workspace
dir mirrors it. **If they ever disagree, the repo wins — re-sync the mirror
(the 2026-09-22 crash was caused by a stale mirror).**

## What runs where

```
Railway project germanicus-wacli (9ece87e9-34c7-4f10-b36e-27f770580eb1)
  env production (6e21ca66-c6fd-4447-b630-afd2cb995e5e)
  service wacli   (9cccc4f3-02e7-4020-8108-609ccd56678c)  ← Amitay's PERSONAL account
  service wacli-notify (0c1f4e3a-…)  ← RETIRED transport (Germanicus account), kept for other uses
  volume at /data (persistent: WhatsApp session + stores)

Container (Dockerfile, prebuilt wacli binary v0.18.2, debian-slim):
  start.sh   — LOCK cleanup → shim → auth check (output-grep) → wacli sync --follow
               --store /data/store --max-db-size 512MB --presence-mode quiet
               --webhook http://127.0.0.1:$PORT/hook (HMAC-signed live messages)
  shim.py v2 — one HTTP server, bearer-gated:
    POST /send, /send-file     send lane (unchanged contract; execs wacli send --store /data/store)
    POST /hook                 webhook intake (HMAC X-Wacli-Signature; localhost caller)
    GET  /messages?since=SEQ   tracked rows, seq-cursored
    POST /allowlist {jids:[]}  atomic allowlist replace + prune + store purge
    GET  /health               {ok, paired, tracked, seq} (no auth)

Germanicus workspace (mdlk9lem5y99):
  config/tracked-contacts.json      — THE allowlist source of truth (edit = config change, no deploy)
  .runline/plugins/wacli/index.ts   — the ONE plugin surface: health / messages / allowlist_sync / send (gated)
  sensor 5z9o0avmbsko crm-chat-capture — 3-min silent tick: push allowlist → pull /messages →
                                      per-chat debounce (quietWindowMin) → ONE wake-ledger action row per settled conversation
  outbox/wake-ledger.jsonl          — delivery engine (vex-pack-assistant wake sensor fires rows)
```

## Data flow (read lane)
WhatsApp msg (tracked chat) → wacli sync → webhook POST /hook → allowlist match
→ append /data/config/tracked.jsonl (seq++) → sensor pulls since seq → buffers
per chat → chat quiet ≥ quietWindowMin → wake-ledger row {kind:action,
info.messages} → wake turn: mode report → review file; mode auto → spine
encounters + future task/reminder rows.

## PRIVACY LAW (hard)
Non-tracked messages are NEVER persisted: /hook drops them in memory before
any write; a purge thread deletes non-allowlisted rows from wacli's own
wacli.db every PURGE_INTERVAL_SEC (600) and on every /allowlist change; shim
logs carry counts and tracked JIDs only — never content, never non-tracked
identifiers.

## Send lane (gated)
`wacli.send {to, text, gateToken}` (plugin) → consumes a single-use spine
grant (action "wacli.send", args {to,text}; mirrors spine grant.consume:
canonical args-hash, digest-keyed, burn anchor spine/anchors/consumed.log
checked first, fail-closed) → only then POSTs /send. No token = refusal.
There is no ungated path; legacy direct-shim callers are retired.

## Env vars (service wacli)
- GERMANICUS_WACLI_SEND_TOKEN — bearer for /send, /messages, /allowlist
- WACLI_WEBHOOK_SECRET — HMAC key for /hook (set 2026-09-22)
- WACLI_PAIR + WACLI_PAIR_PHONE — deliberate pairing only (429 protection)
- PURGE_INTERVAL_SEC, WACLI_DB, SHIM_CONF_DIR — shim tuning (defaults fine)

## Debug quick hits
```sh
curl $URL/health                                        # paired? tracked? seq?
curl -H "Authorization: Bearer $TOK" "$URL/messages?since=0"
curl -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
     -d '{"jids":["9725XXXXXXXX@s.whatsapp.net"]}' $URL/allowlist
# Railway logs: query deploymentLogs(deploymentId:…) via backboard GraphQL
# sensor: sensors.run {id:"5z9o0avmbsko", dryRun:true} via /mutate
```

## Root cause of the 8-day zero-capture (2026-09-30, paid-in-blood)
Capture had produced ZERO rows since the 2026-09-22 rewrite. Root cause was
NOT the allowlist or hidden IDs — it was `start.sh` calling `wacli sync --follow`
with a `--webhook http://127.0.0.1:8080/hook` but WITHOUT `--webhook-allow-private`.
wacli's default webhook HTTP client (`syncWebhookSafeHTTPClient`) refuses any URL
resolving to localhost/private ranges, so EVERY webhook POST failed
("post webhook http://127.0.0.1:8080: Post failed" in sync logs) and nothing was
ever handed to the shim. Fix: add `--webhook-allow-private` to the sync flags.
Two silent-failure lessons baked in the same fix:
- `/health` now carries `stats` (hook_msgs / tracked / drop_* / lid_resolved /
  last_tracked_at) — counts only, never identifiers or content — so a dead pipe
  is visible without reading content. Watch: sensor `3fkmb8m4yr8s`
  (wacli-capture-health) wakes Germanicus on unreachable / unpaired / 48h-silent.
- shim v2.1 also resolves `@lid` chat JIDs to phone numbers via whatsmeow's
  `whatsmeow_lid_map` in session.db (read-only) — belt-and-braces in case a
  tracked chat ever arrives under a hidden ID. Our chats arrived as plain PN,
  so this was not the cause, but it removes a whole future failure class.
CHECKLIST for any wacli sync webhook to a co-located shim: localhost target ⇒
`--webhook-allow-private` is MANDATORY, or every POST silently fails.

## Deploy / rollback runbook (paid-in-blood, 2026-09-22)
1. Commit to `amitayks/wacli` main (git push with classic PAT; NEVER the
   GitHub contents API from the plugin — it double-base64s files).
2. Deploy: `serviceInstanceDeployV2(serviceId, environmentId, commitSha: "<sha>")`.
   **Always pass commitSha** — after any deploymentRedeploy the service is
   pinned to the old commit and a bare DeployV2 rebuilds THAT.
3. Verify: deployment meta.commitHash == your sha, then /health shows the v2
   shape, then a /messages probe.
4. Rollback: `deploymentRedeploy(id: <last-good-deployment-id>)` — restores
   the old image in ~60s. Session on the volume survives.
5. Known traps:
   - **Every wacli call needs `--store /data/store`** (auth status, sync,
     send). Dropping it = "not authenticated" crash-loop on a healthy session.
   - `wacli auth status` exit code lies — grep output for "not authenticated".
   - 0.18.x release tarballs prefix members `./` (tar -xz ... ./wacli).
   - Never build `@latest` on a session-carrying service; ARG WACLI_VERSION pins.
   - Bump ARG CACHE_BUST when only COPY'd files changed.
   - Re-pair only deliberately: WACLI_PAIR=1 + phone code (one cycle, then off).
   - **Sensor sandbox (keisar): `node.fetch` BLANKS a URL held in a workspace
     secret** (redactor) → "fetch() URL must not be a blank string". Bridge
     calls from the capture sensor go through `node.process.exec` curl with
     `$GERMANICUS_WACLI_URL` expanded in the child's own env. Also: no `_e`
     secrets global exists in this sandbox; `node.process.env` works for
     KEISAR_VEX_AMITAY only.

## Design source
openspec change `personal-crm-whatsapp` (proposal/specs/design/tasks) in the
Germanicus workspace; knowledge: knowledge/Reference/wacli-bridge.md,
knowledge/projects/personal-crm-whatsapp (spine matter of the same name).
