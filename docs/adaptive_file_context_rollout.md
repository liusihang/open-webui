# Adaptive File Context Rollout and Rollback

## Scope
- Feature: native adaptive file context in backend middleware/retrieval path
- Guard: `ADAPTIVE_FILE_CONTEXT_ENABLED`
- Risk profile: low-medium (routing behavior only, no schema rewrite)

## Rollout Plan
1. Deploy with `ADAPTIVE_FILE_CONTEXT_ENABLED=false` and migration enabled.
2. Verify migration marker `adaptive_file_context.migration_version == 1`.
3. Enable `ADAPTIVE_FILE_CONTEXT_DEBUG=true` for canary window only.
4. Monitor adaptive status events for reasons (`small_file`, `budget_cap`, `scope_denied`).
5. Disable debug after validation and keep feature enabled.

## Rollback Plan
1. Set `ADAPTIVE_FILE_CONTEXT_ENABLED=false`.
2. Restart service (or reload config if live config is enabled).
3. Run smoke requests with representative file payloads.
4. Confirm no adaptive event emissions and baseline retrieval behavior.

## Verification Checklist
- Config endpoint exposes adaptive settings.
- Middleware mutates only `metadata.files[].context`.
- Malformed and cross-scope files do not crash request handling.
- Full-context manual override is preserved unless request cap forces downgrade.
