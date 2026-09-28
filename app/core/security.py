import hashlib
import hmac
import ipaddress
import socket
import time
from typing import Optional, Set
from urllib.parse import urlparse
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.errors import AppError

security_bearer = HTTPBearer(auto_error=False)

# Blocked IP subnets for SSRF protection
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),    # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),   # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),   # Link-Local / Cloud Metadata
    ipaddress.ip_network("0.0.0.0/8"),        # Broadcast/Current
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthContext:
    def __init__(self, token: str, scopes: Set[str], namespace: Optional[str] = None):
        self.token = token
        self.scopes = scopes
        self.namespace = namespace

    def require_scope(self, required_scope: str):
        if "admin" in self.scopes:
            return
        if required_scope not in self.scopes:
            raise AppError(
                error="forbidden_scope",
                message=f"Для выполнения операции требуется scope: {required_scope}",
                status_code=status.HTTP_403_FORBIDDEN,
            )


def verify_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> AuthContext:
    settings = get_settings()

    if not credentials:
        raise AppError(
            error="unauthorized",
            message="Отсутствует заголовок Authorization: Bearer <token>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = credentials.credentials

    # Development fallback
    if token == settings.dev_token or settings.debug:
        return AuthContext(token=token, scopes={"admin", "write", "read"}, namespace=None)

    token_hash = hash_token(token)
    configured_tokens = [t.strip() for t in settings.api_tokens_hashed.split(",") if t.strip()]

    for entry in configured_tokens:
        parts = entry.split(":")
        entry_hash = parts[0]
        scopes = set(parts[1].split("+")) if len(parts) > 1 else {"read", "write"}
        namespace = parts[2] if len(parts) > 2 else None

        if hmac.compare_digest(token_hash, entry_hash):
            return AuthContext(token=token, scopes=scopes, namespace=namespace)

    raise AppError(
        error="invalid_token",
        message="Предоставлен недействительный токен авторизации",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def validate_ssrf_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise AppError(
            error="invalid_url_scheme",
            message="Разрешены только протоколы http и https",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    hostname = parsed.hostname
    if not hostname:
        raise AppError(
            error="invalid_url_host",
            message="В URL отсутствует корректное доменное имя или IP",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # DNS resolution and IP verification
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise AppError(
            error="dns_resolution_failed",
            message=f"Не удалось разрешить доменное имя: {hostname}",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"hostname": hostname, "error": str(e)},
        )

    for item in addr_info:
        ip_str = item[4][0]
        ip_obj = ipaddress.ip_address(ip_str)

        for blocked in BLOCKED_IP_NETWORKS:
            if ip_obj in blocked:
                raise AppError(
                    error="ssrf_protection_triggered",
                    message="Запрещено скачивание по внутренним или приватным IP-адресам",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"blocked_ip": ip_str},
                )

    return url


def sign_callback_payload(secret: str, raw_body: bytes) -> tuple[int, str]:
    timestamp = int(time.time())
    message = f"{timestamp}.".encode("utf-8") + raw_body
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return timestamp, f"sha256={signature}"
