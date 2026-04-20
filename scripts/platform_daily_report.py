#!/usr/bin/env python3
"""
Platform Daily Report — Agent Platform
Ingests platform data and optionally feeds it to DataAnalyticsAgent for AI insights.
Outputs a Markdown summary or appends to reports.html.

Usage:
    python scripts/platform_daily_report.py           # Markdown summary (no AI)
    python scripts/platform_daily_report.py --ai      # add AI insights (needs Anthropic credits)
    python scripts/platform_daily_report.py --html    # write/append to reports.html
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PLATFORM_DIR = Path(__file__).parent.parent
REPORTS_PATH = PLATFORM_DIR / "reports.html"

# ── Ingestion ─────────────────────────────────────────────────────────────────

def load_metrics() -> dict:
    ingest = PLATFORM_DIR / "scripts" / "ingest_platform_data.py"
    import subprocess
    python = PLATFORM_DIR / "venv" / "bin" / "python3"
    result = subprocess.run([str(python), str(ingest), "--json"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"Ingestion error: {result.stderr}")
        sys.exit(1)
    return json.loads(result.stdout)


# ── Markdown Report ───────────────────────────────────────────────────────────

def markdown_report(data: dict) -> str:
    now   = data["generated_at"][:10]
    h     = data["health"]
    api   = data.get("api_server", {})
    tg    = data.get("telegram", {})
    tok   = data.get("token_usage", {})
    fp    = data.get("fingerprints", {})

    lines = [
        f"# Platform Report — {now}",
        f"\n**Health:** {h['status'].upper()}",
    ]

    if h["issues"]:
        lines.append("\n**Issues:**")
        for issue in h["issues"]:
            lines.append(f"- {issue}")

    lines.append("\n## API Server")
    if api:
        lines.append(f"- Total requests: {api['total_requests']}")
        lines.append(f"- Errors (4xx/5xx): {api['error_count']}")
        if api["by_route"]:
            lines.append("\nTop routes:")
            for route, count in list(api["by_route"].items())[:5]:
                lines.append(f"  - `{route}`: {count}")

    lines.append("\n## Telegram Monitor")
    if tg:
        lines.append(f"- Messages tracked: {tg['total_messages']}")
        if tg["by_chat"]:
            lines.append("\nBy chat:")
            for chat, count in list(tg["by_chat"].items())[:5]:
                lines.append(f"  - {chat}: {count}")
        if tg["outcomes"]:
            lines.append(f"\nOutcomes: {tg['outcomes']}")

    lines.append("\n## Token Usage (Agent Costs)")
    if "status" in tok:
        lines.append(f"- {tok['status']}")
    else:
        lines.append(f"- Total calls: {tok['total_calls']}")
        lines.append(f"- Total cost: ${tok['total_cost']:.4f}")
        lines.append(f"- Date range: {tok['date_range']}")
        if tok.get("by_agent"):
            lines.append("\nBy agent:")
            for agent, d in list(tok["by_agent"].items())[:8]:
                lines.append(f"  - {agent}: {d['calls']} calls, ${d['cost']:.4f}")
        if tok.get("by_day"):
            lines.append("\nDaily cost trend:")
            for day, d in tok["by_day"].items():
                lines.append(f"  - {day}: {d['calls']} calls, ${d['cost']:.4f}")

    lines.append(f"\n## API Fingerprints")
    if fp:
        lines.append(f"- Total calls: {fp['total_api_calls']}")
        lines.append(f"- Unique keys: {fp['unique_keys']}")

    return "\n".join(lines)


# ── AI Insights ───────────────────────────────────────────────────────────────

def get_ai_insights(data: dict) -> str:
    sys.path.insert(0, str(PLATFORM_DIR))
    try:
        from agents.data_analytics_agent import DataAnalyticsAgent
        agent = DataAnalyticsAgent()
        result = agent.run(
            f"Analyze this platform metrics JSON and give me 3-5 actionable insights. "
            f"Focus on: what's broken, what's costing money, and what needs attention. "
            f"Be concise — one sentence per insight.\n\nData:\n{json.dumps(data, indent=2)}"
        )
        return result.get("output", "No insights generated.")
    except Exception as e:
        return f"AI insights unavailable: {e}"


# ── HTML Append ───────────────────────────────────────────────────────────────

def append_html_report(md: str, ai_insights: str = ""):
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = md.replace("\n", "<br>").replace("# ", "<h2>").replace("## ", "<h3>")

    block = f"""
<!-- report:{now} -->
<div class="report" style="font-family:monospace;border:1px solid #333;padding:16px;margin:16px 0;background:#111;color:#eee;border-radius:6px;">
  <h2 style="margin:0 0 8px;color:#7cf">{now}</h2>
  <pre style="white-space:pre-wrap">{md}</pre>
"""
    if ai_insights:
        block += f"""  <h3 style="color:#fc7">AI Insights</h3>
  <pre style="white-space:pre-wrap;color:#fc7">{ai_insights}</pre>
"""
    block += "</div>\n"

    if REPORTS_PATH.exists():
        content = REPORTS_PATH.read_text()
        # inject before </body> or just append
        if "</body>" in content:
            content = content.replace("</body>", block + "</body>")
        else:
            content += block
        REPORTS_PATH.write_text(content)
    else:
        REPORTS_PATH.write_text(f"<html><body>{block}</body></html>")

    print(f"Report appended to {REPORTS_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    use_ai   = "--ai"   in sys.argv
    use_html = "--html" in sys.argv

    data = load_metrics()
    md   = markdown_report(data)

    print(md)

    ai_insights = ""
    if use_ai:
        print("\n## AI Insights\n")
        ai_insights = get_ai_insights(data)
        print(ai_insights)

    if use_html:
        append_html_report(md, ai_insights)
