FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG OLLAMA_VERSION=0.5.7

# Labels for better HA integration
LABEL \
    io.hass.name="Intel AI Core: Ollama" \
    io.hass.description="Ollama with Intel Arc GPU support for Arrow Lake" \
    io.hass.type="addon" \
    io.hass.version="${OLLAMA_VERSION}"

# 1. Base tools & Intel Repos
RUN apt-get update && apt-get install -y \
    curl wget gnupg2 pciutils clinfo jq python3 python3-requests \
    && wget -qO - https://repositories.intel.com/gpu/intel-graphics.key | gpg --dearmor --output /usr/share/keyrings/intel-graphics.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu noble client" > /etc/apt/sources.list.d/intel-gpu.list \
    && apt-get update

# 2. Install Intel Compute Runtime
RUN apt-get install -y \
    intel-opencl-icd \
    level-zero \
    intel-level-zero-gpu \
    libze1 \
    && rm -rf /var/lib/apt/lists/*

# 3. Install Bashio
RUN curl -J -L -o /tmp/bashio.tar.gz \
    "https://github.com/hassio-addons/bashio/archive/v0.16.2.tar.gz" && \
    mkdir /tmp/bashio && \
    tar zxvf /tmp/bashio.tar.gz --strip 1 -C /tmp/bashio && \
    mv /tmp/bashio/lib /usr/lib/bashio && \
    ln -s /usr/lib/bashio/bashio /usr/bin/bashio && \
    rm -rf /tmp/bashio

# 4. Install Ollama
RUN curl -L "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tgz" -o ollama.tgz && \
    tar -C /usr -xzf ollama.tgz && \
    rm ollama.tgz

# Environment Setup
ENV OLLAMA_INTEL_GPU=1

# Healthcheck added: Checks if Ollama API responds every 30s
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:11434/ || exit 1

COPY fetch_models.py /usr/bin/fetch_models.py
COPY web_server.py /web_server.py
COPY index.html /index.html
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENTRYPOINT ["/run.sh"]