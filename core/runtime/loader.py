import json
import os
from pathlib import Path

STATE_PATH = Path(__file__).parent / "control_state.json"


def load_control_state() -> dict:
    if not STATE_PATH.exists():
        raise FileNotFoundError(f"Missing control state file: {STATE_PATH}")
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def get_param(key: str, default=None):
    try:
        return load_control_state().get(key, default)
    except Exception:
        return default
