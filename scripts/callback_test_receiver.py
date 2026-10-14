#!/usr/bin/env python3
"""Тестовый приёмник callback для E2E-проверки C9 (только тесты, не прод).

Принимает POST, проверяет подпись X-Apc-Signature (HMAC "{ts}.{body}", secret),
свежесть X-Apc-Timestamp (<=5 мин), пишет каждую попытку в /tmp/callback_hits.jsonl.
Первые FAIL_FIRST_N запросов отвечает 500 (проверка ретраев), дальше 200.
"""

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ.get("CALLBACK_SIGNING_SECRET", "")
PORT = int(os.environ.get("PORT", "8081"))
FAIL_FIRST_N = int(os.environ.get("FAIL_FIRST_N", "1"))
LOG_PATH = os.environ.get("HITS_LOG", "/tmp/callback_hits.jsonl")

attempts = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"callback-test-receiver")

    def do_POST(self):
        attempts["n"] += 1
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        ts = self.headers.get("X-Apc-Timestamp", "")
        sig = self.headers.get("X-Apc-Signature", "")
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"_raw_len": len(raw)}
        expected = (
            "sha256="
            + hmac.new(
                SECRET.encode(), f"{ts}.{raw.decode('utf-8')}".encode(), hashlib.sha256
            ).hexdigest()
        )
        now = int(time.time())
        try:
            fresh = abs(now - int(ts)) <= 300
        except (TypeError, ValueError):
            fresh = False
        record = {
            "attempt": attempts["n"],
            "ts": ts,
            "ts_fresh": fresh,
            "signature_ok": hmac.compare_digest(sig, expected),
            "job_id": body.get("job_id") if isinstance(body, dict) else None,
            "status": body.get("status") if isinstance(body, dict) else None,
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if attempts["n"] <= FAIL_FIRST_N:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"fail-first")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")


if __name__ == "__main__":
    print(f"callback test receiver on :{PORT}, fail_first={FAIL_FIRST_N}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
