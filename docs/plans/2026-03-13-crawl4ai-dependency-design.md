# Crawl4AI Dependency Design

**Date:** 2026-03-13

## Goal

Make `crawl4ai` a first-class Python dependency for OpenWebUI so Docker builds and package-based installs can include it without manual container patching.

## Scope

- Add `crawl4ai` to the backend dependency manifest used by the Docker image build.
- Add `crawl4ai` to the Python package metadata path used by `pip install open-webui[all]`.
- Refresh the lockfile so the repository records the resolved dependency graph.

## Non-Goals

- No application code integration with `crawl4ai` in this change.
- No new UI, API, or tool wiring in this change.
- No forced browser bootstrap changes in startup scripts for now.

## Approach

Use a pinned dependency version in both dependency entry points:

- `backend/requirements.txt` for the Docker image path.
- `pyproject.toml` optional dependency group `all` for package installs.

Then refresh `uv.lock` from the updated `pyproject.toml` so the repository reflects the new dependency state.

## Notes

- PyPI currently publishes `crawl4ai==0.8.0`.
- Official installation guidance indicates browser setup may still be required at runtime (`crawl4ai-setup` or Playwright browser install), but that is intentionally deferred until there is a concrete runtime integration requirement.
