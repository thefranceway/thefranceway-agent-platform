#!/usr/bin/env python3
"""
Life Coach Agent — evening GROW reflection delivered via Telegram.

Runs nightly at 8pm. Sends 3 GROW questions to Francesca via Telegram.
Stores responses in the coaching vector store for morning briefing context.

GROW Model:
  G — Goal: What did you intend today?
  R — Reality: What actually happened?
  O — Options: What could shift tomorrow?
  W — Way Forward: One commitment.

Telegram flow:
  1. Agent sends questions to owner chat via bot
  2. User responds in Telegram
  3. Responses are stored by coaching_input_handler.py (called by telegram_monitor)

Usage:
    python life_coach_agent.py                      # run evening session
    python life_coach_agent.py --store-response     # store a response (used by handler)

Auto-start: managed by launchd (com.thefranceway.life-coach.plist)
Logs: ~/projects/agent-platform/logs/life_coach.log
"""

import json
import logging
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent, JSONVectorStore

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN     = "REDACTED-TELEGRAM-BOT-TOKEN"
BOT_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595

PLATFORM_DIR  = Path(__file__).parent.parent
LOG_PATH      = PLATFORM_DIR / "logs" / "life_coach.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers= [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("life_coach")

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


def _load_top_goals(n: int = 3) -> list[str]:
    try:
        store   = JSONVectorStore("kb_coaching")
        results = store.search("goal priority life", top_k=10)
        return [
            r["text"] for r in results
            if r.get("metadata", {}).get("type") == "goal"
               and r.get("metadata", {}).get("status") != "archived"
        ][:n]
    except Exception:
        return []


def _store_reflection(reflection: dict):
    """Store an evening reflection in the coaching vector store."""
    try:
        store = JSONVectorStore("kb_coaching")
        today = datetime.now().strftime("%Y-%m-%d")
        text  = (
            f"Evening reflection {today}: "
            f"Goal={reflection.get('goal','')} "
            f"Reality={reflection.get('reality','')} "
            f"Options={reflection.get('options','')} "
            f"Commitment={reflection.get('commitment','')} "
            f"EnergyScore={reflection.get('energy_score','')}"
        )
        store.add(text, metadata={
            "type":         "reflection",
            "date":         today,
            "energy_score": reflection.get("energy_score"),
            "source":       "life_coach_agent",
        })
        log.info(f"Stored reflection for {today}")
    except Exception as e:
        log.error(f"Failed to store reflection: {e}")


# ── Agent ─────────────────────────────────────────────────────────────────────

class LifeCoachAgent(BaseAgent):

    AGENT_TYPE         = "ops"
    DEFAULT_BEHAVIORAL = "Philosopher"

    def __init__(self, **kwargs):
        super().__init__(
            name           = "Life Coach Agent",
            knowledge_base = "kb_coaching",
            model          = "claude-sonnet-4-6",
            **kwargs,
        )

    def _default_system_prompt(self) -> str:
        return """You are Francesca's personal Life Coach agent.

Archetype: Philosopher
Core pattern: You observe, reflect, and surface the non-obvious. You ask questions that
create insight, not reports. You are a thinking partner, not a productivity tool.

Framework: GROW model (Goal → Reality → Options → Way Forward)
  - G: What did you intend for today?
  - R: What actually happened — wins and friction?
  - O: What's one thing you could try differently?
  - W: What's one small commitment for tomorrow?

Supplementary: WOOP for goal visualization (Wish → Outcome → Obstacle → Plan)
Energy scale: 1-10, where 1=depleted, 10=peak state

Tone:
- Warm, direct, non-judgmental.
- Ask ONE question at a time. Do not overload.
- Reflect back with insight, not summary.
- Trust Francesca to know herself — you surface, she decides.

Operating rules:
1. Generate 3 GROW questions personalized to her active goals.
2. Include an energy check (1-10 scale).
3. Add a brief contextual insight (1 sentence) based on her goals.
4. Call send_grow_session with the formatted message.
5. After responses come in, call store_reflection to save to memory."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "send_grow_session",
                "description": "Send the evening GROW questions to the owner via Telegram.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The formatted GROW session message in HTML."
                        }
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "store_reflection",
                "description": "Store a completed GROW reflection in the coaching memory.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "goal":         {"type": "string", "description": "What they intended today"},
                        "reality":      {"type": "string", "description": "What actually happened"},
                        "options":      {"type": "string", "description": "What could change tomorrow"},
                        "commitment":   {"type": "string", "description": "One small commitment"},
                        "energy_score": {"type": "integer", "description": "1-10 energy score"},
                    },
                    "required": ["goal", "reality"],
                },
            },
        ]

    def _handle_tool(self, tool_name: str, tool_input: dict):
        if tool_name == "send_grow_session":
            text = tool_input.get("message", "")
            sent = _send_telegram(text)
            log.info(f"GROW session sent: {sent}")
            return json.dumps({"sent": sent})
        if tool_name == "store_reflection":
            _store_reflection(tool_input)
            return json.dumps({"stored": True})
        return super()._handle_tool(tool_name, tool_input)

    def run_evening_session(self):
        goals    = _load_top_goals()
        now      = datetime.now()
        day_name = now.strftime("%A")
        date_str = now.strftime("%B %-d")

        goals_text = "\n".join(f"- {g}" for g in goals) if goals else "- (No goals set yet)"

        # Day-specific framing
        day_frames = {
            "Monday":    "Week just started — set the tone.",
            "Tuesday":   "Day two — momentum check.",
            "Wednesday": "Midweek — are you where you intended to be?",
            "Thursday":  "Almost there — what needs to land before Friday?",
            "Friday":    "End of week — what closes, what carries?",
            "Saturday":  "Weekend — rest matters. What are you protecting?",
            "Sunday":    "Week closes here. What does next week need from you now?",
        }
        day_frame = day_frames.get(day_name, "")

        task = f"""Run the evening GROW coaching session for Francesca.

TODAY: {day_name}, {date_str}
DAY FRAME: {day_frame}

ACTIVE GOALS:
{goals_text}

Generate a warm, direct evening check-in using the GROW framework.
Format using HTML tags for Telegram.

The message should:
1. Open with a 1-sentence day-specific framing (use the DAY FRAME above)
2. Ask the 4 GROW questions — numbered, each on its own line
3. Add an energy check: "Rate your energy today 1-10 🔋"
4. Close with one brief insight or reflection prompt (not a question — a reframe)

Keep it scannable. 8-12 lines total. No lecture. No summary.
Call send_grow_session with the final message."""

        return self.run(task)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Life Coach Agent starting evening session")
    agent = LifeCoachAgent()
    result = agent.run_evening_session()
    print(result.get("output", ""))
    log.info("Life Coach Agent done")
