ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive

# Install PostgreSQL, Python3, build dependencies, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql \
    postgresql-contrib \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    libpq-dev \
    curl \
    jq \
    gettext-base \
    gzip \
    tar \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install matrix-synapse and psycopg2 in virtualenv
RUN python3 -m venv /opt/synapse-env \
    && /opt/synapse-env/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/synapse-env/bin/pip install --no-cache-dir matrix-synapse psycopg2

ENV PATH="/opt/synapse-env/bin:$PATH"

RUN mkdir -p /data /ssl /backup /config /share

COPY rootfs /

WORKDIR /data

CMD [ "/usr/bin/run.sh" ]
