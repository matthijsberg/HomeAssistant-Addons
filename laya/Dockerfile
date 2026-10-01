FROM python:3.12-slim

ENV LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hf

# Install minimal OS runtime packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only PyTorch first (PRD requirement: CPU-only, avoid CUDA bloat)
ARG TORCH_VERSION=2.5.1
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==${TORCH_VERSION}

# Install pinned laya[serve] and application dependencies
ARG LAYA_VERSION=0.3.21
RUN pip install --no-cache-dir \
    "laya[serve]==${LAYA_VERSION}" \
    fastapi \
    "uvicorn[standard]" \
    pydantic

# Create persistent storage dir and app directory
RUN mkdir -p /data/hf /usr/src/app

COPY rootfs /

WORKDIR /usr/src/app

RUN chmod +x /run.sh

EXPOSE 8000

CMD ["/run.sh"]
