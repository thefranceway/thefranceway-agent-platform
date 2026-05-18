"""
Error Classifier — Shared Module
==================================
Parses raw xcodebuild output into a structured list of classified errors.
Used by both the Build Agent (reporting) and Error Fix Agent (input).
Not an agent — a pure utility module with no LLM calls.
"""

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

_COMPILE_RE = re.compile(r"^(.+\.swift):(\d+):(\d+): error: (.+)$")
_WARNING_RE = re.compile(r"^(.+\.swift):(\d+):(\d+): warning: (.+)$")
_LINKER_RE  = re.compile(r"(ld: |Undefined symbols|clang: error: linker)")
_MODULE_RE  = re.compile(r"error: no such module '([^']+)'")
_SYMBOL_RE  = re.compile(r"error: use of unresolved identifier '([^']+)'")


def classify(xcodebuild_log: str) -> list[ClassifiedError]:
    """Parse xcodebuild stdout and return classified error list."""
    errors: list[ClassifiedError] = []
    lines  = xcodebuild_log.splitlines()

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
