#!/usr/bin/env bash
set -euo pipefail
mkdir -p build
if [[ "${1:-}" == "--restart" && -f build/workbench.pid ]]; then
  pid="$(cat build/workbench.pid)"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    running="$(ps -p "$pid" -o args= || true)"
    case "$running" in
      *"uvicorn research_workbench.api:create_app"*) kill "$pid"; for _ in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep .2; done ;;
    esac
  fi
fi
if ! curl --silent --fail http://127.0.0.1:8090/api/health >/dev/null; then
  nohup python -m uvicorn research_workbench.api:create_app --factory --host 0.0.0.0 --port 8090 >build/workbench.log 2>&1 &
  echo $! >build/workbench.pid
fi
