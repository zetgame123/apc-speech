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
