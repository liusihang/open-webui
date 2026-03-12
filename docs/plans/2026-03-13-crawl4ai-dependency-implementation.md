# Crawl4AI Dependency Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `crawl4ai` as a formal OpenWebUI Python dependency across Docker and package-install paths.

**Architecture:** Update the two active dependency declaration surfaces, then regenerate the lockfile from `pyproject.toml`. Verification is manifest-based and lockfile-based because this change is dependency configuration rather than application behavior.

**Tech Stack:** Python packaging, `requirements.txt`, `pyproject.toml`, `uv lock`

---

### Task 1: Document the desired dependency state

**Files:**
- Create: `docs/plans/2026-03-13-crawl4ai-dependency-design.md`
- Create: `docs/plans/2026-03-13-crawl4ai-dependency-implementation.md`

**Step 1: Confirm active dependency entry points**

Run: `rg -n "requirements.txt|dependencies =|optional-dependencies" Dockerfile pyproject.toml backend/requirements.txt`
Expected: Dockerfile points at `backend/requirements.txt`, package metadata lives in `pyproject.toml`.

**Step 2: Record the chosen approach**

Write the design and implementation notes describing:
- add `crawl4ai` to `backend/requirements.txt`
- add `crawl4ai` to `pyproject.toml` optional dependency group `all`
- refresh `uv.lock`

### Task 2: Prove the dependency is missing before editing

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Step 1: Run the failing pre-change check**

Run: `python - <<'PY'\nfrom pathlib import Path\nreq = Path('backend/requirements.txt').read_text()\npyproject = Path('pyproject.toml').read_text()\nassert 'crawl4ai' in req, 'crawl4ai missing from backend/requirements.txt'\nassert 'crawl4ai' in pyproject, 'crawl4ai missing from pyproject.toml'\nPY`
Expected: FAIL because `crawl4ai` is not declared yet.

### Task 3: Add the dependency and refresh the lockfile

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Step 1: Add the pinned dependency to the Docker/backend dependency list**

Add `crawl4ai==0.8.0` to `backend/requirements.txt` near the existing crawl/search-related optional packages.

**Step 2: Add the same dependency to the package install path**

Add `crawl4ai==0.8.0` to `pyproject.toml` optional dependency group `all`.

**Step 3: Regenerate the lockfile**

Run: `uv lock`
Expected: `uv.lock` updated with the resolved `crawl4ai` dependency graph.

### Task 4: Verify the final state

**Files:**
- Verify: `backend/requirements.txt`
- Verify: `pyproject.toml`
- Verify: `uv.lock`

**Step 1: Re-run the manifest presence check**

Run: `python - <<'PY'\nfrom pathlib import Path\nreq = Path('backend/requirements.txt').read_text()\npyproject = Path('pyproject.toml').read_text()\nassert 'crawl4ai==0.8.0' in req\nassert 'crawl4ai==0.8.0' in pyproject\nprint('dependency declarations verified')\nPY`
Expected: PASS.

**Step 2: Verify the lockfile contains the package**

Run: `rg -n "name = \"crawl4ai\"|crawl4ai==0.8.0" uv.lock`
Expected: at least one `crawl4ai` match in `uv.lock`.

**Step 3: Verify the diff is cleanly formatted**

Run: `git diff --check`
Expected: PASS with no whitespace or patch formatting errors.
