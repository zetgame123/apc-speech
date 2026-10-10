"""B9: у воркеров распознавания нет выхода в интернет.

Проверяет топологию docker-compose.yml: worker-long и worker-fast сидят только
в internal-сети backend, а сервисам с легитимным egress (api, worker-callback,
model-downloader) выход наружу оставлен.
"""

from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"

# Сервисы, которым внешний egress запрещён (только internal-сеть).
ISOLATED = {"worker-long", "worker-fast", "llm-runtime"}
# Сервисы, которым внешний egress нужен: скачивание аудио/весов, callback.
EGRESS = {"api", "worker-callback", "model-downloader", "model-downloader-llm"}


def load_compose():
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def service_networks(svc: dict) -> set[str]:
    nets = svc.get("networks", [])
    if isinstance(nets, dict):
        return set(nets)
    return set(nets or [])


def internal_networks(compose: dict) -> set[str]:
    return {
        name
        for name, cfg in (compose.get("networks") or {}).items()
        if isinstance(cfg, dict) and cfg.get("internal") is True
    }


def test_backend_network_is_internal():
    compose = load_compose()
    internal = internal_networks(compose)
    assert "backend" in internal, "в compose должна быть internal-сеть backend"


def test_recognition_workers_have_no_internet_egress():
    compose = load_compose()
    services = compose["services"]
    internal = internal_networks(compose)
    assert internal, "нет ни одной internal-сети"
    for name in ("worker-long", "worker-fast"):
        assert name in services, f"сервис {name} отсутствует в compose"
        nets = service_networks(services[name])
        assert nets, f"{name}: список сетей пуст"
        assert nets <= internal, (
            f"{name} имеет выход наружу через сети {nets - internal}, "
            f"разрешены только internal-сети {internal}"
        )


def test_egress_services_keep_outside_access():
    compose = load_compose()
    services = compose["services"]
    internal = internal_networks(compose)
    for name in ("api", "worker-callback", "model-downloader"):
        assert name in services, f"сервис {name} отсутствует в compose"
        nets = service_networks(services[name])
        # Пустой список = default-сеть compose с выходом наружу.
        assert not nets or not nets <= internal, (
            f"{name} остался без выхода в интернет, а он ему нужен"
        )


def test_backend_dependencies_reachable_from_workers():
    """БД/очередь/хранилище должны быть в той же internal-сети, иначе
    изолированные воркеры не смогут работать."""
    compose = load_compose()
    services = compose["services"]
    for dep in ("postgres", "redis", "minio"):
        assert dep in services, f"сервис {dep} отсутствует в compose"
        nets = service_networks(services[dep])
        assert "backend" in nets, f"{dep} недоступен из internal-сети backend"


def test_workers_fail_fast_without_network_for_models():
    """Воркеры не должны пытаться тянуть веса из сети: модели кладёт
    model-downloader до их старта."""
    compose = load_compose()
    services = compose["services"]
    for name in ("worker-long", "worker-fast"):
        env = services[name].get("environment", [])
        env_vars = {}
        for item in env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_vars[k] = v
            elif isinstance(item, dict):
                env_vars.update(item)
        assert env_vars.get("HF_HUB_OFFLINE") == "1", (
            f"{name}: нет HF_HUB_OFFLINE=1, рантайм может попытаться выйти в сеть"
        )


ISOLATED_OR_EGRESS = ISOLATED | EGRESS


def test_all_services_accounted():
    compose = load_compose()
    services = set(compose["services"])
    for name in ISOLATED_OR_EGRESS:
        assert name in services, f"сервис {name} отсутствует в compose"


def _default_image(image_ref: str) -> str:
    """Отрезает ${VAR:-default} обёртку, возвращает значение по умолчанию."""
    if image_ref.startswith("${") and ":-" in image_ref and image_ref.endswith("}"):
        return image_ref.split(":-", 1)[1][:-1]
    return image_ref


def test_images_pinned():
    """B2: образы с зафиксированными версиями, без плавающих latest/server."""
    compose = load_compose()
    services = compose["services"]

    redis_image = services["redis"]["image"]
    assert redis_image == "redis:7.2-alpine", f"redis не закреплён: {redis_image}"

    minio_image = _default_image(services["minio"]["image"])
    assert "@sha256:" in minio_image, (
        f"MinIO не закреплён digest'ом (у elestio/minio есть только latest): {minio_image}"
    )

    llm_image = services["llm-runtime"]["image"]
    assert llm_image not in (
        "ghcr.io/ggml-org/llama.cpp:server",
        "ghcr.io/ggml-org/llama.cpp:latest",
    ), f"llm-runtime на плавающем теге: {llm_image}"
    assert ":server-b" in llm_image, f"llm-runtime без зафиксированной версии: {llm_image}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
