ARG BUILD_FROM

# Dockerfile for Roon Spotify Add-on

# Build stage for spotifyd (Using Debian Bullseye to match base image)
FROM rust:bullseye AS spotifyd-builder
RUN apt-get update && apt-get install -y libasound2-dev pkg-config build-essential git
# Use cargo install for the latest stable version
RUN cargo install spotifyd --locked

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
COPY --from=spotifyd-builder /usr/local/cargo/bin/spotifyd /usr/bin/spotifyd

# Copy the ALSA configuration
COPY asoundrc /etc/asound.conf

# Copy the run script
COPY run.sh /
RUN chmod +x /run.sh

CMD [ "/run.sh" ]
