"""Deterministic checks that compare a plan with the brief it came from.

The model drafts; these rules catch mistakes a regex can see for certain. They never
rewrite deliverables (a person does that); they only recover the client's own timing
words and raise warnings that are shown to the reviewer.
"""
from __future__ import annotations

import re

# "a"/"an" are deliberately absent: "a caption for each reel" is not a count of one.
NUMBER_WORDS = {"one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
# Asset words we can count reliably, with the stems used to find them in deliverable names.
ASSETS = {
    "tiktok": ["tiktok"], "reel": ["reel"], "short": ["short"], "story": ["stor"], "carousel": ["carousel"],
    "post": ["post"], "video": ["video"], "audiogram": ["audiogram"], "email": ["email", "newsletter"],
    "trailer": ["trailer"], "graphic": ["graphic"], "caption": ["caption"], "episode": ["episode"],
    "thumbnail": ["thumbnail"], "banner": ["banner"],
}
_NUM = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
PLURALS = {"story": "stories"}
_ASSET_RX = "|".join(sorted([a for a in ASSETS] + list(PLURALS.values()), key=len, reverse=True))
# Greedy word gap, so "2 short Facebook videos" counts videos, not "short".
_COUNT = re.compile(_NUM + r"\s+(?:[\w'-]+\s+){0,3}(" + _ASSET_RX + r")s?\b", re.I)

MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
# Most specific first: an ISO date beats "next Friday", which beats "in January".
_TIMING = [re.compile(p, re.I) for p in (
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b(?:next|this)\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b(?:by|before|on|in|around|sometime in|early|late|mid)\s+(?:the\s+)?(?:" + MONTHS + r"|spring|summer|fall|autumn)(?:\s+\d{1,2})?\b",
    r"\bbefore\s+the\s+[\w\s]{3,30}?season\b",
)]


def timing_phrase(brief: str) -> str | None:
    """The client's own timing words, quoted as written (used when the model omits them)."""
    for rx in _TIMING:
        m = rx.search(brief)
        if m:
            return m.group(0).strip()
    return None


def stated_counts(brief: str) -> dict[str, int]:
    """Asset counts stated explicitly in the brief, e.g. "2 TikToks", "three Instagram reels"."""
    counts: dict[str, int] = {}
    for num, asset in _COUNT.findall(brief):
        n = int(num) if num.isdigit() else NUMBER_WORDS[num.lower()]
        key = {v: k for k, v in PLURALS.items()}.get(asset.lower(), asset.lower())
        counts[key] = max(counts.get(key, 0), n)
    return counts


DISPLAY = {"tiktok": ("TikTok", "TikToks"), "story": ("story", "stories"), "email": ("email", "emails")}


def _label(asset: str, n: int) -> str:
    one, many = DISPLAY.get(asset, (asset, asset + "s"))
    return one if n == 1 else many


def quantity_warnings(brief: str, plan: dict) -> list[str]:
    warnings = []
    for asset, expected in stated_counts(brief).items():
        stems = ASSETS[asset]
        name = _label(asset, expected)
        matching = [d for d in plan.get("deliverables", []) if any(s in d.get("name", "").lower() for s in stems)]
        got = sum(int(d.get("quantity", 0)) for d in matching)
        if matching and got != expected:
            warnings.append(f"The brief asks for {expected} {name}; the plan has {got}.")
        elif not matching and asset in ("tiktok", "reel", "short", "story", "carousel", "audiogram", "trailer", "email"):
            warnings.append(f"The brief asks for {expected} {name}; no deliverable mentions it.")
    return warnings
