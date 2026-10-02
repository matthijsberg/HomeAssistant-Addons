FROM python:3.11-bookworm

ENV LANG C.UTF-8

# Install build dependencies, Node.js (v22), and Git
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    curl \
    git \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    aiohttp \
    websockets \
    pydantic \
    google-auth \
    google-auth-oauthlib \
    google-auth-httplib2 \
    twilio \
    matrix-nio

# Copy application files
COPY . .

# Make run script executable
RUN chmod +x run.sh

CMD [ "./run.sh" ]
