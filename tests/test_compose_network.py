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

    pg_image = services["postgres"]["image"]
    assert pg_image == "pgvector/pgvector:0.8.7-pg16", f"postgres не закреплён: {pg_image}"

    # Сервисы со сборкой (api, воркеры, миграции): базовый образ в Dockerfile
    # закреплён digest'ом — без исключений.
    from pathlib import Path

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8").splitlines()
    from_lines = [ln.strip() for ln in dockerfile if ln.strip().startswith("FROM ")]
    assert from_lines, "в Dockerfile нет FROM"
    for ln in from_lines:
        assert "@sha256:" in ln, f"базовый образ не закреплён digest'ом: {ln}"


def test_published_services_wired_correctly():
    """Пункт 4: сервисы с опубликованными портами и доступом с хоста.

    Уточнение к исходной гипотезе: published ports (`ports:`) принимают входящие
    соединения и на `internal`-сети (internal блокирует только egress наружу),
    поэтому backend-only для llm-runtime допустим. Критично другое:
    - api — единственная публичная точка входа: порт 8000 опубликован И есть
      egress (frontend) для скачивания по внешним URL;
    - minio без опубликованных портов: потребители только внутри backend,
      хосту прямой доступ не нужен.
    """
    compose = load_compose()
    services = compose["services"]

    api = services["api"]
    api_ports = [str(p) for p in (api.get("ports") or [])]
    assert any("8000" in p for p in api_ports), "у api не опубликован порт 8000"
    assert "frontend" in service_networks(api), "у api нет egress (frontend)"

    minio = services["minio"]
    assert not minio.get("ports"), "у minio появились опубликованные порты — проверить сети"
    assert "backend" in service_networks(minio), "minio недоступен из backend"

    llm = services["llm-runtime"]
    assert "backend" in service_networks(llm), "llm-runtime недоступен воркерам из backend"


def test_workers_have_thread_limits_matching_cpu_quota():
    """B1: BLAS-потоки поджаты под CPU-квоту (иначе лишний oversubscription).

    worker-fast/worker-long обязаны задавать OMP*/MKL*-переменные. Потоки самого
    CTranslate2 (ASR_THREADS) намеренно не фиксируем: замер B1 показал, что
    1 поток замедляет инференс в разы даже под квотой (см. known-issues.md п.5).
    """
    compose = load_compose()
    services = compose["services"]
    for name in ("worker-fast", "worker-long"):
        env = services[name].get("environment", [])
        env_vars = {}
        for item in env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_vars[k] = v
            elif isinstance(item, dict):
                env_vars.update(item)
        for var in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
        ):
            assert var in env_vars, f"{name}: нет {var} — потоки не ограничены"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_llm_runtime_threads_configurable_with_default_4():
    """П.1: потоки llama.cpp задаются LLAMA_THREADS, дефолт 4.

    Замер 2026-10-13: -t 2 при 8 vCPU недогружал CPU (медиана 65,4 с);
    -t 6 на 8 физических ядрах — 25,8 с (≤ 30 с OK), -t 8 — 37,7 с.
    """
    compose = load_compose()
    cmd = str(compose["services"]["llm-runtime"].get("command", ""))
    assert "${LLAMA_THREADS:-4}" in cmd, (
        f"llm-runtime не параметризован LLAMA_THREADS с дефолтом 4: {cmd[:200]}"
    )
    assert "-t 2" not in cmd and "--threads-batch 2" not in cmd, (
        "в llm-runtime остался захардкоженный -t 2 (ошибка замера п.3)"
    )
