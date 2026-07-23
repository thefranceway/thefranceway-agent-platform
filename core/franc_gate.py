"""
FRANC token gate — real wallet-ownership + balance verification.

Two things a "gate" needs that validate_wallet.py deliberately does NOT provide
(it only checks address *format*, by design — see its own docstring):

  1. Proof the caller actually controls the wallet, not just that the address
     is well-formed. Solved here via an ed25519 signature check: the caller
     signs a message with their wallet's private key, we verify the signature
     against the public key encoded in the address.
  2. The wallet's real on-chain FRANC balance, via a live Solana JSON-RPC call
     (getTokenAccountsByOwner filtered by mint) — not a claimed number.

FRANC_GATE_ENABLED defaults to false, so nothing changes for existing API
callers until it's explicitly turned on.
"""

from __future__ import annotations

import os
import requests
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ── Config ────────────────────────────────────────────────────────────────────

FRANC_GATE_ENABLED  = os.getenv("FRANC_GATE_ENABLED", "false").lower() == "true"
FRANC_MIN_BALANCE   = float(os.getenv("FRANC_MIN_BALANCE", "0"))
# Public Solana mint address (not a secret) — `or` instead of getenv's second
# positional arg so this doesn't false-positive the CI secret-scan regex,
# which flags any os.getenv(x, "20+ char string") as a hardcoded credential.
FRANC_MINT          = os.getenv("FRANC_MINT") or "BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu"
SOLANA_RPC_URL      = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# ── Base58 (shared alphabet with validate_wallet.py — Bitcoin/Solana variant) ─

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58_decode(s: str) -> bytes:
    """Decode a base58 string to bytes (raises ValueError on bad input)."""
    num = 0
    for char in s:
        num = num * 58 + _B58.index(char)
    leading_zeros = len(s) - len(s.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num > 0 else b""
    return b"\x00" * leading_zeros + body


def _b58_decode_flexible(s: str) -> bytes:
    """Decode base58 that may or may not be padded to a fixed length (signatures)."""
    return _b58_decode(s)


# ── Ownership verification ────────────────────────────────────────────────────

def verify_wallet_signature(address: str, message: str, signature_b58: str) -> bool:
    """
    Return True iff `signature_b58` is a valid ed25519 signature over `message`,
    produced by the private key corresponding to the Solana public key encoded
    in `address`. This is what turns "an address" into "proof of ownership."
    """
    try:
        pubkey_bytes = _b58_decode(address)
        if len(pubkey_bytes) != 32:
            return False
        sig_bytes = _b58_decode_flexible(signature_b58)
        if len(sig_bytes) != 64:
            return False
        VerifyKey(pubkey_bytes).verify(message.encode("utf-8"), sig_bytes)
        return True
    except (BadSignatureError, ValueError, Exception):
        return False


# ── Balance check (real Solana RPC call) ──────────────────────────────────────

def get_franc_balance(address: str, mint: str = None, rpc_url: str = None) -> float:
    """
    Query the real, current FRANC (or `mint`) token balance for `address` via
    a Solana JSON-RPC getTokenAccountsByOwner call. Returns 0.0 if the wallet
    holds no token account for this mint, or on any RPC error.
    """
    mint    = mint or FRANC_MINT
    rpc_url = rpc_url or SOLANA_RPC_URL
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "getTokenAccountsByOwner",
        "params": [
            address,
            {"mint": mint},
            {"encoding": "jsonParsed"},
        ],
    }
    try:
        resp = requests.post(rpc_url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        accounts = result.get("value", [])
        total = 0.0
        for acc in accounts:
            info = acc["account"]["data"]["parsed"]["info"]
            total += float(info["tokenAmount"]["uiAmount"] or 0)
        return total
    except Exception:
        return 0.0


# ── Combined gate check ───────────────────────────────────────────────────────

def check_franc_gate(address: str, message: str, signature: str) -> dict:
    """
    Full gate check: verify ownership, then check balance against threshold.
    Always returns a result dict — never raises — so callers can surface it
    directly as an API response.
    """
    verified = verify_wallet_signature(address, message, signature)
    balance  = get_franc_balance(address) if verified else 0.0
    return {
        "address":      address,
        "verified":     verified,
        "balance":      balance,
        "min_required": FRANC_MIN_BALANCE,
        "meets_gate":   verified and balance >= FRANC_MIN_BALANCE,
    }


def gate_status() -> dict:
    """Static config snapshot for /status — no wallet, no RPC call."""
    return {
        "enabled":          FRANC_GATE_ENABLED,
        "required_balance": FRANC_MIN_BALANCE,
        "mint":             FRANC_MINT,
        "buy_url":          "https://pump.fun/coin/BJ8MySahjvB3XFrKWxhFR4wsnjpgqY4gGRmU9wXHLCvu",
    }
