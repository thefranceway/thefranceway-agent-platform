"""
Error Classifier — Shared Module
==================================
Parses raw xcodebuild output into a structured list of classified errors.
Used by both the Build Agent (reporting) and Error Fix Agent (input).
Not an agent — a pure utility module with no LLM calls.

Extensibility: supplemental patterns can be loaded from AD4M at runtime via
load_supplemental_patterns(). These are appended after the hardcoded patterns
and give the crystallize_patterns cron a way to teach the classifier new error types.
"""

import json
import re
from dataclasses import dataclass, asdict
from typing import Literal


ErrorCategory = Literal[
    "compile_error",
    "linker_error",
    "missing_symbol",
    "missing_module",
    "deprecation",
    "warning",
    "swiftui_preview_error",
]


@dataclass
class ClassifiedError:
    category:      ErrorCategory
    file_path:     str
    line:          int
    column:        int
    error_code:    str
    message:       str
    context_lines: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


# Patterns in priority order — first match wins
_PATTERNS = [
    # Swift compile errors: /path/to/File.swift:42:10: error: message
    (
        "compile_error",
        re.compile(r"^(.+\.swift):(\d+):(\d+): error: (.+)$"),
    ),
    # Linker errors
    (
        "linker_error",
        re.compile(r"(ld: .+|Undefined symbols for architecture|clang: error: linker)"),
    ),
    # Missing module (import failure)
    (
        "missing_module",
        re.compile(r"error: no such module '(.+)'"),
    ),
    # Missing symbol
    (
        "missing_symbol",
        re.compile(r"error: use of unresolved identifier '(.+)'"),
    ),
    # Swift deprecations (treated separately from errors)
    (
        "deprecation",
        re.compile(r"(.+\.swift):(\d+):(\d+): warning: '(.+)' is deprecated"),
    ),
    # General warnings
    (
        "warning",
        re.compile(r"(.+\.swift):(\d+):(\d+): warning: (.+)$"),
    ),
    # SwiftUI preview errors (low priority — don't block build)
    (
        "swiftui_preview_error",
        re.compile(r"PreviewProvider|#Preview.*error"),
    ),
]

_KNOWN_CATEGORIES = [
    "compile_error", "linker_error", "missing_symbol",
    "missing_module", "deprecation", "warning", "swiftui_preview_error",
]

_COMPILE_RE = re.compile(r"^(.+\.swift):(\d+):(\d+): error: (.+)$")
_WARNING_RE = re.compile(r"^(.+\.swift):(\d+):(\d+): warning: (.+)$")
_LINKER_RE  = re.compile(r"(ld: |Undefined symbols|clang: error: linker)")
_MODULE_RE  = re.compile(r"error: no such module '([^']+)'")
_SYMBOL_RE  = re.compile(r"error: use of unresolved identifier '([^']+)'")


def load_supplemental_patterns() -> list[tuple]:
    """
    Load additional error patterns from AD4M (stored by crystallize_patterns.py).
    Returns a list of (category, compiled_regex) tuples, same format as _PATTERNS.
    Silently returns [] if AD4M is unavailable or no patterns have been stored.
    """
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from agents.ad4m_tools import execute_ad4m_tool
        import os

        PERSPECTIVE_UUID = os.getenv("AD4M_ERROR_PERSPECTIVE") or "a47bf0c3-5a86-4367-a462-f88680491525"
        supplemental = []

        for category in _KNOWN_CATEGORIES:
            raw = execute_ad4m_tool("ad4m_read_links", {
                "perspective_uuid": PERSPECTIVE_UUID,
                "source":    f"build://error-patterns/{category}",
                "predicate": "franc://regex-pattern",
            })
            links = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(links, list):
                continue
            for link in links:
                target = link.get("target", "")
                if target.startswith("literal://"):
                    pattern_str = target[len("literal://"):]
                    try:
                        supplemental.append((category, re.compile(pattern_str)))
                    except re.error:
                        pass
        return supplemental
    except Exception:
        return []


def classify(
    xcodebuild_log: str,
    supplemental_patterns: list[tuple] = None,
) -> list[ClassifiedError]:
    """
    Parse xcodebuild stdout and return classified error list.
    supplemental_patterns: optional list of (category, compiled_regex) from load_supplemental_patterns().
    If None, supplementals are not loaded (caller decides when to pay the AD4M round-trip).
    """
    errors: list[ClassifiedError] = []
    lines  = xcodebuild_log.splitlines()
    extra  = supplemental_patterns or []

    for i, line in enumerate(lines):
        context = lines[max(0, i-1): i+3]

        m = _COMPILE_RE.match(line)
        if m:
            errors.append(ClassifiedError(
                category="compile_error",
                file_path=m.group(1),
                line=int(m.group(2)),
                column=int(m.group(3)),
                error_code="",
                message=m.group(4),
                context_lines=context,
            ))
            continue

        m = _MODULE_RE.search(line)
        if m:
            errors.append(ClassifiedError(
                category="missing_module",
                file_path="",
                line=0,
                column=0,
                error_code="module_not_found",
                message=f"No such module '{m.group(1)}'",
                context_lines=context,
            ))
            continue

        m = _SYMBOL_RE.search(line)
        if m and ": error:" in line:
            file_match = _COMPILE_RE.match(line)
            errors.append(ClassifiedError(
                category="missing_symbol",
                file_path=file_match.group(1) if file_match else "",
                line=int(file_match.group(2)) if file_match else 0,
                column=int(file_match.group(3)) if file_match else 0,
                error_code="unresolved_identifier",
                message=m.group(0),
                context_lines=context,
            ))
            continue

        if _LINKER_RE.search(line):
            errors.append(ClassifiedError(
                category="linker_error",
                file_path="",
                line=0,
                column=0,
                error_code="linker",
                message=line.strip(),
                context_lines=context,
            ))
            continue

        # Supplemental patterns (from AD4M / crystallize_patterns) — checked last
        for sup_category, sup_regex in extra:
            if sup_regex.search(line):
                errors.append(ClassifiedError(
                    category=sup_category,
                    file_path="",
                    line=0,
                    column=0,
                    error_code="supplemental",
                    message=line.strip(),
                    context_lines=context,
                ))
                break

    # Deduplicate by (file_path, line, message)
    seen   = set()
    unique = []
    for e in errors:
        key = (e.file_path, e.line, e.message[:60])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


def to_json(errors: list[ClassifiedError]) -> list[dict]:
    return [e.to_dict() for e in errors]


def summary(errors: list[ClassifiedError]) -> str:
    counts = {}
    for e in errors:
        counts[e.category] = counts.get(e.category, 0) + 1
    parts = [f"{v} {k}" for k, v in counts.items()]
    return ", ".join(parts) if parts else "clean"
