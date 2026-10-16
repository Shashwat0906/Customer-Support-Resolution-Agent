#!/bin/sh
# Container entrypoint. ROLE selects what runs:
#   api        FastAPI (optionally with embedded worker: RUN_EMBEDDED_WORKER=true)
#   worker     standalone workflow worker
#   mocks      mock payment/shipping/ticketing services
#   bootstrap  migrate + checkpoint schema + seed (if empty) + ingest policies, then exit
#   all        single-container deployment: mocks on localhost + bootstrap + API with embedded worker
set -eu
PORT="${PORT:-8000}"
case "${ROLE:-api}" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*' ;;
  worker)
    exec python -m app.worker ;;
  mocks)
    exec uvicorn mock_services.main:app --host 0.0.0.0 --port "$PORT" ;;
  bootstrap)
    exec python -m scripts.bootstrap ${BOOTSTRAP_ARGS:-} ;;
  all)
    MOCK_PORT="${MOCK_PORT:-8001}"
    uvicorn mock_services.main:app --host 127.0.0.1 --port "$MOCK_PORT" --log-level warning &
    MOCK_PID=$!
    i=0
    until python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$MOCK_PORT/health', timeout=2)" 2>/dev/null; do
      i=$((i+1)); [ "$i" -gt 30 ] && { echo "mock services failed to start" >&2; exit 1; }
      sleep 1
    done
    python -m scripts.bootstrap ${BOOTSTRAP_ARGS:-}
    # If the mock services die, the health check (/health/ready) reports it and the platform restarts the container.
    trap 'kill $MOCK_PID 2>/dev/null' EXIT
    RUN_EMBEDDED_WORKER=true exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*' ;;
  *)
    echo "unknown ROLE: ${ROLE}" >&2; exit 2 ;;
esac
