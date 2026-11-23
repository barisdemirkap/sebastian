# 0014. Smart defaults: ask less, never repeat

Status: accepted (owner decision, 2026-11-19).

## Context
Thursday 19 Nov 05:23 the owner asked to add a chore at "sabah 7.30" with no date. The bot answered with a stale-question notice, then asked "Hangi gün?", reworded it after two impatient messages ("aloo", "orda misin"), asked again after "sabah 7.30 dedim ya", and finally showed the way-out menu. The obvious answer was the next 07:30 (today, it was 05:24).

## Decisions
1. An add with a time (or clock-bearing day part) and no date takes the NEXT upcoming occurrence: today if the time is still ahead, else tomorrow. The preview shows "Bugün/Yarın <weekday> <date> <start>-<end>", a one-line note that the day was assumed, and the usual buttons; "yarın olsun" edits it. Forms: 7.30, 7:30, 7.30'a, yedi buçuk, sabah yedide; sabah is AM, akşam/öğleden sonra/ikindi/gece are PM.
2. Hours 1-6 without a day part: when exactly one reading (04:00 or 16:00) is still ahead today it is taken; when both are ahead (or both are tomorrow) ONE question with two numbered choices is asked (state `ampm`). With a stated date the older "Saat 03:00 mi?" question stays.
3. Default duration: 15 minutes for chores and short tasks (title stems in `i18n.CHORE_STEMS`), 60 minutes otherwise; a stated duration or end time wins. Reminder stays per config.
4. A slot question that is still needed is answerable in one word: the day question offers `1) Bugün 2) Yarın 3) Başka gün` (a number, bugün, yarın, a weekday or a date; 3 asks for a free date). An answer is also read for the other missing field, so "sabah 7.30 dedim ya" gives the time.
5. "I said it already" wording (`i18n.RESTATE_PHRASES`: dedim ya, tekrar tekrar, yine, ...) never repeats the question: a taken field is acknowledged in one clause ("Haklısın, saati aldım."), otherwise the bot restates what it understood ("Şöyle anladım: Çöp.") and asks only the missing item. Such a turn does not add the way-out menu (the S4 signal is still recorded).
6. Presence checks ("aloo", "orda misin", "cevap ver", also merged by the debounce into one turn) are never answers: "Buradayım." plus the open question in short form (or the open preview's re-offer); the state is untouched.
7. A stale clarification question is dropped silently. Only when an actual PREVIEW expired and the owner then confirms it, the bot says so, writes nothing and shows the same parameters again with a fresh limit ("Önizleme süresi dolmuştu ... Yeniden gösteriyorum:"); a fresh yes is needed. An expired add whose times are all past gets the plain expiry notice.

## Consequences
Fewer questions; a wrong guess costs one edit and is always visible (weekday, full date, assumed-day note). The conformance runner gained an optional adapter step `advance_minutes` for expiry incidents.
