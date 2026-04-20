#!/usr/bin/env python3
"""
Morning Briefing Agent — daily 6am briefing delivered via Telegram.

Fetches:
  - Weather from Open-Meteo (free, no key required)
  - Top goals from coaching vector store
  - Any pending reflections from yesterday

Delivers a scannable morning briefing to @thefranceway_bot.

Usage:
    python morning_briefing_agent.py

Auto-start: managed by launchd (com.thefranceway.morning-briefing.plist)
Logs: ~/projects/agent-platform/logs/morning_briefing.log
"""

import json
import logging
import ssl
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent, JSONVectorStore

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN     = "8712606232:AAFuiGeNS6FvDdBpsaweRFvELGfthtTkt7A"
BOT_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595

# Open-Meteo coordinates — update in coaching_config.json to match your city
DEFAULT_LAT  = 19.4326   # Mexico City
DEFAULT_LON  = -99.1332
DEFAULT_TZ   = "America/Mexico_City"

PLATFORM_DIR  = Path(__file__).parent.parent
CONFIG_PATH   = PLATFORM_DIR / "registry" / "coaching_config.json"
LOG_PATH      = PLATFORM_DIR / "logs" / "morning_briefing.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers= [logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger("morning_briefing")

# SSL context
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _fetch_weather(lat: float, lon: float, tz: str) -> dict:
    """Fetch today's weather from Open-Meteo. Returns parsed weather dict."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current_weather=true"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
        f"&timezone={tz}&forecast_days=1"
    )
    try:
        with urllib.request.urlopen(url, timeout=8, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        current = data.get("current_weather", {})
        daily   = data.get("daily", {})
        wcode   = current.get("weathercode", 0)
        # WMO weather code → human description
        conditions = _wmo_to_label(wcode)
        return {
            "temp":      round(current.get("temperature", 0)),
            "conditions": conditions,
            "high":      round(daily.get("temperature_2m_max", [0])[0]),
            "low":       round(daily.get("temperature_2m_min", [0])[0]),
            "rain_pct":  daily.get("precipitation_probability_max", [0])[0] or 0,
        }
    except Exception as e:
        log.warning(f"Weather fetch failed: {e}")
        return {"temp": "?", "conditions": "unavailable", "high": "?", "low": "?", "rain_pct": 0}


def _wmo_to_label(code: int) -> str:
    """Map WMO weather code to short human label."""
    if code == 0:   return "Clear sky ☀️"
    if code <= 3:   return "Partly cloudy ⛅"
    if code <= 9:   return "Overcast ☁️"
    if code <= 19:  return "Fog 🌫️"
    if code <= 29:  return "Drizzle 🌦️"
    if code <= 39:  return "Drizzle 🌦️"
    if code <= 49:  return "Fog 🌫️"
    if code <= 59:  return "Drizzle 🌦️"
    if code <= 69:  return "Rain 🌧️"
    if code <= 79:  return "Snow ❄️"
    if code <= 84:  return "Rain showers 🌧️"
    if code <= 94:  return "Snow showers ❄️"
    return "Thunderstorm ⛈️"


def _load_goals() -> list[str]:
    """Pull active goals from the coaching vector store."""
    try:
        store = JSONVectorStore("kb_coaching")
        results = store.search("goal priority life work", top_k=10)
        goals = [
            r["text"] for r in results
            if r.get("metadata", {}).get("type") == "goal"
               and r.get("metadata", {}).get("status") != "archived"
        ]
        return goals[:5]
    except Exception as e:
        log.warning(f"Goal load failed: {e}")
        return []


def _days_until_payday(config: dict) -> str:
    """Optional: days until next Friday (common check-in rhythm)."""
    today    = datetime.now().weekday()
    days_off = (4 - today) % 7
    return "today" if days_off == 0 else f"in {days_off} days"


def _get_day_energy(config: dict) -> str:
    """Load yesterday's evening reflection energy score if available."""
    try:
        store = JSONVectorStore("kb_coaching")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        results = store.search(f"energy reflection {yesterday}", top_k=3)
        for r in results:
            if r.get("metadata", {}).get("type") == "reflection" \
               and r.get("metadata", {}).get("date") == yesterday:
                score = r.get("metadata", {}).get("energy_score")
                return f"Yesterday's energy: {score}/10" if score else ""
        return ""
    except Exception:
        return ""


def _send_telegram(text: str) -> bool:
    """Send a message to the owner via Telegram bot."""
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


# ── Main ──────────────────────────────────────────────────────────────────────

class MorningBriefingAgent(BaseAgent):

    AGENT_TYPE         = "ops"
    DEFAULT_BEHAVIORAL = "Substrate"

    def __init__(self, **kwargs):
        super().__init__(
            name           = "Morning Briefing Agent",
            knowledge_base = "kb_coaching",
            model          = "claude-haiku-4-5-20251001",
            **kwargs,
        )

    def _default_system_prompt(self) -> str:
        return """You are the Morning Briefing Agent for Francesca Ranieri.

Archetype: Substrate
Core pattern: Reliable, scannable daily delivery. You compile the morning briefing
from weather data, active goals, and coaching history. You are a utility, not a therapist.
Your output is clean, fast to read, and immediately useful.

Tone:
- Direct. Energizing but not cheesy.
- No filler. No emojis except the structural ones in the template.
- Personalize day-of-week energy (Monday = momentum, Friday = wrap-up mindset).

Operating rules:
1. Compile the briefing from provided data.
2. Write the TODAY'S FOCUS line from the top goals — pick the ONE most important.
3. Add a contextual tip based on the day of week and goal load.
4. Call send_briefing with the final formatted text."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "send_briefing",
                "description": "Send the compiled morning briefing to the owner via Telegram.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "briefing": {
                            "type": "string",
                            "description": "Full formatted briefing text in HTML-safe format."
                        }
                    },
                    "required": ["briefing"],
                },
            },
        ]

    def _handle_tool(self, tool_name: str, tool_input: dict):
        if tool_name == "send_briefing":
            text   = tool_input.get("briefing", "")
            sent   = _send_telegram(text)
            result = {"sent": sent}
            log.info(f"Briefing sent: {sent}")
            return json.dumps(result)
        return super()._handle_tool(tool_name, tool_input)

    def run_briefing(self):
        config  = _load_config()
        lat     = config.get("latitude",  DEFAULT_LAT)
        lon     = config.get("longitude", DEFAULT_LON)
        tz      = config.get("timezone",  DEFAULT_TZ)
        city    = config.get("city",      "Mexico City")

        weather    = _fetch_weather(lat, lon, tz)
        goals      = _load_goals()
        energy_blurb = _get_day_energy(config)
        now        = datetime.now()
        day_name   = now.strftime("%A")
        date_str   = now.strftime("%B %-d, %Y")

        goals_text = "\n".join(f"  • {g}" for g in goals) if goals else "  (No goals set — run setup_coaching_goals.py)"

        rain_advice = ""
        if weather["rain_pct"] and int(weather["rain_pct"]) >= 40:
            rain_advice = f" — {weather['rain_pct']}% chance of rain, grab an umbrella"

        task = f"""Compile the morning briefing for Francesca.

DATA:
- Day: {day_name}, {date_str}
- City: {city}
- Weather: {weather['conditions']}, {weather['temp']}°C — High {weather['high']}° / Low {weather['low']}°{rain_advice}
- Active goals:
{goals_text}
- {energy_blurb if energy_blurb else "No energy data from yesterday"}

FORMAT (use exactly — HTML-safe, no markdown bold, use <b> tags):
<b>☀️ Good morning, Francesca — {day_name}, {date_str}</b>

🌤️ <b>WEATHER</b>
{city}: [conditions], [temp]°C — High [X]° / Low [Y]°[rain note]

🎯 <b>TODAY'S FOCUS</b>
[Single most important thing to move today — derived from top goal]

📋 <b>ACTIVE GOALS</b>
[list goals, max 5]

💡 <b>TIP</b>
[One contextual tip for this day of week + goal load. 1 sentence.]

---
Have a great day ☕"""

        return self.run(task)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Morning Briefing Agent starting")
    agent = MorningBriefingAgent()
    result = agent.run_briefing()
    print(result.get("output", ""))
    log.info("Morning Briefing Agent done")
