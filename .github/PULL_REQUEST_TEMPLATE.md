## What and why
<!-- Link the issue and, for a behaviour or boundary change, the ADR. -->

## Checklist
- [ ] `uv run pytest -q` passes (offline)
- [ ] Anything that writes still goes through preview + confirm
- [ ] No live network or real account in tests
- [ ] No private data: fictional names, ids and domains only; no secrets
- [ ] Docs updated (README, ROADMAP, setup guide or ADR) if behaviour changed
