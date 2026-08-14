# wacli WhatsApp bridge for Germanicus — prebuilt binary (Tom's proven pattern),
# glibc base for the CGO/FTS5 release build, + token-gated send shim.
FROM debian:stable-slim
ARG WACLI_VERSION=0.11.1
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates ffmpeg tzdata python3 curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/wacli/bin /data/store \
    && curl -fsSL "https://github.com/openclaw/wacli/releases/download/v${WACLI_VERSION}/wacli_${WACLI_VERSION}_linux_amd64.tar.gz" \
       | tar -xz -C /opt/wacli/bin wacli \
    && chmod +x /opt/wacli/bin/wacli \
    && ln -s /opt/wacli/bin/wacli /usr/local/bin/wacli
ARG CACHE_BUST=2026081410
COPY shim.py /app/shim.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
WORKDIR /data
ENTRYPOINT ["/app/start.sh"]
