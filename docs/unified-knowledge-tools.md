# Unified knowledge and attachment tools

The model-facing interface has five functions. Permissions and source selection
are resolved before file content or evidence is returned.

| Function                 | Use                                                                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| `search_knowledge_bases` | Find KBs by name/description; an empty query lists accessible KBs.                                                                  |
| `search_knowledge_files` | List files or find filenames within selected KBs and chat attachments. With no source selected, browse authorized KB file metadata. |
| `grep_knowledge_files`   | Find exact text/clauses in selected sources, or narrow to a known file/KB. No implicit all-KB content scan.                         |
| `query_knowledge_files`  | Text/image semantic retrieval through the unified evidence service; `file_ids` or `knowledge_ids` can narrow the scope.             |
| `view_knowledge_file`    | Read parsed file text by offset/line, or explicitly reread a batch of stable `evidence_refs`.                                       |

Old names remain executable for saved calls and approval resumes, but their
definitions are hidden from new requests and Tool Search. `view_file` delegates
to the canonical reader, `list_knowledge_bases` delegates to the KB search, and
`query_chat_files` delegates to the evidence service with chat-only scope.
`list_chat_files` and `grep_chat_files` retain their bounded chat-only behavior.
The old `list_knowledge`, `query_knowledge_bases` and `view_knowledge_evidence`
names are also compatibility-only; evidence rereads use `view_knowledge_file`.

File-only mode cannot access model/folder KBs or discover other KBs. Attachment
IDs select a scope; they do not grant permission. File and evidence reads recheck
current access. Native PDFs remain on the existing native-file path and are not
silently parsed or indexed by these tools. Notes and official external connectors
retain their existing authorized retrieval path.

File directories deduplicate IDs. Evidence results retain stable references,
file versions, location and image metadata, but omit duplicate `preview` text and
internal ranking/encoding fields. The default total evidence-body budget is
10,000 characters, not 10,000 serialized JSON characters. File/evidence rereads
accept a bounded `max_chars`; use `next_offset` to continue. Truncated evidence
can be reread with `view_knowledge_file(evidence_refs=[ref], offset=next_offset)`.
Image pixels still use the existing authorized vision channel, not JSON/base64
in model-visible text. These changes do not modify chunk sizes, embedding models,
vector spaces or stored vectors. Existing compatible vector mappings continue
through the evidence service; unverified spaces stay quarantined. Merely having
an old vector collection without a compatible mapping does not make it searchable.

## Tool Search profile

`docs/config/knowledge-tool-search.json` is an explicit opt-in profile for the
existing flat persistent Config keys. It is not the nested admin export format
and is not automatically imported. Applying it requires deployment/configuration
authorization; adding it to the repository does not change Live settings.

The profile keeps 14 common functions resident when they are otherwise enabled
and authorized: seven terminal functions, user input/time, `search_web` and the
four content-operation functions above. KB discovery and other functions are
deferred. `defer_threshold=0` makes the resident list explicit; this threshold is
measured in schema characters, not tokens. With deferred tools present, the
existing `search_tools` and `call_tool` dispatch definitions are added.

Discovery returns a requested definition as a tool result; it does not mutate the
provider's `tools` array mid-turn. Dispatch resolves the target before the normal
approval and execution path. The profile cannot enable disabled tools or bypass
their permissions. If Tool Search is disabled, the five canonical interfaces
remain visible and compatibility aliases remain hidden.

## Verification

Fast checks (standard library):

```sh
python3 tests/migration/test_unified_knowledge_tools.py
python3 tests/migration/test_knowledge_tool_guidance.py
python3 tests/migration/test_evidence_reread.py
```

Tool-definition checks need Pydantic and langchain-core:

```sh
python3 tests/migration/test_query_image_cache.py
```

Use the disposable SQLite environment in `docs/fork-v0120.md` for:

```sh
python -m pytest -q backend/tests/test_rag_core.py backend/tests/test_rag_integration.py backend/tests/test_knowledge_tool_loading.py
```

These checks cover source narrowing, revoked permissions, shared search/read
contracts, image/citation handling, stable provider definitions and dispatch
approvals. Network boundaries are mocked: they do not establish live model
tool-choice quality or real encoder/provider availability.
