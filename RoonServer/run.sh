#!/usr/bin/with-contenv bashio
cd /backup
if test ! -d roon; then
        echo "Back-up directory not found, creating in /backup/roon for roon native back-ups. Mapped to HA Back-up dir."
        mkdir roon
fi
mkdir /app
cd /app
if test ! -d RoonServer; then
        echo "Downloading Roon Server Installer"
        curl $ROON_SERVER_URL -O
        tar xjf $ROON_SERVER_PKG
        rm -f $ROON_SERVER_PKG
fi
echo "Roonserver Installed"
sleep 1s
if test ! -d /data/RoonServer/Database/Registry/Core; then
        echo "First time Roon install, no existing directories for roon found in data partition"
        echo "Creating directories manually to setup automated back-up schedule..."
        mkdir /data/RoonServer/
        mkdir /data/RoonServer/Database/
        mkdir /data/RoonServer/Database/Registry/
        mkdir /data/RoonServer/Database/Registry/Core
        echo "   created /data/RoonServer/Database/Registry/Core"
else
        echo "Existing installation found, checking config files for backup..."
fi
cd /data/RoonServer/Database/Registry/Core
if test ! -f backups; then
        echo "   Back-up schedule not found, downloading default for this HA install"
        wget https://github.com/matthijsberg/HomeAssistant-Addons/blob/main/RoonServer/config/backups
else
        echo "   Existing Backup schedule config found, not toutching. DO CHECK FILE PATH! Should point to /backup. That folder is mapped to HA backup folder."
fi
if test ! -f backups_status; then
        echo "   Default back-up path not set, downloading default for this HA install"
        wget https://github.com/matthijsberg/HomeAssistant-Addons/blob/main/RoonServer/config/backups_status
else
        echo "   Default back-up path config file found, not toutching. DO CHECK FILE PATH! Should point to /backup. That folder is mapped to HA backup folder."
fi
echo "Installation an basic configuration finished! Starting service."
cd /app/RoonServer/
/app/RoonServer/start.sh
