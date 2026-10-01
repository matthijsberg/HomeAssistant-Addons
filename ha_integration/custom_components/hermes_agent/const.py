"""Constants for the Hermes Agent conversation integration."""

DOMAIN = "hermes_agent"

CONF_URL = "url"
CONF_API_KEY = "api_key"
CONF_PRIMARY_PROFILE = "primary_profile"

DEFAULT_URL = "http://local-hermes-agent:8080"

# Agent turns may run tools (MCP, browser, code); give them room.
REQUEST_TIMEOUT = 180

# Hermes keeps the transcript server-side; these headers continue a session.
SESSION_HEADER = "X-Hermes-Session-Id"
SESSION_KEY_HEADER = "X-Hermes-Session-Key"

VOICE_STYLE_PROMPT = (
    "This message reaches you through Home Assistant Assist and your reply may be "
    "spoken aloud. Answer in the user's language, briefly and naturally. Do not use "
    "markdown, tables, lists or code blocks."
)
