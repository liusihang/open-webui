# OpenWebUI v0.11.0 integration

This integration brings official `open-webui/open-webui@v0.11.0` into the custom fork while keeping `liusihang/open-webui@main` as the authoritative product base.

## Refs

- Custom first parent: `665221e1910a11cfd20e034d9967c93f5d4025d2`
- Official donor: `f9590b8017199e56d5e953657e6498e3cef1d246`
- Shared base: `ecd48e2f718220a6400ecf49eafd4867a38feb10` (`v0.10.2`)

## Policy

- Bring all official v0.11 changes except the exclusions below.
- Preserve the custom AgentScope runtime, Chat/Agent profiles, Terminal Skills, Chatfile/OnlyOffice flows, layered and multimodal retrieval, citations, pgvector behavior, announcements, and multi-worker coordination.
- The initial merge favored the custom side only for textual conflicts. That is a provisional staging choice, not acceptance. Every overlapping path is owned by an audit lane.

## Exclusions

1. Official second Sub-agents runtime and `delegate_task` wiring.
2. A second stock Agent renderer that would replace or collapse the custom Agent transcript/event authority.
3. Official `list_chat_files`, `grep_chat_files`, and `query_chat_files`, including capability, middleware, registry, and UI wiring.

## Parallel ownership

| Lane | Scope | Primary paths |
|---|---|---|
| A | Security/auth/config/dependencies/migrations | manifests and locks, config/env/auth/audit/header modules, Alembic |
| B | Core chat/runtime/provider/multi-worker/performance | main/functions, chats/messages, provider routes, socket, middleware/filter/plugin runtime |
| C | Terminal/Skills/retrieval/knowledge/files backend | terminal, built-in tools and registry, files/knowledge/retrieval backend |
| D | Frontend/UI/accessibility | `src/`, frontend tests and assets |

Each lane must commit its work and create a lane-specific handoff in this directory. Cross-lane file changes must be reported, not silently taken over.
