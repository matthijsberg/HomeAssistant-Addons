FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    HF_HOME=/data/hf

# Install system dependencies and GPG
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    jq \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Add Intel official GPU repository for Ubuntu 24.04 (Noble) and install Intel compute drivers
RUN curl -fsSL https://repositories.intel.com/gpu/intel-graphics.key | \
    gpg --dearmor --output /usr/share/keyrings/intel-graphics.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu noble client unified" > /etc/apt/sources.list.d/intel-gpu.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    intel-opencl-icd \
    libze-intel-gpu1 \
    libze1 \
    ocl-icd-libopencl1 \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch with Intel XPU and CPU acceleration support
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/xpu \
    torch torchvision

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
