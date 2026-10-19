# Context compaction

## Idle tool-output pruning (default)

Saved chats now default to deterministic tool-output pruning instead of summary
compaction. After **more than 26 minutes** without a chat request/activity, the
next new turn retains at most **4000 tokens of original completed tool-result
text in aggregate**, newest results first. This is neither a character limit nor
a per-tool limit. No LLM summary is generated and there is no frontend pause or
confirmation. All user messages and assistant prose, including commentary,
remain in the request. The full original messages and tool results remain in the
database and UI.

Configuration is available through environment defaults and the existing admin
`GET/POST /api/v1/chats/config` endpoint (GET the current config, change the desired
fields, then POST it):

- `ENABLE_IDLE_TOOL_OUTPUT_PRUNING`: `true` by default
- `IDLE_TOOL_OUTPUT_PRUNING_IDLE_SECONDS`: `1560`, minimum `1`
- `IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET`: `4000`, minimum `0`

The database keys are `chat.idle_tool_pruning.enable`, `.idle_seconds`, and
`.token_budget`. Existing admin UI saves preserve these fields when omitted.
This mode takes precedence over automatic/manual text summaries and native
Responses compaction, including old checkpoints. Disable it to restore the
existing compaction behavior described below. This precedence applies from the
first request, not only after an idle episode: old summaries cannot silently
remove dialogue or reintroduce discarded results. Disabling does not erase masks;
reenabling resumes them for unchanged results.

### Retention and safety details

- Only completed assistant turns with completed, paired function results or
  completed legacy code-interpreter results are eligible. Call IDs, names,
  arguments, code, ordering and result envelopes remain intact. The last result
  that fits partially retains its text prefix; older result bodies become empty
  `input_text` parts (or an empty legacy interpreter result). A partial result
  gets a short `[truncated]` marker. The source-content budget excludes these
  small markers and protocol framing; it is not a total request-size cap.
- Tool-result images and other non-text payload parts from eligible old turns
  are omitted before conversion to synthetic user/image messages. A text
  tokenizer cannot reliably budget their provider-specific cost. Genuine user
  attachments and new tool images remain untouched.
- A real `tiktoken` encoding is selected for known model IDs. Unknown/custom or
  prefixed IDs use `cl100k_base` with a warning: this is a local tokenizer budget,
  **not an exact provider token count**. If tokenizer data cannot be loaded,
  eligible old result bodies are conservatively omitted (zero retained source
  tokens), with an error log; there is no character-count approximation or LLM
  fallback. Tokenization runs off the event loop.
- The server records activity before inserting the next turn and after request
  completion, cancellation or failure. UI/title/read-status changes do not
  reset that clock. On the first request after migration only, the existing
  server chat timestamp bootstraps the clock. Client timestamps are ignored.
  Active registered work, Continue/approval-resume requests and a pending
  assistant on the selected branch never start a new pruning episode.
- Durable server-only masks are stored separately from the chat transcript and
  serialized with chat writes (SQLite immediate transaction / PostgreSQL row
  lock). They identify each original result and retain only prefix offsets,
  not copied result content. Subsequent requests and tool-loop continuations
  reuse the masks; new output remains until a later idle episode. Repeated
  episodes may shrink a mask but never expand it. Changing the model or removing
  a newer result does not refill old output. Edited result bodies are new
  evidence; stable IDs survive inserted commentary.
- Only the requested branch is considered. Sibling-only results are untouched;
  masks of genuinely shared ancestor results also apply to those ancestors on
  other branches. Concurrent requests serialize the idle decision, so only the
  first can create a new episode. Temporary/unsaved chats are unchanged.

User- or assistant-prose-heavy chats can still exceed an upstream context window.
This feature intentionally does not silently summarize or remove that dialogue.

With idle tool-output pruning disabled, automatic compaction uses the existing admin **Interface → Context compaction**
switch and token threshold. It does not delete the original chat transcript.
The threshold triggers compaction; it is not a hard maximum request size.

## Text summaries

Models without an explicitly enabled native capability use the existing rolling
text summary. Recent turns are retained according to the configured message
retention percentage. The summary model setting remains independent from the
external task model; an empty summary-model setting follows the chat model.

An administrator can exclude specific model IDs from text summarization using
`CONTEXT_COMPACTION_SUMMARY_EXCLUDED_MODELS` in `GET/POST /api/v1/chats/config`
(`chat.context_compaction.summary_excluded_models` in storage). Use exact full
OpenWebUI IDs, including custom model IDs and connection prefixes. This is useful
for GPT targets whose native route is pending validation: they retain full
history rather than silently falling back to a text summary.

## Native Responses compaction

Enable native compaction only after verifying the actual upstream route and
model, including opaque-state continuation. In that connection's admin
`OPENAI_API_CONFIGS` entry, set the exact upstream model allowlist, for example:

```json
{
	"api_type": "responses",
	"native_compaction_models": ["gpt-6.1-sol", "gpt-6-astra"],
	"native_compaction_protocol": "codex_v2"
}
```

The list uses provider IDs **without** the OpenWebUI connection prefix. A model
name containing `gpt` does not by itself enable native compaction. Chat
Completions, Pipes, direct browser connections and stateful
`previous_response_id` chaining are not native-compaction targets here.

The protocol is explicit and has no automatic fallback:

- `responses_compact` (the default) sends the final, attachment-resolved input
  to the public standalone `/responses/compact` endpoint. Its entire returned
  `output` is the canonical next window; it is not pruned.
- `codex_v2` uses the versioned Codex `remote_compaction_v2` contract: normal
  streaming `/responses`, `store=false`, one final `compaction_trigger`, and
  `x-codex-beta-features: remote_compaction_v2`. This is not public API
  `context_management`, and does not call the old Codex unary endpoint.
  Only completed prior turns are compacted. Every client user/developer/system
  message in that prior window is preserved verbatim, including images; old
  assistant/tool trajectories are replaced by the complete ordered done output.
  Exactly one nonempty opaque compaction item and `response.completed` are
  required. The current user turn is appended after this constructed window.

Both protocols use the **same resolved model as the chat Responses request**. They
do not select the text-summary model or the external task model. An upstream
which rewrites compaction to another model does not meet this contract and must
not be marked as verified.
The complete next window (returned canonical output for standalone, constructed
client-content-plus-output for Codex v2) is saved separately in the message's
`meta.native_compaction` checkpoint. Nothing is decrypted or converted to a text
summary. Next requests replay that whole window followed by new input.
`store=false` is preserved. Native mode and text-summary mode do not run on the
same request. The existing manual text-compaction action does not generate a
text summary for native/excluded targets; automatic native compaction runs at
the next eligible request.

Checkpoints bind to version, protocol, exact original input prefix, resolved model,
connection, endpoint, credential and instructions. Branch changes, edited prefixes or route
changes therefore fall back to the retained full transcript, not incompatible
encrypted state. Tool call/result batches must be complete before compaction.
On failure the previous valid window or full transcript is retained, a failure
status is emitted, and the same branch has a five-minute retry cooldown. Failed
native calls do not fall back to summarizing opaque content. Original chat
messages remain the recovery source.

Limitations: token counting is estimated; Codex-v2 savings depend on the old
assistant/tool share. All user text and attachments remain, so a user/RAG-heavy
window can still be large. This deliberately does not copy Codex's client-message
64k/image pruning policy. No cross-account or cross-provider compatibility of
encrypted state is assumed. A gateway which rotates accounts behind the same URL/key must itself
provide a compatible continuation route. Maximum-window stress, that gateway
behavior, and native model quality require real upstream acceptance, not only
unit tests. Merely saving the configuration is not proof of provider support.

Protocol reference: OpenAI's Compaction guide and Responses compact API reference
at `https://developers.openai.com/api/docs/guides/compaction` and
`https://developers.openai.com/api/reference/resources/responses/methods/compact`.
The separate Codex-v2 request and retained-client-window policy is based on
OpenAI Codex 0.154.0 `compact_remote_v2.rs` and `compact_remote_v2_attempt.rs`;
the contracts must not be interchanged.
