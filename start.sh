#!/bin/sh
# Runs the send-shim alongside the wacli session keeper. On first boot with
# WACLI_PAIR_PHONE set (and no existing session), requests a pairing code
# (printed to logs); after the code is approved on the phone, `auth` proceeds
# into sync. Later boots detect the saved session and run `sync --follow`.
set -e
mkdir -p /data/store /data/state /data/config /data/cache
python3 /app/shim.py &

# `auth status` exits 0 even when unpaired, so detect by its text.
STATUS="$(wacli auth status 2>&1 || true)"
echo "[start] auth status: $STATUS"
if echo "$STATUS" | grep -qiE "not authenticated|no session|run .?wacli auth"; then
  if [ -n "$WACLI_PAIR_PHONE" ]; then
    echo "[start] pairing $WACLI_PAIR_PHONE -- APPROVE the code below in WhatsApp > Linked devices > Link with phone number"
    exec wacli auth --phone "$WACLI_PAIR_PHONE"
  else
    echo "[start] not paired and no WACLI_PAIR_PHONE; idling"
    wait
  fi
else
  echo "[start] session found -> sync --follow"
  exec wacli sync --follow
fi
