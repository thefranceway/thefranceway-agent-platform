#!/usr/bin/env python3
"""
Agent Platform — Contract Layer
=================================
Runtime schema validation for all agent handoffs.
Hard violations (missing required field, wrong type) → raise ContractViolationError.
Soft violations (unexpected extra field) → log warning, continue.
All violations written to logs/contract_violations.jsonl.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel, ConfigDict, ValidationError

PLATFORM_DIR     = Path(__file__).parent.parent
VIOLATIONS_LOG   = PLATFORM_DIR / "logs" / "contract_violations.jsonl"
_VIOLATIONS_LOCK = threading.Lock()


# ── Exception ─────────────────────────────────────────────────────────────────

class ContractViolationError(Exception):
    def __init__(
        self,
        agent:     str,
        field:     str,
        expected:  str,
        got:       str,
        direction: str,  # "input" | "output" | "tool_input"
    ):
        self.agent     = agent
        self.field     = field
        self.expected  = expected
        self.got       = got
        self.direction = direction
        super().__init__(
            f"[ContractViolation] {agent} {direction}: field='{field}' "
            f"expected={expected} got={got}"
        )


# ── Base schema classes ───────────────────────────────────────────────────────

class AgentInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class AgentOutput(BaseModel):
    output: str
    model_config = ConfigDict(extra="allow")


# ── Violation logger ──────────────────────────────────────────────────────────

def _log_violation(
    agent:     str,
    direction: str,
    field:     str,
    expected:  str,
    got:       str,
    severity:  str,  # "hard" | "soft"
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent":     agent,
        "direction": direction,
        "field":     field,
        "expected":  expected,
        "got":       got,
        "severity":  severity,
    }
    try:
        VIOLATIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _VIOLATIONS_LOCK:
            with open(VIOLATIONS_LOG, "a") as f:
                f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ── Core validators ───────────────────────────────────────────────────────────

def validate_input(
    agent_name: str,
    schema_cls: Optional[Type[BaseModel]],
    data:       Any,
) -> None:
    """
    Validate data against schema_cls.
    - Missing required field / wrong type → ContractViolationError (hard) + log
    - Extra fields allowed by schema (extra="allow") → log soft warning
    - If schema_cls is None → no-op (agent has no registered schema yet)
    """
    if schema_cls is None:
        return
    try:
        schema_cls.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            field    = ".".join(str(loc) for loc in err["loc"]) or "root"
            expected = err.get("type", "unknown")
            got      = str(err.get("input", ""))[:120]
            _log_violation(agent_name, "input", field, expected, got, "hard")
            raise ContractViolationError(
                agent     = agent_name,
                field     = field,
                expected  = expected,
                got       = got,
                direction = "input",
            ) from exc


def validate_output(
    agent_name: str,
    schema_cls: Optional[Type[BaseModel]],
    data:       Any,
) -> None:
    """
    Validate agent output dict against schema_cls.
    Same hard/soft rules as validate_input.
    """
    if schema_cls is None:
        return
    try:
        schema_cls.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            field    = ".".join(str(loc) for loc in err["loc"]) or "root"
            expected = err.get("type", "unknown")
            got      = str(err.get("input", ""))[:120]
            _log_violation(agent_name, "output", field, expected, got, "hard")
            raise ContractViolationError(
                agent     = agent_name,
                field     = field,
                expected  = expected,
                got       = got,
                direction = "output",
            ) from exc
