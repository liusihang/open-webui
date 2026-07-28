# Integration TODO

## Common base

- [x] Merge exact official `v0.11.0` into custom `origin/main` with custom-first parent.
- [x] Record exclusions, protected interfaces, and lane ownership.
- [ ] Finish Lane A: security/auth/config/dependencies/migrations.
- [ ] Finish Lane B: core chat/runtime/provider/multi-worker/performance.
- [ ] Finish Lane C: Terminal/Skills/retrieval/knowledge/files backend.
- [ ] Finish Lane D: frontend/UI/accessibility.

## Combined integration

- [ ] Merge lane commits in dependency order A -> B -> C -> D.
- [ ] Audit all 43 prior content-conflict paths.
- [ ] Audit all 37 auto-merged overlapping paths for semantic loss.
- [ ] Confirm official Sub-agents runtime and Files trio are absent/unwired.
- [ ] Confirm all remaining official v0.11 changes are present or equivalently adapted.
- [ ] Reconcile Alembic heads and validate migration graph.
- [ ] Run focused backend and frontend suites.
- [ ] Run production frontend build.
- [ ] Run Chat and Agent protocol, Terminal/Skill/OnlyOffice/retrieval/provider acceptance.
- [ ] Run isolated four-worker acceptance before any separately authorized live upgrade.
