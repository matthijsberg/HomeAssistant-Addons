ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ghcr.io/langfuse/langfuse:4 AS langfuse-web-source
FROM ghcr.io/langfuse/langfuse-worker:4 AS langfuse-worker-source
FROM chrislusf/seaweedfs:latest AS seaweedfs-source

FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive

# Install dependencies: Node.js 22 LTS, PostgreSQL 16, Redis 7, Nginx, ClickHouse, tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    lsb-release \
    jq \
    openssl \
    nginx \
    redis-server \
    postgresql \
    postgresql-contrib \
    gosu \
    apt-transport-https \
    dirmngr \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install ClickHouse Server & Client
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.clickhouse.com/keys/clickhouse.key | gpg --dearmor -o /etc/apt/keyrings/clickhouse.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/clickhouse.gpg] https://packages.clickhouse.com/deb stable main" > /etc/apt/sources.list.d/clickhouse.list \
    && apt-get update && apt-get install -y --no-install-recommends clickhouse-server clickhouse-client \
    && rm -rf /var/lib/apt/lists/*

# Provide su-exec alias to gosu
RUN ln -s /usr/sbin/gosu /usr/local/bin/su-exec || true

# Copy SeaweedFS binary
COPY --from=seaweedfs-source /usr/bin/weed /usr/bin/weed

# Copy Langfuse Web assets to pristine location (for runtime base-path substitution)
COPY --from=langfuse-web-source /app /opt/langfuse-web-pristine

# Copy Langfuse Worker
COPY --from=langfuse-worker-source /app /opt/langfuse-worker

# Setup directory structure
RUN mkdir -p /data /opt/langfuse-web /run /var/log/nginx

# Copy rootfs containing S6-overlay services and scripts
COPY rootfs /

EXPOSE 8099 3000
