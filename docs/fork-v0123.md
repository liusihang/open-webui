# v0.12.3 integration and custom-feature preservation

This work merges official Open WebUI tag `v0.12.3`
(`91ea4e3421d1d8069f13d4a463fcb5986d94ea60`) into the existing fork at
`35baed06cfe5595c7356c649a157aef3781e49a4`, then adds optional
[Intelligent UI](intelligent-ui.md). The upstream merge is a real two-parent
commit, `d11dab93fb0adfb1265ce1647f10f72c33a8cddd`; fork history is retained.

## Preserved baseline

The 83-file custom delta was inventoried against the prior official base.
Retained features include native Responses history/tool continuation, native
PDFs, native/text context compaction, task-model memory review, unified
text/image evidence retrieval and authorization, durable document image assets,
OnlyOffice, Pipe reasoning controls, and offline migration utilities. The
configuration and operating limits in [the previous fork guide](fork-v0120.md)
still apply unless specifically superseded here.

Features intentionally retired in that guide are not resurrected from old
history. This upgrade does not reset runtime configuration or import credentials,
tools, saved models, user conversations, or document corpora.

## Upgrade-specific integration

- Five textual conflicts were resolved by retaining both applicable behaviors:
  backend startup/shutdown, OpenAI/Responses routing, chat middleware, chat
  event/session handling, and file previews.
- Fork evidence migration `c92e41b7d630` and upstream migration head
  `0bd252b206fb` are joined by `d41e823f01ac`. Previously applied migration
  ancestry is not rewritten. The join itself performs no data mutation.
- MinerU V1's newly detected response format retains extracted image assets
  before upstream text sanitization.
- Evidence viewing remains in the shared citation-tool allowlist, including
  resumed approval handling.
- Native Responses replay preservation coexists with upstream provider tool-name
  normalization and tool-search support. Tool-loop text-compaction checkpoints
  now select retained native call IDs for Responses as well as Chat tool IDs;
  summary instructions are appended without mutating the replay source.
- Existing early socket/HTTP acknowledgment handling coexists with upstream
  filesystem session IDs and draft handling.

## Deployment

Source integration is not a production deployment. Back up the live database,
document assets, and configuration before applying the normal migration chain.
The evidence branch retains its existing legacy-table conflict checks. Test the
upgrade on a copy of the actual deployment first, particularly if it uses native
Responses, OnlyOffice, external parsers, or existing evidence indexes.

Intelligent UI is optional and defaults to off. It adds native Svelte rendering
and an explicit presentation prompt without installing a community Python plugin
or relaxing iframe permissions. See its guide and the validation report.
