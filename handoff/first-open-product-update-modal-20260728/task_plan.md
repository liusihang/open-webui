# First-open product update modal plan — 2026-07-28

## Goal

Restore the previously implemented administrator-configurable announcement popup that was partially lost during the v0.10.2 upgrade, then publish a versioned Chat/Agent/memory update notice that every signed-in user sees once per announcement key.

## Scope boundaries

- Restore the existing `AdminAnnouncementModal` path; do not redesign it as the generic changelog modal.
- Do not build an image or recreate any container.
- Preserve unrelated dirty files and existing live-cutover artifacts.
- Do not expose administrator-only defaults, secrets, or internal runtime details in user-facing copy.
- Deployment constraint confirmed: do not rebuild the Docker image; use a guarded frontend/static and backend-source hotpatch with no container recreation.

## Phases

1. Project-context discovery and regression localization — complete
2. RED contract tests for config, API projection, and admin UI — complete
3. Minimal recovery on the current DB-backed config architecture — complete
4. Focused regression, frontend build, and commit — complete (`6ba5c1398`)
5. Isolated-stack static/source hotpatch and real-browser E2E — complete
6. Guarded live hotpatch with immutable container anchors — complete
7. Publish the release announcement, real-browser verify, and remove auth artifacts — complete

## Resolved decisions

- Audience: every signed-in administrator/user.
- Persistence: once per administrator-chosen announcement key per user, using the existing server-backed UI acknowledgement.
- Publisher: administrator-only General settings entry with enable switch, version key, title, and Markdown content.
- Trigger identity: independent announcement key; no package-version bump is required.
- Deployment: source/static hotpatch only, with no Docker image build or container recreation.
- Content: Chat/Agent distinction, conversation mode immutability, model/tool temporary adjustment, enabled memory, and concise usage guidance.

## Corrected task identity

- This is a regression recovery, not a new changelog architecture.
- Recover the previously implemented administrator-configurable announcement popup management surface, then populate it with the Chat/Agent/memory update notice.

## Truth surfaces

| Object           | Truth surface                    | Acceptance                                                               |
| ---------------- | -------------------------------- | ------------------------------------------------------------------------ |
| Source           | this worktree and current branch | focused tests pass and commit contains only scoped files                 |
| Isolated runtime | `aiserver:open-webui-pr7`        | admin publishes; ordinary user sees once; key change retriggers          |
| Formal live      | `aiserver:open-webui`            | container/image/start/restart anchors unchanged; popup behavior verified |

## Rollback

- Before remote hotpatch, archive every replaced backend/static artifact with checksums.
- Restore the archived artifact set in place and rotate workers readiness-gated if acceptance fails.
- Never recreate either container as part of this task.
- Formal-live rollback source/static backup: `/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/backup/`.
- Formal-live announcement rollback snapshot: `/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private/admin-config.before-publish.json`.
- The currently published announcement is intentionally retained; rollback was prepared and not invoked.
