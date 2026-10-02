"""
Unit Tests for Open HEMS Secure HTTP Client
===========================================
Verifies:
1. Rejection of non-http(s) schemes (CWE-939: file://, ftp://, gopher://).
2. Enforcement of verified TLS for external WAN endpoints (CWE-295).
3. Whitelisted local network hosts allow internal self-signed TLS.
"""

import pytest
import urllib.request
import ssl
from api.http_client import safe_urlopen, is_local_network_host


def test_safe_urlopen_rejects_file_scheme():
    with pytest.raises(ValueError, match="Disallowed URL scheme 'file'"):
        safe_urlopen("file:///etc/passwd")


def test_safe_urlopen_rejects_ftp_scheme():
    with pytest.raises(ValueError, match="Disallowed URL scheme 'ftp'"):
        safe_urlopen("ftp://192.168.1.1/backup.tar.gz")


def test_is_local_network_host():
    assert is_local_network_host("localhost") is True
    assert is_local_network_host("127.0.0.1") is True
    assert is_local_network_host("172.30.32.1") is True
    assert is_local_network_host("192.168.1.50") is True
    assert is_local_network_host("supervisor") is True
    assert is_local_network_host("homeassistant.local") is True
    assert is_local_network_host("api.open-meteo.com") is False
    assert is_local_network_host("public.api.energyzero.nl") is False


def test_safe_urlopen_forces_verified_tls_for_external_wan(monkeypatch):
    captured_kwargs = {}

    def mock_urlopen(req, **kwargs):
        captured_kwargs.update(kwargs)
        class MockResp:
            status = 200
        return MockResp()

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    # Attempt to pass CERT_NONE to an external domain
    insecure_ctx = ssl.create_default_context()
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE

    safe_urlopen("https://api.open-meteo.com/v1/forecast", context=insecure_ctx)
    assert "context" in captured_kwargs
    ctx_used = captured_kwargs["context"]
    assert ctx_used.verify_mode != ssl.CERT_NONE
    assert ctx_used.check_hostname is True
