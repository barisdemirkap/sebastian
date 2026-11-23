"""ADR 0023 spike: run the blind-round fixtures through the Assistant with the model assist PROMOTED ('on') and report, against the
fixtures' own expectations, per-kind accuracy and reference-resolution accuracy (separately), latency, cost and the wrong-item rate.

  PYTHONPATH=<tree>/src python scripts/assist_spike.py <tree> <round> <outdir> [--replay cassette.json]

Pointed at an older source tree (a git worktree of the commit before a round's fixes) it measures the rules of THAT time, blind, plus the
model. Real model calls go through `claude -p` (this machine's login); `--replay` serves recorded answers instead (no model, no network).
Reads fixtures only: no sebastian.db, no Telegram, no calendar writes (the adapter's calendar and store are in-memory fakes)."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

tree, rnd, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
replay = json.load(open(sys.argv[sys.argv.index("--replay") + 1])) if "--replay" in sys.argv else None
sys.path.insert(0, f"{tree}/src")
sys.path.insert(0, f"{tree}/tests/conformance")
import invariants as inv                              # noqa: E402
from sebastian_adapter import make_adapter               # noqa: E402
from sebastian import assist as am                       # noqa: E402
from sebastian.textutil import fold                      # noqa: E402
try:                                                  # label accuracy is scored on the effect, so the 'evet' gate is off for the measurement
    from sebastian import statements as _st
    _st.MODEL_FORCES_PREVIEW = False
except Exception:                                     # an older tree has no such gate
    pass

CASES = json.load(open(f"{tree}/tests/conformance/training/r{rnd}blind.json" if rnd.isdigit() else rnd, encoding="utf-8"))


class Recorder(am.ClaudeAssistProvider):
    def __init__(self):
        super().__init__()
        self.log = []

    def call(self, prompt):
        if replay is not None:
            hit = replay.get(prompt)
            if hit is None:
                raise am.AssistError("error")
            raw, meta = hit
            self.log.append({"prompt": prompt, "raw": raw, "meta": meta})
            return raw, meta
        try:
            raw, meta = super().call(prompt)
        except am.AssistError as exc:
            self.log.append({"prompt": prompt, "error": exc.reason})
            raise
        self.log.append({"prompt": prompt, "raw": raw, "meta": meta})
        return raw, meta


def gold(step, tasks):
    def idx(sub):
        return {i for i, t in enumerate(tasks) if fold(sub) in fold(t)}
    done = set().union(*[idx(s) for s in step.get("tasks_done_has", [])]) if step.get("tasks_done_has") else set()
    defer = set().union(*[idx(s) for s in step.get("tasks_due", {})]) if step.get("tasks_due") else set()
    return done, defer


rows = []
for case in CASES:
    prov = Recorder()
    a = make_adapter(case["setup"])
    a.assistant.assist = am.AssistService("on", prov)
    conv = inv.Conversation(a)
    tasks = case["setup"].get("tasks", [])
    res = {"name": case["name"], "ok": True, "steps": []}
    for st in case["steps"]:
        st = dict(st)
        text, chat = st.pop("say"), st.pop("chat", 1)
        n_calls = len(prov.log)
        try:
            conv.say(chat, text, **st)
        except Exception as exc:                                      # a violated expectation (or invariant)
            res["ok"] = False
            res["fail"] = str(exc)[:160]
            res["steps"].append({"say": text, "calls": len(prov.log) - n_calls})
            break
        res["steps"].append({"say": text, "calls": len(prov.log) - n_calls, "gold": [sorted(g) for g in gold(st, tasks)]})
    res["log"] = prov.log
    res["audit"] = [json.loads(r["detail"]) for r in a.store.audit_rows("nl_assist")]
    res["task_ids"] = [r["id"] for r in a.store.c.execute("SELECT id FROM tasks ORDER BY id").fetchall()]
    rows.append(res)
Path(outdir).mkdir(parents=True, exist_ok=True)
json.dump(rows, open(f"{outdir}/spike_r{Path(rnd).stem.replace('blind', '').lstrip('r')}.json", "w", encoding="utf-8"), ensure_ascii=False)
print(rnd, len(rows), "pass", sum(r["ok"] for r in rows), "calls", sum(len(r["log"]) for r in rows))
