# Context compaction

Automatic compaction uses the existing admin **Interface → Context compaction**
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
