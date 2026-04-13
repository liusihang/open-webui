# 2026-04-13 Backend Review Handoff (OnlyOffice, commit 9eb0d843c)

## Task
- Scope limited to backend files:
  - backend/open_webui/config.py
  - backend/open_webui/main.py
  - backend/open_webui/routers/onlyoffice.py
- Target commit: `9eb0d843c`
- Review focus: bug / security risk / behavioral regression / edge cases.

## Actions & Checkpoints
1. Verified target commit exists and extracted scoped diff stats.
2. Pulled full patch for the 3 backend files and line-number snapshots.
3. Cross-checked auth/token behavior against existing auth stack:
   - `backend/open_webui/utils/auth.py` (`create_token`, `decode_token`, `get_current_user`)
   - `backend/open_webui/routers/terminals.py` (session auth forwarding pattern)
   - `backend/open_webui/utils/access_control/__init__.py` (`has_connection_access`)
4. Identified and validated security/behavior findings with exact line anchors.

## Findings Summary
- P1: Session proxy token is embedded inside URL-borne token payload and is reusable as a normal backend auth JWT for its TTL.
- P2: File callback endpoint checks file existence before callback authentication, enabling file-id existence oracle (401 vs 404).
- P2: Terminal document key lacks version signal; same path/server keeps same key, risking stale preview cache behavior.

## Notes
- No code fix applied in this review task.
- Main/config wiring reviewed; no additional high-severity issues found there.
