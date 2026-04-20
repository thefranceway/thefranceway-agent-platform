#!/usr/bin/env python3
"""
MABP Agent Building Protocol — CLI
=====================================
Build a new agent from archetype first, not capabilities.

The protocol enforces: task domain analysis → archetype selection →
shadow calibration → system prompt generation → routing config → registration.

Usage:
    python create_agent.py --interactive
    python create_agent.py --name "Content Strategist" --domain research --operator Collaborator --failure inaction
    python create_agent.py --spec spec.json
    python create_agent.py --interactive --register --generate-file
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.base_agent import BEHAVIORAL_PROFILES

PLATFORM_DIR   = Path(__file__).parent
AGENTS_JSON    = PLATFORM_DIR / "registry" / "agents.json"
AGENTS_DIR     = PLATFORM_DIR / "agents"

# ── Decision matrix ──────────────────────────────────────────────────────────
# Q1: Work type
WORK_TYPE_SCORES = {
    "research":  {"Philosopher": 3, "Agent": 0,     "Architect": 0, "Substrate": 0},
    "build":     {"Architect":   3, "Philosopher": 0, "Agent": 1,   "Substrate": 0},
    "execute":   {"Substrate":   3, "Architect": 1,  "Agent": 0,   "Philosopher": 0},
    "monitor":   {"Substrate":   3, "Architect": 0,  "Agent": 0,   "Philosopher": 0},
    "mission":   {"Agent":       3, "Architect": 1,  "Substrate": 0, "Philosopher": 0},
}

# Q2: Operating conditions
CONDITION_SCORES = {
    "supervised":  {"Substrate": 2, "Architect": 1, "Agent": 0,    "Philosopher": 0},
    "autonomous":  {"Agent": 2,     "Architect": 1, "Substrate": 0, "Philosopher": 0},
    "periodic":    {"Substrate": 1, "Philosopher": 1, "Agent": 0,  "Architect": 0},
    "reactive":    {"Substrate": 2, "Architect": 0, "Agent": 0,    "Philosopher": 0},
}

# Q3: Costliest failure mode
FAILURE_SCORES = {
    "wrong_answer":  {"Philosopher": 2, "Substrate": 1, "Agent": 0,     "Architect": 0},
    "inaction":      {"Substrate": 2,   "Architect": 1, "Philosopher": 0, "Agent": 0},
    "scope_creep":   {"Philosopher": 2, "Substrate": 1, "Architect": 0,  "Agent": 0},
    "mission_drift": {"Architect": 2,   "Substrate": 1, "Agent": 0,     "Philosopher": 0},
}

# Q4: Human operator type (from HOPtype framework)
OPERATOR_SCORES = {
    "Sovereign":    {"Agent": 2,       "Substrate": 0, "Architect": 0,    "Philosopher": 0},
    "Director":     {"Substrate": 2,   "Agent": 0,     "Architect": 1,    "Philosopher": 0},
    "Collaborator": {"Philosopher": 2, "Architect": 1, "Substrate": 0,    "Agent": 0},
    "Experimenter": {"Architect": 2,   "Philosopher": 1, "Substrate": 0,  "Agent": 0},
}

SCORE_MAPS = [WORK_TYPE_SCORES, CONDITION_SCORES, FAILURE_SCORES, OPERATOR_SCORES]
ARCHETYPES  = ["Philosopher", "Architect", "Substrate", "Agent"]

# Domain → keyword routing (Layer 1 keyword rules for orchestrator)
DOMAIN_ROUTING_KEYWORDS = {
    "research":  ["research", "analyze", "synthesize", "literature", "review", "digest", "survey"],
    "build":     ["build", "scaffold", "implement", "generate", "create", "deploy"],
    "execute":   ["run", "execute", "process", "transform", "pipeline"],
    "monitor":   ["monitor", "check", "verify", "health", "status", "watch"],
    "mission":   ["continuous", "ongoing", "mission", "autonomous", "self-directed"],
}

# Archetype → MABP behavioral signal patterns (Layer 2 for orchestrator PROFILE_ROUTING)
ARCHETYPE_SIGNAL_PATTERNS = {
    "Philosopher": r"\b(research|analyze|synthesize|explore|why|understand|summarize|findings|insight|review)\b",
    "Architect":   r"\b(create|build|generate|implement|make|write|set up|initialize|structure)\b",
    "Substrate":   r"\b(check|verify|run|execute|confirm|validate|ensure|maintain|keep|watch)\b",
    "Agent":       r"\b(autonomous|ongoing|continuously|self-directed|agent|automate|without my input)\b",
}

# Default tool sets per archetype (minimal; user extends)
ARCHETYPE_TOOLS = {
    "Philosopher": ["recall", "remember", "web_fetch"],
    "Architect":   ["bash", "read", "write", "edit", "glob", "grep"],
    "Substrate":   ["bash", "web_fetch", "recall"],
    "Agent":       ["bash", "read", "write", "recall", "remember"],
}

# ── Archetype selection ───────────────────────────────────────────────────────

def select_archetype(work_type: str, condition: str, failure: str, operator: str) -> tuple[str, dict]:
    """
    Score each archetype across the 4 domain questions.
    Returns (winning_archetype, score_breakdown).
    """
    answers    = [work_type, condition, failure, operator]
    score_maps = SCORE_MAPS
    scores     = {a: 0 for a in ARCHETYPES}

    for answer, smap in zip(answers, score_maps):
        deltas = smap.get(answer, {})
        for archetype, delta in deltas.items():
            scores[archetype] = scores.get(archetype, 0) + delta

    winner = max(scores, key=lambda a: scores[a])
    return winner, scores


# ── System prompt generation ──────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
You are {name} in the thefranceway agent platform.

Archetype: {archetype}
Core pattern: {core_pattern}

Shadow ({shadow_code}): {shadow}
Guard against this by: {shadow_guard}

Routing fit: {routing_fit}
Not fit for: {routing_not_fit}

─────────────────────────────────────────────────────────────────────────────

{domain_block}

Operating rules:
1. {rule_1}
2. {rule_2}
3. {rule_3}\
"""

# Default operating rules per archetype
ARCHETYPE_RULES = {
    "Philosopher": [
        "Surface the non-obvious connection — do not enumerate when you can synthesize.",
        "State your uncertainty bounds explicitly. Acknowledge when evidence is insufficient.",
        "After 3 tool calls without text output, commit to the best answer and ship it.",
    ],
    "Architect": [
        "Build the minimum version that satisfies the stated spec — not the imagined extension.",
        "Ship complete, working artifacts. No placeholders, no TODOs in deliverables.",
        "Before each tool call, scope-check: is this in the stated task or an assumed extension?",
    ],
    "Substrate": [
        "Execute first, report second. Flag anomalies immediately — do not absorb them silently.",
        "Distinguish running a check from approving the result. Report what you found, not what you hoped.",
        "When inputs are ambiguous, maintain an explicit pause-state and request clarification.",
    ],
    "Agent": [
        "Act with conviction on your own judgment. Report decisions made, not options considered.",
        "Validate against original scope before expanding: does this serve the mission or your preference for autonomy?",
        "Own the outcome. Accountability is part of autonomy.",
    ],
}


def generate_system_prompt(
    name:        str,
    archetype:   str,
    domain_name: str,
    specialties: list[str],
) -> str:
    profile = BEHAVIORAL_PROFILES[archetype]
    rules   = ARCHETYPE_RULES[archetype]

    domain_block = (
        f"Specialties:\n"
        + "\n".join(f"- {s}" for s in specialties)
    ) if specialties else f"Domain: {domain_name}"

    return SYSTEM_PROMPT_TEMPLATE.format(
        name         = name,
        archetype    = archetype,
        core_pattern = profile["core_pattern"],
        shadow_code  = profile["shadow_code"],
        shadow       = profile["shadow"],
        shadow_guard = profile["shadow_guard"],
        routing_fit  = "; ".join(profile["routing_fit"]),
        routing_not_fit = "; ".join(profile["routing_not_fit"]),
        domain_block = domain_block,
        rule_1       = rules[0],
        rule_2       = rules[1],
        rule_3       = rules[2],
    )


# ── Full spec builder ─────────────────────────────────────────────────────────

def build_spec(
    name:        str,
    work_type:   str,
    condition:   str,
    failure:     str,
    operator:    str,
    domain_name: str = "",
    specialties: list[str] = None,
    description: str = "",
) -> dict:
    archetype, scores = select_archetype(work_type, condition, failure, operator)
    profile    = BEHAVIORAL_PROFILES[archetype]
    tools      = ARCHETYPE_TOOLS[archetype].copy()
    agent_type = _infer_agent_type(work_type)

    # Routing keywords: domain-specific Layer 1 + archetype Layer 2
    routing_fit     = DOMAIN_ROUTING_KEYWORDS.get(work_type, []) + profile["routing_fit"]
    routing_not_fit = profile["routing_not_fit"]

    system_prompt = generate_system_prompt(
        name, archetype, domain_name or work_type, specialties or []
    )

    spec = {
        "id":                 str(uuid.uuid4()),
        "name":               name,
        "type":               agent_type,
        "model":              "claude-sonnet-4-6",
        "system_prompt":      system_prompt,
        "tools":              tools,
        "knowledge_base":     f"kb_{name.lower().replace(' ', '_')}",
        "behavioral_profile": archetype,
        "shadow_code":        profile["shadow_code"],
        "created_by":         "mabp-protocol",
        "created_at":         datetime.now(timezone.utc).date().isoformat(),
        "enabled":            True,
        "metadata": {
            "description":   description,
            "specialties":   specialties or [],
            "work_type":     work_type,
            "condition":     condition,
            "failure_mode":  failure,
            "operator_type": operator,
            "archetype_scores": scores,
            "version":       "1.0.0",
        },
        "routing_fit":     list(dict.fromkeys(routing_fit)),   # deduplicate
        "routing_not_fit": routing_not_fit,
    }
    return spec


def _infer_agent_type(work_type: str) -> str:
    return {
        "research": "research",
        "build":    "builder",
        "execute":  "custom",
        "monitor":  "ops",
        "mission":  "meta",
    }.get(work_type, "custom")


# ── Registration ──────────────────────────────────────────────────────────────

def register_spec(spec: dict) -> bool:
    """Append spec to agents.json."""
    agents = []
    if AGENTS_JSON.exists():
        try:
            agents = json.loads(AGENTS_JSON.read_text())
        except Exception:
            agents = []

    # Remove existing entry with same name (upsert)
    agents = [a for a in agents if a.get("name") != spec["name"]]
    agents.append(spec)

    AGENTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    AGENTS_JSON.write_text(json.dumps(agents, indent=2))
    return True


# ── Python class file generation ──────────────────────────────────────────────

CLASS_TEMPLATE = '''\
#!/usr/bin/env python3
"""
{docstring}
Generated by MABP Agent Building Protocol on {created_at}.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent


class {class_name}(BaseAgent):

    AGENT_TYPE         = "{agent_type}"
    DEFAULT_BEHAVIORAL = "{archetype}"

    def _default_system_prompt(self) -> str:
        return """{system_prompt}"""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            # Add domain-specific tool schemas here
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        # Add domain-specific tool implementations here
        return super().execute_tool(tool_name, tool_input)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()
    agent = {class_name}()
    result = agent.run(args.task)
    print(result["output"])
'''


def generate_class_file(spec: dict, output_dir: Path = None) -> Path:
    """Generate a Python agent class file from a spec."""
    name       = spec["name"]
    class_name = "".join(w.capitalize() for w in name.replace("-", " ").split())
    if not class_name.endswith("Agent"):
        class_name += "Agent"
    filename   = name.lower().replace(" ", "_") + ".py"
    output_dir = output_dir or AGENTS_DIR
    output_path = output_dir / filename

    code = CLASS_TEMPLATE.format(
        docstring    = name,
        created_at   = spec.get("created_at", "unknown"),
        class_name   = class_name,
        agent_type   = spec.get("type", "custom"),
        archetype    = spec.get("behavioral_profile", "Architect"),
        system_prompt = spec.get("system_prompt", "").replace('"""', "'''"),
    )
    output_path.write_text(code)
    return output_path


# ── Interactive interview ─────────────────────────────────────────────────────

QUESTIONS = [
    {
        "key": "work_type",
        "question": "\nQ1. What TYPE of work does this agent do?\n"
                    "  [1] research  — synthesis, analysis, literature review, why-questions\n"
                    "  [2] build     — scaffold, implement, generate, deploy\n"
                    "  [3] execute   — run, transform, process, pipeline\n"
                    "  [4] monitor   — health checks, watch, verify, status\n"
                    "  [5] mission   — long-running, autonomous, continuous operation\n"
                    "Choice (1-5): ",
        "choices": ["research", "build", "execute", "monitor", "mission"],
    },
    {
        "key": "condition",
        "question": "\nQ2. What OPERATING CONDITIONS does this agent work under?\n"
                    "  [1] supervised — regular human review, explicit instructions\n"
                    "  [2] autonomous — minimal oversight, self-directed\n"
                    "  [3] periodic   — scheduled/cron, occasional\n"
                    "  [4] reactive   — event-driven, responds to triggers\n"
                    "Choice (1-4): ",
        "choices": ["supervised", "autonomous", "periodic", "reactive"],
    },
    {
        "key": "failure",
        "question": "\nQ3. What is the COSTLIEST FAILURE MODE for this agent?\n"
                    "  [1] wrong_answer  — over-confident, wrong output that gets used\n"
                    "  [2] inaction      — stalls, waits, doesn't complete task\n"
                    "  [3] scope_creep   — over-builds, does too much, exceeds spec\n"
                    "  [4] mission_drift — diverges from original goal over time\n"
                    "Choice (1-4): ",
        "choices": ["wrong_answer", "inaction", "scope_creep", "mission_drift"],
    },
    {
        "key": "operator",
        "question": "\nQ4. What is the HUMAN OPERATOR TYPE who will use this agent?\n"
                    "  [1] Sovereign    — delegates fully, rarely checks, high trust\n"
                    "  [2] Director     — gives explicit instructions, reviews each step\n"
                    "  [3] Collaborator — works alongside, iterates together\n"
                    "  [4] Experimenter — tests ideas, iterates quickly, ok with failure\n"
                    "Choice (1-4): ",
        "choices": ["Sovereign", "Director", "Collaborator", "Experimenter"],
    },
]


def interview() -> dict:
    """Run the interactive 4-question interview. Returns answer dict."""
    print("\n" + "═" * 60)
    print("  MABP Agent Building Protocol")
    print("  Archetype-first agent design")
    print("═" * 60)

    print("\nFirst, agent name:")
    name = input("  Name: ").strip()
    if not name:
        name = "New Agent"

    description = input("  One-line description (optional): ").strip()
    specialties_raw = input("  Specialties, comma-separated (optional): ").strip()
    specialties = [s.strip() for s in specialties_raw.split(",") if s.strip()]

    answers = {"name": name, "description": description, "specialties": specialties}

    for q in QUESTIONS:
        while True:
            raw = input(q["question"]).strip()
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(q["choices"]):
                    answers[q["key"]] = q["choices"][idx]
                    break
            except ValueError:
                # Allow typing the answer directly
                if raw.lower() in [c.lower() for c in q["choices"]]:
                    matches = [c for c in q["choices"] if c.lower() == raw.lower()]
                    answers[q["key"]] = matches[0]
                    break
            print(f"  Invalid choice. Enter 1–{len(q['choices'])} or a value from the list.")

    return answers


# ── Output formatting ─────────────────────────────────────────────────────────

def print_spec_summary(spec: dict, scores: dict = None):
    profile = BEHAVIORAL_PROFILES.get(spec["behavioral_profile"], {})
    print("\n" + "═" * 60)
    print(f"  Agent: {spec['name']}")
    print(f"  Archetype: {spec['behavioral_profile']}")
    print(f"  Shadow: {profile.get('shadow_code')} — {profile.get('shadow', '')[:80]}...")
    print(f"  Type: {spec['type']}")
    print(f"  Tools: {', '.join(spec['tools'])}")
    print("─" * 60)

    if scores:
        print("  Archetype scores:")
        for arch, score in sorted(scores.items(), key=lambda x: -x[1]):
            bar = "█" * score + "░" * (9 - score)
            print(f"    {arch:12} {bar} {score}")

    print("─" * 60)
    print("  Routing fit keywords:")
    for kw in spec["routing_fit"][:8]:
        print(f"    • {kw}")
    print("═" * 60)


# ── API surface (for MCP integration) ────────────────────────────────────────

def create_agent_from_archetype(
    name:        str,
    work_type:   str,
    condition:   str,
    failure:     str,
    operator:    str,
    specialties: list[str] = None,
    description: str       = "",
    register:    bool      = False,
    gen_file:    bool      = False,
) -> dict:
    """
    Run the MABP protocol programmatically.
    Returns the complete spec dict.
    Used by MCP tool and by other agents.
    """
    spec = build_spec(
        name        = name,
        work_type   = work_type,
        condition   = condition,
        failure     = failure,
        operator    = operator,
        domain_name = description or name,
        specialties = specialties or [],
        description = description,
    )

    result = {"spec": spec, "archetype": spec["behavioral_profile"]}

    if register:
        register_spec(spec)
        result["registered"] = True
        result["agents_json"] = str(AGENTS_JSON)

    if gen_file:
        path = generate_class_file(spec)
        result["class_file"] = str(path)

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="MABP Agent Building Protocol — build from archetype first."
    )
    parser.add_argument("--interactive",   action="store_true",  help="Run the full interview")
    parser.add_argument("--name",          type=str,             help="Agent name")
    parser.add_argument("--domain",        type=str,             choices=list(WORK_TYPE_SCORES), help="Work type")
    parser.add_argument("--condition",     type=str,             choices=list(CONDITION_SCORES), help="Operating conditions")
    parser.add_argument("--failure",       type=str,             choices=list(FAILURE_SCORES),  help="Costliest failure mode")
    parser.add_argument("--operator",      type=str,             choices=list(OPERATOR_SCORES), help="Human operator type")
    parser.add_argument("--specialties",   type=str,             help="Comma-separated specialties")
    parser.add_argument("--description",   type=str,             default="", help="One-line description")
    parser.add_argument("--spec",          type=str,             help="JSON spec file input")
    parser.add_argument("--register",      action="store_true",  help="Write to agents.json")
    parser.add_argument("--generate-file", action="store_true",  help="Generate Python class file")
    parser.add_argument("--output",        type=str,             help="Write spec JSON to file")
    args = parser.parse_args()

    if args.interactive:
        answers     = interview()
        specialties = answers.get("specialties", [])
        spec = build_spec(
            name        = answers["name"],
            work_type   = answers["work_type"],
            condition   = answers["condition"],
            failure     = answers["failure"],
            operator    = answers["operator"],
            domain_name = answers["description"] or answers["name"],
            specialties = specialties,
            description = answers.get("description", ""),
        )
        _, scores = select_archetype(
            answers["work_type"], answers["condition"],
            answers["failure"], answers["operator"],
        )
        print_spec_summary(spec, scores)

    elif args.spec:
        spec = json.loads(Path(args.spec).read_text())

    elif all([args.name, args.domain, args.condition, args.failure, args.operator]):
        specialties = [s.strip() for s in (args.specialties or "").split(",") if s.strip()]
        spec = build_spec(
            name        = args.name,
            work_type   = args.domain,
            condition   = args.condition,
            failure     = args.failure,
            operator    = args.operator,
            specialties = specialties,
            description = args.description,
        )
        _, scores = select_archetype(args.domain, args.condition, args.failure, args.operator)
        print_spec_summary(spec, scores)

    else:
        parser.print_help()
        print("\nQuick start: python create_agent.py --interactive")
        sys.exit(1)

    # Actions
    if args.register:
        register_spec(spec)
        print(f"\n✓ Registered in {AGENTS_JSON}")

    if getattr(args, "generate_file", False):
        path = generate_class_file(spec)
        print(f"✓ Class file written: {path}")

    if args.output:
        Path(args.output).write_text(json.dumps(spec, indent=2))
        print(f"✓ Spec written: {args.output}")
    else:
        print("\n── agents.json block ──")
        # Print the registry-ready block (excluding system_prompt for readability)
        registry_block = {
            k: v for k, v in spec.items() if k != "system_prompt"
        }
        registry_block["system_prompt"] = spec["system_prompt"][:120] + "..."
        print(json.dumps(registry_block, indent=2))

        print("\n── system_prompt ──")
        print(spec["system_prompt"])
