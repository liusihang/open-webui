# Docker Build Slimming Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Slim the OpenWebUI Docker build inputs so frontend image layers are smaller and more deterministic on legacy Docker builders.

**Architecture:** Preserve the existing two-stage Docker build. Limit the frontend stage to only the files needed for `npm run build`, and remove the implicit dependency on `.git` by preferring `APP_BUILD_HASH` in the Svelte version config.

**Tech Stack:** Docker multi-stage builds, SvelteKit, Vite, Node.js

---

### Task 1: Tighten build inputs

**Files:**
- Modify: `/Users/liusihang/openwebui/Dockerfile`
- Modify: `/Users/liusihang/openwebui/.dockerignore`
- Modify: `/Users/liusihang/openwebui/svelte.config.js`

**Step 1: Narrow frontend copies**
- Replace the frontend stage `COPY . .` with explicit copies for:
  - `package.json`, `package-lock.json`, `.npmrc`
  - frontend config files
  - `src/`, `static/`, and `scripts/prepare-pyodide.js`
  - `CHANGELOG.md`

**Step 2: Remove unnecessary frontend git dependency**
- Make `svelte.config.js` prefer `process.env.APP_BUILD_HASH` before calling `git rev-parse HEAD`.
- Remove the frontend-stage `apk add git` install once it is no longer needed.

**Step 3: Tighten context exclusions**
- Extend `.dockerignore` to exclude local temp/worktree/learning directories and other non-build assets that should never enter Docker context.

### Task 2: Verify build safety

**Files:**
- Verify: `/Users/liusihang/openwebui/Dockerfile`
- Verify: `/Users/liusihang/openwebui/.dockerignore`
- Verify: `/Users/liusihang/openwebui/svelte.config.js`

**Step 1: Run frontend build locally**

Run: `npm run build`

Expected: frontend build succeeds with `APP_BUILD_HASH`-aware version logic intact.

**Step 2: Sanity-check Dockerfile shape**

Run: `rg -n "COPY \\. \\.|APP_BUILD_HASH|prepare-pyodide|apk add --no-cache git" Dockerfile svelte.config.js .dockerignore`

Expected:
- no frontend-stage `COPY . .`
- `APP_BUILD_HASH` is preferred in `svelte.config.js`
- required frontend inputs are still copied

