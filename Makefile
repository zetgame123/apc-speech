# Проверка качества: линтер, форматтер, guard-скрипты, OpenAPI, тесты, секреты.
# Стек: uv + ruff + pytest (см. .github/workflows/ci.yml).
# Запуск: make check (на чистой Linux-машине; требует uv и gitleaks в PATH).

.PHONY: lint fmt test guards openapi secrets check live-check

lint:
	uv run ruff check .

fmt:
	uv run ruff format --check .

test:
	APP_ENV=development DEV_TOKEN=dev-token uv run pytest tests/ -q

guards:
	uv run python scripts/guards/no_external_asr.py
	uv run python scripts/guards/no_pii_in_logs.py

openapi:
	uv run python -m openapi_spec_validator openapi.yaml

secrets:
	gitleaks detect --no-banner --redact

check: lint fmt guards openapi test secrets
	@echo "make check: всё зелёное"

# Живая проверка дашборда в настоящем Chromium по CDP (без внешних
# зависимостей, только node из mise). Требует поднятый стек, chromium в PATH
# и DASHBOARD_TOKEN (см. README «Дашборд»). В CI и make check не входит:
# там нет ни живого стенда с моделями, ни браузера. Скрипт сам пропускается
# (exit 2 → SKIP) без сервера/браузера/токена.
live-check:
	node scripts/dashboard_live_check.mjs; code=$$?; \
	if [ $$code -eq 2 ]; then echo "make live-check: SKIP (нет сервера/браузера/токена)"; \
	elif [ $$code -ne 0 ]; then echo "make live-check: FAILED"; exit $$code; \
	else echo "make live-check: PASSED"; fi
