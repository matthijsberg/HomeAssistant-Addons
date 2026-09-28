ARG BUILD_FROM=debian:bookworm-slim
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/venv/bin:$PATH"

# Install system dependencies and static analysis tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    jq \
    tar \
    util-linux \
    procps \
    ca-certificates \
    shellcheck \
    && rm -rf /var/lib/apt/lists/*

# Install Gitleaks (amd64)
RUN curl -fsSL https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz \
    | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks

# Install Hadolint (amd64)
RUN curl -fsSL -o /usr/local/bin/hadolint https://github.com/hadolint/hadolint/releases/download/v2.12.0/hadolint-Linux-x86_64 \
    && chmod +x /usr/local/bin/hadolint

# Set up Python virtual environment
RUN python3 -m venv /opt/venv

# Install Python requirements
WORKDIR /opt/mantis-security-agent
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . /opt/mantis-security-agent/

# Set up runtime directories
RUN mkdir -p /data/cache /data/reports /data/workspaces \
    && chmod +x /opt/mantis-security-agent/run.sh

ENTRYPOINT ["/opt/mantis-security-agent/run.sh"]
