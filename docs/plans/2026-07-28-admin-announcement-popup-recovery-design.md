# Administrator announcement popup recovery

## Problem

The application still contains `AdminAnnouncementModal.svelte`, the per-user announcement-key acknowledgement, and the authenticated layout trigger. After the v0.10.2 upgrade, however, administrators can no longer configure an announcement and `/api/config` no longer projects one. The surviving consumer is therefore unreachable in normal operation.

## Intended behavior

- Only administrators can enable or edit an announcement.
- An announcement has an explicit key, title, and Markdown body.
- Every signed-in administrator or user sees an enabled, non-empty announcement once per key.
- Closing the modal stores the acknowledged key in the existing server-backed user UI settings.
- Changing the key republishes the announcement; disabling it stops display.
- The generic application changelog remains a separate feature.

## Recovery

Restore the missing boundaries on the current per-key database configuration architecture:

1. Seed disabled/empty defaults for `ui.announcement_modal.*`.
2. Add those keys to the protected admin config read/write route and schema.
3. Project them to authenticated users from `/api/config`.
4. Restore the administrator controls in General settings.

The existing modal renderer, Markdown sanitization, layout queueing, and per-user acknowledgement require no redesign.

## Deployment

Build frontend assets without building a Docker image. Validate an in-place source/static hotpatch on the isolated stack first. For live, back up replaced artifacts and rotate workers one at a time behind readiness checks. Container ID, image ID, start time, and Docker restart count must remain unchanged. A later container recreation will discard this hotpatch and therefore requires an image carrying the same commit.
