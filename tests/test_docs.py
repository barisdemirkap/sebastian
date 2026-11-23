import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_google_setup_docs_cover_the_exact_steps():
    for name in ("GOOGLE-SETUP.md", "GOOGLE-SETUP.tr.md"):
        t = (ROOT / "docs" / name).read_text()
        for needle in ("calendar.events", "calendar.readonly", "Desktop app", "google.client_secret", "sebastian auth-url personal",
                       "auth-finish", "127.0.0.1:8765", "myaccount.google.com/permissions", "Advanced", "sebastian-personal"):
            assert needle in t, (name, needle)
    assert "unverified" in (ROOT / "docs" / "GOOGLE-SETUP.md").read_text().lower()
    assert "doğrulanmamış" in (ROOT / "docs" / "GOOGLE-SETUP.tr.md").read_text().lower()


def test_adrs_are_numbered_continuously():
    names = sorted(p.name[:4] for p in (ROOT / "docs" / "adr").glob("*.md"))
    assert names == [f"{i:04d}" for i in range(1, len(names) + 1)]


def test_turkish_strings_live_only_in_i18n():
    """Code is English: no Turkish diacritics outside i18n.py (plus the folding table and the model prompt examples)."""
    pat = re.compile("[çğıöşüÇĞİÖŞÜ]")
    for p in (ROOT / "src" / "sebastian").glob("*.py"):
        if p.name in ("i18n.py", "textutil.py", "nl.py"):
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            assert not pat.search(line), (p.name, n, line)


def test_no_secrets_or_real_ids_in_repo_files():
    bad = ("1000000001", "GOCSPX-", "AIza")
    for p in list((ROOT / "src").rglob("*.py")) + list((ROOT / "docs").rglob("*.md")) + [ROOT / "config.example.toml"]:
        t = p.read_text()
        for b in bad:
            assert b not in t, (p.name, b)
