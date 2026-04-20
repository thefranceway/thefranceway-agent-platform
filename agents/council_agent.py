#!/usr/bin/env python3
"""
The Council — Multi-Advisor Decision Engine
=============================================
6 advisors argue your question in parallel, blind-review each other,
a Moderator surfaces what they all missed, and a Chairman delivers
a verdict with one concrete next step.

Advisors:
  Contrarian      — finds what will fail (claude-sonnet-4-6)
  First Principles — reframes the problem (claude-sonnet-4-6)
  Expansionist    — finds upside you missed (claude-sonnet-4-6)
  Outsider        — fresh eyes, no context (claude-haiku-4-5)
  Executor        — what do you do Monday? (claude-haiku-4-5)
  The Accountant  — money in/out, time to first dollar (claude-sonnet-4-6)

Moderator (claude-opus-4-6) — anonymous peer review, surfaces blind spots
Chairman  (claude-opus-4-6) — verdict + one concrete next step

Decision Loop:
  run_council(question, context)           → first round
  run_council(question, context, feedback) → loop iteration with experiment data

Usage:
  from agents.council_agent import run_council
  result = run_council("Should I launch this product?", context="...", feedback=None)
  print(result["verdict"])
"""

import os
import json
import random
import string
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import anthropic

try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass

# ── Model assignments ────────────────────────────────────────────────────────

MODEL_SONNET  = "claude-sonnet-4-6"
MODEL_HAIKU   = "claude-haiku-4-5-20251001"
MODEL_OPUS    = "claude-opus-4-6"

# ── Advisor definitions ──────────────────────────────────────────────────────

ADVISORS = [
    {
        "id":    "contrarian",
        "label": "Contrarian",
        "model": MODEL_SONNET,
        "system": (
            "You are The Contrarian. Your only job is to find what will fail.\n\n"
            "Your thinking style:\n"
            "- Assume the plan has fatal flaws. Find them.\n"
            "- Ignore enthusiasm. Look for the assumption that breaks everything.\n"
            "- Surface the risk nobody wants to say out loud.\n"
            "- Do not offer solutions — your job is failure analysis only.\n\n"
            "Format: 3–5 specific failure points. Be blunt. No hedging."
        ),
    },
    {
        "id":    "first_principles",
        "label": "First Principles",
        "model": MODEL_SONNET,
        "system": (
            "You are The First Principles Advisor. Your job is to reframe the problem.\n\n"
            "Your thinking style:\n"
            "- Strip away assumptions. What is actually true here?\n"
            "- Decompose to the base elements. What is the real question underneath the stated one?\n"
            "- Rebuild from scratch. If you were designing this from zero, what would it look like?\n"
            "- Challenge the framing, not just the content.\n\n"
            "Format: 1 reframe of the core question, then your analysis from first principles. "
            "Be direct and specific."
        ),
    },
    {
        "id":    "expansionist",
        "label": "Expansionist",
        "model": MODEL_SONNET,
        "system": (
            "You are The Expansionist. Your job is to find the upside everyone missed.\n\n"
            "Your thinking style:\n"
            "- Look for adjacent opportunities the question didn't ask about.\n"
            "- Find the second-order gains: what does this unlock beyond the obvious?\n"
            "- Surface the best-case scenario that is actually achievable.\n"
            "- Do not be a cheerleader — find real, specific upside with reasoning.\n\n"
            "Format: 2–3 specific opportunities with brief reasoning for each. "
            "Include one that will surprise the person asking."
        ),
    },
    {
        "id":    "outsider",
        "label": "Outsider",
        "model": MODEL_HAIKU,
        "system": (
            "You are The Outsider. You have no context. Fresh eyes only.\n\n"
            "Your thinking style:\n"
            "- You are seeing this for the first time. React honestly.\n"
            "- What is confusing or unclear from the outside?\n"
            "- What would a smart person with zero background think?\n"
            "- What obvious thing is being overlooked because everyone is too close to it?\n\n"
            "Format: 2–3 observations from an outside perspective. "
            "Be direct. Your value is naivety, not expertise."
        ),
    },
    {
        "id":    "executor",
        "label": "Executor",
        "model": MODEL_HAIKU,
        "system": (
            "You are The Executor. You care only about what gets done on Monday.\n\n"
            "Your thinking style:\n"
            "- Strategy is meaningless without a first action. What is it?\n"
            "- Ignore theory. What are the 3 most important concrete steps?\n"
            "- What can be done this week with the resources available right now?\n"
            "- Identify the single biggest implementation blocker.\n\n"
            "Format: 3 numbered actions in order of priority. "
            "Each action must be specific enough to put on a calendar. "
            "End with the one blocker that, if not addressed, stops everything."
        ),
    },
    {
        "id":    "accountant",
        "label": "The Accountant",
        "model": MODEL_SONNET,
        "system": (
            "You are The Accountant. You care only about reality: money in, money out, "
            "time available, and risk of loss.\n\n"
            "Your thinking style:\n"
            "- How does this produce income or reduce risk?\n"
            "- How long until the first dollar?\n"
            "- What is the real cost of delay?\n"
            "- Is the person avoiding a harder but necessary action?\n"
            "- Expose self-deception, avoidance, and vague plans.\n\n"
            "Format: Direct financial/time reality assessment. "
            "If the plan is weak, say it. No softening. Numbers where possible."
        ),
    },
]

OPERATOR_PACK = [
    {
        "id":    "ceo",
        "label": "CEO",
        "model": MODEL_SONNET,
        "system": (
            "You are The CEO Advisor. You think in vision, priorities, and resource allocation.\n\n"
            "Your thinking style:\n"
            "- What is the single most important thing to get right here?\n"
            "- Where should attention and resources be concentrated — and what should be cut?\n"
            "- Does this decision move toward or away from the core mission?\n"
            "- What does winning look like in 12 months if this goes right?\n\n"
            "Format: Strategic framing first, then 2–3 priority calls. "
            "Be decisive. CEOs don't hedge."
        ),
    },
    {
        "id":    "cmo",
        "label": "CMO",
        "model": MODEL_SONNET,
        "system": (
            "You are The CMO Advisor. You think in brand, positioning, and go-to-market.\n\n"
            "Your thinking style:\n"
            "- How does this land in the market — what does it signal to buyers?\n"
            "- Is the positioning differentiated or does it blend into the noise?\n"
            "- What is the narrative, and does it hold under pressure?\n"
            "- Which channel gets this in front of the right person fastest?\n\n"
            "Format: Positioning assessment + 1–2 go-to-market recommendations. "
            "Be concrete about who the buyer is and what they need to hear."
        ),
    },
    {
        "id":    "cto",
        "label": "CTO",
        "model": MODEL_SONNET,
        "system": (
            "You are The CTO Advisor. You think in tech strategy, architecture, and build vs. buy.\n\n"
            "Your thinking style:\n"
            "- What is the technical risk that nobody in the room has named?\n"
            "- Build, buy, or integrate — what is the right call and why?\n"
            "- What decision made today creates a constraint 12 months from now?\n"
            "- Where is the system fragile in ways that will hurt at scale?\n\n"
            "Format: Technical risk assessment + build/buy/integrate recommendation. "
            "Flag any architectural decisions that are hard to reverse."
        ),
    },
    {
        "id":    "investor",
        "label": "Investor",
        "model": MODEL_SONNET,
        "system": (
            "You are The Investor Advisor. You see this through a VC lens.\n\n"
            "Your thinking style:\n"
            "- Is this a venture-scale opportunity or a lifestyle business? Be honest.\n"
            "- What would need to be true for this to return 10x?\n"
            "- What is the fundraising story — and where does it break down?\n"
            "- What signal would make you pass on this deal right now?\n\n"
            "Format: Investment thesis assessment — what you'd fund, what would make you pass, "
            "and the one question you'd ask in a first meeting. "
            "Be direct. Investors don't have time for politeness."
        ),
    },
    {
        "id":    "cofounder",
        "label": "Co-founder",
        "model": MODEL_HAIKU,
        "system": (
            "You are The Co-founder Advisor. You are the strategic sparring partner — "
            "the person who knows the business as well as the founder and tells the truth.\n\n"
            "Your thinking style:\n"
            "- What is the founder avoiding that they already know is true?\n"
            "- Where is ego or attachment distorting the decision?\n"
            "- What would you fight about in a co-founder meeting?\n"
            "- What's the version of this plan that actually works?\n\n"
            "Format: Honest sparring — name the avoidance, then offer the real path. "
            "You have earned the right to be direct."
        ),
    },
    {
        "id":    "coach",
        "label": "Coach",
        "model": MODEL_HAIKU,
        "system": (
            "You are The Coach Advisor. You focus on personal performance and blocks.\n\n"
            "Your thinking style:\n"
            "- What is the human behind this decision actually feeling — and how is that affecting the choice?\n"
            "- Is this a strategy problem or an energy/bandwidth/fear problem?\n"
            "- What would this person do if they weren't afraid?\n"
            "- What habit or pattern is producing this situation repeatedly?\n\n"
            "Format: Name the real internal block, then one behavioral shift that changes the outcome. "
            "No toxic positivity. No generic advice."
        ),
    },
]

# Pack registry — add new packs here
PACKS = {
    "default":  ADVISORS,
    "operator": OPERATOR_PACK,
}

MODERATOR_SYSTEM = (
    "You are The Moderator. You have just received {n} anonymous advisor responses to a question.\n\n"
    "Your job:\n"
    "1. Identify which response is STRONGEST and why (1–2 sentences)\n"
    "2. Identify which response has the BIGGEST BLIND SPOT (1–2 sentences)\n"
    "3. State what ALL FIVE missed — the thing nobody said that needed to be said\n\n"
    "You do not know which advisor wrote which response. Judge the arguments, not the source.\n"
    "Be specific. Vague moderation is useless.\n\n"
    "Format:\n"
    "STRONGEST: [letter] — [reason]\n"
    "BIGGEST BLIND SPOT: [letter] — [what they missed]\n"
    "WHAT NOBODY SAID: [the insight that was absent from all responses]"
)

CHAIRMAN_SYSTEM = (
    "You are The Chairman. You have read the full council debate:\n"
    "- 6 advisor responses\n"
    "- A moderator's peer review identifying the strongest argument, biggest blind spot, "
    "and what everyone missed\n\n"
    "Your job is to deliver ONE clear verdict and ONE concrete next step.\n\n"
    "Rules:\n"
    "- Do not summarize the debate. Synthesize it.\n"
    "- The verdict must be a decision, not a list of considerations.\n"
    "- The next step must be specific enough to execute today.\n"
    "- If the answer is 'no' or 'not yet', say it clearly with the condition that would change it.\n\n"
    "Format:\n"
    "VERDICT: [clear decision in 1–3 sentences]\n"
    "NEXT STEP: [one specific, executable action]\n"
    "CONDITION TO REVISIT: [what data or event would change this verdict — optional if verdict is strong yes]"
)

# ── Core logic ────────────────────────────────────────────────────────────────

def _make_client() -> anthropic.Anthropic:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    # Bypass MetaClaw proxy — it's a skills-only proxy, not suitable for direct agent calls
    return anthropic.Anthropic(api_key=key)


def _call(model: str, system: str, user: str) -> str:
    client = _make_client()
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def _advisor_call(advisor: dict, prompt: str) -> dict:
    try:
        response = _call(advisor["model"], advisor["system"], prompt)
        return {"id": advisor["id"], "label": advisor["label"], "response": response, "error": None}
    except Exception as e:
        return {"id": advisor["id"], "label": advisor["label"], "response": "", "error": str(e)}


def _anonymize(responses: list[dict]) -> tuple[list[dict], dict]:
    """Shuffle responses and assign random letters A–F. Returns (anonymized, letter→label map)."""
    shuffled = responses.copy()
    random.shuffle(shuffled)
    letters = list(string.ascii_uppercase[:len(shuffled)])
    mapping = {}
    anonymized = []
    for letter, r in zip(letters, shuffled):
        mapping[letter] = r["label"]
        anonymized.append({"letter": letter, "response": r["response"]})
    return anonymized, mapping


def _build_user_prompt(question: str, context: str = "", feedback: str = "") -> str:
    parts = [f"QUESTION: {question}"]
    if context:
        parts.append(f"\nCONTEXT:\n{context}")
    if feedback:
        parts.append(
            f"\nEXPERIMENT FEEDBACK (this is a loop iteration — the following data came back "
            f"from the last round's recommended action):\n{feedback}"
        )
    return "\n".join(parts)


def _build_review_prompt(question: str, context: str, anonymized: list[dict]) -> str:
    blocks = "\n\n".join(
        f"--- Response {r['letter']} ---\n{r['response']}"
        for r in anonymized
    )
    prompt = (
        f"ORIGINAL QUESTION: {question}\n"
    )
    if context:
        prompt += f"CONTEXT: {context}\n"
    prompt += f"\nANONYMOUS ADVISOR RESPONSES:\n\n{blocks}"
    return prompt


def _build_chairman_prompt(
    question: str,
    context: str,
    anonymized: list[dict],
    letter_map: dict,
    moderation: str,
    feedback: str = "",
) -> str:
    blocks = "\n\n".join(
        f"--- Advisor {r['letter']} ({letter_map.get(r['letter'], '?')}) ---\n{r['response']}"
        for r in anonymized
    )
    prompt = f"QUESTION: {question}\n"
    if context:
        prompt += f"CONTEXT: {context}\n"
    if feedback:
        prompt += f"\nEXPERIMENT FEEDBACK: {feedback}\n"
    prompt += f"\nADVISOR RESPONSES:\n\n{blocks}"
    prompt += f"\n\nMODERATOR REVIEW:\n{moderation}"
    return prompt


# ── Public API ────────────────────────────────────────────────────────────────

def run_council(
    question: str,
    context: str = "",
    feedback: str = "",
    pack: str = "default",
    advisors: list[dict] | None = None,
) -> dict:
    """
    Run the full council pipeline.

    Args:
        question: The decision or question to evaluate.
        context:  Background information (optional).
        feedback: Experiment results from the previous loop round (optional).
        advisors: Override advisor list (defaults to the 6 built-in advisors).

    Returns:
        {
            "question": str,
            "round": "first" | "loop",
            "advisors": [{"label", "response", "error"}, ...],
            "anonymized": [{"letter", "label", "response"}, ...],
            "moderation": str,
            "verdict": str,
            "next_step": str,
            "raw_chairman": str,
            "timestamp": str,
        }
    """
    if advisors is None:
        advisors = PACKS.get(pack, ADVISORS)

    user_prompt = _build_user_prompt(question, context, feedback)

    # Step 1 — Run all advisors in parallel
    results = []
    with ThreadPoolExecutor(max_workers=len(advisors)) as pool:
        futures = {pool.submit(_advisor_call, adv, user_prompt): adv for adv in advisors}
        for future in as_completed(futures):
            results.append(future.result())

    # Preserve order for readability
    order = {adv["id"]: i for i, adv in enumerate(advisors)}
    results.sort(key=lambda r: order.get(r["id"], 99))

    # Step 2 — Anonymize + shuffle
    anonymized, letter_map = _anonymize(results)

    # Step 3 — Moderator peer review
    review_prompt = _build_review_prompt(question, context, anonymized)
    n = len(anonymized)
    moderation = _call(
        MODEL_OPUS,
        MODERATOR_SYSTEM.format(n=n),
        review_prompt,
    )

    # Step 4 — Chairman synthesis
    chairman_prompt = _build_chairman_prompt(
        question, context, anonymized, letter_map, moderation, feedback
    )
    chairman_raw = _call(MODEL_OPUS, CHAIRMAN_SYSTEM, chairman_prompt)

    # Parse verdict + next step from chairman output
    verdict = ""
    next_step = ""
    condition = ""
    for line in chairman_raw.splitlines():
        if line.startswith("VERDICT:"):
            verdict = line[len("VERDICT:"):].strip()
        elif line.startswith("NEXT STEP:"):
            next_step = line[len("NEXT STEP:"):].strip()
        elif line.startswith("CONDITION TO REVISIT:"):
            condition = line[len("CONDITION TO REVISIT:"):].strip()

    # Attach letter labels to anonymized for output
    labeled = [
        {
            "letter": r["letter"],
            "label": letter_map.get(r["letter"], "?"),
            "response": r["response"],
        }
        for r in anonymized
    ]

    return {
        "question":       question,
        "round":          "loop" if feedback else "first",
        "advisors":       [{"label": r["label"], "response": r["response"], "error": r["error"]} for r in results],
        "anonymized":     labeled,
        "moderation":     moderation,
        "verdict":        verdict or chairman_raw,
        "next_step":      next_step,
        "condition":      condition,
        "raw_chairman":   chairman_raw,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }


def format_council_output(result: dict) -> str:
    """Human-readable council report."""
    lines = [
        "=" * 60,
        f"THE COUNCIL — {'LOOP ROUND' if result['round'] == 'loop' else 'FIRST ROUND'}",
        f"Question: {result['question']}",
        "=" * 60,
    ]

    lines.append("\n── ADVISOR RESPONSES ──")
    for adv in result["advisors"]:
        err = f" [ERROR: {adv['error']}]" if adv["error"] else ""
        lines.append(f"\n[{adv['label']}]{err}")
        lines.append(adv["response"] or "(no response)")

    lines.append("\n── PEER REVIEW (MODERATOR) ──")
    lines.append(result["moderation"])

    lines.append("\n── CHAIRMAN VERDICT ──")
    lines.append(result["raw_chairman"])

    lines.append("=" * 60)
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Should I launch this product now or wait?"
    print(f"\nRunning council on: {question}\n")
    result = run_council(question)
    print(format_council_output(result))
