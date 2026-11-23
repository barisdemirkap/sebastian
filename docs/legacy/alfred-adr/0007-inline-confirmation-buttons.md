# 0007. Inline confirmation buttons

Status: accepted (owner decision, 2026-11-18). Same design as Acme ADR 0014, built separately.

## Decisions
1. **Buttons under every code-rendered preview.** Add previews: [Kaydet] [Düzenle] [İptal]; edit previews: [Kaydet] [İptal]; delete previews: [Sil] [İptal]. Clarifying questions get none. Labels live in `i18n.py`. Typing "evet", "hayır" and edits works exactly as before. The `alfred import-plan --send` preview carries them too.
2. **callback_data** is `<k|e|x|d>:<pending id>:<hash8>` (save, edit, cancel, delete; hash8 = first 8 hex digits of the stored args hash), far below Telegram's 64 bytes. It points at a stored record; it can carry no parameters.
3. **Every tap is verified in code** (`Assistant.callback`): the sender and the chat are the owner and the chat is private; the data matches the strict pattern; the pending id is the CURRENT open preview, unexpired, with an unchanged stored hash equal to the callback hash; the action fits (a delete preview only answers `d` and `x`, `e` only an add draft). Otherwise the tap is answered with the alert "Bu önizleme eskidi." and nothing happens. Foreign senders, groups and malformed data are ignored.
4. **Execution** is the typed-confirmation path over the STORED arguments, behind an atomic `UPDATE ... WHERE status='open'` (`Store.claim_pending`), so a double tap or a tap racing a typed "evet" executes once. Cancel is a typed "hayır". Edit keeps the draft and asks what to change; nothing is invalidated until an edit arrives.
5. **One live keyboard.** The message carrying it is stored (`kb:<chat>` in `meta`); after each turn and each tap the keyboard is removed (`editMessageReplyMarkup`) when its preview is no longer the current open one. Re-offering the same preview keeps it. A failed removal is harmless (the stale check rejects the tap).
6. **Transport.** `sendMessage` takes `reply_markup` (last chunk) and returns the message id; new `answerCallbackQuery` and `editMessageReplyMarkup`; `getUpdates` allows `["message", "callback_query"]`. Callbacks are deduplicated by `update_id`, never merged into a text burst, and answered without the model.

## Consequences
One tap saves. The model cannot press a button. A button dies after the 10-minute TTL (type "evet" is also expired then; the preview is asked again). A tap lost to a crash after its update was marked processed is not replayed.
