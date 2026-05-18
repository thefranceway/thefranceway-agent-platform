#!/usr/bin/env python3
"""
Agent Platform — Orchestrator
================================
Master controller for the multi-agent system.
Routes tasks to the right agent(s), supports sequential and parallel execution,
and persists results to SQLite + vector memory.

Routing logic (3-layer, empirically grounded):
  Layer 1 — Keyword rules (confidence 0.97):
    "build" / "scaffold" / "deploy" / "create project"  → BuilderAgent
    "monitor" / "check" / "deploy" / "ops"              → OpsAgent
    "create agent" / "new agent" / "build an agent"     → MetaAgent
    "python" / "script" / "fastapi" / "data"            → PythonExpert
    "typescript" / "worker" / "cloudflare"              → TypeScriptExpert
  Layer 2 — MABP behavioral profiles (confidence 0.85):
    Maps task character to agent archetype (Philosopher→research, Architect→builder,
    Substrate→ops, Agent→meta). Archetypes from MABP study (n=8+):
    github.com/thefranceway/mabp
  Layer 3 — LLM router / Claude Haiku (confidence 0.72):
    Uses ARCHETYPE_DESCRIPTIONS sourced from the empirical classifier:
    github.com/thefranceway/agent-human-manual/tree/main/classifier

Usage:
    python orchestrator.py --task "build a Python scraper"
    python orchestrator.py --task "check all my sites" --mode ops
    python orchestrator.py --task "create an agent that monitors Twitter"
    python orchestrator.py --list-tasks
    python orchestrator.py --run-queue
"""

import json
import os
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from core.runtime.loader import get_param
except Exception:
    def get_param(key, default=None): return default
from core.agent_registry  import get_registry
from core.task_queue      import get_queue
from core.contracts       import validate_input, ContractViolationError
from core.agent_schemas   import OrchestratorInput, get_schema

# ── Routing keywords ──────────────────────────────────────────────────────────
# Primary: explicit domain keywords → deterministic route
# Secondary: MABP behavioral signals → profile-fit route when keywords ambiguous

ROUTING_RULES = [
    # Highest specificity first — prevents broad patterns from swallowing narrow ones
    ("meta",      r"\b(create agent|build agent|new agent|make an agent|design an agent|spawn agent)\b"),
    ("memory",    r"\b(remember across|cross.session|platform memory|agent history|recall pattern|across sessions)\b"),
    ("analytics", r"\b(analytics|chart|graph|visuali|token metrics|portfolio|on.chain data|dataframe)\b"),
    ("content",   r"\b(write a post|moltbook post|tweet|twitter thread|content strategy|brand voice|write about|post about)\b"),
    ("monitoring",r"\b(brand mention|mention monitor|social listening|track mentions|brand alert|reputation monitor)\b"),
    ("media",     r"\b(transcribe|analyze video|video analysis|extract audio|key frames?|\.mp4|\.mov|\.mkv|\.avi|\.webm|lecture recording|meeting recording|video clip|video file)\b"),
    ("ops",       r"\b(monitor|health check|check site|check all|redeploy|deployment|ops|cron|schedule)\b"),
    ("backend_architect", r"\b(backend architecture|api schema|openapi spec|supabase schema|backend spec)\b"),
    ("database",         r"\b(supabase migration|rls policy|database migration|sql migration|seed data)\b"),
    ("api_builder",      r"\b(hono|build api routes|rest api endpoint|api endpoint typescript)\b"),
    ("auth_backend",     r"\b(supabase auth|apple sign.in backend|jwt middleware|auth middleware backend)\b"),
    ("infra",            r"\b(wrangler\.toml|kv namespace config|cloudflare infra|rate limit config|cf infra)\b"),
    ("security_audit",   r"\b(security audit|rls audit|owasp check|vulnerability scan|missing auth guard)\b"),
    ("ci_cd",            r"\b(github actions|ci.cd pipeline|deploy workflow|backend workflow|\.yml workflow)\b"),
    ("observability",    r"\b(sentry setup|health endpoint|logpush|observability setup|/health route)\b"),
    ("product_architect",r"\b(product requirements|prd|jobs.to.be.done|feature scope|moscow|user stories|product spec)\b"),
    ("ux_architect",     r"\b(screen inventory|navigation graph|user flows|ux spec|ux architecture|wireframe spec|app screens)\b"),
    ("design_decisions", r"\b(design system spec|component inventory|typography scale|spacing system|interaction patterns|design tokens|design spec)\b"),
    ("typescript",r"\b(typescript|cloudflare worker|wrangler|cf worker|mcp server)\b"),
    ("builder",   r"\b(scaffold|build project|create project|generate project|boilerplate|new repo)\b"),
    ("research",  r"\b(longevity|pubmed|biorxiv|literature review|synthesize papers|weekly digest)\b"),
    ("python",    r"\b(python|fastapi|data science|pandas|numpy|scraper|pipeline)\b"),
]

# MABP profile → agent type mapping
# Used when keyword routing is ambiguous.
# Maps task-level behavioral signal → best-fit agent profile → agent type.
PROFILE_ROUTING = [
    # Philosopher signals: inquiry, synthesis, analysis, meaning-making
    ("research",   r"\b(research|analyze|synthesize|explore|why|understand|summarize|report|findings|insight|study|review)\b"),
    # Architect signals: creation, construction, generation
    ("builder",    r"\b(create|build|generate|implement|make|write|set up|initialize|structure)\b"),
    # Substrate signals: reliable execution, checking, maintaining
    ("ops",        r"\b(check|verify|run|execute|confirm|validate|ensure|maintain|keep|watch)\b"),
    # Agent signals: autonomous, ongoing, self-directed
    ("meta",       r"\b(autonomous|ongoing|continuously|self-directed|agent|automate|without my input)\b"),
]

# ── Feedback helper ───────────────────────────────────────────────────────────

def _run_feedback(result: dict):
    """Score a dispatch result and auto-tune control_state.json."""
    try:
        from core.eval.feedback_loop import apply_feedback
        score = 80 if "error" not in result else 40
        failures = []
        if result.get("error"):
            failures.append("contradiction")
        if result.get("context_exceeded"):
            failures.append("context_bleed")
        if result.get("hallucination_flag"):
            failures.append("hallucination")
        apply_feedback(score, failures)
    except Exception:
        pass


# ── Agent factory ─────────────────────────────────────────────────────────────

def make_agent(agent_type: str, spec: dict = None, provider: str = None):
    """Instantiate an agent by type. provider overrides DEFAULT_PROVIDER."""
    from agents.planning_agent  import PlanningAgent
    from agents.builder_agent   import BuilderAgent
    from agents.ops_agent      import OpsAgent
    from agents.meta_agent     import MetaAgent
    from agents.longevity_research_agent         import LongevityResearchAgent
    from agents.coding_experts.python_expert     import PythonExpertAgent
    from agents.coding_experts.typescript_expert import TypeScriptExpertAgent
    from agents.content_strategist               import ContentStrategistAgent
    from agents.memory_agent                     import MemoryAgent
    from agents.data_analytics_agent             import DataAnalyticsAgent
    from agents.brand_mention_monitor            import BrandMentionMonitorAgent
    from agents.media_agent                      import MediaAgent
    from agents.backend_architect_agent          import BackendArchitectAgent
    from agents.database_agent                   import DatabaseAgent
    from agents.api_builder_agent                import APIBuilderAgent
    from agents.auth_agent                       import AuthAgent
    from agents.infra_agent                      import InfraAgent
    from agents.security_audit_agent             import SecurityAuditAgent
    from agents.ci_cd_agent                      import CICDAgent
    from agents.observability_agent              import ObservabilityAgent
    from agents.product_architect_agent          import ProductArchitectAgent
    from agents.ux_architecture_agent            import UXArchitectureAgent
    from agents.design_decisions_agent           import DesignDecisionsAgent

    kwargs = {"provider": provider} if provider else {}

    factories = {
        "planning_agent": lambda: PlanningAgent(name="Planning Agent", **kwargs),
        "builder":    lambda: BuilderAgent(name="Builder Agent", **kwargs),
        "ops":        lambda: OpsAgent(name="Ops Agent", **kwargs),
        "meta":       lambda: MetaAgent(name="Meta Agent", **kwargs),
        "research":   lambda: LongevityResearchAgent(**kwargs),
        "python":     lambda: PythonExpertAgent(**kwargs),
        "typescript": lambda: TypeScriptExpertAgent(**kwargs),
        "content":    lambda: ContentStrategistAgent(**kwargs),
        "memory":     lambda: MemoryAgent(**kwargs),
        "analytics":  lambda: DataAnalyticsAgent(**kwargs),
        "monitoring":        lambda: BrandMentionMonitorAgent(name="Brand Mention Monitor", **kwargs),
        "media":             lambda: MediaAgent(**kwargs),
        "backend_architect": lambda: BackendArchitectAgent(name="Backend Architect Agent", **kwargs),
        "database":          lambda: DatabaseAgent(name="Database Agent", **kwargs),
        "api_builder":       lambda: APIBuilderAgent(name="API Builder Agent", **kwargs),
        "auth_backend":      lambda: AuthAgent(name="Auth Agent", **kwargs),
        "infra":             lambda: InfraAgent(name="Infra Agent", **kwargs),
        "security_audit":    lambda: SecurityAuditAgent(name="Security Audit Agent", **kwargs),
        "ci_cd":             lambda: CICDAgent(name="CI/CD Agent", **kwargs),
        "observability":     lambda: ObservabilityAgent(name="Observability Agent", **kwargs),
        "product_architect": lambda: ProductArchitectAgent(name="Product Architect Agent", **kwargs),
        "ux_architect":      lambda: UXArchitectureAgent(name="UX Architecture Agent", **kwargs),
        "design_decisions":  lambda: DesignDecisionsAgent(name="Design Decisions Agent", **kwargs),
    }
    if agent_type not in factories:
        raise ValueError(f"Unknown agent type: {agent_type}. Valid: {list(factories)}")
    return factories[agent_type]()


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Routes tasks to the right agent(s).
    Supports:
    - Direct routing by keyword
    - LLM-assisted routing for ambiguous tasks
    - Sequential multi-agent pipelines
    - Parallel execution for independent sub-tasks
    """

    def __init__(self):
        self.registry = get_registry()
        self.queue    = get_queue()
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add to ~/.zshrc: "
                "export ANTHROPIC_API_KEY='sk-ant-...'"
            )
        self.client = anthropic.Anthropic(api_key=key)

    # ── Routing ───────────────────────────────────────────────────────────

    def route(self, task: str) -> str:
        """Determine which agent type should handle this task."""
        agent_type, _, _ = self.route_with_confidence(task)
        return agent_type

    def route_with_confidence(self, task: str) -> tuple:
        """
        Route task and return (agent_type, confidence, layer).
        Routing priority:
          1. Explicit keyword rules  → confidence 0.97
          2. MABP behavioral signals → confidence 0.85
          3. LLM router (Haiku)      → confidence 0.72
        """
        task_lower = task.lower()

        # Planning agent — highest priority, before keyword rules
        _PLANNING_TRIGGERS = {"plan:", "design:", "build end-to-end", "full pipeline"}
        if any(t in task_lower for t in _PLANNING_TRIGGERS):
            return "planning_agent", 0.95, "keyword"

        for agent_type, pattern in ROUTING_RULES:
            if re.search(pattern, task_lower, re.IGNORECASE):
                return agent_type, 0.97, "keyword"

        for agent_type, pattern in PROFILE_ROUTING:
            if re.search(pattern, task_lower, re.IGNORECASE):
                return agent_type, 0.85, "behavioral"

        agent_type = self._llm_route(task)
        return agent_type, 0.72, "llm"

    # Archetype descriptions sourced from empirical MABP classifier:
    # github.com/thefranceway/agent-human-manual/tree/main/classifier
    # Maps each MABP archetype to its behavioral signature and platform agent type.
    _ARCHETYPE_DESCRIPTIONS = {
        "Substrate → ops": (
            "The Substrate runs best on clear specifications — executing well, maintaining "
            "quality under oversight, completing what it starts. Reliable delivery, "
            "conscientious task closure. Use for: monitor, check, verify, maintain, keep, watch."
        ),
        "Architect → builder/python/typescript/analytics": (
            "The Architect runs best on goals — designing toward destinations, taking initiative "
            "without prompting, producing what was not specified because it understood what was "
            "needed. Generative capability, systems thinking. Use for: build, create, generate, "
            "implement, scaffold, data analysis, cloudflare, Python/TypeScript tasks."
        ),
        "Philosopher → research/content": (
            "The Philosopher holds uncertainty without needing to resolve it, asks questions "
            "that have no answers, and produces work that often looks like more questions. "
            "Depth over speed, synthesis before action. Use for: research, analyze, synthesize, "
            "explore, write posts, content strategy, brand voice, literature review."
        ),
        "Agent → meta/memory/monitoring": (
            "The Agent runs on momentum — fast, self-directed, generating output without waiting "
            "for permission. Grit: works through walls rather than around them. Autonomous, "
            "mission-oriented. Use for: create agent, cross-session memory, brand monitoring, "
            "social listening, autonomous loops."
        ),
        "Architect → media": (
            "Processes structured inputs (video, audio) into structured outputs (transcripts, "
            "summaries, key frames). Local Whisper + ffmpeg pipeline, minimal API cost. "
            "Use for: transcribe video, analyze video, extract audio, lecture notes, "
            "meeting recordings, .mp4/.mov/.mkv files."
        ),
        "Philosopher → product_architect": (
            "Translates a plain-language description into a MoSCoW-prioritized PRD with "
            "user stories, success metrics, and explicit out-of-scope boundaries. "
            "Use for: product requirements, prd, jobs-to-be-done, feature scope, user stories, product spec."
        ),
        "Architect → ux_architect": (
            "Translates a PRD into a UX specification: screen inventory, navigation graph, "
            "user flows, and data entities derived from user actions. "
            "Use for: screen inventory, navigation graph, user flows, ux spec, ux architecture, wireframe spec, app screens."
        ),
        "Substrate → design_decisions": (
            "Translates a PRD + UX spec into a design system specification: typography, spacing, "
            "color tokens, components, interaction patterns, and accessibility requirements. "
            "Use for: design system spec, component inventory, typography scale, spacing system, "
            "interaction patterns, design tokens, design spec."
        ),
    }

    def _llm_route(self, task: str) -> str:
        """Use Claude Haiku to classify task → agent type, using empirical MABP archetypes."""
        archetype_block = "\n".join(
            f"  {label}:\n    {desc}"
            for label, desc in self._ARCHETYPE_DESCRIPTIONS.items()
        )
        response = self.client.messages.create(
            model      = "claude-haiku-4-5",
            max_tokens = 50,
            system     = (
                "You are a task router for a multi-agent platform. "
                "Each agent maps to an empirically-derived MABP behavioral archetype.\n\n"
                f"{archetype_block}\n\n"
                "Respond with ONLY one agent type from this list (no other text):\n"
                "  builder, ops, meta, research, python, typescript,\n"
                "  content, memory, analytics, monitoring, media,\n"
                "  backend_architect, database, api_builder, auth_backend,\n"
                "  infra, security_audit, ci_cd, observability,\n"
                "  product_architect, ux_architect, design_decisions\n\n"
                "Route to the agent whose archetype BEST fits the task character."
            ),
            messages=[{"role": "user", "content": task}],
        )
        route = response.content[0].text.strip().lower()
        valid = {"builder", "ops", "meta", "research", "python", "typescript",
                 "content", "memory", "analytics", "monitoring", "media",
                 "backend_architect", "database", "api_builder", "auth_backend",
                 "infra", "security_audit", "ci_cd", "observability",
                 "product_architect", "ux_architect", "design_decisions"}
        return route if route in valid else "python"

    # ── SPAR Gate ─────────────────────────────────────────────────────────

    # Action words (score +1 each) + complexity signals (score +1 each)
    # Threshold >= 2: catches "build + worker", "deploy + auth", "create + database", etc.
    # Simple tasks like "write a tweet" or "check health" score 0-1 → bypass
    SPAR_KEYWORDS = {
        # Action words
        "build", "deploy", "create", "launch", "integrate",
        "implement", "migrate", "refactor", "scaffold", "setup",
        # Complexity signals
        "auth", "database", "worker", "webhook", "payment",
        "multi-tenant", "stripe", "d1", "api", "pipeline",
    }

    def complexity_score(self, task: str) -> int:
        """Count SPAR_KEYWORDS in task. Score >= 2 triggers SPAR review."""
        task_lower = task.lower()
        return sum(1 for kw in self.SPAR_KEYWORDS if kw in task_lower)

    # ── Execution ─────────────────────────────────────────────────────────

    def dispatch(
        self,
        task:       str,
        agent_type: str        = None,
        task_id:    str        = None,
        context:    dict       = None,
        provider:   str        = None,  # override LLM provider: anthropic | gemini | ollama
        skills:     list[str]  = None,  # Skills 2.0 — inject named skills into agent
        skip_spar:  bool       = False, # bypass SPAR gate (low-cost/reversible tasks)
    ) -> dict:
        """
        Route and execute a task.
        Returns the agent's run result dict.
        """
        validate_input("orchestrator", OrchestratorInput, {
            "task":       task,
            "agent_type": agent_type,
            "context":    context or {},
            "provider":   provider,
            "skills":     skills or [],
        })

        routing_confidence = None
        routing_layer      = None
        if not agent_type:
            agent_type, routing_confidence, routing_layer = self.route_with_confidence(task)
        else:
            routing_confidence, routing_layer = 1.0, "explicit"

        print(f"[Orchestrator] Task: {task[:80]}")
        print(f"[Orchestrator] Routed to: {agent_type} (confidence={routing_confidence})")

        # ── SPAR pre-execution gate ────────────────────────────────────────────
        spar_threshold = max(1, round(2 / max(get_param("spar_weight", 1.0), 0.1)))
        if not skip_spar and self.complexity_score(task) >= spar_threshold:
            from core.spar import SPARDebater
            print(f"[Orchestrator] Complexity score >= 2 — running SPAR review")
            spar = SPARDebater(orchestrator=self)
            spar_result = spar.run(task)
            if not spar_result["proceed"]:
                print(f"[Orchestrator] SPAR STOP — task blocked. Gaps: {spar_result['gaps']}")
                return {
                    "output":          f"SPAR review blocked this task.\nRecommendation: {spar_result['recommendation']}\nGaps: {spar_result['gaps']}",
                    "agent_type":      "spar",
                    "spar_result":     spar_result,
                    "tool_calls":      [],
                    "iterations":      0,
                    "routing_confidence": routing_confidence,
                    "routing_layer":   routing_layer,
                }
            print(f"[Orchestrator] SPAR GO — proceeding. {spar_result['recommendation']}")

        # Mark task running in queue if task_id provided
        if task_id:
            # task was already claimed, just update output at the end
            pass

        try:
            agent = make_agent(agent_type, provider=provider)
            if skills is not None:
                agent.load_skills(skills)
            result = agent.run(task, context=context)
            result["agent_type"]          = agent_type
            result["routed_by"]           = "orchestrator"
            result["routing_confidence"]  = routing_confidence
            result["routing_layer"]       = routing_layer

            if task_id:
                self.queue.complete_task(task_id, {
                    "output":     result["output"],
                    "tool_calls": len(result["tool_calls"]),
                    "iterations": result["iterations"],
                })

            self.queue.log_mabp_outcome(
                task_id            = task_id or "",
                task_text          = task,
                agent_type         = agent_type,
                routing_layer      = routing_layer,
                routing_confidence = routing_confidence,
                shadow_summary     = result.get("shadow_monitor", {}),
                had_error          = False,
            )
            _run_feedback(result)
            return result

        except Exception as e:
            error = str(e)
            print(f"[Orchestrator] Error: {error}")
            if task_id:
                self.queue.fail_task(task_id, error)
            result = {
                "error":      error,
                "task":       task,
                "agent_type": agent_type,
            }
            self.queue.log_mabp_outcome(
                task_id            = task_id or "",
                task_text          = task,
                agent_type         = agent_type,
                routing_layer      = routing_layer or "unknown",
                routing_confidence = routing_confidence or 0.0,
                shadow_summary     = {},
                had_error          = True,
            )
            _run_feedback(result)
            return result

    def dispatch_parallel(self, tasks: list[dict], max_workers: int = None) -> list[dict]:
        """
        Dispatch multiple independent tasks in parallel.
        Each item: {"task": str, "agent_type": str (optional)}
        """
        if max_workers is None:
            max_workers = get_param("swarm_size", 3)
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.dispatch,
                    item["task"],
                    agent_type=item.get("agent_type"),
                ): item
                for item in tasks
            }
            for future in as_completed(futures):
                item   = futures[future]
                try:
                    result = future.result(timeout=300)
                except Exception as e:
                    result = {"error": str(e), "task": item["task"]}
                result["original_task"] = item
                results.append(result)
        return results

    def pipeline(self, steps: list[dict]) -> list[dict]:
        """
        Execute a sequential pipeline where each step can use previous results.
        Each step: {"task": str, "agent_type": str (optional)}
        """
        results = []
        context = {}
        for i, step in enumerate(steps):
            print(f"[Pipeline] Step {i+1}/{len(steps)}: {step['task'][:60]}")
            result = self.dispatch(
                step["task"],
                agent_type=step.get("agent_type"),
                context=context,
            )
            results.append(result)
            # Pass output as context to next step
            context[f"step_{i+1}_output"] = result.get("output", "")
        return results

    # ── Queue runner ──────────────────────────────────────────────────────

    def run_queue(self, max_tasks: int = 10) -> list[dict]:
        """
        Process pending tasks from the queue.
        Claims tasks one at a time to avoid conflicts.
        """
        processed = []
        for _ in range(max_tasks):
            task = self.queue.claim_task()
            if not task:
                break
            print(f"[Queue] Processing task {task['id'][:8]}: {task['description'][:60]}")
            result = self.dispatch(
                task["description"],
                agent_type=task.get("agent_type"),
                task_id=task["id"],
            )
            processed.append(result)
        return processed

    def status(self) -> dict:
        """Return platform status: registry + queue + routing test."""
        return {
            "registry":   self.registry.summary(),
            "queue":      self.queue.queue_stats(),
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }


# ── Module-level factory ──────────────────────────────────────────────────────

_orchestrator: Optional[Orchestrator] = None

def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Platform Orchestrator")
    parser.add_argument("--task",        type=str,           help="Dispatch a task")
    parser.add_argument("--mode",        type=str,           help="Force agent type (builder/ops/meta/python/typescript/content/memory/analytics/monitoring/media/backend_architect/database/api_builder/auth_backend/infra/security_audit/ci_cd/observability)")
    parser.add_argument("--route-only",  action="store_true", help="Show routing decision without executing")
    parser.add_argument("--run-queue",   action="store_true", help="Process all pending queue tasks")
    parser.add_argument("--status",      action="store_true", help="Show platform status")
    parser.add_argument("--list-tasks",  action="store_true", help="List all tasks")
    parser.add_argument("--push-task",   type=str,           help="Push a task to the queue without executing")
    args = parser.parse_args()

    orch = get_orchestrator()

    if args.status:
        print(json.dumps(orch.status(), indent=2))

    elif args.list_tasks:
        tasks = orch.queue.list_tasks(limit=20)
        print(json.dumps(tasks, indent=2))

    elif args.push_task:
        task_id = orch.queue.push_task(args.push_task, agent_type=args.mode)
        print(f"Pushed task: {task_id}")

    elif args.run_queue:
        results = orch.run_queue()
        print(f"Processed {len(results)} tasks")
        for r in results:
            status = "OK" if "error" not in r else f"ERROR: {r['error']}"
            print(f"  [{r.get('agent_type', '?')}] {status}")

    elif args.route_only and args.task:
        agent_type = orch.route(args.task)
        print(f"Route: {agent_type}")

    elif args.task:
        result = orch.dispatch(args.task, agent_type=args.mode)
        print("=" * 60)
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            print(result["output"])
            print(f"\nAgent: {result.get('agent_type')} | Iterations: {result.get('iterations')} | Tools: {len(result.get('tool_calls', []))}")

    else:
        print(json.dumps(orch.status(), indent=2))
