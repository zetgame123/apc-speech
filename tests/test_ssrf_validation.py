import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["DEBUG"] = "true"
os.environ["DEV_TOKEN"] = "dev-token"
os.environ["APP_ENV"] = "test"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["S3_ENDPOINT_URL"] = "http://localhost:9000"
os.environ["CALLBACK_SIGNING_SECRET"] = "test-secret-key-32-characters!!!"

from unittest.mock import patch

import pytest

from app.core.errors import AppError
from app.core.security import validate_ssrf_url


def test_ssrf_blocks_localhost():
    with pytest.raises(AppError):
        validate_ssrf_url("http://127.0.0.1/file")


def test_ssrf_blocks_private_10():
    with pytest.raises(AppError):
        validate_ssrf_url("http://10.0.0.1/file")


def test_ssrf_blocks_private_172():
    with pytest.raises(AppError):
        validate_ssrf_url("http://172.16.0.1/file")


def test_ssrf_blocks_private_192():
    with pytest.raises(AppError):
        validate_ssrf_url("http://192.168.1.1/file")


def test_ssrf_blocks_metadata():
    with pytest.raises(AppError):
        validate_ssrf_url("http://169.254.169.254/latest/meta-data/")


def test_ssrf_blocks_ftp_scheme():
    with pytest.raises(AppError):
        validate_ssrf_url("ftp://example.com")


def test_ssrf_allows_public_url():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        url = "https://example.com/audio.wav"
        result = validate_ssrf_url(url)
        assert result == url


def test_ssrf_blocks_ipv4_mapped_ipv6():
    with pytest.raises(AppError):
        validate_ssrf_url("http://[::ffff:127.0.0.1]/audio.wav")


@pytest.mark.asyncio
async def test_download_ssrf_blocks_redirect_to_loopback(monkeypatch):
    """When a 302 redirect targets a private/loopback URL, download must be rejected."""
    import tempfile

    from app.core.security import download_file_ssrf_safe

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:

        class FakeResponse:
            def __init__(self, status_code, headers, content=b""):
                self.status_code = status_code
                self.headers = headers
                self._content = content

            def raise_for_status(self):
                pass

            async def aiter_bytes(self, chunk_size=1024):
                yield self._content

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, method, url):
                if "safe.com" in url:
                    return FakeResponse(302, {"location": "http://127.0.0.1/metadata.json"})
                return FakeResponse(200, {}, b"ok")

        monkeypatch.setattr("httpx.AsyncClient", FakeClient)

        with patch("socket.getaddrinfo") as mock_dns:

            def dns_side_effect(host, port):
                if "safe.com" in host:
                    return [(2, 1, 6, "", ("93.184.216.34", 0))]
                return [(2, 1, 6, "", ("127.0.0.1", 0))]

            mock_dns.side_effect = dns_side_effect

            with pytest.raises(AppError) as exc_info:
                await download_file_ssrf_safe(
                    url="https://safe.com/redirect",
                    target_path=tmp_path,
                    max_bytes=1000,
                )
            assert exc_info.value.error == "ssrf_protection_triggered"
            assert not os.path.exists(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_download_ssrf_blocks_file_too_large(monkeypatch):
    """Files exceeding max_bytes must be rejected and cleaned up."""
    import tempfile

    from app.core.security import download_file_ssrf_safe

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:

        class FakeResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-length": "10000"}

            def raise_for_status(self):
                pass

            async def aiter_bytes(self, chunk_size=1024):
                yield b"x" * 10000

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, method, url):
                return FakeResponse()

        monkeypatch.setattr("httpx.AsyncClient", FakeClient)

        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
            with pytest.raises(AppError) as exc_info:
                await download_file_ssrf_safe(
                    url="https://safe.com/huge.wav",
                    target_path=tmp_path,
                    max_bytes=100,
                )
            assert exc_info.value.error == "payload_too_large"
            assert not os.path.exists(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_download_ssrf_safe_success(monkeypatch):
    """Valid public file is streamed, hash returned, file saved."""
    import hashlib
    import tempfile

    from app.core.security import download_file_ssrf_safe

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:

        class FakeResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = {}

            def raise_for_status(self):
                pass

            async def aiter_bytes(self, chunk_size=1024):
                yield b"hello world audio"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, method, url):
                return FakeResponse()

        monkeypatch.setattr("httpx.AsyncClient", FakeClient)

        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
            h = await download_file_ssrf_safe(
                url="https://safe.com/audio.wav",
                target_path=tmp_path,
                max_bytes=1000,
            )
            assert h == hashlib.sha256(b"hello world audio").hexdigest()
            assert os.path.exists(tmp_path)
            with open(tmp_path, "rb") as f:
                assert f.read() == b"hello world audio"
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
