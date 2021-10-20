ARG BUILD_FROM=ghcr.io/hassio-addons/debian-base/amd64:5.1.1
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG C.UTF-8
ENV ROON_SERVER_PKG RoonServer_linuxx64.tar.bz2
ENV ROON_SERVER_URL http://download.roonlabs.com/builds/${ROON_SERVER_PKG}
ENV ROON_DATAROOT /data
ENV ROON_ID_DIR /data

# According to instruction from Roon and for future non root process?
RUN addgroup root audio

# Install requirements for add-on
RUN apt-get update \
        && apt-get -y upgrade \
        && apt-get -y install bash curl bzip2 ffmpeg cifs-utils alsa-utils wget

ARG BUILD_DATE
ARG BUILD_REF
ARG BUILD_VERSION

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Matthijs" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Home Assistant Community Add-ons" \
    org.opencontainers.image.authors="Matthijs" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://addons.community" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
