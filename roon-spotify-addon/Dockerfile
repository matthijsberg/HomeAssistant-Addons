ARG BUILD_FROM

# Dockerfile for Roon Spotify Add-on

# Build stage for spotifyd
FROM rust:latest AS spotifyd-builder
RUN apk add --no-cache alsa-lib-dev pkgconfig build-base git
RUN git clone https://github.com/Spotifyd/spotifyd.git && \
    cd spotifyd && \
    cargo build --release

# Main image
FROM ${BUILD_FROM}

# Install dependencies
RUN apk add --no-cache \
    ffmpeg \
    curl \
    cifs-utils \
    bzip2 \
    alsa-utils \
    jq

# Copy spotifyd from the build stage
COPY --from=spotifyd-builder /spotifyd/target/release/spotifyd /usr/bin/spotifyd

# Copy the ALSA configuration
COPY asoundrc /etc/asound.conf

# Copy the run script
COPY run.sh /
RUN chmod +x /run.sh

CMD [ "/run.sh" ]