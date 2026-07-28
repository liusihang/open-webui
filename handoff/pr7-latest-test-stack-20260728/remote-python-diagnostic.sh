#!/usr/bin/env bash
set -Eeuo pipefail

docker inspect open-webui-pr7 --format 'configured_workdir={{.Config.WorkingDir}}'
docker exec open-webui-pr7 pwd
docker exec open-webui-pr7 test -f /app/backend/open_webui/__init__.py
docker exec open-webui-pr7 python -c 'import sys; print("python=" + sys.executable); print("path=" + "|".join(sys.path))'
