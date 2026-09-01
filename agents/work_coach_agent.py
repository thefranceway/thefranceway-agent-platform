import os, subprocess, requests, random
from core.base_agent import BaseAgent

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 7049234595

def ask_claude_subscription(prompt):
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        return result.stdout.strip() or "Top 3 priorities for today?"
    except Exception as e:
        print(f"Claude CLI failed: {e}")
        return "Top 3 priorities for today?"

class WorkCoachAgent(BaseAgent):
    def run(self):
        prompt = "Generate one short work coaching question about priorities. Just the question."
        question = ask_claude_subscription(prompt)
        
        if not BOT_TOKEN:
            print(f"[DRY RUN] {question}")
            return
            
        requests.post(f"{BOT_API}/sendMessage", 
                      json={"chat_id": OWNER_CHAT_ID, "text": question}, 
                      timeout=10)
        print(f"Sent via Pro subscription: {question}")

if __name__ == "__main__":
    WorkCoachAgent().run()
