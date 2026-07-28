#!/usr/bin/env bash
set -euo pipefail

container=${LIVE_WEBUI_CONTAINER:-open-webui}

docker inspect "$container" --format '{{.Id}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}'
docker top "$container" -eo pid,ppid,lstart,args

docker exec -i "$container" python - <<'PY'
import inspect

import uvicorn
from uvicorn.supervisors.multiprocess import Multiprocess

print(f'uvicorn_version={uvicorn.__version__}')
print(f'multiprocess_source={inspect.getsourcefile(Multiprocess)}')
print('handle_hup_source_begin')
print(inspect.getsource(Multiprocess.handle_hup).rstrip())
print('handle_hup_source_end')
print('restart_all_source_begin')
print(inspect.getsource(Multiprocess.restart_all).rstrip())
print('restart_all_source_end')
print('keep_subprocess_alive_source_begin')
print(inspect.getsource(Multiprocess.keep_subprocess_alive).rstrip())
print('keep_subprocess_alive_source_end')
PY
