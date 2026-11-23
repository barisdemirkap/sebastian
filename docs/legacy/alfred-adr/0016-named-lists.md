# 0016. Named lists (first: Alınacaklar)

Status: accepted (owner decision, 2026-11-19).

## Context
The owner wants a shopping list ("alınacaklar") in the chat: items added by voice or text, checked off in the shop, shown in the morning digest on shopping days.

## Decisions
1. **Generic lists**: `i18n.LISTS` maps a key to a display title and folded aliases; the first list is `alinacaklar` ("Alınacaklar"; aliases alışveriş listesi, alışveriş, alınacaklar listesi, market listesi). A bare `market` or `alışveriş` is not a list command (`marketi sil` stays a calendar matter); a date or time in the sentence also makes it a calendar matter.
2. **Storage**: table `list_items` (chat_id, list_key, text, status open | done | removed, created_at, closed_at) in the existing store, per owner. The schema is extended idempotently (`CREATE TABLE IF NOT EXISTS`, `meta.schema_version` = 2). An EXISTING database without the table is first copied once with SQLite's backup API to `<db>.pre-lists.bak` (mode 0600); a fresh or already migrated database is not backed up.
3. **Wordings** (`lists.parse`, deterministic, before the model): add (`alınacaklara üçlü priz ve kahve ekle`, `alışveriş listesine süt, yumurta ekle`, `süt listeme ekle`); show (`listemde ne var`, `alışveriş listem`, `/liste`, `/alinacaklar`); check off (`yumurtayı aldım`, `1 ve 3 aldım`, `hepsini aldım`); remove (`kahveyi listeden çıkar`); clear (`listeyi temizle`). Items split on commas and `ve`; quantities are kept (`2 litre süt`); an item whose name is already open is merged away and reported (`Zaten listede, tekrar eklemedim`). An unknown "X aldım" falls through to the normal flow.
4. **Gates**: adding and checking off are immediate (Alfred's own store, like tasks, ADR 0013). Removing an item and clearing the list are a normal pending action with a preview and a yes (Kaydet/İptal buttons, ADR 0006/0007); not while another preview is open.
5. **View**: numbered, `☐` for open items, then an `Alındı:` section with `✔` for items checked today; checked items of earlier days leave the view. Clearing removes open and done items.
6. **Buttons**: every list view carries `[Tümü alındı] [Temizle]` (callback `a:<list no>:<hash8>` / `c:...`); the hash covers the current open items, so a tap under an outdated view is the stale alert. Tümü alındı is the tap itself; Temizle only opens the confirm preview (never clears alone). A list keyboard stays until replaced; it is not tied to a pending action.
7. **Digest**: the morning digest lists the open items on days whose calendar has an event with `alışveriş` or `market` in its title (`Scheduler.lists`).
8. **Operator seeding**: `alfred list-add <list> <item>... [--send] [--ttl-minutes 1440]` creates ONE pending `list_add` preview (same confirm gate); `--send` pushes it to the owner chat with its buttons and logs the message (like `import-plan`).

## Consequences
The conformance adapter treats list previews as an open question (I10) and keeps list data out of calendar `writes()`. Adding another list is one `i18n.LISTS` entry.
