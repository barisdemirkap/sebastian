"""The shape every language pack fills in (ADR 0006). `tests/test_language_packs.py` checks that all packs agree on it."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pack:
    code: str                              # the value of [persona] language
    name: str
    messages: dict[str, str]               # reply templates, formatted with str.format ({s} = form of address, {ack} = acknowledgement)
    default_owner_title: str               # the fictional default for [persona] owner_title
    address_forms: tuple[str, ...]         # rotation of forms of address; entries equal to default_owner_title follow the config
    acks: tuple[str, ...]                  # acknowledgements that open a receipt
    repeat_leads: tuple[str, ...]          # put in front of a reply that would repeat the previous one word for word
    ask_variants: dict[str, list[str]]     # alternative wordings of a question asked again
    days: tuple[str, ...]                  # Monday first
    days_short: tuple[str, ...]
    months: tuple[str, ...]
    months_short: tuple[str, ...]
    flags: dict[str, str]                  # digest flags: conflict, back-to-back
    day_words: dict[str, str]              # today, tomorrow
    plugin_titles: dict[str, str]          # shown in the plugin-disabled refusal
    list_titles: dict[str, str]            # display title of each named list key
    recall_marker: str                     # text that identifies a read-back reply (so reading back never reads back itself)
    agent_note: str                        # appended to the agent-mode system prompt (e.g. "reply in English"); "" for the base language
