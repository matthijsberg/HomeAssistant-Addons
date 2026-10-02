%%DASHBOARD_TOKEN_MAPS%%

    # ── HTTP (direct LAN access) ─────────────────────────────────────
    server {
        listen %%HTTP_PORT%%;
        server_name _;

        server_tokens off;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header Referrer-Policy "no-referrer" always;

        %%AUTH_BASIC_ON%%

        location = / {
            %%AUTH_BASIC_OFF%%
            root /var/www;
            try_files /landing.html =404;
            add_header Cache-Control "no-cache";
        }

%%HTTP_PROFILE_LOCATIONS%%

        location = /cert/ca.crt {
            %%AUTH_BASIC_OFF%%
            alias %%CERTS_DIR%%/ca.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="hermes-agent-ca.crt"';
        }

        location = /health {
            %%AUTH_BASIC_OFF%%
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }

        location = /ingress/user {
            %%AUTH_BASIC_OFF%%
            access_log off;
            default_type application/json;
            return 200 '{"user":"$http_x_ingress_user","remote_user":"$http_x_remote_user_name","hass_user":"$http_x_hass_user_name"}';
        }
    }

    # ── HTTPS (direct LAN access, TLS) ───────────────────────────────
    server {
        listen %%HTTPS_PORT%% ssl;
        server_name _;

        server_tokens off;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Strict-Transport-Security "max-age=31536000" always;

        ssl_certificate %%CERTS_DIR%%/server.crt;
        ssl_certificate_key %%CERTS_DIR%%/server.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers on;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;

        %%AUTH_BASIC_ON%%

        location = / {
            %%AUTH_BASIC_OFF%%
            root /var/www;
            try_files /landing.html =404;
            add_header Cache-Control "no-cache";
        }

%%HTTPS_PROFILE_LOCATIONS%%

        location = /cert/ca.crt {
            %%AUTH_BASIC_OFF%%
            alias %%CERTS_DIR%%/ca.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="hermes-agent-ca.crt"';
        }

        location = /health {
            %%AUTH_BASIC_OFF%%
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }

        location = /ingress/user {
            %%AUTH_BASIC_OFF%%
            access_log off;
            default_type application/json;
            return 200 '{"user":"$http_x_ingress_user","remote_user":"$http_x_remote_user_name","hass_user":"$http_x_hass_user_name"}';
        }
    }
