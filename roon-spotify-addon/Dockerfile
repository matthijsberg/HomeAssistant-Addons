ARG BUILD_FROM

# Dockerfile for Roon Spotify Add-on

# Build stage for spotifyd (Using Debian Buster to match base image)
FROM rust:1.73.0-buster AS spotifyd-builder
RUN apt-get update && apt-get install -y libasound2-dev pkg-config build-essential git
RUN git clone https://github.com/Spotifyd/spotifyd.git && \
    cd spotifyd && \
    cargo build --release

# Main image
FROM ${BUILD_FROM}

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    cifs-utils \
    bzip2 \
    alsa-utils \
    jq \
    bash \
    wget

# Copy spotifyd from the build stage
COPY --from=spotifyd-builder /spotifyd/target/release/spotifyd /usr/bin/spotifyd

# Copy the ALSA configuration
COPY asoundrc /etc/asound.conf

# Copy the run script
COPY run.sh /
RUN chmod +x /run.sh

CMD [ "/run.sh" ]