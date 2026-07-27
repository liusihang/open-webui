#!/usr/bin/env bash
set -Eeuo pipefail

started=$(docker inspect open-webui-pr7 --format '{{.State.StartedAt}}')
docker logs --timestamps --since "${started}" open-webui-pr7 2>&1 | grep -Ea 'Installing external dependencies of functions and tools|Installing requirements:|Error installing packages:|Error installing requirements:|ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS is disabled|Offline mode enabled' || true
docker inspect open-webui-pr7 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS|OFFLINE_MODE)=' | sort
