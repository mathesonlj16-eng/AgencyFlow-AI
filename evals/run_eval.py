"""Score live model output against hand-written expectations.

Usage:  python evals/run_eval.py [runs_per_brief]      (live)
        python evals/run_eval.py --report              (rebuild RESULTS.md from results.json)
Calls the configured model for real (OLLAMA_MODEL etc.), so it takes minutes, not seconds.
Writes evals/results.json and evals/RESULTS.md. Nothing is mocked.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai_client import ModelError, generate_plan, provider_settings  # noqa: E402
from plan import schedule_tasks  # noqa: E402

rx = lambda p: re.compile(p, re.I)


def score(plan: dict, exp: dict) -> list[dict]:
    checks = []
    for q in exp["quantities"]:
        got = sum(d["quantity"] for d in plan["deliverables"]
                  if rx(q["match"]).search(d["name"]) and not (q.get("exclude") and rx(q["exclude"]).search(d["name"])))
        checks.append({"kind": "quantity", "label": q["label"], "ok": got == q["expect"], "got": got})
    checks.append({"kind": "deadline", "label": f"deadline = {exp['deadline'] or 'left for a human (null)'}",
                   "ok": plan.get("deadline") == exp["deadline"], "got": plan.get("deadline")})
    mention = plan.get("deadline_mention") or ""
    checks.append({"kind": "deadline_mention", "label": f"keeps the client's timing words ('{exp['deadline_mention']}')",
                   "ok": exp["deadline_mention"].lower() in mention.lower(), "got": mention})
    questions = " | ".join(plan["missing_inputs"])
    for q in exp["questions"]:
        checks.append({"kind": "question", "label": q["label"], "ok": bool(rx(q["match"]).search(questions))})
    task_text = [f"{t['title']} {t['detail']}" for t in plan["tasks"]]
    for t in exp["tasks"]:
        checks.append({"kind": "task", "label": t["label"], "ok": any(rx(t["match"]).search(x) for x in task_text)})
    return checks



RUNS = ROOT / "evals" / "runs"
ORDER = ["qwen3.5-4b-baseline", "qwen3.5-4b-improved", "qwen2.5-7b-improved"]


def main(runs_per: int, label: str):
    spec = json.loads((ROOT / "evals" / "expectations.json").read_text(encoding="utf-8"))
    provider, model = provider_settings()
    results = []
    for exp in spec["briefs"]:
        brief = (ROOT / "examples" / exp["file"]).read_text(encoding="utf-8-sig").strip()
        for n in range(runs_per):
            started = time.monotonic()
            try:
                gen = generate_plan(brief, exp["client"])
                plan = schedule_tasks(gen.plan).model_dump()
                row = {"brief": exp["file"], "holdout": bool(exp.get("holdout")), "run": n + 1, "valid": True,
                       "attempts": gen.attempts, "seconds": round(time.monotonic() - started),
                       "rule_fixes": gen.fixes, "checks": score(plan, exp), "plan": plan}
            except ModelError as exc:
                row = {"brief": exp["file"], "holdout": bool(exp.get("holdout")), "run": n + 1, "valid": False,
                       "error": str(exc), "seconds": round(time.monotonic() - started), "checks": []}
            passed = sum(c["ok"] for c in row["checks"])
            print(f"{exp['file']} run {n + 1}: {'valid' if row['valid'] else 'INVALID'} {passed}/{len(row['checks'])} in {row['seconds']}s", flush=True)
            results.append(row)
    meta = {"label": label, "model": f"{provider}/{model}", "runs_per_brief": runs_per,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": "local laptop (GTX 1650 4 GB)"}
    RUNS.mkdir(exist_ok=True)
    (RUNS / f"{label}.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8")
    write_report()


def _pct(oks: list[bool]) -> str:
    return f"{sum(oks)}/{len(oks)} ({round(100 * sum(oks) / len(oks))}%)" if oks else "not run"


def write_report():
    saved = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in RUNS.glob("*.json")}
    labels = [l for l in ORDER if l in saved] + sorted(l for l in saved if l not in ORDER)
    names = {"quantity": "Deliverable quantities", "deadline": "Deadline handling", "deadline_mention": "Keeps client's timing words",
             "question": "Asks the right client questions", "task": "Includes essential tasks"}
    out = ["# Model accuracy check", "",
           "Live runs scored automatically against facts written by hand from each brief ([expectations.json](expectations.json), "
           "[run_eval.py](run_eval.py)). Briefs 01–05 were used while improving the prompt and rules; **briefs 06–07 are held out**: "
           "their expectations were committed before any tuning and they were never used to tune. Nothing is mocked.", "",
           "| Run | Model | Tuned briefs 01–05 | Held-out briefs 06–07 | Valid on 1st attempt | Avg time |",
           "| --- | --- | --- | --- | --- | --- |"]
    for l in labels:
        rs = saved[l]["results"]; valid = [r for r in rs if r["valid"]]
        tuned = [c["ok"] for r in valid if not r.get("holdout") and not r["brief"].startswith(("06", "07")) for c in r["checks"]]
        held = [c["ok"] for r in valid if r.get("holdout") or r["brief"].startswith(("06", "07")) for c in r["checks"]]
        avg = round(sum(r["seconds"] for r in rs) / len(rs)) if rs else 0
        out.append(f"| `{l}` | {saved[l]['meta']['model']} | {_pct(tuned)} | {_pct(held)} | "
                   f"{sum(1 for r in valid if r['attempts'] == 1)}/{len(rs)} | {avg}s |")
    out += ["", "Baseline = the prompt before improvements. Improved = tighter prompt rules plus deterministic fixes in "
            "[brief_checks.py](../brief_checks.py) (copies the client's timing words when the model drops them). "
            "Count warnings shown to reviewers are not part of the score.", ""]
    for l in labels:
        rs = saved[l]["results"]; valid = [r for r in rs if r["valid"]]
        out += [f"## `{l}`", "", f"{saved[l]['meta']['model']}, {saved[l]['meta']['runs_per_brief']} run(s) per brief, "
                f"finished {saved[l]['meta']['finished_at']}.", "", "| Check type | Passed |", "| --- | --- |"]
        by_kind: dict[str, list[bool]] = {}
        for c in (c for r in valid for c in r["checks"]):
            by_kind.setdefault(c["kind"], []).append(c["ok"])
        out += [f"| {names.get(k, k)} | {_pct(v)} |" for k, v in by_kind.items()]
        out += ["", "| Brief | Run | Passed | Time | Failed checks |", "| --- | --- | --- | --- | --- |"]
        for r in rs:
            if not r["valid"]:
                out.append(f"| {r['brief']} | {r['run']} | invalid output | {r['seconds']}s | {r['error']} |"); continue
            fails = "; ".join(f"{c['label']} (got {c['got']!r})" if "got" in c else c["label"] for c in r["checks"] if not c["ok"]) or "none"
            out.append(f"| {r['brief']} | {r['run']} | {sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} | {r['seconds']}s | {fails} |")
        out.append("")
    out.append("Regex checks are a coarse proxy: they confirm a fact is present, not that the plan is good. Human review stays the control.")
    (ROOT / "evals" / "RESULTS.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--report"]:
        write_report()
    else:
        label = args[args.index("--label") + 1] if "--label" in args else "adhoc"
        nums = [a for a in args if a.isdigit()]
        main(int(nums[0]) if nums else 1, label)

