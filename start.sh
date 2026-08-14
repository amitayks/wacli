#!/bin/sh
# Session keeper + send shim. Pairing is DELIBERATE (WACLI_PAIR=1) so the
# container never loops pairing requests into WhatsApp's rate limiter.
set -e
mkdir -p /data/store /data/state /data/config /data/cache
python3 /app/shim.py &

STATUS="$(wacli auth status 2>&1 || true)"
echo "[start] auth status: $STATUS"
if echo "$STATUS" | grep -qiE "not authenticated|no session|run .?wacli auth"; then
  if [ "$WACLI_PAIR" = "1" ] && [ -n "$WACLI_PAIR_PHONE" ]; then
    echo "[start] PAIRING (one cycle) $WACLI_PAIR_PHONE -- approve the code in WhatsApp > Linked devices"
    # On success, `auth` proceeds into sync and blocks (never returns).
    wacli auth --phone "$WACLI_PAIR_PHONE" || echo "[start] pairing cycle ended without approval"
    echo "[start] idling to avoid WhatsApp 429; set WACLI_PAIR=1 + redeploy to retry pairing"
    exec tail -f /dev/null
  else
    echo "[start] not paired; idling (zero WhatsApp calls). Set WACLI_PAIR=1 + redeploy to pair."
    exec tail -f /dev/null
  fi
else
  echo "[start] session found -> sync --follow"
  exec wacli sync --follow --max-db-size 512MB
fi
