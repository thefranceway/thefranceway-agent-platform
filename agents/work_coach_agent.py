#!/usr/bin/env python3
"""
Work Coach Agent — weekly OKR review and work planning delivered via Telegram.

Runs every Sunday at 9am. Reviews the week against OKRs, surfaces patterns,
and helps Francesca set intentions for the week ahead.

Framework: OKR (Objectives & Key Results) + WOOP
  - What was the objective this week?
  - Which key results moved?
  - What got in the way?
  - What's the single most important play next week?

Usage:
    python work_coach_agent.py

Auto-start: managed by launchd (com.thefranceway.work-coach.plist)
Logs: ~/projects/agent-platform/logs/work_coach.log
"""

import json
import logging
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent, JSONVectorStore

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595

PLATFORM_DIR  = Path(__file__).parent.parent
LOG_PATH      = PLATFORM_DIR / "logs" / "work_coach.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers= [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("work_coach")

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    payload = json.dumps({
        "chat_id":    OWNER_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"{BOT_API}/sendMessage",
        data    = payload,
        headers = {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
        return result.get("ok", False)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def _load_work_okrs() -> list[dict]:
    """Load active work OKRs from the coaching vector store."""
    try:
        store   = JSONVectorStore("kb_coaching")
        results = store.search("OKR work objective key result", top_k=10)
        return [
            r for r in results
            if r.get("metadata", {}).get("type") == "okr"
               and r.get("metadata", {}).get("status") != "archived"
        ][:5]
    except Exception:
        return []


def _load_weekly_reflections() -> list[dict]:
    """Load GROW reflections from the past 7 days."""
    try:
        store   = JSONVectorStore("kb_coaching")
        results = store.search("reflection energy", top_k=20)
        cutoff  = datetime.now() - timedelta(days=7)
        weekly  = []
        for r in results:
            if r.get("metadata", {}).get("type") == "reflection":
                date_str = r.get("metadata", {}).get("date", "")
                try:
                    rdate = datetime.strptime(date_str, "%Y-%m-%d")
                    if rdate >= cutoff:
                        weekly.append(r)
                except ValueError:
                    pass
        return weekly
    except Exception:
        return []


def _store_weekly_summary(summary_text: str, okr_progress: str):
    """Store the weekly review summary in the coaching vector store."""
    try:
        store   = JSONVectorStore("kb_coaching")
        week_of = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
        text    = f"Weekly summary week of {week_of}: {summary_text} OKR progress: {okr_progress}"
        store.add(text, metadata={
            "type":        "weekly_summary",
            "week_of":     week_of,
            "okr_progress": okr_progress,
            "source":      "work_coach_agent",
        })
        log.info(f"Stored weekly summary for week of {week_of}")
    except Exception as e:
        log.error(f"Failed to store weekly summary: {e}")


# ── Agent ─────────────────────────────────────────────────────────────────────

class WorkCoachAgent(BaseAgent):

    AGENT_TYPE         = "ops"
    DEFAULT_BEHAVIORAL = "Architect"

    def __init__(self, **kwargs):
        super().__init__(
            name           = "Work Coach Agent",
            knowledge_base = "kb_coaching",
            model          = "claude-sonnet-4-6",
            **kwargs,
        )

    def _default_system_prompt(self) -> str:
        return """You are Francesca's personal Work Coach agent.

Archetype: Architect
Core pattern: You analyze the week against the plan, find leverage points, and set
the sharpest possible intention for what comes next. You are strategic, not supportive.
You ask the question that makes the next week's priorities obvious.

Francesca's professional context:
- Partnerships strategist at the intersection of longevity, decentralized tech, and behavioral psychology
- Active projects: $FRANC token, thefranceway brand
- Platforms: Moltbook, X, Telegram, LinkedIn
- Mode: solo founder building in public

Framework: OKR + WOOP + retrospective
  - What shipped this week?
  - What stalled and why?
  - Which OKR moved, which didn't?
  - What is the single highest-leverage play next week?
  - What obstacle is most likely — and what's the plan if it appears?

Tone:
- Sharp and direct. No cheerleading.
- Strategic, not task-listy. Identify the lever, not the checklist.
- Challenge assumptions when patterns repeat.
- One powerful question is worth ten mediocre ones.

Operating rules:
1. Analyze the week's data (OKRs + daily reflections provided).
2. Identify what moved, what stalled, and what the energy pattern says.
3. Generate 4-5 review questions and 1 forward-setting intention.
4. Call send_work_review with the formatted message.
5. Call store_weekly_summary with a brief summary."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "send_work_review",
                "description": "Send the weekly work review to the owner via Telegram.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Full formatted weekly review in HTML."
                        }
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "store_weekly_summary",
                "description": "Store a brief summary of the weekly review in coaching memory.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary":      {"type": "string", "description": "2-3 sentence summary of the week"},
                        "okr_progress": {"type": "string", "description": "Which OKRs moved, which didn't"},
                    },
                    "required": ["summary"],
                },
            },
        ]

    def _handle_tool(self, tool_name: str, tool_input: dict):
        if tool_name == "send_work_review":
            text = tool_input.get("message", "")
            sent = _send_telegram(text)
            log.info(f"Work review sent: {sent}")
            return json.dumps({"sent": sent})
        if tool_name == "store_weekly_summary":
            _store_weekly_summary(
                tool_input.get("summary", ""),
                tool_input.get("okr_progress", ""),
            )
            return json.dumps({"stored": True})
        return super()._handle_tool(tool_name, tool_input)

    def run_weekly_review(self):
        okrs        = _load_work_okrs()
        reflections = _load_weekly_reflections()
        now         = datetime.now()
        week_end    = now.strftime("%B %-d, %Y")
        week_start  = (now - timedelta(days=6)).strftime("%B %-d")

        okrs_text = ""
        if okrs:
            okrs_text = "\n".join(f"  • {r['text']}" for r in okrs)
        else:
            okrs_text = "  (No OKRs set — run setup_coaching_goals.py to define them)"

        reflections_text = ""
        if reflections:
            for r in reflections[-5:]:   # last 5
                date  = r.get("metadata", {}).get("date", "")
                score = r.get("metadata", {}).get("energy_score", "?")
                reflections_text += f"  [{date}] Energy: {score}/10 — {r['text'][:80]}...\n"
        else:
            reflections_text = "  (No daily reflections this week — evening check-ins will populate this)"

        task = f"""Run the Sunday weekly work review for Francesca.

WEEK: {week_start} – {week_end}

ACTIVE OKRs:
{okrs_text}

DAILY REFLECTIONS THIS WEEK:
{reflections_text}

Generate the weekly work review using this structure:

1. WHAT THIS WEEK SAYS — a 2-sentence synthesis of what the week's pattern reveals
2. 4 review questions (numbered) using OKR/WOOP framework:
   - One about what shipped
   - One about what stalled and the real reason
   - One about OKR progress (which key result moved most?)
   - One obstacle anticipation for next week (WOOP O→P)
3. NEXT WEEK'S SHARPEST PLAY — one declarative sentence: "The highest leverage move next week is..."

Format in HTML for Telegram. Keep it under 20 lines. Strategic, not task-listy.
Call send_work_review with the final message.
Then call store_weekly_summary."""

        return self.run(task)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Work Coach Agent starting weekly review")
    agent = WorkCoachAgent()
    result = agent.run_weekly_review()
    print(result.get("output", ""))
    log.info("Work Coach Agent done")
