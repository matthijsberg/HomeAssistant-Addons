# Roon Spotify Home Assistant Add-on

This Home Assistant add-on runs the Roon Server software alongside a Spotify Connect endpoint. This allows you to select your Home Assistant device as a Spotify Connect speaker and have the audio play through your Roon audio outputs.

## Features

*   **Roon Server:** Runs the official Roon Server software in a container.
*   **Spotify Connect:** Includes `spotifyd` to create a Spotify Connect endpoint.
*   **Audio Piping:** Uses an ALSA loopback device to pipe audio from Spotify directly to Roon.
*   **Persistent Data:** Roon's database and settings are stored in Home Assistant's `/data` directory, so they persist across add-on restarts and updates.

## Installation

1.  **Copy the Add-on:** Copy the `roon-spotify-addon` directory to the `/addons` directory of your Home Assistant installation.
2.  **Add the Repository:** In Home Assistant, go to the Add-on Store, click the three dots in the top right, select "Repositories," and add your local addons path (e.g., `/addons`).
3.  **Install:** The "Roon Spotify Add-on" should now appear in your local add-ons at the bottom of the Add-on Store. Click on it and then click "Install."

## Configuration

1.  **Spotify Credentials:** Before starting the add-on, go to the "Configuration" tab and enter your Spotify username and password. You can also set a custom name for the Spotify Connect device.
2.  **Start the Add-on:** Go to the "Info" tab and click "Start."
3.  **Enable Roon Input:**
    *   Open your Roon Remote on another device.
    *   Go to `Settings` > `Audio`.
    *   Under "Input Devices," you should see a new ALSA device. It might be named after the loopback device (e.g., "Loopback").
    *   Enable this device.

## Usage

1.  Start playing music from the Spotify app on your phone or computer.
2.  Select the device name you configured in the add-on (default is "Roon").
3.  In Roon, select the new ALSA input device as your audio source to hear the Spotify stream.

## Troubleshooting

*   **Logs:** Check the add-on logs in the "Log" tab in Home Assistant for any errors related to `spotifyd` or Roon Server.
*   **ALSA:** This is a complex audio setup. If you have issues, you may need to experiment with the `/etc/asound.conf` file within the container.
*   **Spotify Hi-Fi:** Support for Spotify's lossless streaming is dependent on the `spotifyd` project and is not guaranteed.
