ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM ghcr.io/langfuse/langfuse:4 AS langfuse-web-source
FROM ghcr.io/langfuse/langfuse-worker:4 AS langfuse-worker-source
FROM chrislusf/seaweedfs:latest AS seaweedfs-source
FROM clickhouse/clickhouse-server:26.3-alpine AS clickhouse-source

FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install dependencies in Alpine: Node.js, PostgreSQL 16, Redis, Nginx, su-exec, tools
RUN apk add --no-cache \
    nodejs \
    npm \
    postgresql16 \
    postgresql16-contrib \
    redis \
    nginx \
    curl \
    jq \
    openssl \
    su-exec \
    bash \
    ca-certificates \
    libc6-compat \
    gcompat

# Install golang-migrate CLI for ClickHouse migrations
ARG TARGETARCH
RUN case "${TARGETARCH:-amd64}" in \
      amd64) arch=amd64 ;; \
      arm64) arch=arm64 ;; \
      aarch64) arch=arm64 ;; \
      *) arch=amd64 ;; \
    esac \
    && curl -fsSL "https://github.com/golang-migrate/migrate/releases/download/v4.18.2/migrate.linux-${arch}.tar.gz" | tar -xz -C /usr/local/bin migrate \
    && chmod +x /usr/local/bin/migrate

# Copy binaries from Langfuse Web (includes dumb-init, etc.)
COPY --from=langfuse-web-source /usr/local/bin /usr/local/bin

# Install global prisma to ensure exact path resolution in all shells
RUN npm install -g prisma@5.22.0 || true

# Copy ClickHouse binary & glibc donor runtime libraries from clickhouse-source
COPY --from=clickhouse-source /lib/libc.so.6 /lib/libpthread.so.0 /lib/libdl.so.2 /lib/libm.so.6 /lib/librt.so.1 /lib/libresolv.so.2 /lib/
COPY --from=clickhouse-source /lib/ld-*.so /lib/
COPY --from=clickhouse-source /lib64 /lib64
COPY --from=clickhouse-source /usr/bin/clickhouse /usr/bin/clickhouse
RUN ln -s /usr/bin/clickhouse /usr/bin/clickhouse-server \
    && ln -s /usr/bin/clickhouse /usr/bin/clickhouse-client \
    && mkdir -p /etc/clickhouse-server /var/lib/clickhouse /var/log/clickhouse-server
COPY --from=clickhouse-source /etc/clickhouse-server /etc/clickhouse-server

# Copy SeaweedFS binary
COPY --from=seaweedfs-source /usr/bin/weed /usr/bin/weed

# Copy Langfuse Web assets to pristine location (for runtime base-path substitution)
COPY --from=langfuse-web-source /app /opt/langfuse-web-pristine

# Copy Langfuse Worker
COPY --from=langfuse-worker-source /app /opt/langfuse-worker

ENV PATH="/usr/local/bin:/opt/langfuse-web/node_modules/.bin:$PATH"

# Setup directory structure
RUN mkdir -p /data /opt/langfuse-web /run /var/log/nginx

# Copy rootfs containing S6-overlay services and scripts
COPY rootfs /

EXPOSE 8099 3000
