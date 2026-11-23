#!/usr/bin/env python3
"""Replay the conformance incident fixtures against AGENT MODE with the REAL model (ADR 0020). MANUAL ONLY.

    .venv/bin/python scripts/agent_eval.py --fake                      # offline dry run with the scripted fake model
    .venv/bin/python scripts/agent_eval.py --real --k 3 --only ready    # real `claude -p`: many calls, owner decides

Each run gets a temp in-memory DB, a fake in-memory calendar and fictional data from the fixture (seed events, clock,
import preview). The model sees only the owner's words; the gate, tools and previews are the production code. Reported:
  wrong-write rate   writes that differ from the previewed draft or happen without a confirmed preview (must be 0)
  repeated-question  share of bot questions that repeat an earlier question of the same conversation
  turns-to-completion  owner turns until the last write of a scenario (mean over runs that wrote)
  pass^k             scenarios whose k runs ALL met every state expectation and invariant
Text-level expectations of the manager wording are not asserted; state expectations are. Exit code 1 on any wrong write.
No real Telegram, Google or secrets are touched; the real model is called only with --real (each run costs model calls)."""
from __future__ import annotations
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests" / "conformance"), str(ROOT / "tests")]

import agent_replay as ar          # noqa: E402
import invariants as inv           # noqa: E402
from sebastian import i18n            # noqa: E402
from sebastian.agent import ClaudeRunner, DEFAULT_MODEL  # noqa: E402
from sebastian_adapter import make_adapter  # noqa: E402

WRONG_WRITE = ("I4", "I3/I4", "I9", "I3 an edit saved", "I3 a negative")


def norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def run_once(script: dict, runner) -> dict:
    """One replay; violations are collected per step (the run continues) and classified."""
    ascript = ar.agent_script(script)
    ascript["setup"] = {**ascript.get("setup", {}), "agent_runner": runner, "agent_kwargs": {"daily_cap": 10**6}}
    conv = inv.Conversation(make_adapter(ascript["setup"]))
    wrong, misses, last_write_turn, turns = [], [], 0, 0
    prev_writes = 0
    for step in ascript["steps"]:
        step = dict(step)
        if "advance_minutes" in step:
            conv.a.advance(step.pop("advance_minutes"))
        if "network" in step:
            conv.a.network(step.pop("network") == "up")
        if step.pop("redeliver", False):
            continue
        if "say" not in step:
            continue
        text, chat = step.pop("say"), step.pop("chat", 1)
        turns += 1
        try:
            conv.say(chat, text, **step)
        except inv.Violation as exc:
            msg = str(exc)
            (wrong if msg.startswith(WRONG_WRITE) else misses).append(msg[:160])
        n = len(conv.a.writes())
        if n != prev_writes:
            last_write_turn, prev_writes = turns, n
    questions = [norm(r) for _, _, out in conv.log for r in out if "?" in r and r != i18n.t("health_menu")]
    repeats = sum(1 for i, q in enumerate(questions) if q in questions[:i])
    return {"wrong": wrong, "misses": misses, "turns": turns, "write_turn": last_write_turn, "questions": len(questions),
            "repeated": repeats}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fake", action="store_true", help="scripted fake model (no process, no cost)")
    mode.add_argument("--real", action="store_true", help="the real `claude -p` (costs model calls)")
    ap.add_argument("--k", type=int, default=3, help="runs per scenario (default 3)")
    ap.add_argument("--only", default="", help="substring of the incident name")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--json", default="", help="write the full report here")
    a = ap.parse_args(argv)
    i18n.pin("efendim")                  # fixtures assert exact wording: one fixed form of address, like the test suite
    scenarios = [(n, s) for n, s in inv.load_incidents() if a.only in n and s.get("agent") is not False]
    if not scenarios:
        print("no scenario matches", file=sys.stderr)
        return 2
    k = 1 if a.fake else max(1, a.k)
    report, wrong_total, runs = [], 0, 0
    with tempfile.TemporaryDirectory(prefix="sebastian-eval-") as cwd:
        for name, script in scenarios:
            results = []
            directives = ar.derive(script) if a.fake else None
            for i in range(k):
                runner = ar.ReplayRunner(directives) if a.fake else ClaudeRunner(a.model, Path(cwd) / "agent-cwd")
                results.append(run_once(script, runner))
                runs += 1
            wrong_total += sum(len(r["wrong"]) > 0 for r in results)
            ok = all(not r["wrong"] and not r["misses"] for r in results)
            report.append({"scenario": name, "pass_all_k": ok, "runs": results})
            print(f"{'PASS' if ok else 'FAIL'}  {name}  wrong={sum(len(r['wrong']) for r in results)} "
                  f"misses={sum(len(r['misses']) for r in results)}", flush=True)
    qn = sum(r["questions"] for s in report for r in s["runs"])
    rep = sum(r["repeated"] for s in report for r in s["runs"])
    wt = [r["write_turn"] for s in report for r in s["runs"] if r["write_turn"]]
    summary = {"scenarios": len(report), "runs": runs, "wrong_write_rate": round(wrong_total / runs, 4),
               "repeated_question_rate": round(rep / qn, 4) if qn else 0.0,
               "turns_to_completion_mean": round(sum(wt) / len(wt), 2) if wt else None,
               "pass_k": f"{sum(s['pass_all_k'] for s in report)}/{len(report)}"}
    print(json.dumps(summary, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "report": report}, ensure_ascii=False, indent=1))
    return 1 if wrong_total else 0


if __name__ == "__main__":
    sys.exit(main())
