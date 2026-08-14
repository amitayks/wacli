#!/bin/sh
# Session keeper + send shim, Tom-pattern hardened. Pairing is deliberate
# (WACLI_PAIR=1) so the container never loops pairing into WhatsApp's limiter.
set -e
STORE=/data/store
mkdir -p "$STORE"
# stale-LOCK cleanup (Tom's ExecStartPre): clear artifacts left by a hard kill
if [ -f "$STORE/LOCK" ]; then
  kill -0 "$(cat "$STORE/LOCK" 2>/dev/null)" 2>/dev/null || rm -f "$STORE/LOCK" "$STORE/HEARTBEAT" "$STORE/.send.sock"
fi
python3 /app/shim.py &

STATUS="$(wacli --store "$STORE" auth status 2>&1 || true)"
echo "[start] auth status: $STATUS"
if echo "$STATUS" | grep -qiE "not authenticated|no session|run .?wacli auth"; then
  if [ "$WACLI_PAIR" = "1" ] && [ -n "$WACLI_PAIR_PHONE" ]; then
    echo "[start] PAIRING (one cycle) $WACLI_PAIR_PHONE -- approve the code in WhatsApp > Linked devices"
    wacli --store "$STORE" auth --phone "$WACLI_PAIR_PHONE" || echo "[start] pairing cycle ended without approval"
    echo "[start] idling to avoid WhatsApp 429; set WACLI_PAIR=1 + redeploy to retry"
    exec tail -f /dev/null
  else
    echo "[start] not paired; idling (zero WhatsApp calls). Set WACLI_PAIR=1 + redeploy to pair."
    exec tail -f /dev/null
  fi
else
  echo "[start] session found -> sync --follow"
  exec wacli --store "$STORE" sync --follow --disable-history-sync
fi
