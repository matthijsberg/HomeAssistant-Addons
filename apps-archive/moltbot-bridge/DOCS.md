# Moltbot-HA Bridge Add-on Documentation

This bridge connects Home Assistant with various messaging platforms and ecosystem services.

## Home Assistant Setup

Good news! The Moltbot Bridge is now **fully automatic**. 

The add-on uses the internal Home Assistant API permission to connect. You do **not** need to create a Long-lived Access Token or configure the URL unless you are connecting to a *remote* Home Assistant instance.

### Manual Override (Optional)
If you need to connect to a different Home Assistant instance:
1. Set the `ha_url` in the configuration (e.g., `http://192.168.1.10:8123/api`).
2. Provide a Long-lived Access Token in the `ha_token` field.


## Messaging Platforms

### Twilio (WhatsApp)
1. Go to the [Twilio Console](https://www.twilio.com/console).
2. Find your **Account SID** and **Auth Token** on the dashboard.
3. For the **From** field, use your WhatsApp enabled Twilio phone number (e.g., `whatsapp:+14155238886`).

### BlueBubbles (iMessage)
1. Ensure you have a BlueBubbles server running on a Mac.
2. Go to the BlueBubbles server settings to find your **Server URL** and **API Password**.

### Matrix
1. You will need a **Home Server URL** and an **Access Token** for a dedicated bridge user.
2. Consult your Matrix provider on how to generate a bot/access token.

## Google Home Integration
1. Place your `google_creds.json` file in the `/config/moltbot/` directory on your Home Assistant machine.
2. The bridge will automatically detect and use these credentials for HomeGraph synchronization.
