#!/usr/bin/env python3
"""
Agent Platform — SPAR Pre-Execution Stress-Test
=================================================
Runs a 2-round dialectical debate between ChallengerAgent and PragmatistAgent
before a high-stakes task is dispatched. Surfaces gaps upstream, before API
credits and time are spent on a bad plan.

Pattern: structured disagreement (not consensus) — based on SPAR-Kit.

Round 1:  Challenger attacks the task → Pragmatist stress-tests feasibility
Round 2:  Challenger responds to Pragmatist gaps → synthesis
Output:   {proceed: bool, gaps: list, recommendation: str}

Usage:
    from core.spar import SPARDebater
    debater = SPARDebater(orchestrator)
    result  = debater.run("build a multi-tenant auth system on Cloudflare Workers")
    if result["proceed"]:
        orchestrator.dispatch(task)
"""

import json
import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import os

PLATFORM_DIR = Path(__file__).parent.parent
DB_PATH      = PLATFORM_DIR / "registry" / "agent_platform.db"

CHALLENGER_PROMPT = (
    "You are the Challenger in a SPAR pre-execution review. "
    "Surface what could go wrong BEFORE this task is executed. "
    "Attack the plan: find hidden assumptions, unstated dependencies, "
    "edge cases, and worst-case failure modes. "
    "Be specific and adversarial. Do NOT offer solutions — only expose gaps. "
    "Respond with a numbered list of risks. Maximum 5 items."
)

PRAGMATIST_PROMPT = (
    "You are the Pragmatist in a SPAR pre-execution review. "
    "Given the task and the Challenger's risks, assess execution feasibility. "
    "Can this be built with available tools and time? Are the constraints realistic? "
    "Flag anything that turns a 1-day build into a 2-week slog. "
    "Be concrete — reference specific tools or constraints. "
    "Respond with: (1) Feasibility verdict: GO / CAUTION / STOP, "
    "(2) top 3 execution risks, (3) recommended scope reduction if needed."
)

SYNTHESIS_PROMPT = (
    "You are synthesizing a SPAR pre-execution debate. "
    "Given the task, the Challenger's risks, and the Pragmatist's feasibility verdict, "
    "produce a final structured assessment:\n"
    "- proceed: true/false\n"
    "- gaps: list of critical unknowns that must be resolved before building\n"
    "- recommendation: one sentence on what to do next\n"
    "Respond in valid JSON only, no markdown."
)


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _write_signal(spar_id, from_agent, to_agent, event_type, payload):
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO signals (id, from_agent, to_agent, swarm_id, event_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), from_agent, to_agent or "spar",
                spar_id, event_type, json.dumps(payload),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # signals are observability, never block execution


class SPARDebater:
    """
    Two-round dialectical stress-test before task execution.
    Uses Haiku for both agents — cheap gate, not a builder.
    """

    def __init__(self, orchestrator=None):
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set.")
        self.client = anthropic.Anthropic(api_key=key)
        self.model  = "claude-haiku-4-5-20251001"

    def _call(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model      = self.model,
            max_tokens = 512,
            system     = system,
            messages   = [{"role": "user", "content": user}],
        )
        return response.content[0].text.strip()

    def run(self, task: str, verbose: bool = True) -> dict:
        """
        Run 2-round SPAR debate on a task.

        Returns:
            {
                "spar_id":        str,
                "task":           str,
                "proceed":        bool,
                "gaps":           list[str],
                "recommendation": str,
                "challenger_r1":  str,
                "pragmatist_r1":  str,
                "challenger_r2":  str,
                "raw_synthesis":  str,
            }
        """
        spar_id = str(uuid.uuid4())

        if verbose:
            print(f"\n[SPAR] Starting pre-execution review — {spar_id[:8]}")
            print(f"[SPAR] Task: {task[:100]}")

        # ── Round 1: Challenger attacks ────────────────────────────────────────
        challenger_r1 = self._call(
            CHALLENGER_PROMPT,
            f"Task to stress-test:\n{task}"
        )
        if verbose:
            print(f"\n[SPAR:Challenger R1]\n{challenger_r1}")
        _write_signal(spar_id, "challenger", "pragmatist", "challenge_r1",
                      {"task": task, "output": challenger_r1})

        # ── Round 1: Pragmatist assesses feasibility ───────────────────────────
        pragmatist_r1 = self._call(
            PRAGMATIST_PROMPT,
            f"Task:\n{task}\n\nChallenger's risks:\n{challenger_r1}"
        )
        if verbose:
            print(f"\n[SPAR:Pragmatist R1]\n{pragmatist_r1}")
        _write_signal(spar_id, "pragmatist", "challenger", "feasibility_r1",
                      {"output": pragmatist_r1})

        # ── Round 2: Challenger responds to Pragmatist ─────────────────────────
        challenger_r2 = self._call(
            CHALLENGER_PROMPT,
            f"Task:\n{task}\n\n"
            f"Pragmatist's feasibility verdict:\n{pragmatist_r1}\n\n"
            f"Are there additional risks the Pragmatist missed or understated? "
            f"Keep it to 3 items max."
        )
        if verbose:
            print(f"\n[SPAR:Challenger R2]\n{challenger_r2}")
        _write_signal(spar_id, "challenger", "synthesis", "challenge_r2",
                      {"output": challenger_r2})

        # ── Synthesis ──────────────────────────────────────────────────────────
        synthesis_input = (
            f"Task: {task}\n\n"
            f"Challenger R1:\n{challenger_r1}\n\n"
            f"Pragmatist R1:\n{pragmatist_r1}\n\n"
            f"Challenger R2:\n{challenger_r2}"
        )
        raw_synthesis = self._call(SYNTHESIS_PROMPT, synthesis_input)
        if verbose:
            print(f"\n[SPAR:Synthesis]\n{raw_synthesis}")

        # Parse synthesis JSON
        try:
            synthesis = json.loads(raw_synthesis)
            proceed        = bool(synthesis.get("proceed", True))
            gaps           = synthesis.get("gaps", [])
            recommendation = synthesis.get("recommendation", "Proceed with caution.")
        except (json.JSONDecodeError, AttributeError):
            # Fallback: CAUTION if synthesis fails to parse
            proceed        = True
            gaps           = ["Synthesis parse failed — review manually"]
            recommendation = "SPAR synthesis inconclusive. Review risks before proceeding."

        _write_signal(spar_id, "synthesis", None, "spar_complete",
                      {"proceed": proceed, "gaps": gaps, "recommendation": recommendation})

        if verbose:
            status = "GO" if proceed else "STOP"
            print(f"\n[SPAR] Result: {status} | Gaps: {len(gaps)} | {recommendation}\n")

        return {
            "spar_id":        spar_id,
            "task":           task,
            "proceed":        proceed,
            "gaps":           gaps,
            "recommendation": recommendation,
            "challenger_r1":  challenger_r1,
            "pragmatist_r1":  pragmatist_r1,
            "challenger_r2":  challenger_r2,
            "raw_synthesis":  raw_synthesis,
        }
