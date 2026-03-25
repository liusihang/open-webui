# Docker Build Slimming Design

**Goal:** Reduce Docker build context size and avoid oversized frontend build layers without changing the runtime image layout or deployment workflow.

**Recommended Approach:** Keep the current multi-stage build structure, but narrow the frontend build inputs from `COPY . .` to explicit file and directory copies. Tighten `.dockerignore` to exclude local work artifacts and non-build assets. Update `svelte.config.js` to prefer `APP_BUILD_HASH` before shelling out to `git`, so the build no longer needs repository metadata copied into the image.

**Why this is low risk:**
- The final runtime image layout remains unchanged.
- The backend stage still copies the same built frontend output and backend code.
- The frontend build still runs through the same `npm ci` and `npm run build` commands.
- The only behavior change is version-hash resolution becoming more deterministic when `APP_BUILD_HASH` is already provided by Docker.

**Files to touch:**
- `/Users/liusihang/openwebui/Dockerfile`
- `/Users/liusihang/openwebui/.dockerignore`
- `/Users/liusihang/openwebui/svelte.config.js`

**Expected impact:**
- Smaller Docker build context
- Smaller frontend source layer before `npm run build`
- No need to drag `.git` into the build stage
- Lower risk of legacy Docker builder stalling while committing oversized layers
