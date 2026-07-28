# Findings — first-open product update modal

## Existing product mechanism

- The app already exposes `/api/changelog`, parses backend `CHANGELOG.md`, and renders `src/lib/components/ChangelogModal.svelte`.
- `src/routes/(app)/+layout.svelte` compares the saved settings version with the current app version and controls the modal.
- Current automatic display is guarded by `user.role === 'admin'`, while the interface setting and About page can manually show “What's New”.
- The modal records the current version when closed, using the existing settings/localStorage flow.
- Because the saved `settings.version` is server-backed, the existing acknowledgement follows the account across devices after close; localStorage is only a local mirror.
- The backend currently returns the latest five parsed changelog versions, so adding only a new changelog entry would display a generic multi-version release list rather than a focused guided introduction.

## Product implication

- Reusing the existing modal trigger is the lowest-cost path, but its automatic audience must change if ordinary users should see this onboarding.
- A focused first-open update page can still reuse the existing version acknowledgement, modal shell, sequencing with administrator announcements, and manual “What's New” entry point.

## Confirmed requirement

- This is the existing version-update “What's New” popup, not a separate onboarding system.
- Every signed-in user should receive it once after each application version changes.
- Closing it should persist the current version in the existing account UI settings, so it does not reappear on subsequent opens for that version.

## Version and feature truth

- Current package/application version is `0.10.2`; the popup key is the application version, not the image build hash.
- Therefore this update copy must ship with the next application-version value (or an explicitly designed release-notes key); merely changing the changelog under `0.10.2` would not reopen the modal for users who already acknowledged `0.10.2`.
- Memory is a normal config-backed feature (`memories.enable` / `enable_memories`) and defaults enabled; the user-facing notice should describe what it does and where users can review/manage memories without promising that every message will be memorized.
- Chat/Agent mode selection is visible in the chat navbar; a conversation persists its selected mode and does not switch modes mid-conversation.

## Confirmed content hierarchy

- The popup will lead with a focused product guide covering Chat mode, Agent mode, memory, mode-fixed conversations, model choice, and temporary per-conversation adjustments.
- The normal detailed changelog remains below the guide, preserving the existing “What's New” information surface and manual entry points.

## Candidate trigger approaches

1. Recommended: keep the existing application-version acknowledgement, bump the next release to `0.10.3`, add a matching changelog section, show the modal to all signed-in users, and render a structured guide for the current version above detailed notes.
2. Add a separate release-notes key independent of application version. This can re-show notes without a package bump but creates a second version identity and additional settings/config state.
3. Use the administrator announcement modal. It is configurable and already all-user, but is not intrinsically tied to application versions and would duplicate the existing changelog flow.

## Deployment constraint

- User authorizes code changes only if production deployment avoids a Docker image rebuild.
- Svelte source changes require a frontend static build, but the generated assets can be staged and copied into the existing container without rebuilding/recreating it.
- Backend changelog/source changes can be copied in place and loaded through the already-proven readiness-gated worker rotation if a process reload is required.
- The hotpatch must keep the container/image/start/restart anchors, back up replaced files, and carry a durability warning because a later container recreation would discard it.

## Regression evidence

- Original commit `d2420ff67` implemented a configurable announcement popup across five files: admin config API, admin General settings controls, modal component, settings type, and app-layout trigger.
- Current HEAD does not contain `d2420ff67` as an ancestor. Replay commit `92c49dfae` carried the app-layout trigger/modal projection into the current branch, but the current admin General settings file and admin auth config API contain no `ANNOUNCEMENT_MODAL_*` fields.
- This explains the observed behavior: runtime code can display an announcement if config exists, but administrators have no UI/API surface to configure or manually publish it after the upgrade.
- Upstream's generic “See what's new” button is present, but it is a different feature; it only manually opens the static changelog and is not the custom configurable announcement publisher the user referenced.

## Root-cause hypothesis

- The custom-gap replay was incomplete at the configuration boundary: consumer/rendering pieces survived, producer/admin-management pieces were dropped during the upgrade merge/replay.
- The correct fix is to port the missing portions of the original commit onto the current admin config schema, not redesign the changelog modal.

## Root cause confirmed

- Original commit `d2420ff67` added the four `ANNOUNCEMENT_MODAL_*` fields to the admin config route.
- Replay commit `92c49dfae` restored the modal, layout trigger, settings type, General settings controls, and the former persistent-config exports, but omitted `backend/open_webui/routers/auths.py`; therefore the publisher API was already incomplete at replay time.
- Full-history inspection shows the later official v0.10.2 merge replaced the replayed General settings side with upstream content, removing the administrator controls.
- The current per-key DB config migration exposes settings through `ADMIN_CONFIG_KEYS`, `DEFAULT_CONFIG`, and `/api/config`. None currently carries `ui.announcement_modal.*`, so the surviving layout consumer receives no runtime announcement object.
- This is one regression with three missing boundaries: admin read/write mapping, registered defaults/runtime projection, and the General settings presentation.

## Minimal recovery design

- Register four DB-backed defaults, preserving disabled/empty defaults.
- Add the four fields to the current `ADMIN_CONFIG_KEYS` and `AdminConfig` schema so only administrators can mutate them through the existing protected route.
- Include the announcement object only in the authenticated administrator/user branch of `/api/config`.
- Restore the existing controls in General settings with defensive UI defaults; leave the existing modal, acknowledgement, sequencing, and sanitization untouched.
- Do not couple the announcement key to the package version or modify the generic changelog mechanism.

## Review hardening

- AdminConfig uses defaults so older clients can parse the new schema, but omitted announcement fields are removed from the update map using `model_fields_set`; an older General page cannot silently disable or erase a published announcement.
- When the popup is enabled, the backend rejects blank version keys and blank Markdown bodies. The frontend performs the same validation before POST and provides translated feedback.
- The General page only normalizes announcement defaults after a complete admin config response; a failed GET no longer exposes a partially populated save form.

## Repository state

- Branch: `codex/pr7-chat-agent-dual-mode-20260726` at `90683a1a2`.
- Existing unrelated dirty files under the older dual-mode handoff, `.playwright-cli/`, and `output/` must remain untouched.

## Remote preflight — 2026-07-28 13:46 +08:00

| Runtime | Container | Image | Health | Restarts | Started | Workers |
|---|---|---|---|---:|---|---:|
| Isolated WebUI | `715d9301220d...` | `sha256:ab6d8f1816a...` | healthy | 0 | `2026-07-27T17:41:56Z` | 4 |
| Isolated AgentScope | `739472bd3274...` | `sha256:f7396ba23e49...` | healthy | 0 | `2026-07-21T22:06:22Z` | n/a |
| Formal WebUI | `ae1b858332b7...` | `sha256:ab6d8f1816a...` | healthy | 0 | `2026-07-28T03:19:21Z` | 4 |
| Formal AgentScope | `2f96c76d462a...` | `sha256:f7396ba23e49...` | healthy | 0 | `2026-07-28T03:11:10Z` | n/a |

- Isolated health: `http://127.0.0.1:18085/health` returned `{"status":true}`.
- Formal health: `http://127.0.0.1/health` returned `{"status":true}`.
- Formal live worker PIDs remain `520375 521504 525837 528771` under master `115044`.
- Both WebUI containers currently have matching source/build metadata, so the isolated stack is a faithful first hotpatch target.
- No remote state was changed; only `/tmp/pr7-announcement-preflight.sh` was uploaded and executed read-only.
