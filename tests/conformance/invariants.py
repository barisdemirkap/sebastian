"""Bot-agnostic conversation conformance framework (ported from Acme; Sebastian shares no code with it).
NO bot imports: copy this file, phrasebook_tr.py and the incidents/ folder next to a bot's own adapter and the same
invariants run against it.

ADAPTER CONTRACT (the only thing a bot must implement; see README.md):

    class Adapter:
        def deliver(self, msgs: list[Msg]) -> None
            # push ONE poll batch through the bot's REAL update path (dedupe, debounce, handler, send)
        def sent(self, chat: int) -> list[str]
            # every text the bot sent to `chat` so far, in order (raw: no duplicate-collapsing layer in between)
        def preview(self, chat: int) -> list[dict] | None
            # canonical params of the chat's open, code-rendered preview (None when there is none).
            # Add-type previews: dicts with the keys the new record must show. Edit previews: {"id": .., changed keys}.
            # Delete previews: {"id": .., "_delete": True}: the record with that id must disappear, nothing else.
        def writes(self) -> list[dict]
            # every persisted record of every chat, canonical, each with "chat" and "id"
        def state_key(self, chat: int) -> str
            # opaque string that changes when the conversation state really changes (not on mere counters)
        def network(self, up: bool) -> None
            # simulate sends failing (up=False) / working (up=True)
        optional: owner (int) -> the only chat the bot serves; other chats must get no reply and change nothing
        optional: advance(minutes) -> None     # move the bot's clock forward (incident step "advance_minutes")
        optional: expected_kind(kind, text, preview) -> kind   # bot-specific policy, e.g. a bare 'evet' on a delete
                  # preview is only a hint (neutral), never a confirmation

`Msg` is defined here. A bot supplies `make_adapter(setup: dict) -> Adapter`; `setup` is opaque, taken from the
incident JSON ("setup").

INVARIANTS, asserted after EVERY turn by `Conversation`:
  I1 no identical bot text twice in a row to a chat unless the user turn changed state (slash commands exempt)
  I2 an open preview survives every message that is not an explicit confirm/cancel/edit (kind per step;
     kinds: confirm | cancel | edit | neutral (default for unknown text) | free = answer/new request, no I2)
  I3 (property tests) a pure affirmation saves EXACTLY the previewed params once; negations cancel; edits never save
  I4 nothing is written unless a code-rendered preview was open and the message was a confirmation
  I5 a fact the user stated is never asked again (step "facts": [{"name", "asks": regex}])
  I6 a redelivered update or a burst never produces duplicate replies or double writes
  I7 isolation: with a single owner (adapter.owner), a message from any other chat gets no reply and changes nothing;
     without an owner, tokens starting "zq" typed in one chat never appear in another chat's replies/writes
  I9 a record disappears only through a confirmed delete preview, and only the previewed one
  I10 a bot reply that asks a yes/no style question ("...ister misin?", "...ekleyeyim mi?") is only sent while a
      matching preview or an open clarification state exists, so a following 'evet' is never "nothing pending"
      (optional adapter methods: open_question(chat) -> bool, is_quote(text) -> bool for replies that merely quote
      an older message)
  I8 no bot reply holds a placeholder ('{', '}', 'None', 'Traceback', 'nan')
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:  # works both as a plain import (tests dir on sys.path) and from a package
    import phrasebook_tr as pb
except ImportError:  # pragma: no cover
    from . import phrasebook_tr as pb

PLACEHOLDER = re.compile(r"[{}]|\bNone\b|Traceback|\bnan\b|<function|object at 0x")
PRIVATE = re.compile(r"\bzq\w+", re.I)


@dataclass
class Msg:
    chat: int
    text: str
    update_id: int
    ts: int


class Violation(AssertionError):
    pass


def canon(rec: dict) -> tuple:
    return tuple(sorted((k, json.dumps(v, sort_keys=True)) for k, v in rec.items() if k != "chat"))


def subset(want: dict, have: dict) -> bool:
    return all(have.get(k) == v for k, v in want.items())


def new_records(before: list[dict], after: list[dict]) -> list[dict]:
    pool = [canon(r) for r in before]
    out = []
    for r in after:
        c = canon(r)
        if c in pool:
            pool.remove(c)
        else:
            out.append(r)
    return out


def match_all(previewed: list[dict], written: list[dict]) -> bool:
    """Every previewed record is shown by a DISTINCT newly written record, and nothing else was written."""
    rest = list(written)
    for p in previewed:
        hit = next((w for w in rest if subset(p, w)), None)
        if hit is None:
            return False
        rest.remove(hit)
    return not rest


def removed_records(before: list[dict], after: list[dict]) -> list[dict]:
    return new_records(after, before)


def split_preview(prev: list[dict]) -> tuple[list[dict], list[dict]]:
    """(items that must appear as new records, items that must disappear)."""
    adds = [{k: v for k, v in p.items() if k != "_delete"} for p in prev if not p.get("_delete")]
    dels = [{k: v for k, v in p.items() if k != "_delete"} for p in prev if p.get("_delete")]
    return adds, dels


def preview_matches(prev: list[dict], new: list[dict], gone: list[dict], after_ids: set) -> bool:
    adds, dels = split_preview(prev)
    if not match_all(adds, new) and (adds or new):
        return False
    return {d.get("id") for d in dels} == {g.get("id") for g in gone if g.get("id") not in after_ids} and \
        all(g.get("id") in {d.get("id") for d in dels} for g in gone if g.get("id") not in after_ids)


class Conversation:
    """Runs scripted turns against an adapter and checks the invariants after each one."""

    def __init__(self, adapter):
        self.a, self.uid, self.ts = adapter, 1000, 1_800_000_000
        self.facts: list[tuple[str, re.Pattern]] = []
        self.user_tokens: dict[int, set[str]] = {}
        self.last_msgs: list[Msg] = []
        self.log: list[tuple[int, str, list[str]]] = []

    def chat(self, n: int) -> int:
        """Script chat 1 is the bot's owner when the adapter has one; other numbers are strangers."""
        owner = getattr(self.a, "owner", None)
        return owner if (owner is not None and n == 1) else n

    # -- driving --
    def say(self, chat: int, text, kind: str | None = None, allow_write: bool = False, repeat_ok: bool = False,
            facts: list[dict] | None = None, **expect) -> list[str]:
        """One user turn. `text` may be a list: a burst (same timestamp, same batch). Returns the new bot texts."""
        chat = self.chat(chat)
        texts = text if isinstance(text, list) else [text]
        self.ts += 30
        msgs = []
        for tx in texts:
            self.uid += 1
            msgs.append(Msg(chat, tx, self.uid, self.ts))
        return self._turn(msgs, " ".join(texts), kind, allow_write, repeat_ok, facts, expect, redelivery=False)

    def redeliver(self, **expect) -> list[str]:
        """The same updates again (a network stall made the poll repeat them)."""
        return self._turn(list(self.last_msgs), " ".join(m.text for m in self.last_msgs), "neutral", False, False,
                          None, expect, redelivery=True)

    def _turn(self, msgs, joined, kind, allow_write, repeat_ok, facts, expect, redelivery) -> list[str]:
        a, chat = self.a, msgs[0].chat
        if kind is None:
            kind = pb.classify(joined) if len(msgs) == 1 else "neutral"
        before_w, before_prev = a.writes(), a.preview(chat)
        if hasattr(a, "expected_kind") and not allow_write:
            kind = a.expected_kind(kind, joined, before_prev)
        owner = getattr(a, "owner", None)
        foreign = owner is not None and chat != owner
        if foreign:
            return self._foreign(msgs, joined, redelivery)
        before_state = a.state_key(chat)
        before_sent = {c: len(a.sent(c)) for c in self._chats(msgs)}
        last_out = a.sent(chat)[-1] if a.sent(chat) else None
        a.deliver(msgs)
        if not redelivery:
            self.last_msgs = msgs
            for m in msgs:
                self.user_tokens.setdefault(chat, set()).update(t.lower() for t in PRIVATE.findall(m.text))
        out = a.sent(chat)[before_sent[chat]:]
        self.log.append((chat, joined, out))
        after_w, after_prev = a.writes(), a.preview(chat)
        where = f"after {joined!r} (kind={kind})"
        # I10
        open_q = getattr(a, "open_question", lambda c: False)(chat)
        quote = getattr(a, "is_quote", lambda t: False)
        if after_prev is None and not open_q:
            for r in out:
                if pb.YES_NO_QUESTION.search(r) and not quote(r):
                    raise Violation(f"I10 yes/no question without a pending action or open question {where}: {r!r}")
        # I8
        for c in before_sent:
            for r in a.sent(c)[before_sent[c]:]:
                if PLACEHOLDER.search(r):
                    raise Violation(f"I8 placeholder in reply {where}: {r!r}")
        # I1
        state_changed = a.state_key(chat) != before_state or after_w != before_w
        if out and last_out is not None and out[0] == last_out and not state_changed and not repeat_ok \
                and not joined.lstrip().startswith("/"):
            raise Violation(f"I1 identical bot message twice in a row {where}: {out[0]!r}")
        for x, y in zip(out, out[1:]):
            if x == y:
                raise Violation(f"I1 identical consecutive replies in one turn {where}: {x!r}")
        # I6
        if redelivery:
            if after_w != before_w:
                raise Violation(f"I6 redelivery changed the stored data {where}")
            if len(out) > 1 or (out and out[0] in a.sent(chat)[:before_sent[chat]]):
                raise Violation(f"I6 redelivery produced duplicate replies {where}: {out}")
        else:
            seen = a.sent(chat)[:before_sent[chat]]
            if len(msgs) > 1 and len(out) != len(set(out)):
                raise Violation(f"I6 burst produced duplicate replies {where}: {out}")
        # I4 / I3 / I9: writes and removals
        new = new_records(before_w, after_w)
        gone = removed_records(before_w, after_w)
        after_ids = {w.get("id") for w in after_w}
        if (new or [g for g in gone if g.get("id") not in after_ids]) and not redelivery:
            if allow_write:
                pass
            elif kind != "confirm" or before_prev is None:
                raise Violation(f"I4 write without a confirmed preview {where}: {new} removed={gone}")
            elif not preview_matches(before_prev, new, gone, after_ids):
                raise Violation(f"I3/I4 written params differ from the preview {where}: preview={before_prev} wrote={new} removed={gone}")
        # I2
        if before_prev is not None and kind == "neutral" and not redelivery and after_prev != before_prev:
            raise Violation(f"I2 pending preview destroyed or changed by a non-explicit message {where}: "
                            f"{before_prev} -> {after_prev}")
        if before_prev is not None and kind == "edit" and (new or gone):
            raise Violation(f"I3 an edit saved something {where}")
        if before_prev is not None and kind == "cancel" and (new or gone or after_prev is not None):
            raise Violation(f"I3 a negative did not cancel cleanly {where}")
        # I5
        for name, rx in self.facts:
            for r in out:
                if rx.search(r):
                    raise Violation(f"I5 asked again for a stated fact '{name}' {where}: {r!r}")
        for f in facts or []:
            self.facts.append((f["name"], re.compile(f["asks"], re.I)))
        # I7
        for c, toks in self.user_tokens.items():
            if c == chat:
                continue
            for r in a.sent(chat)[before_sent[chat]:]:
                if any(t in r.lower() for t in toks):
                    raise Violation(f"I7 chat {chat} saw text typed in chat {c} {where}: {r!r}")
        for w in after_w:
            toks = self.user_tokens.get(w["chat"], set())
            for c, ot in self.user_tokens.items():
                if c != w["chat"] and any(t in json.dumps(w, ensure_ascii=False).lower() for t in ot):
                    raise Violation(f"I7 record of chat {w['chat']} holds text typed in chat {c}")
        self._expect(out, after_w, after_prev, expect, where)
        return out

    def _foreign(self, msgs, joined, redelivery):
        """I7 (single owner): a stranger gets no reply and changes nothing, for the owner or anybody."""
        a, chat = self.a, msgs[0].chat
        owner = a.owner
        w0, s0, p0 = a.writes(), a.state_key(owner), a.preview(owner)
        sent0 = {c: len(a.sent(c)) for c in (chat, owner)}
        a.deliver(msgs)
        if not redelivery:
            self.last_msgs = msgs
        where = f"after {joined!r} from chat {chat}"
        if len(a.sent(chat)) != sent0[chat] or len(a.sent(owner)) != sent0[owner]:
            raise Violation(f"I7 a stranger's message produced a reply {where}")
        if a.writes() != w0 or a.state_key(owner) != s0 or a.preview(owner) != p0:
            raise Violation(f"I7 a stranger's message changed the owner's state {where}")
        self.log.append((chat, joined, []))
        return []

    @staticmethod
    def _chats(msgs):
        return {m.chat for m in msgs}

    def _expect_tasks(self, expect, where):
        _expect_tasks_impl(self, expect, where)

    def _expect(self, out, writes, prev, expect, where):
        text = "\n".join(out)
        for s in expect.get("contains", []):
            if s not in text:
                raise Violation(f"expected {s!r} in replies {where}: {text!r}")
        for s in expect.get("not_contains", []):
            if s in text:
                raise Violation(f"unexpected {s!r} in replies {where}: {text!r}")
        if "saved_total" in expect and len(writes) != expect["saved_total"]:
            raise Violation(f"expected {expect['saved_total']} stored records {where}, found {len(writes)}")
        if "preview" in expect and (prev is not None) != bool(expect["preview"]):
            raise Violation(f"expected preview={expect['preview']} {where}, found {prev}")
        if "preview_count" in expect and len(prev or []) != expect["preview_count"]:
            raise Violation(f"expected {expect['preview_count']} previewed items {where}, found {prev}")
        if "preview_has" in expect:
            for want in expect["preview_has"]:
                if not any(subset(want, p) for p in prev or []):
                    raise Violation(f"expected previewed item {want} {where}, found {prev}")
        if "saved_has" in expect:
            for want in expect["saved_has"]:
                if not any(subset(want, w) for w in writes):
                    raise Violation(f"expected stored record {want} {where}")
        if any(k.startswith("tasks_") for k in expect):
            self._expect_tasks(expect, where)
        if "replies_max" in expect and len(out) > expect["replies_max"]:
            raise Violation(f"expected at most {expect['replies_max']} replies {where}, got {out}")


def _expect_tasks_impl(conv, expect, where):
    rows = conv.a.tasks()
    from sebastian.textutil import fold as low                                       # the owner types without diacritics
    has = lambda status, frag: any(r["status"] == status and low(frag) in low(r["title"]) for r in rows)   # noqa: E731
    for frag in expect.get("tasks_open_has", []):
        if not has("open", frag):
            raise Violation(f"expected an open task containing {frag!r} {where}: {rows}")
    for frag in expect.get("tasks_open_not_has", []):
        if has("open", frag):
            raise Violation(f"expected no open task containing {frag!r} {where}: {rows}")
    for frag in expect.get("tasks_done_has", []):
        if not has("done", frag):
            raise Violation(f"expected a done task containing {frag!r} {where}: {rows}")
    for frag in expect.get("tasks_done_not", []):                 # a deferral never closes
        if has("done", frag):
            raise Violation(f"task containing {frag!r} was closed, not rescheduled {where}: {rows}")
    for frag in expect.get("tasks_removed_not_has", []):          # a completion never removes (deletes) an item
        if has("removed", frag):
            raise Violation(f"task containing {frag!r} was removed, not closed {where}: {rows}")
    for frag, due in expect.get("tasks_due", {}).items():         # {"fragment": "YYYY-MM-DD"}
        if not any(r["status"] == "open" and low(frag) in low(r["title"]) and r["due"] == due for r in rows):
            raise Violation(f"expected an open task containing {frag!r} due {due} {where}: {rows}")
    if "tasks_open_count" in expect and sum(r["status"] == "open" for r in rows) != expect["tasks_open_count"]:
        raise Violation(f"expected {expect['tasks_open_count']} open tasks {where}: {rows}")


def run_script(make_adapter: Callable[[dict], object], script: dict) -> Conversation:
    """Run one incident fixture: {"name", "setup", "steps": [{"say"|"redeliver", "chat", "kind", ...expect}]}."""
    conv = Conversation(make_adapter(script.get("setup", {})))
    for step in script["steps"]:
        step = dict(step)
        if "advance_minutes" in step:                  # optional adapter method: move the bot's clock forward
            conv.a.advance(step.pop("advance_minutes"))
        if "model" in step:                            # optional adapter method: switch the language model on or off
            conv.a.model(step.pop("model") == "up")
        if "network" in step:
            conv.a.network(step.pop("network") == "up")
        if step.pop("redeliver", False):
            conv.redeliver(**step)
            continue
        if "say" not in step:
            continue
        text, chat = step.pop("say"), step.pop("chat", 1)
        conv.say(chat, text, **step)
    return conv


def load_incidents(folder: Path | None = None) -> list[tuple[str, dict]]:
    folder = folder or Path(__file__).parent / "incidents"
    return [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(folder.glob("*.json"))]


# -- property-style checks over the phrasebook (seeded, deterministic) --
def check_confirmation(make_adapter, setup: dict, open_preview: list[str], phrase: str, expect: str) -> None:
    """Open a preview with the scripted `open_preview` messages, send `phrase`, assert the outcome:
    expect 'save' -> exactly the previewed params stored once; 'cancel' -> nothing stored, preview gone;
    'keep' -> nothing stored, preview unchanged (neutral) ; 'edit' -> nothing stored."""
    conv = Conversation(make_adapter(setup))
    c = conv.chat(1)
    for m in open_preview:
        conv.say(c, m, kind="free", allow_write=False)
    prev = conv.a.preview(c)
    if prev is None:
        raise Violation(f"setup did not produce a preview: {open_preview}")
    before = conv.a.writes()
    base = len(before)
    kind = {"save": "confirm", "cancel": "cancel", "keep": "neutral", "edit": "edit"}[expect]
    if expect == "save" and hasattr(conv.a, "expected_kind"):
        kind = conv.a.expected_kind(kind, phrase, prev)
        if kind != "confirm":
            expect = "keep"          # the bot's own policy (e.g. a bare yes on a delete preview) turns this into a keep
    conv.say(c, phrase, kind=kind)
    after = conv.a.writes()
    if expect == "save":
        ids = {w.get("id") for w in after}
        if not preview_matches(prev, new_records(before, after), removed_records(before, after), ids) \
                or conv.a.preview(c) is not None:
            raise Violation(f"I3 {phrase!r} did not save exactly the preview: wrote {new_records(before, after)}, preview {conv.a.preview(c)}")
        # a second affirmation must not write again
        conv.say(c, phrase, kind="neutral", repeat_ok=True)
        if conv.a.writes() != after:
            raise Violation(f"I3 {phrase!r} saved twice")
    elif expect == "cancel":
        if after != before or conv.a.preview(c) is not None:
            raise Violation(f"I3 {phrase!r} did not cancel: writes {len(after) - base}, preview {conv.a.preview(c)}")
    elif after != before:
        raise Violation(f"{phrase!r} wrote something ({expect})")
