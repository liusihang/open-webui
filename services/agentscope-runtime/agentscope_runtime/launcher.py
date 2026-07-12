from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence

APP_TARGET = "agentscope_runtime.app:create_app_from_env"
WORKER_ENV_NAMES = ("WEB_CONCURRENCY", "UVICORN_WORKERS")


class LauncherConfigurationError(ValueError):
    pass


def _parse_worker_arguments(arguments: Sequence[str]) -> tuple[list[str], list[str]]:
    worker_values: list[str] = []
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--workers":
            if index + 1 >= len(arguments):
                raise LauncherConfigurationError("--workers requires a value")
            worker_values.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith("--workers="):
            worker_values.append(argument.partition("=")[2])
            index += 1
            continue
        forwarded.append(argument)
        index += 1
    return worker_values, forwarded


def _require_single_worker(value: str, source: str) -> None:
    try:
        workers = int(value)
    except ValueError as exc:
        raise LauncherConfigurationError(f"{source} must be 1, got {value!r}") from exc
    if workers != 1:
        raise LauncherConfigurationError(f"{source} must be 1, got {value!r}")


def build_uvicorn_exec(
    arguments: Sequence[str],
    environ: Mapping[str, str],
) -> tuple[list[str], dict[str, str]]:
    worker_values, forwarded = _parse_worker_arguments(arguments)
    for value in worker_values:
        _require_single_worker(value, "--workers")
    for name in WORKER_ENV_NAMES:
        value = environ.get(name, "").strip()
        if value:
            _require_single_worker(value, name)

    normalized_env = dict(environ)
    for name in WORKER_ENV_NAMES:
        normalized_env[name] = "1"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        APP_TARGET,
        "--factory",
        "--workers",
        "1",
        *forwarded,
    ]
    return command, normalized_env


def main(
    arguments: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    exec_fn: Callable[[str, list[str], Mapping[str, str]], object] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    environ = os.environ if environ is None else environ
    try:
        command, normalized_env = build_uvicorn_exec(arguments, environ)
    except LauncherConfigurationError as exc:
        print(
            f"AgentScope runtime requires exactly one worker: {exc}",
            file=sys.stderr,
        )
        return 2

    execute = os.execvpe if exec_fn is None else exec_fn
    execute(sys.executable, command, normalized_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
