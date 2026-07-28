# Integration TODO

## Common base

- [x] Merge exact official `v0.11.0` into custom `origin/main` with custom-first parent.
- [x] Record exclusions, protected interfaces, and lane ownership.
- [x] Finish Lane A: security/auth/config/dependencies/migrations.
- [x] Finish Lane B: core chat/runtime/provider/multi-worker/performance.
- [x] Finish Lane C: Terminal/Skills/retrieval/knowledge/files backend.
- [x] Finish Lane D: frontend/UI/accessibility.

## Combined integration

- [x] Merge lane commits in dependency order A -> B -> C -> D.
- [x] Audit all 43 prior content-conflict paths.
- [x] Audit all 37 auto-merged overlapping paths for semantic loss.
- [x] Confirm official Sub-agents runtime and Files trio are absent/unwired.
- [x] Confirm all remaining official v0.11 changes are present or equivalently adapted.
- [x] Reconcile Alembic heads and validate migration graph.
- [x] Run focused and full backend plus frontend suites.
- [x] Run production frontend build.
- [x] Run automated Chat/Agent protocol, Terminal/Skill/OnlyOffice/retrieval/provider acceptance.
- [x] Resolve read-only integration review findings and add regression guards.
- [x] Build and independently inspect the exact-source external-services test image.
- [x] Back up the isolated PostgreSQL database and rehearse upgrade, snapshot-restore rollback, and re-upgrade on a restored copy.
- [ ] Upgrade only the isolated test WebUI service and preserve its database, Redis, AgentScope runtime, and formal-live anchors.
- [ ] Complete authenticated Agent-run authorization, orjson, health, version, four-worker, and AgentScope acceptance probes.
- [ ] Run authenticated browser acceptance on a disposable integration environment.
- [ ] Run isolated four-worker acceptance before any separately authorized live upgrade.
