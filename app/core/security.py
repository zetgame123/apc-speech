import hashlib
import hmac
import ipaddress
import socket
import time
from urllib.parse import urlparse

from fastapi import Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.errors import AppError

security_bearer = HTTPBearer(auto_error=False)

# Blocked IP subnets for SSRF protection
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),  # Link-Local / Cloud Metadata
    ipaddress.ip_network("0.0.0.0/8"),  # Broadcast/Current
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthContext:
    def __init__(self, token: str, scopes: set[str], namespace: str | None = None):
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

    def verify_namespace(self, requested_namespace: str | None):
        """Enforces tenant isolation. If auth token is scoped to a specific namespace,
        the client cannot access another namespace."""
        if not self.namespace or not requested_namespace:
            return
        if self.namespace != requested_namespace:
            raise AppError(
                error="forbidden_namespace",
                message=f"Токен привязан к namespace '{self.namespace}', доступ к '{requested_namespace}' запрещен",
                status_code=status.HTTP_403_FORBIDDEN,
            )


def authenticate_token(token: str) -> AuthContext:
    settings = get_settings()

    # Dev token check (allowed ONLY in non-production environments)
    if settings.app_env != "production" and settings.dev_token and token == settings.dev_token:
        return AuthContext(token=token, scopes={"admin", "write", "read"}, namespace=None)

    # Debug mode fallback only in non-production
    if settings.app_env != "production" and settings.debug and token == "debug-token":
        return AuthContext(token=token, scopes={"admin", "write", "read"}, namespace=None)

    token_hash = hash_token(token)

    # Check if token has been revoked (in-memory set, managed via admin API)
    if token_hash in _revoked_token_hashes:
        raise AppError(
            error="token_revoked",
            message="Токен был отозван",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

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


def verify_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
) -> AuthContext:
    if not credentials:
        raise AppError(
            error="unauthorized",
            message="Отсутствует заголовок Authorization: Bearer <token>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return authenticate_token(credentials.credentials)


# --- Token revocation support (hot, without restart) ---
_revoked_token_hashes: set[str] = set()


def revoke_token(token_hash: str) -> bool:
    """Add a token hash to the revocation set. Returns True if newly revoked."""
    if token_hash in _revoked_token_hashes:
        return False
    _revoked_token_hashes.add(token_hash)
    return True


def unrevoke_token(token_hash: str) -> bool:
    """Remove a token hash from the revocation set."""
    return (
        _revoked_token_hashes.discard(token_hash) is None
        and token_hash not in _revoked_token_hashes
    )


def list_revoked_tokens() -> list[str]:
    """Return list of revoked token hashes."""
    return list(_revoked_token_hashes)


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
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise AppError(
                error="invalid_ip_address",
                message=f"Недопустимый IP-адрес: {ip_str}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Handle IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1)
        if getattr(ip_obj, "ipv4_mapped", None):
            ip_obj = ip_obj.ipv4_mapped

        # Check standard private/loopback/link-local flags
        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
        ):
            raise AppError(
                error="ssrf_protection_triggered",
                message="Запрещено обращение к внутренним или приватным IP-адресам",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"blocked_ip": str(ip_obj)},
            )

        for blocked in BLOCKED_IP_NETWORKS:
            try:
                if ip_obj in blocked:
                    raise AppError(
                        error="ssrf_protection_triggered",
                        message="Запрещено обращение к внутренним или приватным IP-адресам",
                        status_code=status.HTTP_400_BAD_REQUEST,
                        details={"blocked_ip": str(ip_obj)},
                    )
            except TypeError:
                pass

    return url


def sign_callback_payload(secret: str, raw_body: bytes) -> tuple[int, str]:
    timestamp = int(time.time())
    message = f"{timestamp}.".encode() + raw_body
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return timestamp, f"sha256={signature}"
