#!/bin/bash
# П.3: свип -t llama.cpp на 8 физических ядрах (cpuset 0,2,4,6,8,10,12,14).
# Пересоздаёт apc_small-llm-runtime-1 с -t/--threads-batch 4,6,8;
# на каждом: прогрев 1 звонок + 5 замерных (первые 5 набора B, v1.5);
# в конце восстанавливает исходный контейнер (-t 2, cpuset 4,6).
# Запуск с хоста: bash scripts/bench_llm_threads.sh
set -u
API=apc_small-api-1
LLM=apc_small-llm-runtime-1
IMG=ghcr.io/ggml-org/llama.cpp:server-b5350
NET=apc_small_backend
MODELS_SRC=/home/zet/Projects/oc/apc-speech/models
CPUSET8="0,2,4,6,8,10,12,14"

run_llm() { # $1=t $2=cpuset
  local t=$1 cpuset=$2
  docker stop "$LLM" >/dev/null 2>&1
  docker rm "$LLM" >/dev/null 2>&1
  docker run -d --name "$LLM" \
    --restart unless-stopped \
    --entrypoint /bin/sh \
    --network "$NET" --network-alias llm-runtime \
    --cpuset-cpus "$cpuset" --cpus 6.0 --memory 10737418240 \
    -v "${MODELS_SRC}:/models:ro" \
    "$IMG" -c \
    "if [ -f /models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf ]; then
       MODEL=/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf;
     else
       MODEL=/models/qwen2.5-7b-instruct-q4_k_m.gguf;
     fi;
     exec /app/llama-server -m \$MODEL --alias qwen2.5-7b-instruct --host 0.0.0.0 --port 8080 -c 4096 --n-gpu-layers 0 -t $t --threads-batch $t" >/dev/null
  echo "llm-runtime restarted: -t $t cpuset $cpuset"
  for i in $(seq 1 36); do
    if docker exec "$API" python -c "import httpx; httpx.get('http://llm-runtime:8080/health', timeout=5)" >/dev/null 2>&1; then
      echo "ready after ~$((i * 5))s"
      return 0
    fi
    sleep 5
  done
  echo "TIMEOUT waiting for llm-runtime -t $t"
  return 1
}

bench_once() { # $1=label
  local label=$1
  # прогрев: 1 звонок (b000), не в зачёт
  docker exec -e APP_ENV=development -e DEV_TOKEN=dev-token -e PYTHONPATH=/tmp/ov "$API" \
    python /tmp/bench_llm_5.py /tmp/dump_b.json 1 | tail -n 2
  # замер: 5 звонков
  docker exec -e APP_ENV=development -e DEV_TOKEN=dev-token -e PYTHONPATH=/tmp/ov "$API" \
    python /tmp/bench_llm_5.py /tmp/dump_b.json 5 | tail -n 6 > "/tmp/bench_t_${label}.log"
  cat "/tmp/bench_t_${label}.log"
}

for t in 4 6 8; do
  run_llm "$t" "$CPUSET8" || exit 1
  echo "=== -t $t ==="
  bench_once "$t"
done

echo "=== restore: -t 2, cpuset 4,6 ==="
run_llm 2 "4,6"
docker exec "$API" python -c "import httpx; print('health:', httpx.get('http://llm-runtime:8080/health', timeout=10).status_code)"
echo "SWEEP DONE"
