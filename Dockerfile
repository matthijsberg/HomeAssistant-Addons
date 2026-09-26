ARG BUILD_FROM=alpine:3.20
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install python and runtime dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-requests \
    py3-yaml \
    py3-numpy \
    py3-scipy \
    curl \
    jq \
    bash

WORKDIR /opt/open-hems

# Copy HEMS codebase
COPY . /opt/open-hems/

# Copy and prepare entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8099/healthz || exit 1

CMD [ "/run.sh" ]
