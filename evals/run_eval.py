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


def main():
    runs_per = int(sys.argv[1]) if len(sys.argv) > 1 else 1
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
                checks = score(plan, exp)
                row = {"brief": exp["file"], "run": n + 1, "valid": True, "attempts": gen.attempts,
                       "seconds": round(time.monotonic() - started), "checks": checks, "plan": plan}
            except ModelError as exc:
                row = {"brief": exp["file"], "run": n + 1, "valid": False, "error": str(exc),
                       "seconds": round(time.monotonic() - started), "checks": []}
            passed = sum(c["ok"] for c in row["checks"])
            print(f"{exp['file']} run {n + 1}: {'valid' if row['valid'] else 'INVALID'} {passed}/{len(row['checks'])} in {row['seconds']}s", flush=True)
            results.append(row)
    meta = {"model": f"{provider}/{model}", "runs_per_brief": runs_per,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": "local laptop (GTX 1650 4 GB)"}
    (ROOT / "evals" / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8")
    write_markdown(meta, results)


def write_markdown(meta: dict, results: list[dict]):
    valid = [r for r in results if r["valid"]]
    all_checks = [c["ok"] for r in valid for c in r["checks"]]
    by_kind = {}
    for c in (c for r in valid for c in r["checks"]):
        by_kind.setdefault(c["kind"], []).append(c["ok"])
    lines = [f"# Model accuracy check", "",
             f"Model `{meta['model']}`, {meta['runs_per_brief']} live run(s) per brief, finished {meta['finished_at']} on {meta['host']}. "
             "Expectations were written by hand from each brief in [expectations.json](expectations.json); scoring is automatic ([run_eval.py](run_eval.py)).", "",
             f"**{sum(all_checks)}/{len(all_checks)} checks passed ({round(100 * sum(all_checks) / max(1, len(all_checks)))}%)**, "
             f"{len(valid)}/{len(results)} outputs schema-valid, "
             f"{sum(1 for r in valid if r['attempts'] == 1)}/{len(valid)} valid on the first attempt.", "",
             "| Check type | Passed |", "| --- | --- |"]
    names = {"quantity": "Deliverable quantities", "deadline": "Deadline handling", "deadline_mention": "Keeps client's timing words",
             "question": "Asks the right client questions", "task": "Includes essential tasks"}
    for kind, oks in by_kind.items():
        lines.append(f"| {names.get(kind, kind)} | {sum(oks)}/{len(oks)} |")
    lines += ["", "| Brief | Run | Passed | Time | Failed checks |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        if not r["valid"]:
            lines.append(f"| {r['brief']} | {r['run']} | invalid output | {r['seconds']}s | {r['error']} |"); continue
        fails = "; ".join(f"{c['label']} (got {c['got']!r})" if "got" in c else c["label"] for c in r["checks"] if not c["ok"]) or "none"
        lines.append(f"| {r['brief']} | {r['run']} | {sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} | {r['seconds']}s | {fails} |")
    lines += ["", "Regex checks are a coarse proxy: they confirm a fact is present, not that the wording is good. Human review stays the control."]
    (ROOT / "evals" / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        saved = json.loads((ROOT / "evals" / "results.json").read_text(encoding="utf-8"))
        write_markdown(saved["meta"], saved["results"])
    else:
        main()
