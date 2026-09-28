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

# Note on Container Privilege (CWE-250 / P1): Open HEMS runs as root inside its isolated container
# namespace because Home Assistant OS mounts /data/options.json with 0600 root:root permissions and
# /config with root ownership. Container isolation is enforced by HAOS via Docker cgroups and AppArmor.
EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8099/healthz || exit 1

CMD [ "/run.sh" ]
