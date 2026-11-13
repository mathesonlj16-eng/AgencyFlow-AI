"""Compare the AI's original draft with the human-approved plan.

This is the honest measure of how useful the model was: how much of its draft a
reviewer kept, changed, removed or had to add.
"""
from __future__ import annotations

from typing import Any

TASK_FIELDS = ("title", "role", "priority", "detail", "days_before_deadline")
DELIVERABLE_FIELDS = ("name", "channel", "quantity", "notes")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _match(ai_items: list[dict], human_items: list[dict], key: str, fields: tuple[str, ...]):
    """Pair items by normalized key (title/name). Returns (kept, edited, removed, added)."""
    pool = {}
    for item in human_items:
        pool.setdefault(_norm(item.get(key)), []).append(item)
    kept, edited, removed = [], [], []
    for ai in ai_items:
        candidates = pool.get(_norm(ai.get(key)))
        if not candidates:
            removed.append(ai)
            continue
        human = candidates.pop(0)
        changes = [f for f in fields if f != key and _norm(ai.get(f)) != _norm(human.get(f))]
        (edited if changes else kept).append({key: human.get(key), "changed": changes} if changes else {key: human.get(key)})
    added = [item for items in pool.values() for item in items]
    return kept, edited, removed, added


def _text_list(ai: list[str], human: list[str]):
    a, h = {_norm(x): x for x in ai}, {_norm(x): x for x in human}
    return {"kept": [h[k] for k in h if k in a], "removed": [a[k] for k in a if k not in h], "added": [h[k] for k in h if k not in a]}


def diff_plans(ai: dict, human: dict) -> dict:
    t_kept, t_edited, t_removed, t_added = _match(ai.get("tasks", []), human.get("tasks", []), "title", TASK_FIELDS)
    d_kept, d_edited, d_removed, d_added = _match(ai.get("deliverables", []), human.get("deliverables", []), "name", DELIVERABLE_FIELDS)
    fields = [f for f in ("campaign_name", "summary") if _norm(ai.get(f)) != _norm(human.get(f))]
    ai_tasks = len(ai.get("tasks", []))
    return {
        "fields_changed": fields,
        "deadline": {"ai": ai.get("deadline"), "approved": human.get("deadline"),
                     "set_by_reviewer": not ai.get("deadline") and bool(human.get("deadline")),
                     "changed_by_reviewer": bool(ai.get("deadline")) and ai.get("deadline") != human.get("deadline")},
        "tasks": {"ai_count": ai_tasks, "kept": len(t_kept), "edited": t_edited,
                  "removed": [t.get("title") for t in t_removed], "added": [t.get("title") for t in t_added]},
        "deliverables": {"ai_count": len(ai.get("deliverables", [])), "kept": len(d_kept), "edited": d_edited,
                         "removed": [d.get("name") for d in d_removed], "added": [d.get("name") for d in d_added]},
        "missing_inputs": _text_list(ai.get("missing_inputs", []), human.get("missing_inputs", [])),
        "risks": _text_list(ai.get("risks", []), human.get("risks", [])),
        "ai_tasks_kept_unchanged_pct": round(100 * len(t_kept) / ai_tasks) if ai_tasks else None,
    }


def summarize(diffs: list[dict]) -> dict:
    """Aggregate across approved runs."""
    n = len(diffs)
    if not n:
        return {"approved_runs": 0}
    pcts = [d["ai_tasks_kept_unchanged_pct"] for d in diffs if d["ai_tasks_kept_unchanged_pct"] is not None]
    return {
        "approved_runs": n,
        "avg_ai_tasks_kept_unchanged_pct": round(sum(pcts) / len(pcts)) if pcts else None,
        "runs_where_reviewer_added_tasks": sum(1 for d in diffs if d["tasks"]["added"]),
        "runs_where_reviewer_removed_tasks": sum(1 for d in diffs if d["tasks"]["removed"]),
        "runs_where_reviewer_set_deadline": sum(1 for d in diffs if d["deadline"]["set_by_reviewer"]),
        "runs_with_deliverable_fixes": sum(1 for d in diffs if d["deliverables"]["edited"] or d["deliverables"]["added"] or d["deliverables"]["removed"]),
    }
