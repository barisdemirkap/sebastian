import dataclasses
import pytest
from datetime import datetime, timedelta, timezone
from sebastian import oauth
from sebastian.config import parse
from sebastian.digest import morning_digest, evening_digest
from sebastian.http import Response
from sebastian.insights import unanswered_threads, meeting_prep
from sebastian.mail import (GmailProvider, OutlookMailProvider, MailMessage, FixtureMailProvider, parse_gmail_message,
                         parse_graph_message, redact)
from sebastian.models import Account, Event
from sebastian.notify import StubNotifier
from sebastian.scheduler import Scheduler, State
from sebastian.service import MailFetcher, build_mail_fetcher
from sebastian import whatsapp as wa

T = timezone.utc
NOW = datetime(2026, 11, 18, 8, 30, tzinfo=T)
G = Account("google", "me@example.com", "personal")
O = Account("outlook", "me@corp.example", "company")


class Fake:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, headers=None, form=None):
        self.calls.append((method, url))
        return Response(200, self.responses.pop(0))


class Tok:
    def access_token(self):
        return "t"


def gmsg(i, tid, frm, subj, date, labels=()):
    return {"id": i, "threadId": tid, "labelIds": list(labels), "payload": {"headers": [
        {"name": "From", "value": frm}, {"name": "To", "value": "Me <me@example.com>"},
        {"name": "Subject", "value": subj}, {"name": "Date", "value": date}]}}


def msg(tid, sender, subject, hours_ago, mine=False, to=("me@example.com",), label="personal"):
    return MailMessage(label, tid, f"{tid}{hours_ago}", sender, to, subject, NOW - timedelta(hours=hours_ago), mine)


# --- readers
def test_gmail_lists_then_fetches_metadata_only_get():
    f = Fake([{"messages": [{"id": "m1"}], "nextPageToken": "p2"}, {"messages": [{"id": "m2"}]},
              gmsg("m1", "t1", "Dana <Dana@Example.org>", "Hello", "Tue, 17 Nov 2026 09:00:00 +0000"),
              gmsg("m2", "t1", "me@example.com", "Re: Hello", "Tue, 17 Nov 2026 10:00:00 +0000", ["SENT"])])
    out = GmailProvider(G, Tok(), f).messages(NOW - timedelta(days=7), NOW)
    assert [m.from_owner for m in out] == [False, True]
    assert out[0].sender == "dana@example.org" and out[0].sender_name == "Dana"
    assert all(c[0] == "GET" for c in f.calls)
    assert all("format=metadata" in c[1] for c in f.calls[2:]) and not any("format=full" in c[1] for c in f.calls)


def test_outlook_selects_no_body_and_skips_drafts():
    item = {"id": "a", "conversationId": "c1", "subject": "Plan", "receivedDateTime": "2026-11-17T09:00:00Z",
            "from": {"emailAddress": {"address": "Boss@Corp.example", "name": "Boss"}},
            "toRecipients": [{"emailAddress": {"address": "me@corp.example"}}], "ccRecipients": []}
    f = Fake([{"value": [item, {**item, "id": "d", "isDraft": True}], "@odata.nextLink": "https://x/next"}, {"value": []}])
    out = OutlookMailProvider(O, Tok(), f).messages(NOW - timedelta(days=7), NOW)
    assert len(out) == 1 and out[0].sender == "boss@corp.example" and out[0].thread_id == "c1"
    assert "body" not in f.calls[0][1].lower() and "select=" in f.calls[0][1]
    assert {c[0] for c in f.calls} == {"GET"}


def test_message_model_has_no_body_field():
    assert not {"body", "snippet", "text"} & {f.name for f in dataclasses.fields(MailMessage)}


def test_mail_scopes_readonly_and_opt_in():
    assert "gmail.readonly" in oauth.MAIL_READONLY_SCOPES["google"][0]
    oauth.check_scopes("google", oauth.MAIL_READONLY_SCOPES["google"])
    oauth.check_scopes("outlook", ("Mail.Read",))
    for bad in (("https://www.googleapis.com/auth/gmail.modify",), ("https://mail.google.com/",)):
        with pytest.raises(ValueError):
            oauth.check_scopes("google", bad)
    for bad in ("Mail.ReadWrite", "Mail.Send"):
        with pytest.raises(ValueError):
            oauth.check_scopes("outlook", (bad,))
    # default consent never includes mail
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(oauth.build_auth_request("google", oauth.ClientCredentials("c"), "http://x").url).query)
    assert q["scope"] == ["https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events"]


# --- insights
def test_unanswered_rules():
    ms = [msg("a", "dana@example.org", "Draft", 30),                 # waiting, old
          msg("b", "x@example.org", "Q", 9), msg("b", "me@example.com", "Re: Q", 2, True),  # answered
          msg("c", "noreply@tool.example", "Report", 12),            # automated
          msg("d", "boss@example.com", "Urgent", 5),                 # priority
          msg("e", "z@example.org", "Fresh", 1),                     # too new
          msg("f", "old@example.org", "Ancient", 24 * 10)]           # too old
    out = unanswered_threads(ms, NOW, priority_senders=("boss@example.com",))
    assert [n.thread_id for n in out] == ["d", "a"] and out[0].priority


def test_threads_not_merged_across_accounts():
    ms = [msg("same", "a@example.org", "x", 10, label="personal"), msg("same", "me@corp.example", "re", 5, True, label="company")]
    assert [n.account_label for n in unanswered_threads(ms, NOW)] == ["personal"]


def test_meeting_prep_by_attendee_and_keyword():
    ev = Event("Contract review", NOW, NOW + timedelta(hours=1), "personal", attendees=("me@example.com", "dana@example.org"))
    ms = [msg("a", "dana@example.org", "Draft v2", 30), msg("b", "q@example.org", "Contract terms", 50),
          msg("c", "x@example.org", "Lunch", 10)]
    note = meeting_prep(ev, ms, NOW, owners=("me@example.com",))
    assert {s for s, _, _ in note.threads} == {"Draft v2", "Contract terms"}
    assert meeting_prep(Event("Gym", NOW, NOW, "personal"), ms, NOW) is None


def test_digest_sections_and_backward_compat():
    ev = [Event("Contract review", NOW + timedelta(hours=1), NOW + timedelta(hours=2), "personal", attendees=("dana@example.org",))]
    ms = [msg("a", "dana@example.org", "Draft v2", 30)]
    text = morning_digest(ev, NOW, ms, ("me@example.com",))
    assert "Meeting prep:" in text and "Needs reply:" in text and "waiting on you" in text and "[personal]" in text
    assert "Needs reply" not in morning_digest(ev, NOW)
    assert "Needs reply:" in evening_digest(ev, NOW, ms)


def test_scheduler_morning_digest_includes_mail_and_survives_mail_failure(tmp_path):
    ev = [Event("Sync", NOW + timedelta(hours=1), NOW + timedelta(hours=2), "personal")]
    n = StubNotifier()
    s = Scheduler(parse({}).morning, parse({}).evening, lambda now: ev, n, State(tmp_path / "s.json"),
                  mail_fetch=lambda now: [msg("a", "dana@example.org", "Draft", 30)])
    assert "Needs reply:" in s.tick(NOW)[0]

    def boom(now):
        raise RuntimeError("down")
    n2 = StubNotifier()
    s2 = Scheduler(parse({}).morning, parse({}).evening, lambda now: ev, n2, State(tmp_path / "s2.json"), mail_fetch=boom)
    assert "Günaydın" in s2.tick(NOW)[0]


# --- gating and privacy
def test_mail_off_by_default_and_requires_both_flags():
    c = parse({"accounts": [{"provider": "google", "address": "a@b.c", "label": "p", "enabled": True}]})
    assert not c.accounts[0].mail_enabled and build_mail_fetcher(c) is None
    c = parse({"accounts": [{"provider": "google", "address": "a@b.c", "label": "p", "mail_enabled": True}]})
    assert build_mail_fetcher(c) is None  # account itself not enabled


def test_mail_fetcher_isolates_failures_and_caches():
    class Bad:
        account = G
        def messages(self, s, e): raise RuntimeError("x")
    ok = FixtureMailProvider([msg("a", "d@example.org", "S", 5)])
    mf = MailFetcher([Bad(), ok])
    assert len(mf(NOW)) == 1 and mf.errors == ["personal: RuntimeError"]


def test_redact():
    assert redact("from dana@example.org failed") == "from d***@example.org failed"


def test_parsers_direct():
    assert parse_gmail_message(gmsg("m", "t", "a@b.co", "S", "Tue, 17 Nov 2026 09:00:00 +0000"), G).date.tzinfo
    assert parse_graph_message({"id": "i", "conversationId": "c", "receivedDateTime": "2026-11-17T09:00:00Z"}, O).sender == ""


# --- WhatsApp (offline)
WEBHOOK = {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": "905551112233", "profile": {"name": "Ayse"}}],
          "messages": [{"from": "905551112233", "id": "w1", "timestamp": "1795000000", "type": "text",
                        "text": {"body": "secret content"}}]}}]}]}


def test_webhook_parse_drops_body_and_masks_phone():
    ms = wa.parse_webhook(WEBHOOK)
    assert ms[0].name == "Ayse" and ms[0].inbound and ms[0].kind == "text"
    assert "secret" not in repr(ms) and "text" not in {f.name for f in dataclasses.fields(wa.WaMessage)}
    assert wa.mask("905551112233") == "*********233"


def test_wa_unanswered_and_window():
    t = lambda h: NOW - timedelta(hours=h)
    ms = [wa.WaMessage("111", "a", t(30), True), wa.WaMessage("222", "b", t(5), True), wa.WaMessage("222", "c", t(2), False),
          wa.WaMessage("333", "d", t(0.2), True)]
    out = wa.unanswered_chats(wa.FixtureWhatsAppReader(ms).messages(t(100), NOW), NOW)
    assert [o.chat for o in out] == ["***"] and not out[0].window_open
    assert "[window closed]" in wa.format_wa_needs_reply(out)[1]


def test_wa_interface_cannot_send():
    for name in ("send", "reply", "send_message"):
        assert not hasattr(wa.WhatsAppReader, name) and not hasattr(wa.FixtureWhatsAppReader, name)
