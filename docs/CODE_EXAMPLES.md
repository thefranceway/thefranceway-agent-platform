# MABP Agent Router API — Code Examples

**Base URL:** `https://mabp-router.thefranceway.workers.dev`
**Auth:** Pass your key as `X-RapidAPI-Key` header.

---

## Quick Start

### curl
```bash
curl -X POST https://mabp-router.thefranceway.workers.dev/route \
  -H "X-RapidAPI-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"task": "Analyze this Python function for performance issues"}'
```

### Python
```python
import requests

url     = "https://mabp-router.thefranceway.workers.dev/route"
headers = {
    "X-RapidAPI-Key": "YOUR_KEY",
    "Content-Type":   "application/json",
}

response = requests.post(url, json={"task": "Analyze this Python function for performance issues"}, headers=headers)
result   = response.json()

print(result["output"])
print(f"Routing confidence: {result['routing_confidence']}")
print(f"Tokens used: {result['tokens_used']}")
```

### JavaScript / Node.js
```javascript
const response = await fetch("https://mabp-router.thefranceway.workers.dev/route", {
  method:  "POST",
  headers: {
    "X-RapidAPI-Key": "YOUR_KEY",
    "Content-Type":   "application/json",
  },
  body: JSON.stringify({ task: "Write a Twitter thread about AI agent behavioral patterns" }),
});

const result = await response.json();
console.log(result.output);
console.log("Confidence:", result.routing_confidence);
```

---

## Async Mode (long-running tasks)

```python
import requests, time

headers = {"X-RapidAPI-Key": "YOUR_KEY", "Content-Type": "application/json"}
base    = "https://mabp-router.thefranceway.workers.dev"

# Submit async
r       = requests.post(f"{base}/route", json={"task": "Write a full longevity research digest", "async_mode": True}, headers=headers)
task_id = r.json()["task_id"]

# Poll for result
while True:
    r      = requests.get(f"{base}/task/{task_id}", headers=headers)
    result = r.json()
    if result["status"] in ("done", "failed"):
        print(result["output"])
        break
    time.sleep(2)
```

---

## List Available Agents

```bash
curl https://mabp-router.thefranceway.workers.dev/agents \
  -H "X-RapidAPI-Key: YOUR_KEY"
```

---

## Example Response

```json
{
  "task_id": "a3f9b2c1-...",
  "status": "done",
  "output": "The function has O(n²) complexity due to nested loops on lines 12–18. Recommended fix: use a hash map to reduce to O(n)...",
  "routing_confidence": 0.97,
  "tokens_used": {
    "input": 312,
    "output": 847
  },
  "shadow_flags": [],
  "timestamp": "2026-03-07T20:00:00Z"
}
```

---

## Error Reference

| Code | Meaning | Fix |
|------|---------|-----|
| 401 | Invalid or missing API key | Add `X-RapidAPI-Key` header |
| 429 | Rate limit exceeded | Wait 60s or upgrade plan |
| 422 | Invalid request body | Ensure `task` field is present |
| 500 | Agent execution error | Retry — transient LLM error |
