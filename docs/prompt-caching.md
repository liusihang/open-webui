# Native GPT chat cache routing key

Saved OpenWebUI chats sent through a connection configured with
`api_type: responses` receive a default `prompt_cache_key` when that top-level
field is missing. This applies to actual provider model IDs matching numeric
GPT generations 4 and newer (including `gpt-6-astra`, `gpt-6.1-sol` and
`gpt-6-luna`), after connection prefix removal and model parameter merging.
Display aliases do not determine eligibility.

The key is a routing hint for compatible gateways such as Sub2API. It does not
guarantee a cache hit, account selection, reduced latency or lower cost. This
change has only offline payload and continuation tests, not a live gateway or
provider cache benchmark.

## Scope and explicit values

The default is `owui:pc:v1:` followed by a truncated SHA-256 hex digest, totaling
64 ASCII characters. Its inputs are the authenticated user's ID, saved chat ID,
connection index, normalized connection base URL and final provider model ID.
URL scheme/host case, trailing path slashes and fragments are normalized.
API keys are not inputs. Usernames, email addresses and prompt text are not
inputs or plaintext parts of the resulting key.

The same saved chat keeps its key across turns, tool calls, summaries, memories,
RAG changes, socket reconnects and native compaction. Changing the user, chat,
model or connection changes the key. The hash is not an authentication or access
control mechanism; existing permissions and identity headers are unchanged.

Any existing top-level `prompt_cache_key` wins, including an empty string or
`null`. Existing request/model custom-parameter merging and passthrough semantics
are unchanged. A value discarded by that existing merge is not a top-level
explicit value at this hook. No retention, TTL or billing option is added.

## Paths without an automatic key

- Chat Completions connections, non-GPT models and unresolved provider aliases.
- Legacy Azure deployment routes; Azure `/openai/v1` Responses routes use the
  normal schema and are eligible.
- Background task requests, internal/direct requests and pipeline models.
- Missing authenticated user IDs or saved chat IDs, including `temporary:`,
  legacy `local:` and `channel:` IDs under the existing saved-chat rule.
- The raw `/openai/responses` passthrough endpoint. It creates no chat identity.

There is no per-request or prompt-content fallback. The saved-chat check reuses
the existing ID classification; it adds no database query or ownership lookup.
Chat ownership checks remain in the existing chat path.

The chat router adds the key after Responses conversion and before native
compaction. Codex V2 compaction forwards the same key on its normal `/responses`
request. The smaller legacy `/responses/compact` request schema is unchanged.
Checkpoint binding, encrypted items, tools order, custom headers and cached-token
usage details are not changed.

## Offline regression

Run `python tests/migration/test_prompt_cache.py` with the existing project
environment. Tests execute the production helper, the actual chat router through
serialization, the compaction request builder and checkpoint/reopen logic.
Network, storage and the V2 event collector are replaced at their boundaries;
there are no real provider requests or stream-transport tests.
