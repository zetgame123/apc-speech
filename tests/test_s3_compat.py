"""Код ходит в хранилище только через стандартный S3-клиент (ADR-023).

Проверяет, что замена MinIO на любое S3-совместимое хранилище не требует
изменений кода: нет MinIO-специфичных SDK/вызовов, endpoint и бакеты — через env.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PY = list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py"))

# MinIO-специфичные маркеры, которых не должно быть в коде.
MINIO_MARKERS = re.compile(r"^\s*(from|import)\s+minio\b", re.MULTILINE)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_no_minio_sdk_imports():
    offenders = [str(p) for p in APP_PY if MINIO_MARKERS.search(_read(p))]
    assert not offenders, f"MinIO-специфичный SDK в коде: {offenders}"


def test_storage_uses_standard_boto3_s3_client():
    s3_py = _read(ROOT / "app" / "storage" / "s3.py")
    assert "boto3.client(" in s3_py and '"s3"' in s3_py, (
        "хранилище должно использовать стандартный boto3.client('s3')"
    )
    for op in (
        "put_object",
        "get_object",
        "delete_object",
        "head_object",
        "generate_presigned_url",
        "list_objects_v2",
    ):
        assert op in s3_py, f"ожидался стандартный S3-вызов {op} в app/storage/s3.py"


def test_s3_connection_params_come_from_env_settings():
    s3_py = _read(ROOT / "app" / "storage" / "s3.py")
    for param in ("s3_endpoint_url", "s3_access_key", "s3_secret_key", "s3_region"):
        assert param in s3_py, f"{param} должен браться из настроек (env)"
    config_py = _read(ROOT / "app" / "core" / "config.py")
    for alias in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_REGION"):
        assert alias in config_py, f"{alias} должен задаваться через env"
