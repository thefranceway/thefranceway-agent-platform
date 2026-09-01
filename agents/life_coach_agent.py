import os, subprocess, requests, random

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595

def ask_claude_subscription(prompt):
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        out = result.stdout.strip()
        return out if out else "What did you move forward today?"
    except Exception as e:
        print(f"Claude CLI failed: {e}")
        return "What did you move forward today?"

class LifeCoachAgent:
    # No BaseAgent - so no ANTHROPIC_API_KEY check
    def run(self):
        prompt = "Generate one short, punchy GROW coaching question for life reflection. Just the question, no explanation."
        question = ask_claude_subscription(prompt)
        if not BOT_TOKEN:
            print(f"[DRY RUN] {question}")
            return
        requests.post(f"{BOT_API}/sendMessage", json={"chat_id": OWNER_CHAT_ID, "text": question}, timeout=10)
        print(f"Sent via Pro subscription: {question}")

if __name__ == "__main__":
    LifeCoachAgent().run()
