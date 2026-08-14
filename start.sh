#!/bin/sh
# Runs the send-shim alongside the wacli session keeper. First boot with
# WACLI_PAIR_PHONE set requests a pairing code (printed to logs); after the
# code is approved on the phone, subsequent boots run `sync --follow`.
set -e
mkdir -p /data/store /data/state /data/config /data/cache
python3 /app/shim.py &
if wacli auth status >/dev/null 2>&1; then
  echo "[start] paired -> sync --follow"
  exec wacli sync --follow
elif [ -n "$WACLI_PAIR_PHONE" ]; then
  echo "[start] NOT paired -> requesting code for $WACLI_PAIR_PHONE"
  echo "[start] approve it in WhatsApp > Linked devices > Link with phone number"
  wacli auth --phone "$WACLI_PAIR_PHONE" --events || true
  echo "[start] auth exited; restart will resume in sync mode if pairing succeeded"
  sleep 5
else
  echo "[start] NOT paired and no WACLI_PAIR_PHONE set; idling (set it + redeploy to pair)"
  wait
fi
