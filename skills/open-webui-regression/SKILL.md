---
name: open-webui-regression
description: Use this skill when the user wants a fully automated Open WebUI regression run after syncing upstream, changing UI or backend behavior, validating a container build, or checking a remote deployment candidate without manual click-through.
---

# Open WebUI Regression

## Overview

Use this skill to run the repo-local automated regression workflow for this fork of Open WebUI.
Default execution is script-first with `scripts/openwebui_regression.py`, which coordinates build checks, focused pytest coverage, container startup, API smoke checks, Cypress UI regression, and optional remote validation.

This skill is intended to produce a PASS / FAIL / SKIP summary without requiring manual per-page verification.

## When This Skill Should Trigger

Trigger this skill when the user asks to:
- validate the repo after syncing upstream
- regression test UI changes
- regression test backend changes
- verify a Docker image before rollout
- run pre-release or post-deploy checks
- validate the fixed remote environment at `em@192.168.1.164`

## Workflow

1. Confirm the target mode (`quick`, `full`, `local-only`, `remote-only`, or `ui-only`).
2. Export any required env vars for admin auth and optional remote SSH password.
3. Run `scripts/openwebui_regression.py` with the selected flags.
4. Let the script build, test, start temporary validation targets, and run automated smoke checks.
5. Review the structured summary and address any FAIL items.

## Script

Path: `scripts/openwebui_regression.py`

The script covers these phases:
- repo and tool preflight
- `npm run build`
- optional `npm run check`
- focused backend pytest targets
- temporary local Docker validation container
- API smoke for `/`, `/health`, `/api/version`
- optional authenticated smoke for `/api/v1/models`, `/api/v1/auths/`, `/api/v1/configs`
- Cypress regression via `cypress/e2e/regression.cy.ts`
- optional remote build and remote temporary container validation

## Modes

### Quick

Runs build, focused pytest, local temporary container startup, and API smoke.

```bash
python3 scripts/openwebui_regression.py quick
```

### Full

Runs build, check, expanded pytest set, local temporary container validation, API smoke, Cypress UI regression, and optional remote validation if remote flags are provided.

```bash
python3 scripts/openwebui_regression.py full --strict-check
```

### Local only

Runs the local automated flow only.

```bash
python3 scripts/openwebui_regression.py local-only
```

### Remote only

Builds and validates a temporary remote container without touching the production container.

```bash
python3 scripts/openwebui_regression.py remote-only \
  --remote-host 192.168.1.164 \
  --remote-user em
```

### UI only

Runs the Cypress regression suite against an existing base URL.

```bash
python3 scripts/openwebui_regression.py ui-only --base-url http://127.0.0.1:8080
```

## Common Flags

- `--strict-check`
- `--allow-check-fail`
- `--skip-build`
- `--skip-pytest`
- `--skip-cypress`
- `--base-url <url>`
- `--local-container-port <port>`
- `--remote-host <host>`
- `--remote-user <user>`
- `--remote-password-env OPENWEBUI_REMOTE_PASS`
- `--remote-verify-port <port>`
- `--expect-terminal`
- `--expect-retrieval`
- `--expect-code-interpreter`
- `--admin-email-env OPENWEBUI_ADMIN_EMAIL`
- `--admin-password-env OPENWEBUI_ADMIN_PASSWORD`
- `--cypress-spec cypress/e2e/regression.cy.ts`

## Helpful npm wrappers

- `npm run cy:run:regression`
- `npm run test:regression`

## Required Environment Variables

### Admin credentials for authenticated smoke and Cypress

- `OPENWEBUI_ADMIN_EMAIL`
- `OPENWEBUI_ADMIN_PASSWORD`
- `OPENWEBUI_ADMIN_NAME` (optional; defaults to `Admin User`)

### Remote SSH password

Only needed when password-based SSH is used.

- `OPENWEBUI_REMOTE_PASS`

### Feature expectation toggles

Pass these through script flags rather than hardcoding:
- `--expect-terminal`
- `--expect-retrieval`
- `--expect-code-interpreter`

These tell the Cypress regression whether those UI affordances must exist in the target environment.

## Local Validation Examples

Quick local validation:

```bash
OPENWEBUI_ADMIN_EMAIL=admin@example.com \
OPENWEBUI_ADMIN_PASSWORD=password \
python3 scripts/openwebui_regression.py quick
```

Full local validation with stricter frontend gating:

```bash
OPENWEBUI_ADMIN_EMAIL=admin@example.com \
OPENWEBUI_ADMIN_PASSWORD=password \
OPENWEBUI_ADMIN_NAME="Admin User" \
python3 scripts/openwebui_regression.py full --strict-check --expect-code-interpreter
```

Run Cypress only against an existing instance:

```bash
OPENWEBUI_ADMIN_EMAIL=admin@example.com \
OPENWEBUI_ADMIN_PASSWORD=password \
python3 scripts/openwebui_regression.py ui-only --base-url http://127.0.0.1:8080
```

## Remote Validation Examples

Remote temporary container validation:

```bash
export OPENWEBUI_REMOTE_PASS='***'
export OPENWEBUI_ADMIN_EMAIL='admin@example.com'
export OPENWEBUI_ADMIN_PASSWORD='password'

python3 scripts/openwebui_regression.py remote-only \
  --remote-host 192.168.1.164 \
  --remote-user em \
  --remote-password-env OPENWEBUI_REMOTE_PASS \
  --expect-terminal
```

Full run with local and remote coverage:

```bash
export OPENWEBUI_REMOTE_PASS='***'
export OPENWEBUI_ADMIN_EMAIL='admin@example.com'
export OPENWEBUI_ADMIN_PASSWORD='password'

python3 scripts/openwebui_regression.py full \
  --remote-host 192.168.1.164 \
  --remote-user em \
  --remote-password-env OPENWEBUI_REMOTE_PASS \
  --expect-terminal \
  --expect-retrieval
```

## Failure Handling

- If `npm run build` fails, fix the frontend build before trusting any later result.
- If `npm run check` fails in non-strict mode, the failure is reported as warning-level continuation rather than hard stop.
- If pytest fails, use the listed target file set to reproduce the exact backend failure.
- If `/health` or `/api/version` never become ready, inspect the temporary container logs reported by the script.
- If remote sync or remote Docker build fails, rerun only after fixing SSH, Docker, or dependency issues on the remote host.
- If Cypress fails because an optional feature is absent, rerun without the corresponding expectation flag.
