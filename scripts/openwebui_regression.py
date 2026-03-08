#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONTAINER_PORT = 38080
REMOTE_VERIFY_PORT = 38080
LOCAL_IMAGE_TAG = "open-webui-regression:local"
CYPRESS_DEFAULT_SPEC = "cypress/e2e/regression.cy.ts"
REMOTE_PASSWORD_ENV = "OPENWEBUI_REMOTE_PASS"
ADMIN_EMAIL_ENV = "OPENWEBUI_ADMIN_EMAIL"
ADMIN_PASSWORD_ENV = "OPENWEBUI_ADMIN_PASSWORD"
ADMIN_NAME_ENV = "OPENWEBUI_ADMIN_NAME"
DEFAULT_ADMIN_NAME = "Admin User"
DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_PASSWORD = "password"
STARTUP_TIMEOUT_SECONDS = 240

REQUIRED_FILES = [
    "package.json",
    "Dockerfile",
    "backend/start.sh",
    "cypress.config.ts",
]

QUICK_PYTEST_TARGETS = [
    "backend/open_webui/test/util/test_middleware_responses_streaming.py",
    "backend/open_webui/test/util/test_openai_cliproxy_file_upload.py",
    "backend/open_webui/test/util/test_chat_context_budget.py",
    "backend/open_webui/test/util/test_message_merge.py",
]

FULL_PYTEST_TARGETS = QUICK_PYTEST_TARGETS + [
    "backend/open_webui/test/apps/webui/routers/test_auths.py",
    "backend/open_webui/test/apps/webui/routers/test_models.py",
    "backend/open_webui/test/apps/webui/routers/test_users.py",
    "backend/open_webui/test/apps/webui/storage/test_provider.py",
]


@dataclass
class PhaseResult:
    name: str
    status: str
    detail: str


class RegressionError(RuntimeError):
    pass


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.results: list[PhaseResult] = []
        self.local_container_name: str | None = None
        self.remote_container_name: str | None = None
        self.remote_image_tag: str | None = None
        self.remote_workdir: str | None = None
        self.python = shutil.which("python3") or shutil.which("python")
        if not self.python:
            raise RegressionError("python3 or python is required")

    def add_result(self, name: str, status: str, detail: str) -> None:
        self.results.append(PhaseResult(name=name, status=status, detail=detail))
        print(f"[{status}] {name}: {detail}")

    def has_failures(self) -> bool:
        return any(result.status == "FAIL" for result in self.results)

    def run(self) -> int:
        try:
            self.print_header()
            self.preflight()

            if self.should_run_local_build_checks():
                self.run_local_build_checks()

            if self.should_run_pytests():
                self.run_pytests()

            local_base_url = None
            if self.should_run_local_runtime():
                local_base_url = self.resolve_local_base_url()
                if self.args.mode != "ui-only":
                    self.run_api_smoke(local_base_url, "local api smoke")

            if self.should_run_local_cypress():
                local_cypress_url = local_base_url or self.resolve_local_base_url()
                self.run_cypress(local_cypress_url, "cypress regression")

            remote_base_url = None
            if self.should_run_remote_flow():
                remote_base_url = self.run_remote_validation()
                self.run_api_smoke(remote_base_url, "remote api smoke")

            if self.should_run_remote_cypress() and remote_base_url:
                self.run_cypress(remote_base_url, "remote cypress regression")
        except RegressionError as exc:
            self.add_result("runner", "FAIL", str(exc))
        finally:
            self.cleanup()
            self.print_summary()

        return 1 if self.has_failures() else 0

    def print_header(self) -> None:
        print("=== Open WebUI Regression Runner ===")
        print(f"Repository: {ROOT}")
        print(f"Mode: {self.args.mode}")
        print()

    def should_run_local_build_checks(self) -> bool:
        return self.args.mode in {"quick", "full", "local-only"} and not self.args.skip_build

    def should_run_pytests(self) -> bool:
        return self.args.mode in {"quick", "full", "local-only"} and not self.args.skip_pytest

    def should_run_local_runtime(self) -> bool:
        return self.args.mode in {"quick", "full", "local-only"} or (
            self.args.mode == "ui-only" and not self.args.base_url
        )

    def should_run_local_cypress(self) -> bool:
        return self.args.mode in {"full", "local-only", "ui-only"} and not self.args.skip_cypress

    def should_run_remote_flow(self) -> bool:
        if self.args.mode == "remote-only":
            return True
        if self.args.mode == "full" and self.args.remote_host and self.args.remote_user:
            return True
        return False

    def should_run_remote_cypress(self) -> bool:
        return self.args.mode == "remote-only" and not self.args.skip_cypress

    def preflight(self) -> None:
        repo_state = self.collect_git_state()
        self.add_result("git state", "PASS", repo_state)

        missing_files = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
        if missing_files:
            raise RegressionError(f"Missing required files: {', '.join(missing_files)}")
        self.add_result("file presence", "PASS", ", ".join(REQUIRED_FILES))

        required_commands = ["npm"]

        if self.should_run_local_runtime() or (self.args.mode == "ui-only" and not self.args.base_url):
            required_commands.append("docker")

        if self.should_run_local_cypress() or self.should_run_remote_cypress():
            required_commands.append("npx")

        if self.should_run_remote_flow():
            required_commands.extend(["ssh", "tar"])

        if self.should_run_remote_flow() and self.remote_password():
            required_commands.append("sshpass")

        missing_commands: list[str] = []
        for command in required_commands:
            command_name = Path(command).name if os.path.sep in command else command
            if not shutil.which(command_name):
                missing_commands.append(command_name)

        if self.should_run_pytests():
            pytest_check = self.run_command(
                [self.python, "-m", "pytest", "--version"],
                phase_name="pytest availability",
                check=False,
            )
            if pytest_check.returncode != 0:
                missing_commands.append("pytest")

        if missing_commands:
            raise RegressionError(f"Missing required commands: {', '.join(sorted(set(missing_commands)))}")

        env_details = []
        if self.admin_email():
            env_details.append(f"admin email env={self.args.admin_email_env}")
        else:
            env_details.append("admin api smoke skipped")
        if self.should_run_remote_flow():
            env_details.append(f"remote host={self.args.remote_user}@{self.args.remote_host}")
        self.add_result("environment", "PASS", "; ".join(env_details))

    def collect_git_state(self) -> str:
        inside = self.run_command(
            ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
            phase_name="git availability",
            check=False,
            capture_stdout=True,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return "not a git repository"

        branch = self.run_command(
            ["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            phase_name="git branch",
            check=False,
            capture_stdout=True,
        ).stdout.strip()
        commit = self.run_command(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            phase_name="git commit",
            check=False,
            capture_stdout=True,
        ).stdout.strip()
        dirty = self.run_command(
            ["git", "-C", str(ROOT), "status", "--short"],
            phase_name="git dirty state",
            check=False,
            capture_stdout=True,
        ).stdout.strip()
        dirty_state = "dirty" if dirty else "clean"
        return f"branch={branch} commit={commit} state={dirty_state}"

    def run_local_build_checks(self) -> None:
        build = self.run_command(["npm", "run", "build"], phase_name="npm build", check=False)
        if build.returncode != 0:
            self.add_result("npm build", "FAIL", "npm run build failed")
            raise RegressionError("Stopping because npm run build failed")
        self.add_result("npm build", "PASS", "npm run build succeeded")

        if self.args.mode != "quick":
            check_result = self.run_command(["npm", "run", "check"], phase_name="npm check", check=False)
            if check_result.returncode == 0:
                self.add_result("npm check", "PASS", "npm run check succeeded")
            elif self.args.strict_check:
                self.add_result("npm check", "FAIL", "npm run check failed in strict mode")
                raise RegressionError("Stopping because npm run check failed in strict mode")
            else:
                self.add_result("npm check", "WARN", "npm run check failed but continue is allowed")

    def run_pytests(self) -> None:
        targets = QUICK_PYTEST_TARGETS if self.args.mode == "quick" else FULL_PYTEST_TARGETS
        cmd = [self.python, "-m", "pytest", *targets]
        result = self.run_command(cmd, phase_name="pytest", check=False)
        if result.returncode == 0:
            self.add_result("pytest", "PASS", f"{len(targets)} target files passed")
        else:
            self.add_result("pytest", "FAIL", f"pytest failed for {len(targets)} target files")

    def resolve_local_base_url(self) -> str:
        if self.args.base_url:
            self.add_result("local target", "PASS", f"using provided base URL {self.args.base_url}")
            return self.args.base_url.rstrip("/")

        if self.local_container_name:
            return f"http://127.0.0.1:{self.args.local_container_port}"

        if self.args.skip_build:
            self.add_result(
                "local container build",
                "WARN",
                "--skip-build does not skip Docker image build for runtime smoke",
            )

        docker_build = self.run_command(
            ["docker", "build", "-t", LOCAL_IMAGE_TAG, "."],
            phase_name="docker build local image",
            check=False,
        )
        if docker_build.returncode != 0:
            raise RegressionError("Local Docker image build failed")
        self.add_result("docker build", "PASS", f"built {LOCAL_IMAGE_TAG}")

        self.local_container_name = f"open-webui-regression-{os.getpid()}"
        run_result = self.run_command(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                self.local_container_name,
                "-p",
                f"{self.args.local_container_port}:8080",
                LOCAL_IMAGE_TAG,
            ],
            phase_name="docker run local container",
            check=False,
            capture_stdout=True,
        )
        if run_result.returncode != 0:
            raise RegressionError("Failed to start local regression container")

        base_url = f"http://127.0.0.1:{self.args.local_container_port}"
        self.wait_for_http(base_url + "/health", "local container health")
        self.check_container_logs(self.local_container_name, "local container logs")
        self.add_result("local container", "PASS", f"running at {base_url}")
        return base_url

    def run_api_smoke(self, base_url: str, phase_prefix: str) -> None:
        root_response = self.http_request("GET", base_url + "/")
        if root_response["ok"]:
            self.add_result(phase_prefix + " root", "PASS", f"status={root_response['status']}")
        else:
            self.add_result(phase_prefix + " root", "FAIL", root_response["detail"])

        version_response = self.http_request("GET", base_url + "/api/version")
        if version_response["ok"]:
            version = version_response["json"].get("version", "unknown")
            self.add_result(phase_prefix + " version", "PASS", f"version={version}")
        else:
            self.add_result(phase_prefix + " version", "FAIL", version_response["detail"])

        admin_email = self.admin_email()
        admin_password = self.admin_password()
        if not admin_email or not admin_password:
            self.add_result(phase_prefix + " auth smoke", "SKIP", "admin credentials not provided")
            return

        signup = self.http_request(
            "POST",
            base_url + "/api/v1/auths/signup",
            payload={
                "name": os.getenv(self.args.admin_name_env, DEFAULT_ADMIN_NAME),
                "email": admin_email,
                "password": admin_password,
            },
            headers={"Content-Type": "application/json"},
        )
        if signup["status"] not in {200, 400}:
            self.add_result(phase_prefix + " auth bootstrap", "FAIL", signup["detail"])
            return
        self.add_result(
            phase_prefix + " auth bootstrap",
            "PASS",
            f"signup status={signup['status']} for {admin_email}",
        )

        signin = self.http_request(
            "POST",
            base_url + "/api/v1/auths/signin",
            payload={"email": admin_email, "password": admin_password},
            headers={"Content-Type": "application/json"},
        )
        if not signin["ok"]:
            self.add_result(phase_prefix + " auth smoke", "FAIL", signin["detail"])
            return

        token = signin["json"].get("token")
        if not token:
            self.add_result(phase_prefix + " auth smoke", "FAIL", "signin response missing token")
            return

        auth_headers = {"Authorization": f"Bearer {token}"}
        endpoints = [
            "/api/v1/models",
            "/api/v1/auths/",
            "/api/v1/configs",
        ]

        failures = []
        for endpoint in endpoints:
            response = self.http_request("GET", base_url + endpoint, headers=auth_headers)
            if not response["ok"]:
                failures.append(f"{endpoint}: {response['detail']}")

        if failures:
            self.add_result(phase_prefix + " auth smoke", "FAIL", "; ".join(failures))
        else:
            self.add_result(phase_prefix + " auth smoke", "PASS", ", ".join(endpoints))

    def run_cypress(self, base_url: str, phase_name: str) -> None:
        spec = self.args.cypress_spec or CYPRESS_DEFAULT_SPEC
        env_pairs = {
            "ADMIN_NAME": os.getenv(self.args.admin_name_env, DEFAULT_ADMIN_NAME),
            "ADMIN_EMAIL": self.admin_email() or DEFAULT_ADMIN_EMAIL,
            "ADMIN_PASSWORD": self.admin_password() or DEFAULT_ADMIN_PASSWORD,
            "EXPECT_TERMINAL": self.bool_env_value(self.args.expect_terminal),
            "EXPECT_RETRIEVAL": self.bool_env_value(self.args.expect_retrieval),
            "EXPECT_CODE_INTERPRETER": self.bool_env_value(self.args.expect_code_interpreter),
        }
        cypress_env = ",".join(f"{key}={value}" for key, value in env_pairs.items())
        cmd = [
            "npx",
            "cypress",
            "run",
            "--spec",
            spec,
            "--config",
            f"baseUrl={base_url}",
            "--env",
            cypress_env,
        ]
        result = self.run_command(cmd, phase_name=phase_name, check=False)
        if result.returncode == 0:
            self.add_result(phase_name, "PASS", f"spec={spec} baseUrl={base_url}")
        else:
            self.add_result(phase_name, "FAIL", f"spec={spec} baseUrl={base_url}")

    def run_remote_validation(self) -> str:
        if not self.args.remote_host or not self.args.remote_user:
            raise RegressionError("remote validation requires --remote-host and --remote-user")

        self.remote_workdir = f"/tmp/openwebui-regression-{int(time.time())}"
        self.remote_image_tag = f"open-webui-regression:{int(time.time())}"
        self.remote_container_name = f"open-webui-regression-{os.getpid()}"

        self.run_remote_command(f"mkdir -p {shlex.quote(self.remote_workdir)}", "remote mkdir")
        self.sync_to_remote()
        self.run_remote_command(
            f"cd {shlex.quote(self.remote_workdir)} && docker build -t {shlex.quote(self.remote_image_tag)} .",
            "remote docker build",
        )
        self.add_result("remote docker build", "PASS", f"built {self.remote_image_tag}")

        self.run_remote_command(
            (
                f"docker rm -f {shlex.quote(self.remote_container_name)} >/dev/null 2>&1 || true && "
                f"docker run -d --name {shlex.quote(self.remote_container_name)} "
                f"-p {self.args.remote_verify_port}:8080 {shlex.quote(self.remote_image_tag)}"
            ),
            "remote docker run",
        )

        base_url = f"http://{self.args.remote_host}:{self.args.remote_verify_port}"
        self.wait_for_http(base_url + "/health", "remote container health")
        self.check_remote_logs("remote container logs")
        self.add_result("remote container", "PASS", f"running at {base_url}")
        return base_url

    def sync_to_remote(self) -> None:
        if not self.remote_workdir:
            raise RegressionError("remote workdir not initialized")

        excludes = [
            ".git",
            "node_modules",
            "backend/data",
            ".venv",
            "venv",
            "dist",
            "build",
        ]
        tar_command = ["tar", "-czf", "-"] + [f"--exclude={item}" for item in excludes] + ["-C", str(ROOT), "."]
        extract_command = self.remote_command_parts(
            f"mkdir -p {shlex.quote(self.remote_workdir)} && tar -xzf - -C {shlex.quote(self.remote_workdir)}"
        )
        print(f"$ {' '.join(shlex.quote(part) for part in tar_command)} | {' '.join(shlex.quote(part) for part in extract_command)}")
        with subprocess.Popen(tar_command, cwd=ROOT, stdout=subprocess.PIPE) as tar_proc:
            assert tar_proc.stdout is not None
            extract_proc = subprocess.run(extract_command, cwd=ROOT, stdin=tar_proc.stdout)
            tar_proc.stdout.close()
            tar_return = tar_proc.wait()
        if tar_return != 0 or extract_proc.returncode != 0:
            raise RegressionError("Failed to sync repository to remote host")
        self.add_result("remote sync", "PASS", f"synced to {self.remote_workdir}")

    def remote_command_parts(self, command: str) -> list[str]:
        parts: list[str] = []
        password = self.remote_password()
        if password:
            parts.extend(["sshpass", "-p", password])
        parts.extend(["ssh", f"{self.args.remote_user}@{self.args.remote_host}", command])
        return parts

    def run_remote_command(self, command: str, phase_name: str) -> None:
        shell_command = self.remote_command_parts(command)
        print(f"$ {' '.join(shlex.quote(part) for part in shell_command)}")
        result = subprocess.run(shell_command, cwd=ROOT)
        if result.returncode != 0:
            raise RegressionError(f"{phase_name} failed")

    def check_container_logs(self, container_name: str, phase_name: str) -> None:
        result = self.run_command(
            ["docker", "logs", container_name],
            phase_name=phase_name,
            check=False,
            capture_stdout=True,
        )
        if result.returncode != 0:
            self.add_result(phase_name, "WARN", "unable to read container logs")
            return

        log_output = result.stdout + result.stderr
        if "Uvicorn running on" in log_output or "Application startup complete" in log_output:
            self.add_result(phase_name, "PASS", "startup markers detected")
        else:
            self.add_result(phase_name, "WARN", "startup markers not detected in logs")

    def check_remote_logs(self, phase_name: str) -> None:
        if not self.remote_container_name:
            return
        shell_command = self.remote_command_parts(f"docker logs {shlex.quote(self.remote_container_name)}")
        result = subprocess.run(shell_command, cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            self.add_result(phase_name, "WARN", "unable to read remote container logs")
            return

        log_output = (result.stdout or "") + (result.stderr or "")
        if "Uvicorn running on" in log_output or "Application startup complete" in log_output:
            self.add_result(phase_name, "PASS", "startup markers detected")
        else:
            self.add_result(phase_name, "WARN", "startup markers not detected in logs")

    def wait_for_http(self, url: str, phase_name: str) -> None:
        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        last_detail = ""
        while time.time() < deadline:
            response = self.http_request("GET", url)
            if response["ok"]:
                self.add_result(phase_name, "PASS", f"status={response['status']} url={url}")
                return
            last_detail = response["detail"]
            time.sleep(2)
        raise RegressionError(f"Timed out waiting for {url}: {last_detail}")

    def http_request(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        request_headers = headers.copy() if headers else {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = None
                if body:
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        parsed = None
                return {
                    "ok": 200 <= response.status < 400,
                    "status": response.status,
                    "body": body,
                    "json": parsed or {},
                    "detail": f"status={response.status}",
                }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return {
                "ok": False,
                "status": exc.code,
                "body": body,
                "json": {},
                "detail": f"status={exc.code} body={body[:240]}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": None,
                "body": "",
                "json": {},
                "detail": str(exc),
            }

    def run_command(
        self,
        command: Iterable[str],
        phase_name: str,
        check: bool,
        capture_stdout: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        print(f"$ {' '.join(shlex.quote(part) for part in command_list)}")
        result = subprocess.run(
            command_list,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
        if check and result.returncode != 0:
            raise RegressionError(f"{phase_name} failed with exit code {result.returncode}")
        if capture_stdout:
            return result
        return result

    def admin_email(self) -> str | None:
        return os.getenv(self.args.admin_email_env)

    def admin_password(self) -> str | None:
        return os.getenv(self.args.admin_password_env)

    def remote_password(self) -> str | None:
        return os.getenv(self.args.remote_password_env)

    @staticmethod
    def bool_env_value(value: bool) -> str:
        return "true" if value else "false"

    def cleanup(self) -> None:
        if self.local_container_name:
            subprocess.run(
                ["docker", "rm", "-f", self.local_container_name],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )

        if self.remote_container_name:
            try:
                self.run_remote_command(
                    f"docker rm -f {shlex.quote(self.remote_container_name)} >/dev/null 2>&1 || true",
                    "remote cleanup container",
                )
            except RegressionError:
                self.add_result("remote cleanup container", "WARN", "failed to remove remote container")

        if self.remote_workdir:
            try:
                self.run_remote_command(
                    f"rm -rf {shlex.quote(self.remote_workdir)}",
                    "remote cleanup workdir",
                )
            except RegressionError:
                self.add_result("remote cleanup workdir", "WARN", "failed to remove remote temp directory")

    def print_summary(self) -> None:
        print()
        print("=== Regression Summary ===")
        for result in self.results:
            print(f"- {result.name}: {result.status} — {result.detail}")
        final_status = "FAIL" if self.has_failures() else "PASS"
        print(f"Final: {final_status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Open WebUI automated regression checks")
    parser.add_argument(
        "mode",
        choices=["quick", "full", "local-only", "remote-only", "ui-only"],
        help="Regression mode to execute",
    )
    parser.add_argument("--strict-check", action="store_true", help="Fail when npm run check fails")
    parser.add_argument(
        "--allow-check-fail",
        action="store_true",
        help="Accepted for compatibility; npm run check failures continue by default",
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip npm run build")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest targets")
    parser.add_argument("--skip-cypress", action="store_true", help="Skip Cypress regression")
    parser.add_argument("--base-url", help="Use an existing local base URL instead of starting a local container")
    parser.add_argument(
        "--local-container-port",
        type=int,
        default=LOCAL_CONTAINER_PORT,
        help="Host port for the temporary local validation container",
    )
    parser.add_argument("--remote-host", help="Remote host for validation")
    parser.add_argument("--remote-user", help="Remote SSH user for validation")
    parser.add_argument(
        "--remote-password-env",
        default=REMOTE_PASSWORD_ENV,
        help="Environment variable that contains the remote SSH password",
    )
    parser.add_argument(
        "--remote-verify-port",
        type=int,
        default=REMOTE_VERIFY_PORT,
        help="Published port for the remote validation container",
    )
    parser.add_argument("--expect-terminal", action="store_true", help="Require terminal-related UI checks")
    parser.add_argument("--expect-retrieval", action="store_true", help="Require retrieval-related UI checks")
    parser.add_argument(
        "--expect-code-interpreter",
        action="store_true",
        help="Require code interpreter UI checks",
    )
    parser.add_argument(
        "--admin-email-env",
        default=ADMIN_EMAIL_ENV,
        help="Environment variable name for the admin email used in API smoke and Cypress",
    )
    parser.add_argument(
        "--admin-password-env",
        default=ADMIN_PASSWORD_ENV,
        help="Environment variable name for the admin password used in API smoke and Cypress",
    )
    parser.add_argument(
        "--admin-name-env",
        default=ADMIN_NAME_ENV,
        help="Environment variable name for the admin display name used by Cypress",
    )
    parser.add_argument(
        "--cypress-spec",
        default=CYPRESS_DEFAULT_SPEC,
        help="Cypress spec or comma-separated spec list to run",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    runner = Runner(args)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
