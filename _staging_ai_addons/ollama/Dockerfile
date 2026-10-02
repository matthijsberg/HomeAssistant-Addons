ARG BUILD_FROM
FROM intelanalytics/ipex-llm-inference-cpp-xpu:latest

# Install python dependencies for our helper scripts
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-psutil \
    python3-requests \
    jq \
    zstd \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for hardware acceleration
ENV DEVICE=NPU
ENV OLLAMA_NUM_GPU=999
ENV ONEAPI_DEVICE_SELECTOR=level_zero:0
ENV ZES_ENABLE_SYSMAN=1
ENV OLLAMA_HOST="0.0.0.0"
ENV OLLAMA_MODELS="/share/ollama/models"

# Copy files
COPY run.sh /
COPY check_hardware.py /
COPY fetch_models.py /
COPY web_server.py /
COPY index.html /

RUN chmod a+x /run.sh

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:11434/ || exit 1

CMD [ "/run.sh" ]
