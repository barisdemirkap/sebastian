"""Language packs (ADR 0006): one module per language, registered here. `docs/LANGUAGES.md` says how to add one."""
from __future__ import annotations

from .pack import Pack
from . import en, tr

DEFAULT_LANGUAGE = "tr"
PACKS: dict[str, Pack] = {p.code: p for p in (tr.PACK, en.PACK)}


def get(code: str) -> Pack:
    """The pack for a language code; an unknown code raises ValueError naming the available ones."""
    try:
        return PACKS[code]
    except KeyError:
        raise ValueError(f"unknown language {code!r} (available: {', '.join(sorted(PACKS))})") from None
