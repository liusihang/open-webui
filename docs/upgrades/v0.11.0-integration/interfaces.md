# Protected integration contracts

## Agent protocol authority

- Chat and Agent modes remain immutable conversation-level choices.
- Agent commentary/process events and final answers remain separate protocol phases.
- Approval, user input, cancellation, reconnect, refresh recovery, artifacts, and subagent attribution remain durable AgentRun state.
- Official ordinary-chat fixes may be ported, but they must not reinterpret or flatten custom Agent events.

## Subagent authority

- `backend/open_webui/agent/subagents.py` and the AgentScope runtime are authoritative.
- Do not expose official `delegate_task`.
- Do not retain official `backend/open_webui/utils/subagents.py` runtime wiring.
- No duplicate admin setting or frontend toggle may activate a second subagent engine.

## Files-tool exclusion

- Do not define or register `list_chat_files`, `grep_chat_files`, or `query_chat_files`.
- Do not advertise a Files capability backed by those functions.
- Preserve existing file/Terminal/knowledge tools and their ACL semantics.

## Terminal and Skill contracts

- Package-backed Skills remain terminal-context dependent.
- Existing install/read/update Skill package paths and terminal session authority remain compatible.
- Official Terminal security, policy, lifecycle, and UX fixes should be integrated into this contract.

## Retrieval contracts

- Layered knowledge, multimodal evidence, citations, document image assets, external lexical retrieval, and pgvector fallback behavior remain available.
- Official ACL, bounded search, extraction, performance, and loader fixes should be integrated without bypassing these layers.

## Migration contract

- Custom Alembic head at the integration parent: `c0d3b4a5e6f7`.
- Official v0.11 head: `f0bd01a18a3d`.
- Integration head: `a11c0d3f0bd0`, an explicit merge of the custom and official branches.
- Deployment requires a duplicate normalized-email preflight and upgrade/snapshot-restore/re-upgrade evidence on a restored database copy before any target database is migrated. A branch-targeted Alembic downgrade must not be used when it would traverse the custom branch back to the branches' shared ancestor.
