#!/usr/bin/env python3
"""
Weekly agent eval runner.
Dispatches golden test cases per agent, scores pass/fail, writes results JSON,
and sends a Telegram summary to the owner.

Run: python3 core/eval/run_evals.py
Scheduled: Monday 9am via com.franceway.weekly-evals launchd
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN_FILE   = Path(__file__).parent / "golden_sets.json"
RESULTS_DIR   = ROOT / "registry" / "eval_results"
PLATFORM_URL  = "http://localhost:8788"
BOT_TOKEN     = None  # loaded from Keychain at runtime
OWNER_CHAT_ID = 7049234595


def get_keychain(service: str) -> str:
    import subprocess
    result = subprocess.run(
        ["security", "find-generic-password", "-a", "francesca", "-s", service, "-w"],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def dispatch(task: str, agent_type: str) -> str:
    payload = json.dumps({
        "description": task,
        "agent_type":  agent_type,
        "async_mode":  False,
    }).encode()
    req = urllib.request.Request(
        f"{PLATFORM_URL}/task",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
            return data.get("output", "")
    except Exception as e:
        return f"ERROR: {e}"


def score_case(case: dict, output: str) -> dict:
    passed   = True
    failures = []

    for term in case.get("must_contain", []):
        if term.lower() not in output.lower():
            passed = False
            failures.append(f"missing '{term}'")

    for term in case.get("must_not_contain", []):
        if term.lower() in output.lower():
            passed = False
            failures.append(f"contains banned '{term}'")

    min_len = case.get("min_length", 0)
    if len(output) < min_len:
        passed = False
        failures.append(f"output too short ({len(output)} < {min_len})")

    return {"passed": passed, "failures": failures, "output_len": len(output)}


def send_telegram(text: str):
    if not BOT_TOKEN:
        return
    payload = json.dumps({"chat_id": OWNER_CHAT_ID, "text": text[:4000]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def main():
    global BOT_TOKEN
    BOT_TOKEN = get_keychain("telegram-bot-token")

    with open(GOLDEN_FILE) as f:
        golden = json.load(f)

    run_ts    = datetime.now(timezone.utc).isoformat()
    run_date  = run_ts[:10]
    results   = {"run_at": run_ts, "agents": {}}

    total_pass = 0
    total_fail = 0
    agent_summaries = []

    for agent_type, cases in golden.items():
        if agent_type.startswith("_"):
            continue

        agent_results = []
        agent_pass    = 0
        agent_fail    = 0

        for case in cases:
            print(f"  [{agent_type}] {case['id']} ...", end="", flush=True)
            output = dispatch(case["input"], agent_type)
            scored = score_case(case, output)

            result = {
                "id":       case["id"],
                "input":    case["input"][:80],
                "passed":   scored["passed"],
                "failures": scored["failures"],
                "output_preview": output[:200],
            }
            agent_results.append(result)

            if scored["passed"]:
                agent_pass  += 1
                total_pass  += 1
                print(" ✓")
            else:
                agent_fail  += 1
                total_fail  += 1
                print(f" ✗ {scored['failures']}")

        results["agents"][agent_type] = {
            "passed":  agent_pass,
            "failed":  agent_fail,
            "total":   len(cases),
            "cases":   agent_results,
        }

        emoji = "✅" if agent_fail == 0 else ("⚠️" if agent_fail < len(cases) else "❌")
        agent_summaries.append(f"{emoji} {agent_type}: {agent_pass}/{len(cases)}")

    # Write results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{run_date}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    # Telegram summary
    total = total_pass + total_fail
    header = f"🧪 Agent Evals — {run_date}\n{total_pass}/{total} passed\n"
    body   = "\n".join(agent_summaries)
    msg    = header + "\n" + body
    send_telegram(msg)

    print(f"\n{msg}")
    print(f"\nResults written to {out_path}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
