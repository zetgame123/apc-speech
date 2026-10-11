"""URL-утилиты без зависимостей проекта (листовой модуль, циклов импорта нет)."""

from urllib.parse import urlparse


def redact_url_for_log(url: str) -> str:
    """Для логов: только схема и хост (без пути, query, фрагмента, userinfo).

    Query-параметры callback/download URL могут содержать секреты и ПД.
    """
    try:
        parsed = urlparse(url or "")
        host = parsed.hostname or ""
        if not parsed.scheme or not host:
            return "<invalid-url>"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except Exception:
        return "<invalid-url>"
