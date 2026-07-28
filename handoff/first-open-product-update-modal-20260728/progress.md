# Progress — first-open product update modal

## 2026-07-28 discovery

- Started a new handoff directory for this feature.
- Read the required brainstorming and file-planning instructions.
- Located the existing changelog API, version-aware modal, user setting, and layout trigger.
- Confirmed automatic “What's New” display is currently administrator-only and acknowledgement is persisted in server-backed user UI settings.
- Reached the first product clarification: whether this update guidance should be shown to every signed-in user or only administrators.
- User confirmed the existing version-update popup should appear once for every signed-in user after each version update; the current administrator-only trigger is not desired.
- Confirmed the current app version is `0.10.2`, memory is config-backed/default-enabled, and conversation mode is fixed after conversation creation.
- Next clarification is the modal content hierarchy: focused guided update only, or focused highlights plus the standard detailed changelog.
- User selected focused highlights plus the standard detailed changelog.
- Prepared three trigger approaches; the recommended design preserves the existing account-level application-version acknowledgement and ships as version `0.10.3`.
- User asked whether product code must change. Clarified that no product code has been changed yet; minimal code changes are required because the current automatic trigger is administrator-only and the current generic modal has no focused guide section.
- User approved a code-based solution on the condition that no Docker image is rebuilt; a guarded static/source hotpatch is acceptable.
- Prepared the concise design for final approval before implementation.
- User corrected the task: a configurable administrator announcement popup had already been implemented before the upgrade, and the management UI was omitted during the update.
- Verified original commit `d2420ff67`; current branch has the modal/layout consumer from replay `92c49dfae` but lacks the admin General controls and admin config API fields.
- Root-cause investigation continues by comparing the original five-file implementation against the current schemas before writing a regression test.
- Confirmed the exact regression boundary against `d2420ff67`, `92c49dfae`, and the v0.10.2 merge history.
- Declared separate source, isolated-runtime, and formal-live truth surfaces.
- User's correction and hotpatch constraint establish the approved recovery design: restore the prior administrator publisher without an image rebuild.
- Entered TDD RED phase; no product code or remote runtime has been changed yet.
- RED evidence: backend announcement contract failed 3/3 and General presentation failed 3/3 for the expected missing fields; the initial missing `pytest` executable was corrected by using `.venv/bin/pytest` and was not counted as evidence.
- GREEN implementation restores DB defaults, protected admin read/write fields, authenticated `/api/config` projection, General settings controls, and Chinese administrator labels.
- Focused GREEN evidence: backend 3 passed; frontend 4 passed.
- Drafted the publishable Chat/Agent/memory announcement at `announcement-content.md` with key `2026-07-chat-agent-memory-v1`.
- Related regression: backend announcement/default-seeding/cache tests 9 passed; frontend announcement and Chat/Agent UI tests 49 passed.
- Production frontend build completed successfully with `npm run build` and wrote `build/`; no Docker image was built.
- Full-repository `npm run check` remains red on pre-existing type errors in unrelated legacy components (first failures in `RichTextInput/AutoCompletion.js` and `listDragHandlePlugin.js`). The production build and scoped tests do not report an announcement-component error.
- Requested an independent read-only review of the scoped diff before commit.
- Completed fresh read-only remote preflight. Isolated and formal WebUI/AgentScope containers are healthy, restart zero; both WebUI services run four workers on the same image. Formal live remains container `ae1b858332b7...` with the four previously observed worker PIDs.
- Independent review found no Critical issue and confirmed the admin-only mutation boundary, authenticated projection, DB-direct multi-worker reads, and Markdown sanitization.
- Review fixes completed with a second RED/GREEN cycle: legacy submissions now preserve omitted announcement fields; enabled announcements require non-empty key/content in backend and frontend; failed admin-config loads remain in the loading state rather than exposing a partial form.
- Final related regression after review fixes: backend 13 passed; frontend 52 passed.
- Rebuilt the final static package successfully (`6375` transformed modules, adapter-static completed, exit 0). Only existing repository-wide Svelte warnings remain.
