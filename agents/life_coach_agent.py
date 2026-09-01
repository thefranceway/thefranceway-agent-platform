import os, subprocess, requests
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","")
BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
def ask(p):
    try:
        r = subprocess.run(["claude","-p",p], capture_output=True, text=True, timeout=60)
        return r.stdout.strip() or "What did you move forward today?"
    except: return "What did you move forward today?"
if __name__ == "__main__":
    q = ask("One short GROW coaching question for life, just the question")
    print(q)
    if BOT_TOKEN: requests.post(f"{BOT_API}/sendMessage", json={"chat_id":7049234595,"text":q}, timeout=10)
