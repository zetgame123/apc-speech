import hashlib
import secrets
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
tokens = {
    "admin": "audit-admin",
    "a": "audit-write-a",
    "b": "audit-write-b",
    "read": "audit-read-a",
}
entries = ",".join(
    hashlib.sha256(v.encode()).hexdigest()
    + ":"
    + ("admin" if k == "admin" else "read" if k == "read" else "read+write")
    + ("" if k == "admin" else ":" + ("namespace-B" if k == "b" else "namespace-A"))
    for k, v in tokens.items()
)
env = {
    "APP_PROFILE": "small",
    "APP_ENV": "production",
    "DEBUG": "false",
    "API_TOKENS_HASHED": entries,
    "DEV_TOKEN": "",
    "CALLBACK_SIGNING_SECRET": secrets.token_hex(32),
    "DB_USER": "apc_user",
    "DB_PASSWORD": "audit_db_pass",
    "MINIO_ROOT_USER": "minio_admin",
    "MINIO_ROOT_PASSWORD": "audit_minio_pass",
    "S3_ACCESS_KEY": "minio_admin",
    "S3_SECRET_KEY": "audit_minio_pass",
    "LLM_EXTERNAL_PROVIDER_ENABLED": "false",
    "LOCAL_LLM_URL": "http://llm-runtime:8080/v1",
    "ASR_THREADS": "2",
}
(root / "audit_tests/audit.env").write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
p = yaml.safe_load((root / "docker-compose.yml").read_text())
for name, s in p["services"].items():
    if "env_file" in s:
        s["env_file"] = [str(root / "audit_tests/audit.env")]
    if "build" in s:
        s["build"]["context"] = str(root)
    if "volumes" in s:
        s["volumes"] = [str(root / x[2:]) if x.startswith("./") else x for x in s["volumes"]]
    if name in ["api", "worker-long", "worker-callback"]:
        s["volumes"].append(str(root / "audit_tests") + ":/app/audit_tests:ro")
    if name == "api":
        s["ports"] = ["127.0.0.1:18000:8000"]
    if name == "minio":
        s["ports"] = ["127.0.0.1:19000:9000"]
    if name == "llm-runtime":
        s["ports"] = ["127.0.0.1:18080:8080"]
(root / "audit_tests/compose.yml").write_text(yaml.safe_dump(p, sort_keys=False))
print("Isolated audit project prepared, original env/containers unchanged")
