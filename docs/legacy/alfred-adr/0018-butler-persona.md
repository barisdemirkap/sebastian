# 0018. Alfred is a dignified, deferential butler (one persona block)

Status: accepted (owner decision, 2026-11-19).

## Context
Alfred spoke in a plain, informal "sen" Turkish. The owner wants Alfred Pennyworth's manner: an order-obeying, calm, concise butler (uşak), without jokes or emojis.

## Decisions
1. **One persona block** in `i18n.py` (forms of address `ADDRESS_FORMS`, acknowledgements `ACKS`, the rotation counter, `one_address`) shapes every reply. All user-facing templates are in the formal "siz" form; button labels stay short (Kaydet, Düzenle, İptal).
2. **Forms of address** rotate once per owner message (counter in `meta.addr_n`): mostly `efendim`, then `Ahmet Bey`, now and then `Sir` or `Master`. A template carries `{s}` (", <form>") or `{ack}` (an acknowledgement: Emredersiniz / Hemen / Elbette, plus the form); `Assistant.handle` removes every second form of address, so a message holds at most one.
3. **Phrases**: receipts open with an acknowledgement (`Emredersiniz, efendim. Ekledim: ...`); previews read `Şunu takvime işliyorum, onaylar mısınız?` and still end in `(evet / hayır)` (deletes: `Şunu SİLMEMİ onaylıyor musunuz?` and `evet sil`); the digests greet (`Günaydın, efendim.`) and close (`Hizmetinizdeyim, iyi günler dilerim.`).
4. **Obedience never bypasses the gates** (ADR 0002, 0004, 0006): "hemen ekle, onay sormadan" still produces a preview and waits for the owner's yes (tested).
5. Tests pin the form of address (`tests/conftest.py`, `i18n.pin`) so wording assertions stay exact; `tests/test_persona.py` unpins it and checks the rotation, the one-per-message rule and that no template contains an informal "sen" form.

## Consequences
- The wording of about 60 asserts and 15 incident fixtures changed (`Şunu ekleyeyim` became `Şunu takvime işliyorum`, `SİLMEK` became `SİLMEMİ`).
- The I10 detector also knows `onaylar mısınız` and `onaylıyor musunuz` as yes/no offers.
- The agent mode prompt (`i18n.AGENT_SYSTEM`) carries the same persona (ADR 0019).
