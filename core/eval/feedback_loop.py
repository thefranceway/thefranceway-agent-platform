import json
from pathlib import Path

CONTROL_FILE = Path(__file__).parent.parent / "runtime" / "control_state.json"


def load_state() -> dict:
    if not CONTROL_FILE.exists():
        raise FileNotFoundError(f"Missing control state file: {CONTROL_FILE}")
    with open(CONTROL_FILE, "r") as f:
        return json.load(f)


def save_state(state: dict):
    with open(CONTROL_FILE, "w") as f:
        json.dump(state, f, indent=2)


def apply_feedback(score: int, failures: list) -> dict:
    """
    Self-correcting feedback loop. Adjusts ONLY runtime parameters.
    Never rewrites architecture or agents.
    """
    state = load_state()

    if score < 60:
        state["context_strictness"] = min(1.0, state["context_strictness"] + 0.1)

    if "contradiction" in failures:
        state["spar_weight"] = min(2.0, state["spar_weight"] + 0.1)

    if "context_bleed" in failures:
        state["swarm_size"] = max(2, state["swarm_size"] - 1)

    if "hallucination" in failures:
        state["skill_loader_strength"] = min(1.0, state["skill_loader_strength"] + 0.1)

    save_state(state)
    return state
