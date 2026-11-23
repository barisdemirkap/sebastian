# 0011. A yes/no question always has an action behind it (invariant I10); removal wordings

Status: accepted (owner decision, 2026-11-19).

## Context
The owner asked "camasiri persembe gununden kaldirdin mi?". The model answered in prose "...Silmemi ister misin?" but nothing was stored, so "evet" got "Onay bekleyen bir işlem yok". "camasiri kaldir" also removed nothing.

## Decisions
1. **Invariant I10** (conformance suite): a bot reply that asks a yes/no style question ("...ister misin?", "...ekleyeyim mi?", "(evet / hayır)") is only sent while a matching pending preview or an open clarification state exists, so a following "evet" is never answered with "nothing pending".
2. **One path for model questions**: `Assistant._ask_free` is the only way a free-text question from the model reaches the owner. If it offers a yes (`i18n.YES_NO_QUESTION`), the owner's own words are run through the rules; a resulting add, edit or delete is dispatched (so a normal pending preview exists); a read or chitchat is answered; otherwise the question is replaced by an open question (`open_question`). Every question sent stores a clarification state.
3. **"evet" while a question is open** is answered with that question ("Bu bir evet/hayır sorusu değildi. ..."), never with "nothing pending".
4. **Removal wordings**: kaldır, kaldırdın mı, sil, iptal et, çıkar, "X kalmasın", "X'e gerek yok" (the last three only when the text does not also ask to add) find the event (fuzzy title via `textutil.title_match`, date hint) and open the existing pending delete preview with its `evet sil` rules and Sil/İptal buttons.

## Consequences
Rules and model share the delete path. A yes/no offer that no rule can turn into an action costs one open question instead of a dead end.
