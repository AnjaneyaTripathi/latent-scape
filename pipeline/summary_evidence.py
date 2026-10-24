"""Pure source-evidence rules shared by export and offline build reporting.

This module deliberately depends only on the standard library: the Pages
workflow invokes its reporting script without installing pipeline extras.
"""

from __future__ import annotations

import re

ABSTRACT_PLACEHOLDERS = {
    "abstract unavailable",
    "abstract not available",
    "unavailable",
    "none",
    "n/a",
}


def summary_eligibility_reason(abstract: str, abstract_quality: str = "") -> str | None:
    """Return why an abstract cannot support an explanatory summary, if any."""
    if abstract_quality == "official-landing-page-possibly-full-text":
        return "abstract quality requires source cleanup"
    normalized = abstract.casefold().strip()
    if not abstract.strip() or normalized in ABSTRACT_PLACEHOLDERS:
        return "official abstract unavailable"
    if len(re.findall(r"\b[\w’'-]+\b", abstract)) < 40:
        return "insufficient abstract evidence (fewer than 40 words)"
    return None
