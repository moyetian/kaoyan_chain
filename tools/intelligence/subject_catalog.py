# -*- coding: utf-8 -*-
"""Shared structured entrance-exam subject helpers.

The intelligence layer historically carried subjects as free-form ``majors``
strings.  That made a missing school record fall through to the generic
``301/8xx`` template.  This module keeps the legacy text representation
available while providing a small structured vocabulary for known candidate
profiles and report rendering.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


NURSING_308_SUBJECTS: tuple[Dict[str, str], ...] = (
    {"code": "101", "name": "思想政治理论"},
    {"code": "201", "name": "英语（一）"},
    {"code": "308", "name": "护理综合"},
)

_SUBJECT_CODE_RE = re.compile(r"^\s*\(?([0-9]{3})\)?\s*(.*)$")


def is_nursing_major(value: Any) -> bool:
    """Return whether a requested major explicitly refers to 105400/308 nursing."""
    text = str(value or "").strip().lower()
    return bool(text) and any(token in text for token in ("护理", "105400", "308"))


def nursing_308_subjects() -> List[Dict[str, str]]:
    """Return a mutable copy suitable for profile payloads."""
    return [dict(item) for item in NURSING_308_SUBJECTS]


def normalize_subject_items(raw: Any) -> List[Dict[str, str]]:
    """Normalize dict/string subject records into ``[{code, name}]``.

    Unknown free-form values are retained with an empty code.  This is
    deliberate: a report may show an unstructured source without inventing a
    code for it.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    result: List[Dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            code = str(item.get("code") or item.get("subject_code") or "").strip()
            name = str(item.get("name") or item.get("subject_name") or "").strip()
        else:
            text = str(item or "").strip()
            match = _SUBJECT_CODE_RE.match(text)
            code = match.group(1) if match else ""
            name = (match.group(2) if match else text).strip()
            # Legacy records often append a parenthetical note after the name;
            # keep it because it can distinguish English/math variants.
        if not code and not name:
            continue
        result.append({"code": code, "name": name})
    return result


def profile_subject_items(profile: Dict[str, Any]) -> List[Dict[str, str]]:
    """Read structured subjects first, then fall back to legacy ``majors``."""
    if not isinstance(profile, dict):
        return []
    raw = profile.get("exam_subjects") or profile.get("subjects")
    items = normalize_subject_items(raw)
    if items:
        return items
    return normalize_subject_items(profile.get("majors"))


def format_subject_items(items: Iterable[Dict[str, str]]) -> str:
    """Render normalized records without dropping unknown text."""
    rendered = []
    for item in items:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if code and name:
            rendered.append(f"({code}){name}")
        elif name:
            rendered.append(name)
        elif code:
            rendered.append(f"({code})")
    return "、".join(rendered)
