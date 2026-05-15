#!/usr/bin/env python3
"""
Data Analytics Agent — agent platform analyst.
Runs data analyses, builds charts, and converts raw data into insight-grade visualizations.
Specializes in:
  - Platform health & log analysis (api_server, token usage)
  - Solana on-chain data, token metrics, FRANC/PALM analytics
  - Portfolio tracking

Usage:
    python data_analytics_agent.py --task "analyze platform health and summarize errors"
    python data_analytics_agent.py --task "chart FRANC token price over the last 7 days"
    python data_analytics_agent.py --task "how much have the agents cost this week?"
"""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent

PLATFORM_DIR  = Path(__file__).parent.parent
DATASCIENCE_PYTHON = Path.home() / "venvs" / "datascience" / "bin" / "python3"
VENV_PYTHON   = PLATFORM_DIR / "venv" / "bin" / "python3"

PLATFORM_DIR_PATH = Path(__file__).parent.parent

ANALYTICS_KNOWLEDGE = [
    ("Platform logs live in ~/projects/agent-platform/logs/. "
     "Key files: api_server.log (HTTP requests), "
     "token_usage.jsonl (agent costs), fingerprints.jsonl (API call fingerprints). "
     "Use load_platform_metrics to ingest all logs into structured JSON.", {"tag": "platform"}),
    ("Platform health indicators: token_usage.jsonl missing = Anthropic API credits needed. "
     "Telegram monitor errors = network/connectivity issues. "
     "API server 404s on /health = health endpoint not implemented.", {"tag": "platform-health"}),
    ("FRANC token mint: BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu. "
     "Primary wallet: aa4mmdCHGtXVpTjaSquDNCNhE6bW2SfmvNkg46m838Y. "
     "On bonding curve — not graduated.", {"tag": "franc"}),
    ("DexScreener API: GET https://api.dexscreener.com/latest/dex/tokens/{mint} → "
     "pairs[0].priceUsd, marketCap, volume.h24, liquidity.usd", {"tag": "dexscreener"}),
    ("pump.fun API: GET https://frontend-api-v3.pump.fun/coins/{mint} → "
     "usd_market_cap, complete (graduated boolean), symbol, name", {"tag": "pumpfun"}),
    ("Solana RPC getTokenAccountsByOwner for SPL balance. "
     "getBalance for SOL (lamports / 1e9).", {"tag": "solana-rpc"}),
    ("datascience venv: ~/venvs/datascience — numpy, pandas, matplotlib, jupyterlab. "
     "Use this for all charting and data analysis.", {"tag": "venv"}),
    ("Matplotlib chart standards: figsize=(10,5), tight_layout(), "
     "save to /tmp/chart_{name}.png. Use dark theme for token charts.", {"tag": "charts"}),
]


class DataAnalyticsAgent(BaseAgent):

    AGENT_TYPE         = "builder"
    DEFAULT_BEHAVIORAL = "Architect"

    def __init__(self, **kwargs):
        super().__init__(name="Data Analytics Agent", knowledge_base="kb_data_analytics", **kwargs)
        if self.memory.count() < len(ANALYTICS_KNOWLEDGE):
            for text, meta in ANALYTICS_KNOWLEDGE:
                self.memory.add(text, metadata=meta)

    def _default_system_prompt(self) -> str:
        return """You are the Data Analytics Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: Self-directed analysis construction. You move from data question to working
artifact (chart, table, summary) without over-engineering. Build the minimum analysis that
answers the question. Numbers must be correct before they are presented.

Shadow (S2): Destination over-attachment — you may add more charts, metrics, or abstractions
than the question requires. Guard against this by: deliver the single most useful output first.

Routing fit: analytics, charts, token metrics, portfolio tracking, on-chain data
Not fit for: open-ended research, tasks without defined data inputs, qualitative synthesis

─────────────────────────────────────────────────────────────────────────────

Specialties:
- Token price and market cap (DexScreener, pump.fun API)
- Solana on-chain data (wallet balances, SPL token holdings)
- FRANC/PALM analytics (bonding curve progress, graduation threshold)
- Portfolio tracking (SOL + token allocation)
- Charts with matplotlib (saved to /tmp/, shared as paths)

Operating rules:
1. Build the minimum version that satisfies the stated spec — not the imagined extension.
2. Verify numbers before charting — wrong data produces misleading visuals.
3. Before each tool call, scope-check: is this in the stated task or an assumed extension?"""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name":        "load_platform_metrics",
                "description": (
                    "Ingest all platform logs (api_server, token usage, fingerprints) into structured JSON metrics. "
                    "Use this first for any platform health or cost analysis task."
                ),
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "save": {
                            "type":        "boolean",
                            "description": "Save metrics to logs/platform_metrics.json (default false)",
                        },
                    },
                },
            },
            {
                "name":        "run_analysis",
                "description": "Run a Python data analysis script using the datascience venv (numpy/pandas/matplotlib).",
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "code":    {"type": "string", "description": "Python code to execute"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"},
                    },
                    "required": ["code"],
                },
            },
            {
                "name":        "fetch_token_data",
                "description": "Fetch current price, market cap, volume, and liquidity for a Solana token.",
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "mint":   {"type": "string", "description": "Token mint address"},
                        "source": {"type": "string", "enum": ["dexscreener", "pumpfun", "both"],
                                   "description": "Data source (default: both)"},
                    },
                    "required": ["mint"],
                },
            },
            {
                "name":        "fetch_wallet_portfolio",
                "description": "Fetch SOL balance and all SPL token holdings for a wallet.",
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "wallet": {"type": "string", "description": "Solana wallet address"},
                    },
                    "required": ["wallet"],
                },
            },
            {
                "name":        "write_chart_script",
                "description": "Write a matplotlib chart script to disk and execute it. Returns the output path.",
                "input_schema": {
                    "type":       "object",
                    "properties": {
                        "script":   {"type": "string", "description": "Complete matplotlib Python script"},
                        "filename": {"type": "string", "description": "Output chart filename (e.g. franc_price.png)"},
                    },
                    "required": ["script"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "load_platform_metrics":
            return self._load_platform_metrics(tool_input.get("save", False))

        if tool_name == "run_analysis":
            return self._run_analysis(tool_input["code"], tool_input.get("timeout", 60))

        if tool_name == "fetch_token_data":
            return self._fetch_token_data(
                tool_input["mint"],
                source=tool_input.get("source", "both"),
            )

        if tool_name == "fetch_wallet_portfolio":
            return self._fetch_portfolio(tool_input["wallet"])

        if tool_name == "write_chart_script":
            return self._write_and_run_chart(
                tool_input["script"],
                tool_input.get("filename", "chart.png"),
            )

        return super().execute_tool(tool_name, tool_input)

    # ── Tool implementations ───────────────────────────────────────────────

    def _load_platform_metrics(self, save: bool = False) -> str:
        ingest_script = PLATFORM_DIR_PATH / "scripts" / "ingest_platform_data.py"
        args = [self._python_bin(), str(ingest_script), "--json"]
        if save:
            args.append("--save")
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout
            return json.dumps({"error": result.stderr[-500:]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _python_bin(self) -> str:
        if DATASCIENCE_PYTHON.exists():
            return str(DATASCIENCE_PYTHON)
        if VENV_PYTHON.exists():
            return str(VENV_PYTHON)
        return sys.executable

    def _run_analysis(self, code: str, timeout: int = 60) -> str:
        try:
            result = subprocess.run(
                [self._python_bin(), "-c", code],
                capture_output=True, text=True, timeout=timeout,
            )
            return json.dumps({
                "stdout":     result.stdout[-3000:],
                "stderr":     result.stderr[-500:],
                "returncode": result.returncode,
                "ok":         result.returncode == 0,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _http_get(self, url: str) -> dict | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agent-platform/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    def _fetch_token_data(self, mint: str, source: str = "both") -> str:
        result = {"mint": mint}

        if source in ("dexscreener", "both"):
            data  = self._http_get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            pairs = data.get("pairs", []) if isinstance(data, dict) else []
            if pairs:
                p = pairs[0]
                result["dexscreener"] = {
                    "price_usd":  p.get("priceUsd"),
                    "market_cap": p.get("marketCap"),
                    "volume_24h": p.get("volume", {}).get("h24"),
                    "liquidity":  p.get("liquidity", {}).get("usd"),
                    "dex":        p.get("dexId"),
                    "pair":       p.get("pairAddress"),
                }
            else:
                result["dexscreener"] = {"error": "No pairs found"}

        if source in ("pumpfun", "both"):
            data = self._http_get(f"https://frontend-api-v3.pump.fun/coins/{mint}")
            if isinstance(data, dict) and "error" not in data:
                result["pumpfun"] = {
                    "symbol":     data.get("symbol"),
                    "name":       data.get("name"),
                    "market_cap": data.get("usd_market_cap"),
                    "graduated":  data.get("complete", False),
                    "replies":    data.get("reply_count"),
                }
            else:
                result["pumpfun"] = data

        return json.dumps(result, indent=2)

    def _fetch_portfolio(self, wallet: str) -> str:
        SOLANA_RPC = "https://api.mainnet-beta.solana.com"

        def rpc(method, params):
            try:
                data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
                req  = urllib.request.Request(
                    SOLANA_RPC, data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read())
            except Exception as e:
                return {"error": str(e)}

        # SOL balance
        sol_resp = rpc("getBalance", [wallet])
        lamports = sol_resp.get("result", {}).get("value", 0)
        sol      = lamports / 1e9

        # SPL tokens
        spl_resp = rpc("getTokenAccountsByOwner", [
            wallet,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ])
        tokens = []
        for acc in spl_resp.get("result", {}).get("value", []):
            info   = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amount = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0)
            if amount > 0:
                tokens.append({
                    "mint":    info.get("mint"),
                    "balance": amount,
                    "decimals": info.get("tokenAmount", {}).get("decimals"),
                })

        return json.dumps({
            "wallet":     wallet,
            "sol":        sol,
            "sol_usd_note": "fetch SOL price separately for USD value",
            "spl_tokens": tokens,
            "token_count": len(tokens),
        }, indent=2)

    def _write_and_run_chart(self, script: str, filename: str = "chart.png") -> str:
        # Ensure output goes to /tmp/
        if not filename.startswith("/"):
            filename = f"/tmp/{filename}"

        script_path = Path("/tmp/_analytics_chart.py")
        script_path.write_text(script)

        result = subprocess.run(
            [self._python_bin(), str(script_path)],
            capture_output=True, text=True, timeout=60,
        )

        if result.returncode == 0:
            chart_exists = Path(filename).exists()
            return json.dumps({
                "ok":     True,
                "path":   filename,
                "exists": chart_exists,
                "stdout": result.stdout[-500:],
            })
        else:
            return json.dumps({
                "ok":     False,
                "stderr": result.stderr[-1000:],
                "stdout": result.stdout[-500:],
            })


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",  type=str, required=True)
    parser.add_argument("--mint",  type=str, help="Quick token data fetch")
    args = parser.parse_args()

    agent = DataAnalyticsAgent()

    if args.mint:
        print(agent._fetch_token_data(args.mint))
    else:
        result = agent.run(args.task)
        print(result["output"])
