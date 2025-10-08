#!/usr/bin/env bash

# Install Roon Server if not already installed
if [ ! -d /data/RoonServer ]; then
    echo "Roon Server not found in /data, installing..."
    curl -o RoonServer_linuxx64.tar.bz2 http://download.roonlabs.net/builds/RoonServer_linuxx64.tar.bz2
    tar -xjf RoonServer_linuxx64.tar.bz2
    rm RoonServer_linuxx64.tar.bz2
    mv RoonServer /data/
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
/data/RoonServer/start.sh

# Keep the container running by tailing the log file
# The log file is created by Roon's start script inside the /data directory
while [ ! -f /data/RoonServer/Logs/RoonServer_log.txt ]; do
    sleep 1
done
tail -f /data/RoonServer/Logs/RoonServer_log.txt