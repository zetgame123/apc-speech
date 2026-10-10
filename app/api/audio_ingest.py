"""Скачивание аудио по внешнему URL в объектное хранилище (сторона api).

Воркеры распознавания живут в изолированной сети compose без выхода в интернет
(B9), поэтому скачивание по `source_url` выполняется здесь, в `api` (у него есть
egress), с той же SSRF-защитой (`download_file_ssrf_safe`). Воркер получает
готовый `s3_key` и egress ему не нужен.
"""

import os
import tempfile
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.security import download_file_ssrf_safe
from app.storage.s3 import get_storage


async def fetch_url_to_s3(
    *,
    url: str,
    namespace: str,
    call_id: str,
    max_bytes: int,
) -> tuple[str, str, int]:
    """Скачивает файл по URL (SSRF-safe) и кладёт в бакет аудио.

    Возвращает (s3_key, sha256_hex, size_bytes). Ошибки скачивания пробрасываются
    как AppError (413/400/...), записей в БД до успешного скачивания не создаётся.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    s3_key = f"{namespace}/{now.strftime('%Y/%m/%d')}/{call_id}/original_from_url"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tmp")
    tmp_path = tmp.name
    tmp.close()
    try:
        file_hash = await download_file_ssrf_safe(
            url=url,
            target_path=tmp_path,
            max_bytes=max_bytes,
            timeout=60.0,
        )
        size_bytes = os.path.getsize(tmp_path)
        storage = get_storage()
        if hasattr(storage, "upload_file"):
            storage.upload_file(
                local_path=tmp_path,
                bucket=settings.s3_bucket_audio,
                key=s3_key,
                content_type="application/octet-stream",
            )
        else:  # pragma: no cover — запасной путь для несовместимых стореджей
            with open(tmp_path, "rb") as f:
                storage.upload_bytes(
                    bucket=settings.s3_bucket_audio,
                    key=s3_key,
                    data=f.read(),
                    content_type="application/octet-stream",
                )
        return s3_key, file_hash, size_bytes
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
