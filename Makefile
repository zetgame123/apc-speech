# Проверка качества: линтер, форматтер, guard-скрипты, OpenAPI, тесты, секреты.
# Стек: uv + ruff + pytest (см. .github/workflows/ci.yml).
# Запуск: make check (на чистой Linux-машине; требует uv и gitleaks в PATH).

.PHONY: lint fmt test guards openapi secrets check

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
