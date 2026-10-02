# Home Assistant Add-on: Matrix Homeserver (Conduit)

This Add-on runs a private, self-hosted **Matrix Homeserver** using **Conduit**, a high-performance Rust implementation designed for low memory consumption (< 50MB RAM).

## Configuration

In the Add-on **Configuration** tab:

- **`server_name`**: The public or local domain name for your Matrix server (e.g. `matrix.home.arpa` or `hass.b3rg.nl`).
- **`allow_registration`**: Set to `true` if you want to allow new users to register accounts directly via Matrix clients (e.g., Element). Set to `false` (recommended) to restrict registration.
- **`port`**: Listening port for Matrix Client-Server API requests (default: `6167`).
- **`log_level`**: Container logging level (`warn`, `info`, `debug`).

## Connecting Clients

Once started:
1. Open your Matrix client (Element, FluffyChat, etc.).
2. Set the Homeserver URL to `http://<YOUR_HA_IP>:6167`.
3. Create your admin account (if registration is temporarily enabled) or log in.
