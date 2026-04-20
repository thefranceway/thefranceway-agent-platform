#!/usr/bin/env python3
"""
Quality Gate — 3-Gate Product Quality Pipeline
================================================
Sequential, blocking quality control for any product being built or shipped.

Gates:
  Gate 1 — Adversary   Does it work? Can it be broken? Does it expose internals?
  Gate 2 — Stranger    Can someone get value in under 5 minutes with no context?
  Gate 3 — Buyer       Would someone pay for this monthly? Is it differentiated?

Each gate must PASS before the next runs.
A FAIL at any gate returns specific failure reasons and blocks shipping.

Usage:
    from core.quality_gate import QualityGate
    gate = QualityGate()
    result = gate.run(product_spec)

    # Or via API: POST /quality-check
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class GateResult:
    gate:     int
    name:     str
    verdict:  str          # "PASS" | "FAIL" | "WARN"
    score:    int          # 0–100
    findings: list[str]    = field(default_factory=list)
    blockers: list[str]    = field(default_factory=list)
    output:   str          = ""

    @property
    def passed(self) -> bool:
        return self.verdict in ("PASS", "WARN")


@dataclass
class QualityReport:
    product_name: str
    verdict:      str          # "SHIP" | "HOLD" | "REJECT"
    score:        int          # weighted average 0–100
    gates:        list[GateResult] = field(default_factory=list)
    timestamp:    str          = ""
    summary:      str          = ""

    def to_dict(self) -> dict:
        return {
            "product":   self.product_name,
            "verdict":   self.verdict,
            "score":     self.score,
            "summary":   self.summary,
            "timestamp": self.timestamp,
            "gates": [
                {
                    "gate":     g.gate,
                    "name":     g.name,
                    "verdict":  g.verdict,
                    "score":    g.score,
                    "findings": g.findings,
                    "blockers": g.blockers,
                }
                for g in self.gates
            ],
        }


# ── Gate prompts ──────────────────────────────────────────────────────────────

GATE1_PROMPT = """You are the Adversary — a hostile but fair technical reviewer.
Your job: find every way this product fails, breaks, or disappoints.

Product spec:
{spec}

Evaluate ruthlessly across these dimensions:
1. Does it work end-to-end? (happy path)
2. Does it handle bad inputs gracefully — empty payloads, wrong types, missing fields?
3. Does it expose internal architecture, stack traces, or sensitive data in error responses?
4. Is auth enforced? Can it be bypassed?
5. Is there a rate limiter? Can it be abused?
6. Does the health check pass?
7. Are error messages clean and informative (not raw exceptions)?

Respond in this exact JSON format:
{{
  "verdict": "PASS" | "FAIL" | "WARN",
  "score": 0-100,
  "findings": ["specific finding 1", "specific finding 2"],
  "blockers": ["blocker that must be fixed before shipping — leave empty if none"]
}}

Be specific. Name exact failure modes. No generic feedback."""


GATE2_PROMPT = """You are the Stranger — you have never seen this product before.
You have only the listing copy and docs provided. You get 5 minutes to get value.

Product spec and docs:
{spec}

Evaluate from first principles:
1. Can you understand what this does in one sentence from the listing?
2. Can you make your first successful API call in under 5 minutes using only the docs?
3. Is the response format self-explanatory — do you know what each field means?
4. Do you need to ask any question before getting output?
5. Is there a working code example you can copy-paste?
6. Would you know what to do if something goes wrong?

Respond in this exact JSON format:
{{
  "verdict": "PASS" | "FAIL" | "WARN",
  "score": 0-100,
  "findings": ["specific finding 1", "specific finding 2"],
  "blockers": ["blocker that must be fixed before shipping — leave empty if none"]
}}

Be the dumbest smart developer possible. If anything requires inference, it fails."""


GATE3_PROMPT = """You are the Buyer — a developer who pays for tools with their own money.
You are skeptical, busy, and have seen a hundred mediocre APIs.

Product spec:
{spec}

Evaluate through the lens of value and differentiation:
1. Does this do something non-trivial to replicate in an afternoon?
2. Is there a clear reason to use this over building it yourself?
3. Is it differentiated from what already exists on RapidAPI and similar marketplaces?
4. Does the price reflect the value delivered — would you pay this monthly, not just once?
5. Is there a reason to come back next month (switching costs, data persistence, improving over time)?
6. Is the value proposition specific enough to remember — or is it forgettable?

Use the angel investor 4-lens framework:
- Credibility: are the claims defensible?
- Clarity: is the value obvious in under 10 seconds?
- Momentum: does the product narrative build confidence?
- Risk flags: anything that breaks trust or invites skepticism?

Respond in this exact JSON format:
{{
  "verdict": "PASS" | "FAIL" | "WARN",
  "score": 0-100,
  "findings": ["specific finding 1", "specific finding 2"],
  "blockers": ["blocker that must be fixed before shipping — leave empty if none"]
}}

Mediocre is not acceptable. If it wouldn't survive a real dev's scrutiny, say so."""


SUMMARY_PROMPT = """You are a senior product quality lead.

Three reviewers have evaluated this product:
- Gate 1 (Adversary / Technical): {g1_verdict} — score {g1_score}/100
- Gate 2 (Stranger / UX): {g2_verdict} — score {g2_score}/100
- Gate 3 (Buyer / Value): {g3_verdict} — score {g3_score}/100

Gate 1 blockers: {g1_blockers}
Gate 2 blockers: {g2_blockers}
Gate 3 blockers: {g3_blockers}

Write a 2-3 sentence executive summary of the product's quality status.
Be direct. State the overall verdict (SHIP / HOLD / REJECT) and the single most important thing to fix.
No fluff. Analyst register."""


# ── Quality Gate engine ───────────────────────────────────────────────────────

class QualityGate:
    """
    Run a product through 3 sequential quality gates.
    Each gate uses a different agent persona to evaluate from a different angle.
    Gates are blocking — a FAIL stops the pipeline.
    """

    def run(self, product_spec: dict) -> QualityReport:
        """
        Run all 3 gates sequentially.
        product_spec keys:
          name        — product name
          description — what it does
          endpoints   — list of endpoint descriptions
          docs        — documentation / readme content
          pricing     — pricing tiers
          live_url    — base URL (optional, for live testing)
        """
        from core.orchestrator import make_agent

        name      = product_spec.get("name", "Unnamed Product")
        spec_text = json.dumps(product_spec, indent=2)
        gates     = []

        # Gate 1 — Adversary (ops agent, Substrate archetype)
        g1 = self._run_gate(
            gate_num  = 1,
            gate_name = "Adversary (Technical)",
            prompt    = GATE1_PROMPT.format(spec=spec_text),
            agent_type= "ops",
        )
        gates.append(g1)
        if not g1.passed:
            return self._build_report(name, gates, forced_verdict="REJECT")

        # Gate 2 — Stranger (content agent, Philosopher archetype)
        g2 = self._run_gate(
            gate_num  = 2,
            gate_name = "Stranger (UX)",
            prompt    = GATE2_PROMPT.format(spec=spec_text),
            agent_type= "content",
        )
        gates.append(g2)
        if not g2.passed:
            return self._build_report(name, gates, forced_verdict="HOLD")

        # Gate 3 — Buyer (meta agent, uses angel investor protocol)
        g3 = self._run_gate(
            gate_num  = 3,
            gate_name = "Buyer (Value)",
            prompt    = GATE3_PROMPT.format(spec=spec_text),
            agent_type= "meta",
        )
        gates.append(g3)

        return self._build_report(name, gates)

    def _run_gate(
        self,
        gate_num:   int,
        gate_name:  str,
        prompt:     str,
        agent_type: str,
    ) -> GateResult:
        """Run a single gate using the specified agent type."""
        try:
            from core.orchestrator import make_agent
            agent  = make_agent(agent_type)
            result = agent.run(prompt)
            output = result.get("output", "")

            # Parse JSON verdict from agent output
            parsed = self._parse_verdict(output)
            return GateResult(
                gate     = gate_num,
                name     = gate_name,
                verdict  = parsed.get("verdict", "FAIL"),
                score    = int(parsed.get("score", 0)),
                findings = parsed.get("findings", []),
                blockers = parsed.get("blockers", []),
                output   = output,
            )
        except Exception as e:
            return GateResult(
                gate     = gate_num,
                name     = gate_name,
                verdict  = "FAIL",
                score    = 0,
                blockers = [f"Gate evaluation error: {str(e)}"],
                output   = str(e),
            )

    def _parse_verdict(self, text: str) -> dict:
        """Extract JSON verdict block from agent output."""
        import re
        # Try to find JSON block in output
        match = re.search(r'\{[\s\S]*?"verdict"[\s\S]*?\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Fallback: look for verdict keyword
        if "PASS" in text.upper():
            return {"verdict": "PASS", "score": 70, "findings": [], "blockers": []}
        if "WARN" in text.upper():
            return {"verdict": "WARN", "score": 55, "findings": [], "blockers": []}
        return {"verdict": "FAIL", "score": 0, "findings": [text[:300]], "blockers": ["Could not parse gate output"]}

    def _build_report(
        self,
        name:           str,
        gates:          list[GateResult],
        forced_verdict: Optional[str] = None,
    ) -> QualityReport:
        """Assemble the final quality report."""
        scores = [g.score for g in gates]
        avg    = int(sum(scores) / len(scores)) if scores else 0

        if forced_verdict:
            verdict = forced_verdict
        elif avg >= 75 and all(g.passed for g in gates):
            verdict = "SHIP"
        elif avg >= 50:
            verdict = "HOLD"
        else:
            verdict = "REJECT"

        # Build summary using the gate results
        summary = self._build_summary(gates, verdict)

        return QualityReport(
            product_name = name,
            verdict      = verdict,
            score        = avg,
            gates        = gates,
            timestamp    = datetime.now(timezone.utc).isoformat(),
            summary      = summary,
        )

    def _build_summary(self, gates: list[GateResult], verdict: str) -> str:
        """Generate a plain-language summary of the quality report."""
        blockers = []
        for g in gates:
            blockers.extend(g.blockers)

        if not blockers:
            return f"Verdict: {verdict}. All gates passed with no blockers."

        top_blocker = blockers[0] if blockers else "No specific blocker identified."
        gate_names  = [g.name for g in gates if not g.passed]
        failed_str  = ", ".join(gate_names) if gate_names else "unknown gate"

        return (
            f"Verdict: {verdict}. "
            f"Failed at: {failed_str}. "
            f"Primary blocker: {top_blocker}"
        )


# ── Module-level singleton ─────────────────────────────────────────────────────

_gate: Optional[QualityGate] = None


def get_quality_gate() -> QualityGate:
    global _gate
    if _gate is None:
        _gate = QualityGate()
    return _gate
