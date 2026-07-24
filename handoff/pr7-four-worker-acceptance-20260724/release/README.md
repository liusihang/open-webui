# PR7 live release package

This package is intentionally inert by default. It does not switch or restart formal live unless the operator invokes a mutating action with the exact confirmation string.

## Artifacts

- `compose.pr7-live.yaml`: additive Compose override for the accepted WebUI image and persistent AgentScope runtime.
- `pr7-live-release.sh`: preflight, online backup, explicit migration owner, Bifrost Pipe 0.2.17 update, switch, fast rollback, and guarded full f3 restore.
- `four-worker-pid-probe.py`: non-mutating keep-alive/socket probe that proves requests are pinned to four distinct worker PIDs.
- `test-pr7-live-release.sh`: static safety and syntax contract.
- Required source artifact: repository-managed `tools/openwebui/functions/bifrostapi.py`, md5 `0d629a726b022cde297e64679798b97c`.

## Operator sequence

1. Copy this release directory and the exact Bifrost source to a private staging directory on `aiserver`; keep generated state files mode 600/700.
2. Run `BIFROST_SOURCE=/private/path/bifrostapi.py ./pr7-live-release.sh preflight`. Any changed live container ID, image, start time, Compose hash, `.env` hash, DB head, Pipe hash, image ID, or insufficient disk is a hard stop.
3. Run the same command with `backup`. This keeps live serving while producing a custom-format PostgreSQL dump and verified file snapshot. The measured rehearsal baseline was 28m28s for dump and 81m38s for full restore.
4. Enter the announced maintenance window, then run `CONFIRM_SWITCH=switch-pr7-live-5b35e9f1b ... ./pr7-live-release.sh switch`.
5. Preserve the generated `switch-*` audit directory. The switch is accepted only if DB reaches f8, Bifrost content/manifest read back as 0.2.17, runtime and WebUI are healthy with restart=0, four distinct worker PIDs are pinned by real requests, singleton log counts are exact, core row counts are unchanged, and API smoke passes.

The script fails closed and records `failed_stage`; it does not automatically hide a failure with rollback. After diagnosis, fast rollback is:

```bash
CONFIRM_ROLLBACK_FAST=rollback-pr7-live-to-old-image \
  BIFROST_SOURCE=/private/path/bifrostapi.py \
  ./pr7-live-release.sh rollback-fast
```

Fast rollback retains f8 and Pipe 0.2.17. This exact topology was rehearsed successfully with the old live image in 246 seconds, four stable workers, unchanged core API counts, and no startup failure/respawn/Traceback. Full f3 restore is a long DR path and requires the separate exact confirmation string; use it only when old-image-on-f8 is not sufficient.
