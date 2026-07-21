# PR7 live-compatible Agent hardening rollout

- [x] Restore the current PR7 stack after the incompatible-base switch attempt.
- [x] Create an isolated branch/worktree from the actual live source line `c14bba3da`.
- [x] Map the minimal newer UI/status-history and `d72ffcaca` hardening changes onto this source line.
- [x] Port changes semantically and add deployment compatibility/rollback tests.
- [x] Run focused and full runtime/backend/frontend/build verification.
- [x] Commit the corrected source and exact deployment assets.
- [x] Rebuild WebUI/runtime images from the corrected commit.
- [x] Switch only PR7 WebUI/runtime with DB and runtime-store rollback protection.
- [x] Verify native phase ordering, cancellation, interaction, refresh, and browser state.
- [ ] Rebuild and switch the runtime-only true-final-streaming correction.
- [ ] Re-run native phase and require more than one live `final.delta` before release closure.
- [ ] Record final live truth, residual risks, and release recommendation.
