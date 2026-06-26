# OpenWebUI Pyodide Mirror And Cache Design

Date: 2026-06-26

## Goal

Make `npm run pyodide:fetch` deterministic on constrained networks by:

1. Preferring a checked-in or prebuilt local `static/pyodide` artifact when it matches the requested Pyodide version.
2. Making the remote fallback path configurable so Pyodide package fetches and PyPI metadata/wheel fetches can use controllable mirror endpoints instead of hard-coded public origins.

## Current Problem

The current `scripts/prepare-pyodide.js` mixes three different network surfaces:

- Pyodide runtime and built-in package assets loaded by `loadPyodide()` and `micropip.install()`.
- PyPI metadata fetched from `https://pypi.org/pypi/<pkg>/json`.
- Wheel URLs returned by metadata, commonly under `https://files.pythonhosted.org`.

The backend Python dependency install already uses a domestic mirror, but that does not affect the Pyodide build path. As a result, offline or partially proxied rebuilds can still fail during frontend asset preparation.

## Requirements

- Preserve the existing `npm run pyodide:fetch` entrypoint.
- Prefer local `static/pyodide` artifacts by default.
- Allow strict local-only mode for CI or controlled rebuilds.
- Support configurable Pyodide mirror base URL.
- Support configurable PyPI metadata and wheel mirror base URLs.
- Keep the behavior testable without running a full Docker build.

## Chosen Design

### 1. Local cache first

`prepare-pyodide.js` will validate a local artifact before attempting any network fetch.

The local artifact is considered usable when:

- `static/pyodide/package.json` exists
- `static/pyodide/pyodide-lock.json` exists
- the local Pyodide version matches the version requested by the repo

If the artifact is valid and local cache usage is enabled, the script exits early without running network fetches.

### 2. Configurable network fallback

When local cache is missing or invalid, the script will fall back to remote fetches using explicit configuration derived from environment variables.

Planned variables:

- `PYODIDE_CACHE_POLICY`
  - `prefer-local` default
  - `refresh`
  - `local-only`
- `PYODIDE_INDEX_URL`
  - base URL for Pyodide runtime and built-in packages
- `PYODIDE_PYPI_API_BASE_URL`
  - base URL for package metadata, default `https://pypi.org/pypi`
- `PYODIDE_PYPI_FILES_BASE_URL`
  - optional base URL used to rewrite wheel download URLs from metadata

### 3. Script refactor for testability

The current script executes immediately on import. It will be refactored into exported helpers plus a CLI `main()` entrypoint so Vitest can verify behavior directly.

The main helpers will cover:

- cache-policy parsing
- local artifact validation
- environment-derived source configuration
- PyPI metadata URL construction
- optional wheel URL rewriting for mirrored file hosts

## Data Flow

1. Read repo-level Pyodide version.
2. Read environment config and cache policy.
3. If policy allows local reuse and local artifact is valid, exit successfully.
4. If policy is `local-only` and local artifact is invalid, fail with a clear error.
5. Otherwise fetch/update from configured mirror endpoints.
6. Copy generated assets into `static/pyodide` and persist `pyodide-lock.json`.

## Testing Plan

Add focused Vitest coverage for the script helpers:

- valid local cache is accepted
- invalid local cache is rejected
- `local-only` policy fails when cache is absent
- Pyodide index URL normalization is stable
- PyPI metadata URLs use the configured API base
- wheel URLs are rewritten only when a files mirror base is configured

## Rollout Notes

- Default behavior becomes safer on machines where `static/pyodide` is already populated.
- Existing developers can still force refresh by setting `PYODIDE_CACHE_POLICY=refresh`.
- Controlled rebuild hosts such as `aiserver` can use:
  - prebuilt `static/pyodide`
  - mirror env vars
  - local-only mode when desired

## TODO

- Refactor `scripts/prepare-pyodide.js` into importable helpers plus CLI entrypoint.
- Add Vitest coverage for cache validation and URL generation.
- Document the new environment variables near the slim build workflow.
- Rebuild the PR7 isolated image on `aiserver` using the new default local-first behavior.
