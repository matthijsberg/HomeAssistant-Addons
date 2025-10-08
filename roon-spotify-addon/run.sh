#!/bin/bash
set -e

# Install Roon Server if not already installed
if [ ! -d /data/roon/RoonServer ]; then
    echo "Installing Roon Server..."
    mkdir -p /data/roon/RoonServer
    curl -O http://download.roonlabs.net/builds/roonserver-installer-linuxx64.sh && \
    chmod +x roonserver-installer-linuxx64.sh && \
    ./roonserver-installer-linuxx64.sh && \
    rm roonserver-installer-linuxx64.sh
    mv /opt/RoonServer /data/roon/
    if [ -d /var/roon ]; then
        mv /var/roon/* /data/roon/
        rm -rf /var/roon
    fi
    ln -s /data/roon /var/roon
fi

# Get options
SPOTIFY_USERNAME=$(jq -r '.spotify_username' /data/options.json)
SPOTIFY_PASSWORD=$(jq -r '.spotify_password' /data/options.json)
SPOTIFY_DEVICE_NAME=$(jq -r '.spotify_device_name' /data/options.json)

# Create spotifyd.conf
cat > /etc/spotifyd.conf <<EOL
[global]
username = "${SPOTIFY_USERNAME}"
password = "${SPOTIFY_PASSWORD}"
device_name = "${SPOTIFY_DEVICE_NAME}"
backend = "alsa"
device = "default"
EOL

# Start spotifyd
echo "Starting spotifyd..."
spotifyd --no-daemon &

# Start Roon Server
echo "Starting Roon Server..."
/data/roon/RoonServer/start.sh

# Wait for the log file to be created
while [ ! -f /data/roon/RoonServer/Logs/RoonServer_log.txt ]; do
    sleep 1
done

# Keep the container running by tailing the log file
tail -f /data/roon/RoonServer/Logs/RoonServer_log.txt
