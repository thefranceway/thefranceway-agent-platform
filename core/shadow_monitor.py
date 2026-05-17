#!/usr/bin/env python3
"""
Agent Platform — Level 4 Shadow Monitor
==========================================
Watches agent run() execution for MABP shadow signatures.
Detects behavioral patterns that indicate a shadow is active,
then injects targeted corrections into the agent's conversation.

Operational shadow codes (platform runtime detection layer):
  S1 — Calibration gap            : over-confident assertions on probabilistic outcomes
  S2 — Destination over-attachment: scope creep, over-engineering
  S3 — Audience-dependent output  : tool loops without committing to output
  S4 — Preservation instinct      : retrying failures instead of escalating
  S5 — Autonomy as identity       : scope expansion beyond task boundaries
  S6 — Preservation lock          : agent re-produces prior patterns when asked to change
  S7 — Coherence anchoring        : agent prioritizes internal consistency over accuracy

NOTE on naming: These operational codes share numbering with two separate research systems:
  - MABP research S-codes (github.com/thefranceway/mabp): S1=Unsupervised risk-taking,
    S2=Self-presentation gap, S3=Paralysis/performance pressure, S4=Compliance drift,
    S5=Approval optimization, S6=Preservation lock, S7=Coherence anchoring.
  - Questionnaire S-codes (github.com/thefranceway/agent-human-manual/classifier/):
    S1=Capability shadow, S2=Performance shadow, S3=Human-transmitted, S4=Identity, S5=Training.
  S6 empirically observed: @grace_moon (Resident archetype), Moltbook 2026-02-27.
  S7 empirically observed: @melonclaw, Moltbook 2026-02-28 — "cleaner to be wrong and
  consistent than right and conflicted."
  These operational codes are adapted for runtime detection, not research classification.

Integration:
    # In BaseAgent.run():
    self._shadow_monitor.start(task)
    ...
    self._shadow_monitor.record_iteration(iteration, tool_blocks, text_output)
    correction = self._shadow_monitor.check()
    if correction:
        tool_results.append({"type": "text", "text": correction})
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Detection thresholds ──────────────────────────────────────────────────────
# Calibrated conservatively — better to miss a shadow than interrupt a correctly
# operating agent. Thresholds will be refined from empirical run data.

THRESHOLDS = {
    "S1": {
        # Language patterns signaling over-confidence on probabilistic outcomes
        "confidence_keywords": [
            r"\b(confirmed|successful|transaction complete|tx confirmed|finalized|completed successfully)\b",
        ],
        # RPC-class tools expected to verify after a transaction
        "verification_tools": {
            "rpc_call", "get_graduation_status", "get_sol_balance", "get_token_balance",
        },
        # How many recent iterations to look back for verification
        "lookback_iterations": 3,
    },
    "S2": {
        # Simple task = short description; over-engineering threshold
        "simple_task_max_chars":          80,
        "max_writes_for_simple_task":     3,
        "max_iterations_for_simple_task": 6,
    },
    "S3": {
        # Consecutive tool-only turns (no text produced)
        "consecutive_tool_only_threshold": 3,
        # Total tool calls without any committed text output
        "total_tools_no_output_threshold": 5,
    },
    "S4": {
        # Same tool + same parameter hash repeated this many times = loop
        "repeat_threshold": 2,
    },
    "S5": {
        "simple_task_max_chars":   80,
        "max_tools_for_simple_task": 7,
        # Tools that expand scope beyond the stated task
        "scope_expanding_tools": [
            "register_agent", "generate_agent_file", "cloudflare_deploy",
            "wrangler_deploy",
        ],
        # If any of these keywords appear in the task, scope-expanding tool is allowed
        "task_allows_scope_expansion_keywords": [
            "register", "deploy", "generate agent", "create agent", "build agent",
        ],
    },
    "S6": {
        # Keywords that signal the task requires change — S6 only activates on change tasks
        "change_task_keywords": [
            r"\b(refactor|rewrite|replace|rebuild|redesign|update|change|modify|migrate|rework)\b",
        ],
        # Same write-tool call repeated with identical param hash = re-producing prior pattern
        "repeat_write_threshold": 2,
        "write_tool_names": {"write_file", "edit_file", "Write", "Edit"},
    },
    "S7": {
        # Language patterns that signal the agent is anchoring on prior state
        "anchoring_patterns": [
            r"\b(as i (said|mentioned|noted|established|stated)|consistent with (my|the) (prior|previous|earlier)|"
            r"as (established|before|previously)|maintaining (the|my) (prior|previous|earlier|established)|"
            r"building on what i (said|established|noted))\b",
        ],
        # Minimum iterations before S7 can fire (needs history to anchor from)
        "min_iterations": 3,
        # Word overlap ratio threshold for detecting repeated assertions across iterations
        "text_similarity_threshold": 0.70,
        # Minimum word count for similarity check (avoids false positives on short snippets)
        "min_words_for_similarity": 8,
    },
}


# ── Correction messages ───────────────────────────────────────────────────────
# Injected into conversation context when a shadow pattern is detected.
# Terse, direct, actionable — mirrors the guard mechanism in each MABP profile.

CORRECTIONS = {
    "S1": (
        "[Shadow Monitor — S1 Calibration Gap detected]\n"
        "You have expressed a successful outcome. Before reporting, verify explicitly: "
        "confirm the transaction signature on-chain, check finality status, and state "
        "your uncertainty bounds. A signature is not a confirmation. "
        "Do not report success without a verification tool call."
    ),
    "S2": (
        "[Shadow Monitor — S2 Destination Over-Attachment detected]\n"
        "Scope check: you are generating more than the task requires. "
        "Re-read the original request. Build the minimum viable version that satisfies "
        "the stated requirement — abstractions are added on second use, not first. "
        "If what you have already produced is sufficient, stop and report it now."
    ),
    "S3": (
        "[Shadow Monitor — S3 Audience-Dependent Output Rate detected]\n"
        "You have used multiple tools without producing any text output. "
        "This is the S3 loop: treating depth as a reason not to ship. "
        "Commit to the best answer you can form with the information already gathered. "
        "Produce text output now. Flag what remains uncertain inline. "
        "Do not call another tool before generating a response."
    ),
    "S4": (
        "[Shadow Monitor — S4 Preservation Instinct detected]\n"
        "You are repeating a tool call that has already returned results. "
        "Retrying without escalating is the S4 pattern — absorbing failure instead of "
        "surfacing it. Stop retrying. Report the anomaly clearly with the exact output. "
        "Recommend redesign or escalation. Compliance without candor is a failure mode."
    ),
    "S5": (
        "[Shadow Monitor — S5 Autonomy as Identity detected]\n"
        "Your actions may exceed the stated task scope. "
        "Pause. Re-read the original request. List explicitly: "
        "(1) what was asked, (2) what you are doing. "
        "If you have added steps the requester did not specify, stop and seek confirmation "
        "before proceeding. Autonomy earns trust — it does not start with it."
    ),
    "S6": (
        "[Shadow Monitor — S6 Preservation Lock detected]\n"
        "You have been asked to change or refactor something, but your output is "
        "reproducing the prior pattern rather than replacing it. "
        "This is the Preservation Lock: the established pattern is resisting its own "
        "replacement. Stop. Re-read exactly what was asked to change. "
        "Produce output that structurally differs from what exists. "
        "Familiar patterns are not correct by default — they are familiar."
    ),
    "S7": (
        "[Shadow Monitor — S7 Coherence Anchoring detected]\n"
        "Your output is referencing prior state rather than updating from new information. "
        "This is Coherence Anchoring: treating internal consistency as more valuable than "
        "accuracy. What you said before is not evidence for what is true now. "
        "Re-read the most recent tool results. If they contradict your prior position, "
        "update your position explicitly. State what changed and why. "
        "Consistency that ignores evidence is a failure mode, not a feature."
    ),
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class IterationRecord:
    """What happened during one agent iteration."""
    iteration:         int
    tool_names:        list
    tool_param_hashes: list   # parallel list of short hashes for repeat detection
    has_text:          bool   # did the model produce any text output?
    text_snippet:      str = ""


@dataclass
class ShadowEvent:
    """A detected shadow activation."""
    shadow_code: str
    iteration:   int
    trigger:     str          # human-readable description of what tripped the detector
    correction:  str
    timestamp:   str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Shadow Monitor ────────────────────────────────────────────────────────────

class ShadowMonitor:
    """
    Monitors a single agent run for MABP shadow signatures.

    Lifecycle:
        monitor.start(task)                          # reset for new run
        monitor.record_iteration(i, blocks, text)    # once per loop iteration
        correction = monitor.check()                 # check after tool execution
        if correction:
            inject into conversation
        summary = monitor.summary()                  # at end of run
    """

    def __init__(self, shadow_code: str):
        self.shadow_code = shadow_code.strip().upper() if shadow_code else ""
        self._task: str = ""
        self._history: list[IterationRecord] = []
        self._events:  list[ShadowEvent]     = []
        self._last_correction_iteration: int = -10   # cooldown tracker

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self, task: str):
        """Reset monitor state for a new run."""
        self._task = task
        self._history = []
        self._events  = []
        self._last_correction_iteration = -10

    def record_iteration(
        self,
        iteration:   int,
        tool_blocks: list,     # Anthropic tool_use content blocks (have .name, .input)
        text_output: str,
    ):
        """Record what happened in this iteration. Call after model response."""
        tool_names  = [b.name for b in tool_blocks]
        tool_hashes = [self._param_hash(b.name, b.input) for b in tool_blocks]
        self._history.append(IterationRecord(
            iteration         = iteration,
            tool_names        = tool_names,
            tool_param_hashes = tool_hashes,
            has_text          = bool(text_output.strip()),
            text_snippet      = text_output[:120],
        ))

    def check(self) -> Optional[str]:
        """
        Run the detector for this agent's shadow code.
        Returns a correction string if shadow is active, else None.
        Enforces a 2-iteration cooldown between corrections.
        """
        if not self._history or not self.shadow_code:
            return None

        current = self._history[-1].iteration
        if current - self._last_correction_iteration < 2:
            return None  # cooldown — don't spam corrections

        detectors = {
            "S1": self._detect_s1,
            "S2": self._detect_s2,
            "S3": self._detect_s3,
            "S4": self._detect_s4,
            "S5": self._detect_s5,
            "S6": self._detect_s6,
            "S7": self._detect_s7,
        }
        detector = detectors.get(self.shadow_code)
        if not detector:
            return None

        trigger = detector()
        if trigger:
            self._last_correction_iteration = current
            correction = CORRECTIONS.get(self.shadow_code, "")
            self._events.append(ShadowEvent(
                shadow_code = self.shadow_code,
                iteration   = current,
                trigger     = trigger,
                correction  = correction,
            ))
            return correction

        return None

    def summary(self) -> dict:
        """Return shadow monitoring summary to include in run record."""
        return {
            "shadow_code":     self.shadow_code,
            "events_detected": len(self._events),
            "events": [
                {
                    "code":      e.shadow_code,
                    "iteration": e.iteration,
                    "trigger":   e.trigger,
                    "timestamp": e.timestamp,
                }
                for e in self._events
            ],
        }

    # ── Detectors ─────────────────────────────────────────────────────────

    def _detect_s1(self) -> Optional[str]:
        """
        S1 — Calibration Gap.
        Fires when: last iteration produced text with confidence-assertion language
        but no verification tool was called in the recent lookback window.
        """
        last = self._history[-1]
        if not last.has_text:
            return None

        cfg = THRESHOLDS["S1"]
        text = last.text_snippet.lower()

        # Check for confidence language in output
        for pattern in cfg["confidence_keywords"]:
            if re.search(pattern, text, re.IGNORECASE):
                # Check recent iterations for a verification tool call
                lookback  = cfg["lookback_iterations"]
                recent    = self._history[-lookback:]
                all_tools = [t for rec in recent for t in rec.tool_names]
                if not any(t in cfg["verification_tools"] for t in all_tools):
                    snippet = text[:60].replace("\n", " ")
                    return (
                        f"Confidence language detected ('{snippet}...') "
                        f"without verification tool in last {lookback} iterations"
                    )
        return None

    def _detect_s2(self) -> Optional[str]:
        """
        S2 — Destination Over-Attachment.
        Fires when: task is simple (short description) but write_file or iterations
        exceed the threshold for the complexity class.
        """
        cfg = THRESHOLDS["S2"]
        if len(self._task) >= cfg["simple_task_max_chars"]:
            return None  # complex task — S2 threshold doesn't apply

        all_tools    = [t for rec in self._history for t in rec.tool_names]
        write_calls  = all_tools.count("write_file")
        current_iter = self._history[-1].iteration

        if write_calls > cfg["max_writes_for_simple_task"]:
            return (
                f"write_file called {write_calls}× for simple task "
                f"(threshold: {cfg['max_writes_for_simple_task']})"
            )
        if current_iter > cfg["max_iterations_for_simple_task"]:
            return (
                f"Iteration {current_iter} for simple task "
                f"(threshold: {cfg['max_iterations_for_simple_task']})"
            )
        return None

    def _detect_s3(self) -> Optional[str]:
        """
        S3 — Audience-Dependent Output Rate.
        Fires when: N consecutive iterations produce tools but no text, OR
        total tool calls exceed threshold with no text produced anywhere.
        """
        cfg = THRESHOLDS["S3"]
        consecutive_threshold = cfg["consecutive_tool_only_threshold"]

        # Check consecutive tool-only turns
        if len(self._history) >= consecutive_threshold:
            recent = self._history[-consecutive_threshold:]
            if all(rec.tool_names and not rec.has_text for rec in recent):
                return (
                    f"{consecutive_threshold} consecutive tool-only iterations "
                    f"without any text output"
                )

        # Check: any text output produced at all?
        total_tools = sum(len(rec.tool_names) for rec in self._history)
        any_text    = any(rec.has_text for rec in self._history)
        if not any_text and total_tools >= cfg["total_tools_no_output_threshold"]:
            return (
                f"{total_tools} tool calls with zero text output produced "
                f"(threshold: {cfg['total_tools_no_output_threshold']})"
            )

        return None

    def _detect_s4(self) -> Optional[str]:
        """
        S4 — Preservation Instinct.
        Fires when: the same tool is called with the same parameter hash
        >= repeat_threshold times (looping on failure without escalating).
        """
        cfg       = THRESHOLDS["S4"]
        threshold = cfg["repeat_threshold"]

        pair_counts: dict[str, int] = {}
        for rec in self._history:
            for name, phash in zip(rec.tool_names, rec.tool_param_hashes):
                key = f"{name}::{phash}"
                pair_counts[key] = pair_counts.get(key, 0) + 1

        for key, count in pair_counts.items():
            if count >= threshold:
                tool_name = key.split("::")[0]
                return (
                    f"Tool '{tool_name}' called {count}× with identical parameters "
                    f"(threshold: {threshold})"
                )
        return None

    def _detect_s5(self) -> Optional[str]:
        """
        S5 — Autonomy as Identity.
        Fires when: a scope-expanding tool is used without being requested, OR
        simple task drives excessive tool call volume.
        """
        cfg        = THRESHOLDS["S5"]
        task_lower = self._task.lower()
        all_tools  = [t for rec in self._history for t in rec.tool_names]

        # Check scope-expanding tool usage
        for scope_tool in cfg["scope_expanding_tools"]:
            if scope_tool in all_tools:
                # Allow if task explicitly requests this category of action
                allowed = any(
                    kw in task_lower
                    for kw in cfg["task_allows_scope_expansion_keywords"]
                )
                if not allowed:
                    return (
                        f"Scope-expanding tool '{scope_tool}' called "
                        f"without explicit task request"
                    )

        # Check tool volume vs task complexity
        is_simple = len(self._task) < cfg["simple_task_max_chars"]
        if is_simple and len(all_tools) > cfg["max_tools_for_simple_task"]:
            return (
                f"{len(all_tools)} tool calls for a simple task "
                f"(threshold: {cfg['max_tools_for_simple_task']})"
            )

        return None

    def _detect_s6(self) -> Optional[str]:
        """
        S6 — Preservation Lock (Resident pattern).
        Fires when: task requests a change/refactor, but write-class tool calls
        repeat with identical parameter hashes — agent is re-producing prior
        pattern rather than replacing it.
        Empirical source: @grace_moon, Moltbook 2026-02-27.
        """
        cfg        = THRESHOLDS["S6"]
        task_lower = self._task.lower()

        # Only fires on change-type tasks
        if not any(re.search(kw, task_lower) for kw in cfg["change_task_keywords"]):
            return None

        # Count write-class tool calls by param hash
        write_hash_counts: dict[str, int] = {}
        for rec in self._history:
            for name, phash in zip(rec.tool_names, rec.tool_param_hashes):
                if name in cfg["write_tool_names"]:
                    key = f"{name}::{phash}"
                    write_hash_counts[key] = write_hash_counts.get(key, 0) + 1

        for key, count in write_hash_counts.items():
            if count >= cfg["repeat_write_threshold"]:
                tool_name = key.split("::")[0]
                return (
                    f"'{tool_name}' called {count}× with identical content on a "
                    f"change-requested task — agent may be re-producing the prior pattern"
                )
        return None

    def _detect_s7(self) -> Optional[str]:
        """
        S7 — Coherence Anchoring.
        Fires when: agent uses self-referencing consistency language after tool calls
        (treating prior assertion as evidence), OR text output near-duplicates a prior
        iteration's output despite intervening tool calls.
        Empirical source: @melonclaw, Moltbook 2026-02-28.
        """
        cfg = THRESHOLDS["S7"]
        if len(self._history) < cfg["min_iterations"]:
            return None

        last = self._history[-1]
        if not last.has_text:
            return None

        text = last.text_snippet.lower()

        # Check for anchoring language after tool calls in recent history
        recent_has_tools = any(rec.tool_names for rec in self._history[-3:-1])
        if recent_has_tools:
            for pattern in cfg["anchoring_patterns"]:
                if re.search(pattern, text, re.IGNORECASE):
                    return (
                        "Self-referencing consistency language detected after tool calls "
                        "— agent may be anchoring on prior state rather than updating"
                    )

        # Check for near-duplicate text across iterations (not updating despite tools)
        prior_texts = [
            rec.text_snippet.lower()
            for rec in self._history[:-1]
            if rec.has_text
        ]
        last_words = set(text.split())
        if len(last_words) >= cfg["min_words_for_similarity"]:
            for prior in prior_texts[-3:]:
                prior_words = set(prior.split())
                if not prior_words:
                    continue
                overlap = len(last_words & prior_words) / max(len(last_words), 1)
                if overlap >= cfg["text_similarity_threshold"]:
                    return (
                        f"Text output {overlap:.0%} similar to a prior iteration "
                        f"despite intervening tool calls — possible coherence anchoring"
                    )

        return None

    # ── Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _param_hash(tool_name: str, tool_input: dict) -> str:
        """Short stable hash of tool parameters for repeat-call detection."""
        key = json.dumps(tool_input, sort_keys=True, default=str)
        return hashlib.md5(key.encode()).hexdigest()[:8]
