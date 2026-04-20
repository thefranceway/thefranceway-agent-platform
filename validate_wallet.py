"""
FastAPI endpoint — Solana wallet address format validation.

GET  /validate/{address}  →  200 valid | 400 invalid
POST /validate            →  body: {"address": "..."} → 200 valid | 400 invalid

Run:
    uvicorn validate_wallet:app --reload
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Base58 alphabet (Bitcoin/Solana variant — no 0, O, I, l) ──────────────────
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_SET = frozenset(_B58)


def _b58_decode(address: str) -> bytes:
    """Decode a base58 string to bytes (raises ValueError on bad input)."""
    num = 0
    for char in address:
        num = num * 58 + _B58.index(char)
    leading_zeros = len(address) - len(address.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num > 0 else b""
    return b"\x00" * leading_zeros + body


def is_valid_solana_address(address: str) -> bool:
    """
    Return True iff `address` is a valid Solana public key format:
      - 32–44 characters long
      - All characters in the base58 alphabet (no 0, O, I, l)
      - Decodes to exactly 32 bytes
    """
    if not isinstance(address, str):
        return False
    if not (32 <= len(address) <= 44):
        return False
    if not _B58_SET.issuperset(address):
        return False
    try:
        return len(_b58_decode(address)) == 32
    except Exception:
        return False


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Solana Wallet Validator",
    description="Validates Solana wallet address format (base58, 32-byte pubkey).",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Response / request models ─────────────────────────────────────────────────
class ValidationResult(BaseModel):
    address: str
    valid: bool
    message: str


class AddressRequest(BaseModel):
    address: str


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get(
    "/validate/{address}",
    response_model=ValidationResult,
    summary="Validate a Solana address (path param)",
)
def validate_address_get(
    address: str = Path(..., description="Base58-encoded Solana public key"),
) -> ValidationResult:
    """
    Returns **200** with `valid: true` for a well-formed address,
    or **400** with `valid: false` and a reason message.
    """
    if is_valid_solana_address(address):
        return ValidationResult(
            address=address,
            valid=True,
            message="Address is valid.",
        )
    raise HTTPException(
        status_code=400,
        detail=ValidationResult(
            address=address,
            valid=False,
            message="Invalid Solana address: must be a base58-encoded 32-byte public key (32–44 chars).",
        ).model_dump(),
    )


@app.post(
    "/validate",
    response_model=ValidationResult,
    summary="Validate a Solana address (request body)",
)
def validate_address_post(body: AddressRequest) -> ValidationResult:
    """
    Accepts `{"address": "..."}` in the request body.
    Returns **200** or **400** with the same schema as the GET endpoint.
    """
    return validate_address_get(address=body.address)


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("validate_wallet:app", host="0.0.0.0", port=8000, reload=True)
