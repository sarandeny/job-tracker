"""Decide whether a job fits the profile and how well."""
from __future__ import annotations

import re
from datetime import date, datetime

from .sources import Job

_MONTHS_FMT = ("%B %d, %Y", "%b %d, %Y")


def days_since_posted(posted: str, today: date | None = None) -> int | None:
    """Turn the many 'posted' formats into an age in days, or None if unknown."""
    today = today or date.today()
    p = (posted or "").strip()
    if not p:
        return None
    low = p.lower()
    if "today" in low or "just posted" in low:
        return 0
    if "yesterday" in low:
        return 1
    m = re.search(r"(\d+)(\+?)\s*days?\s*ago", low)
    if m:
        # "30+ Days Ago" means 30 or more, so treat it as older than 30
        return int(m.group(1)) + (1 if m.group(2) else 0)
    try:
        return max(0, (today - datetime.fromisoformat(p.replace("Z", "+00:00")).date()).days)
    except ValueError:
        pass
    for fmt in _MONTHS_FMT:
        try:
            return max(0, (today - datetime.strptime(p, fmt).date()).days)
        except ValueError:
            continue
    return None


def min_years_required(text: str) -> int | None:
    """Smallest 'N years' figure in the text (ignores things like '18 years of age')."""
    nums = []
    for m in re.finditer(
        r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:to|-|–)\s*\d{1,2}\s*)?(?:\+\s*)?years?", text.lower()
    ):
        n = int(m.group(1))
        if n <= 15:
            nums.append(n)
    return min(nums) if nums else None


def _excluded(title: str, words: list[str]) -> str | None:
    for w in words:
        if re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", title.lower()):
            return w
    return None


def evaluate(job: Job, cfg: dict, today: date | None = None) -> Job | None:
    """Return the job with score and reasons filled in, or None if it should be dropped."""
    prof = cfg["profile"]
    title = job.title.lower()
    text = f"{job.title} {job.description}".lower()
    reasons: list[str] = []

    if _excluded(job.title, prof.get("exclude_title_words", [])):
        return None

    # Title must contain at least one wanted phrase.
    hits = {t: w for t, w in prof["title_terms"].items() if t.lower() in title}
    if not hits:
        return None
    best = max(hits, key=hits.get)
    score = hits[best]
    reasons.append(f"title: {best}")

    # Location: India only; unknown or multi-location listings are kept but unscored.
    loc = job.location.lower()
    if not loc:
        reasons.append("location unknown")
    elif any(c in loc for c in prof["locations_include"]):
        score += 15
        reasons.append("India")
    elif re.search(r"\d+\s+locations?", loc):
        reasons.append("multiple locations, check")
    else:
        return None

    # Age
    age = days_since_posted(job.posted, today)
    if age is not None and age > prof.get("max_age_days", 30):
        return None

    # Experience
    years = min_years_required(text)
    if years is not None:
        if years > prof.get("max_years_required", 3):
            return None
        score += 10
        reasons.append(f"{years}+ yrs")
    if re.search(r"entry[- ]level|early career|graduate|junior|\bassociate\b", text):
        score += 10
        reasons.append("entry-level wording")

    # Skills
    found = [s for s in prof.get("skills", []) if s.lower() in text]
    if found:
        score += min(25, 5 * len(found))
        reasons.append("skills: " + ", ".join(found[:5]))

    # Company bonus
    bonus = prof.get("priority_companies", {}).get(job.company, 0)
    if bonus:
        score += bonus
        reasons.append("priority company")

    if score < prof.get("min_score", 45):
        return None

    job.score = score
    job.reasons = reasons
    return job
