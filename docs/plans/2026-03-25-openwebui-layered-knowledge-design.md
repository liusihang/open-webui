# OpenWebUI Layered Knowledge Design

## Goal

Upgrade the current Open WebUI knowledge base from single-layer chunk retrieval into a three-layer retrieval system:

- `abstract`
- `key_findings` / `key_data`
- `full_text`

The system keeps Open WebUI as the user-facing multi-user platform and uses Open Notebook as a shared backend service for generating structured per-file insights.

## Scope

This design is based on the current local `main` branch under `/Users/liusihang/openwebui`.

Relevant current code anchors:

- knowledge models: `backend/open_webui/models/knowledge.py`
- knowledge router: `backend/open_webui/routers/knowledge.py`
- builtin knowledge tools: `backend/open_webui/tools/builtin.py`
- tool injection logic: `backend/open_webui/utils/tools.py`
- workspace knowledge UI: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`
- workspace knowledge API client: `src/lib/apis/knowledge/index.ts`

## Current State

Open WebUI already has:

- multi-user knowledge bases with access grants
- knowledge-to-file linking
- file ingestion and vector retrieval
- builtin knowledge tools dynamically injected into chats based on effective knowledge scope

Current retrieval is effectively a single-layer `full_text` chunk search exposed through `query_knowledge_files`.

## Desired Product Behavior

For each knowledge file, the platform should maintain:

- `Abstract`: what the document is broadly about
- `Key Findings`: conclusions, claims, takeaways, risks, comparisons
- `Key Data`: numbers, metrics, dates, money, parameters, experiment results
- `Full Text`: original chunk-level retrieval already handled by Open WebUI

During chat:

- all layered retrieval tools are exposed to the model
- the model decides which tool to call based on the user question
- the rules for choosing layers are encoded in tool descriptions, not in a backend state machine
- source labels are shown with a layer prefix such as `Abstract: paper.pdf`

During workspace management:

- users manage layered outputs from the existing knowledge/file views
- access control remains exactly the current Open WebUI knowledge/file model
- no new audit or admin-only governance layer is added in v1

## Non-Goals

- replacing Open WebUI's existing `full_text` retrieval stack
- turning Open Notebook into a user-facing multi-tenant product
- adding per-user credentials inside Open Notebook
- building retrieval audit, governance dashboards, or complex orchestration policies
- forcing a deterministic retrieval state machine in backend middleware

## System Roles

### Open WebUI

Open WebUI remains the system of record for:

- users
- groups
- knowledge bases
- file ownership and permissions
- chat sessions
- final retrieval scope
- `full_text` retrieval

### Open Notebook

Open Notebook is used only as a shared internal backend service for:

- transformation-driven insight generation
- generating `abstract`
- generating `key_findings`
- generating `key_data`

Open Notebook is not treated as a multi-user auth system.

## Open Notebook Deployment Assumption

Use a single shared Open Notebook backend service for all Open WebUI users.

Important implications:

- Open Notebook auth is a global password middleware, not a user model
- Open Notebook settings are global singleton settings
- Open Notebook credentials are global shared credentials
- Open WebUI backend should be the only caller of Open Notebook

Recommended deployment model:

- Open WebUI backend stores one Open Notebook service password
- Open Notebook is not exposed directly to end users
- Open Notebook provider credentials are configured once and shared for all insight generation jobs

## Data Model

Keep current Open WebUI tables unchanged:

- `knowledge`
- `knowledge_file`

Add a sidecar table for per-file layered outputs.

### New table: `knowledge_file_layer`

Suggested fields:

- `id`
- `knowledge_id`
- `file_id`
- `layer_type` — enum-like string: `abstract`, `key_findings`, `key_data`
- `title` — optional short label
- `content` — generated text payload
- `status` — `pending`, `ready`, `failed`, `stale`
- `source_system` — e.g. `open_notebook`
- `source_ref_id` — Open Notebook `insight_id`
- `transformation_ref_id` — Open Notebook transformation identifier
- `content_hash` — optional file-content hash for stale detection
- `created_at`
- `updated_at`

Why a sidecar table instead of `knowledge.meta`:

- layers belong to files, not knowledge bases
- each file needs independent generation status
- each layer needs refresh/retry behavior
- the structure needs to remain queryable

## Retrieval Layers

### Layer 1: `abstract`

Use when the user asks:

- what this document is about
- which documents are relevant
- broad topic discovery

### Layer 2a: `key_findings`

Use when the user asks:

- what are the conclusions
- what are the main takeaways
- what are the risks, comparisons, contributions, or claims

### Layer 2b: `key_data`

Use when the user asks:

- specific numbers
- metrics
- amounts
- dates
- parameters
- experiment results

### Layer 3: `full_text`

Use when the user asks:

- original evidence
- exact wording
- quote verification
- surrounding context
- detailed trace-back into the source

`full_text` stays in Open WebUI's existing retrieval path and is not generated by Open Notebook.

## Open Notebook API Usage

The Open WebUI backend should integrate through a thin internal client and only use the subset needed for layered insights:

- `POST /api/sources/{source_id}/insights`
- `GET /api/sources/{source_id}/insights`
- `GET /api/insights/{insight_id}`

Optional later use:

- `POST /api/insights/{insight_id}/save-as-note`

Each layered output maps to a dedicated Open Notebook transformation:

- one transformation for `abstract`
- one transformation for `key_findings`
- one transformation for `key_data`

## Open WebUI Internal Integration

Add a thin service module responsible for:

- mapping Open WebUI `file_id` to the corresponding Open Notebook source identity
- triggering insight generation
- syncing completed insight content into `knowledge_file_layer`
- exposing layer-search helpers for builtin tools

Suggested module:

- `backend/open_webui/utils/layered_knowledge.py`

This service should hide Open Notebook request details from routers and builtin tools.

## Retrieval Tool Design

Expose all layered tools to the model. The model decides which one to call.

### New builtin tools

- `query_knowledge_abstract`
- `query_knowledge_key_findings`
- `query_knowledge_key_data`
- `query_knowledge_full_text`
- `view_knowledge_layers`

Optional existing tools to keep:

- `view_file`
- `view_note`

### Tool behavior

`query_knowledge_abstract`

- searches only `abstract` layer rows in the effective knowledge scope
- best for broad relevance and initial document selection

`query_knowledge_key_findings`

- searches only `key_findings` layer rows
- best for conclusions and takeaways

`query_knowledge_key_data`

- searches only `key_data` layer rows
- best for metrics and structured facts

`query_knowledge_full_text`

- runs the current Open WebUI chunk retrieval flow
- best for evidence and detailed verification

`view_knowledge_layers`

- returns all known generated layers for a single file
- helps the model inspect what is already available before going deeper

## Tool Description Strategy

The model-facing behavior is guided through docstrings/tool descriptions rather than backend orchestration.

Key decision:

- do not implement a forced retrieval state machine in middleware
- do describe intended layer usage clearly in each tool definition

This keeps the system simpler and aligns with the current Open WebUI tool exposure model.

## Tool Injection Changes

Open WebUI already computes effective knowledge scope and resolves scoped builtin knowledge tools in:

- `backend/open_webui/utils/tools.py`

Currently it resolves:

- `query_knowledge_files`
- `view_file`
- `view_note`

The new design changes scoped knowledge tool resolution to expose the layered tools instead.

When scoped knowledge is active, the builtin set should become:

- `query_knowledge_abstract`
- `query_knowledge_key_findings`
- `query_knowledge_key_data`
- `query_knowledge_full_text`
- `view_knowledge_layers`
- plus `view_file` / `view_note` when needed by scope type

## Sync and Generation Lifecycle

### Trigger

When a file is added to a knowledge base and its existing Open WebUI ingestion is complete:

1. enqueue layered generation
2. request `abstract`
3. request `key_findings`
4. request `key_data`
5. sync results into `knowledge_file_layer`

### Refresh

User actions in the workspace should allow:

- regenerate one layer
- regenerate all layers for one file

### Failure handling

If generation fails:

- store `failed` in the row status
- keep the file otherwise usable
- allow user-triggered retry from the file detail view

## Search Semantics

Layered searches should remain scope-aware and permission-aware through existing Open WebUI scope resolution.

Search ranking principles:

- query only within the active effective knowledge scope
- rank within a single layer per tool call
- return layer-prefixed sources in tool output

Example tool result item:

- `layer`: `abstract`
- `file_id`: `...`
- `source`: `Abstract: annual_report.pdf`
- `content`: `...`

## UI Changes

Extend the existing knowledge file experience rather than creating a parallel management UI.

### Workspace knowledge detail

In the existing knowledge detail/file area, add a `Layers` section showing:

- `Abstract`
- `Key Findings`
- `Key Data`
- generation status
- last updated time
- regenerate action

### Source presentation in chat

Display a layer prefix in the source label:

- `Abstract: ...`
- `Key Findings: ...`
- `Key Data: ...`
- `Full Text: ...`

No extra audit or governance panels are included in v1.

## Permissions

Permissions remain fully owned by Open WebUI.

Rules:

- if a user can read a knowledge/file in Open WebUI, they can read its layers
- if a user can manage the file/knowledge resource in Open WebUI, they can regenerate its layers
- Open Notebook never performs user-level authorization decisions

## Configuration

### Open Notebook

Recommended shared backend configuration:

- `OPEN_NOTEBOOK_PASSWORD`
- `OPEN_NOTEBOOK_ENCRYPTION_KEY`
- shared provider credentials configured once

Suggested operating mode:

- internal-only service
- not directly user-facing
- Open WebUI backend as the sole caller

### Open WebUI

Add a small integration config block, for example:

- Open Notebook base URL
- Open Notebook bearer password
- transformation IDs for `abstract`, `key_findings`, `key_data`
- request timeout / retry values

## Why This Design

This design preserves the strongest parts of both systems:

- Open WebUI keeps its mature multi-user workspace, access control, and chat integration
- Open Notebook contributes structured per-document insight generation
- `full_text` retrieval remains local to Open WebUI and does not become dependent on an external service
- layered retrieval becomes explicit and controllable without adding a heavy orchestration engine

## Risks

- mapping Open WebUI file identity to Open Notebook source identity needs careful consistency rules
- insight freshness must be tied to file content changes
- tool descriptions must be written clearly enough so models do not overuse `full_text`
- layered search quality depends on transformation prompt quality

## v1 Acceptance Criteria

- every knowledge file can have `abstract`, `key_findings`, and `key_data`
- layers are generated asynchronously via Open Notebook
- all layered tools are available to the model in scoped knowledge chats
- model can retrieve different layers without backend state-machine forcing
- chat source labels include the layer prefix
- users can inspect and regenerate layers from the existing knowledge workspace
