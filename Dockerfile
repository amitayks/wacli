# Build wacli from source (drops the upstream `VOLUME` line Railway rejects).
FROM golang:1.26.5-alpine AS build
RUN apk add --no-cache build-base ca-certificates git
ENV CGO_ENABLED=1 CGO_CFLAGS="-Wno-error=missing-braces"
RUN go install -tags sqlite_fts5 github.com/openclaw/wacli/cmd/wacli@latest \
    && cp /go/bin/wacli /usr/local/bin/wacli

FROM alpine:3.23
RUN apk add --no-cache ca-certificates ffmpeg tzdata python3 \
    && adduser -D -u 10001 -h /home/wacli wacli \
    && mkdir -p /data/store /data/state /data/config /data/cache /app \
    && chown -R wacli:wacli /data /app
ENV HOME=/home/wacli \
    WACLI_STORE_DIR=/data/store \
    XDG_STATE_HOME=/data/state \
    XDG_CONFIG_HOME=/data/config \
    XDG_CACHE_HOME=/data/cache
COPY --from=build /usr/local/bin/wacli /usr/local/bin/wacli
COPY shim.py /app/shim.py
COPY start.sh /app/start.sh
USER root
RUN chmod +x /app/start.sh
USER wacli
WORKDIR /data
ENTRYPOINT ["/app/start.sh"]
