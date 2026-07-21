# Findings

- Live database shape is a required pre-deployment truth surface; build success, fresh-schema smoke tests, and Alembic head equality are insufficient across divergent source lines.
- The correct repair is to move source forward on the live-compatible lineage, not reshape the live config table backward.
- Runtime named-volume SQLite must be backed up before target startup because runtime image rollback may otherwise be blocked by a raised store schema version.
- Any runtime-store downgrade must fail closed when a pending continuation exists; image rollback cannot discard or reinterpret pending work.
- The existing live branch already contains durable decision execution, exact cancellation/final privacy, response-envelope normalization, native phase streaming, global prompt, transcript/UI work, and prior deployment fixes. Port only verified gaps.
- Patch ancestry differs between the reviewed branch and the live line, but behavior prerequisites are present under different commit IDs. Direct focused tests are a better dependency check than replaying patch history that would duplicate the same production logic.
- The reviewed `d72ffcaca` commit also touched a separate Agent Memory feature present only on `9810f912a`. Those files must be excluded from the live hardening port unless Agent Memory itself is separately authorized and migrated; otherwise they create a second product expansion and a conflicting migration chain.
- The live line's native attached-knowledge configuration test had not been updated after the per-key config refactor. Runtime code was correct; the test now verifies symbol export, `DEFAULT_CONFIG` registration, and app-config path mapping instead of requiring the removed `ConfigVar` implementation detail.
- A target-image config read against the real live database is a cheap and decisive pre-switch gate. It caught the wrong-base image immediately in the first attempt and now passes for the live-compatible image before any migration or container recreation.
- Runtime rollback now restores an SQLite backup before recreating the old runtime. Merely lowering the schema version is not a general rollback strategy; restoring the full consistent database is safer and preserves the exact pre-switch execution/checkpoint state.
