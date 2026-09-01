import os, subprocess, requests, random
from core.base_agent import BaseAgent

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595

def ask_claude_subscription(prompt):
    """Uses your Claude Pro via claude CLI - $0, no API billing"""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        return result.stdout.strip() or "What did you move forward today?"
    except Exception as e:
        print(f"Claude CLI failed: {e}, using fallback")
        return random.choice([
            "What did you move forward today?",
            "What's blocking you?",
            "What would make tomorrow a win?"
        ])

class LifeCoachAgent(BaseAgent):
    def run(self):
        prompt = "Generate one short, punchy GROW coaching question for life reflection. Just the question, no explanation."
        question = ask_claude_subscription(prompt)
        
        if not BOT_TOKEN:
            print(f"[DRY RUN] {question}")
            return
            
        requests.post(f"{BOT_API}/sendMessage", 
                      json={"chat_id": OWNER_CHAT_ID, "text": question}, 
                      timeout=10)
        print(f"Sent via Pro subscription: {question}")

if __name__ == "__main__":
    LifeCoachAgent().run()
