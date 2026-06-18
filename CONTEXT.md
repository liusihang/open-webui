# OpenWebUI Runtime Concepts

This context names product concepts for OpenWebUI runtime capabilities, including package-backed skills and long-lived assistant memory.

## Language

### Skill Runtime

**Skill**:
A reusable assistant capability represented to the model as instructions.
_Avoid_: Tool, function, plugin

**Skill Package**:
A versioned distribution of a skill that contains instructions and the assets needed to use that skill.
_Avoid_: Script folder, uploaded file, plugin

**Package Asset**:
A file that belongs to a skill package, such as a script, template, reference file, or example.
_Avoid_: Chat attachment, knowledge file, generated output

**Materialized Skill Copy**:
A user-specific runtime copy of a skill package made available in an execution workspace.
_Avoid_: Synced skill, canonical package, uploaded file

**Read-Time Sync**:
The act of making an existing package-backed skill's assets available in a user's terminal workspace while serving `read_skill`.
_Avoid_: Installing an existing skill, registry import

**Skill Install**:
Creating a new OpenWebUI skill from a package source that already exists in the user's terminal workspace.
_Avoid_: Materializing an existing skill, enabling a readable/shared skill

**Skill Update**:
A controlled change to a skill's instructions or package assets that becomes the next canonical version of that skill.
_Avoid_: Runtime patch, terminal edit, cache mutation

**Terminal Workspace**:
The user-owned execution workspace where materialized skill copies and runtime artifacts can live.
_Avoid_: OpenWebUI storage, package registry

### Memory

**User Memory**:
A user-managed personalization memory made of discrete snippets tied to the user's account.
_Avoid_: Agent Memory, Codex memory, chat history, knowledge file

**Agent Memory**:
A user-scoped assistant memory layer that distills prior conversations into durable guidance for future assistant work.
_Avoid_: User Memory, Codex bridge, raw chat history, RAG knowledge

**Memory Scope**:
The audience boundary for Agent Memory. User-global memory is the default; folder memory adds project-like guidance only when a conversation belongs to a folder.
_Avoid_: Project, workspace, chat-only memory

**Memory Promotion**:
The act of moving a repeated or stable folder-local Agent Memory signal into user-global memory.
_Avoid_: Copying, syncing, merging everything globally

**Memory Extraction**:
The first-pass distillation of a completed conversation into raw Agent Memory material for later consolidation.
_Avoid_: Direct memory write, final memory, live chat injection

**Memory Sanitization**:
The deterministic filtering and redaction step applied before and after model-based memory work.
_Avoid_: Prompt-only safety, raw chat ingestion, model-only redaction

**Extraction Cache**:
The stored result of Memory Extraction for a single source conversation at a specific source freshness.
_Avoid_: Final Agent Memory, memory artifact, project memory

**Extraction Job**:
A background work claim for producing or refreshing an Extraction Cache from one eligible conversation.
_Avoid_: Consolidation job, chat request, task queue item

**Memory Eligibility**:
The rule set that decides whether a conversation may enter Memory Extraction.
_Avoid_: Remember everything, summarize everything, chat completion

**Memory Artifact**:
A consolidated Agent Memory document used by the assistant at read time.
_Avoid_: Note, Extraction Cache, raw memory

**Memory Note**:
A user-visible Note linked to a Memory Artifact so the user can inspect and revise assistant memory.
_Avoid_: Memory Artifact, source of truth, ordinary note

**Memory Index**:
A vector-searchable copy of Memory Artifact chunks built with OpenWebUI's configured embedding and vector store.
_Avoid_: Memory Artifact, User Memory collection, knowledge base

**Memory Chunk**:
A Markdown-structured piece of a Memory Artifact stored in the Memory Index for retrieval.
_Avoid_: Whole artifact embedding, arbitrary text split, Extraction Cache

**Human Revision**:
A user edit to a Memory Note that must be considered as input to the next consolidation.
_Avoid_: Direct artifact edit, note-only memory, silent override

**Memory Consolidation**:
The process that turns Extraction Caches and Human Revisions for one Memory Scope into Memory Artifacts and a Memory Index.
_Avoid_: Memory Extraction, note sync, vector indexing only

**Consolidation Contract**:
The strict structured output that a model must return for Memory Consolidation before the backend writes artifacts.
_Avoid_: Freeform file edits, direct DB writes, arbitrary path output

**Memory Read Path**:
The native-tool path that gives a model current Memory Scope summaries and controlled read tools for Agent Memory.
_Avoid_: Non-native injection, User Memory tools, direct memory editing

**Memory Model Configuration**:
The admin-controlled model selection for Memory Extraction and Memory Consolidation.
_Avoid_: Hard-coded model, per-chat model choice, hidden environment-only setting

**Memory Enablement**:
The global, user, and conversation-level controls that decide whether Agent Memory can run or be read.
_Avoid_: Model capability, User Memory enablement, implicit consent

**Memory Forgetting**:
The removal of Agent Memory evidence or outputs after a user disables, deletes, or opts out of memory.
_Avoid_: Model cleanup guess, hidden retention, archive
