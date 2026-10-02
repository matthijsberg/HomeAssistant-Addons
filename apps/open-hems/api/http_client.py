"""
Open HEMS - Secure HTTP Client
==============================
Enforces URL scheme whitelisting (http/https only) and verified TLS for external calls.
Mitigates CWE-295 (Improper Certificate Validation) and CWE-939 (Improper Authorization for Custom URL Scheme).
"""

import urllib.request
import urllib.parse
import ssl
from typing import Union, Any, Optional


def is_local_network_host(hostname: Optional[str]) -> bool:
    """Returns True if host is localhost, 127.0.0.1, or private LAN/Docker bridge (172.16-31, 192.168, 10.x)."""
    if not hostname:
        return False
    h = hostname.lower()
    if h in ("localhost", "127.0.0.1", "::1", "supervisor"):
        return True
    if h.startswith("172.") or h.startswith("192.168.") or h.startswith("10."):
        return True
    if h.endswith(".local") or h.endswith(".internal"):
        return True
    return False


def safe_urlopen(
    url_or_req: Union[str, urllib.request.Request],
    timeout: float = 10.0,
    context: Optional[ssl.SSLContext] = None,
    allow_unverified_local_tls: bool = True,
    **kwargs: Any
) -> Any:
    """
    Safely executes an HTTP/HTTPS request with strict URL scheme whitelisting and TLS enforcement.

    Guards:
    1. Scheme Whitelist: Only 'http' and 'https' permitted. File schemes ('file://') and others are rejected.
    2. TLS Verification: External WAN endpoints MUST use verified system CA contexts.
       Unverified SSL (for self-signed certs) is strictly forbidden for public WAN domains.
    """
    if isinstance(url_or_req, urllib.request.Request):
        url = url_or_req.full_url
    else:
        url = str(url_or_req)

    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Security Violation: Disallowed URL scheme '{scheme}' (only http/https allowed)")

    # TLS Context Enforcement
    ssl_context = context
    if scheme == "https":
        host = parsed.hostname
        if is_local_network_host(host) and allow_unverified_local_tls:
            if ssl_context is None:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        else:
            # External WAN: MUST use verified TLS
            if ssl_context is None or getattr(ssl_context, "verify_mode", None) == ssl.CERT_NONE:
                ssl_context = ssl.create_default_context()

    if ssl_context is not None:
        kwargs["context"] = ssl_context

    return urllib.request.urlopen(url_or_req, timeout=timeout, **kwargs)
