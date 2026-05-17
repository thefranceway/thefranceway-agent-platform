#!/usr/bin/env python3
"""
Agent Platform — Swarm Coordinator
=====================================
Coordinates multiple agents using hierarchical or pipeline topologies.

Topologies:
  - hierarchical: lead agent breaks task into subtasks → fan out to N workers
                  in parallel → lead agent synthesizes results
  - pipeline:     sequential chain where each agent's output feeds the next

All signals (subtask dispatch, results, status) are written to the SQLite
signals table for full observability.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PLATFORM_DIR = Path(__file__).parent.parent
DB_PATH      = PLATFORM_DIR / "registry" / "agent_platform.db"

try:
    from core.runtime.loader import get_param
except Exception:
    def get_param(key, default=None): return default


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


class SwarmCoordinator:
    """
    Two topologies for multi-agent coordination.

    hierarchical:
        1. Lead agent receives the task and generates a list of subtasks
        2. Subtasks are dispatched in parallel to worker agents
        3. Lead agent synthesizes all worker outputs into a final result

    pipeline:
        1. Task flows through agents sequentially
        2. Each agent's output becomes the next agent's input context
        3. Returns the final agent's output as the result
    """

    def __init__(self, orchestrator, task_queue=None):
        self.orch  = orchestrator
        self.queue = task_queue

    # ── Public API ────────────────────────────────────────────────────────────

    def hierarchical(
        self,
        task: str,
        lead_agent: str,
        worker_agents: list,
        max_workers: int = None,
    ) -> dict:
        """
        Lead agent decomposes task → workers execute in parallel → lead synthesizes.

        Args:
            task:          The high-level task description
            lead_agent:    Agent type for decomposition + synthesis (e.g. "meta")
            worker_agents: Agent types for parallel execution (e.g. ["builder", "ops"])
            max_workers:   Max concurrent worker threads

        Returns:
            dict with keys: topology, task, subtasks, worker_results, synthesis, signals
        """
        if max_workers is None:
            max_workers = get_param("swarm_size", 3)
        swarm_id = str(uuid.uuid4())

        # Step 1: Lead agent decomposes the task
        decompose_prompt = (
            f"You are coordinating a swarm of agents. Break this task into {len(worker_agents)} "
            f"concrete, independent subtasks — one per worker agent. "
            f"Respond with a JSON array of subtask strings only, no explanation.\n\n"
            f"Task: {task}\n"
            f"Worker agents available: {', '.join(worker_agents)}"
        )
        self._write_signal(
            swarm_id, lead_agent, None, swarm_id, "status",
            {"phase": "decompose", "task": task, "workers": worker_agents}
        )

        decompose_result = self.orch.dispatch(decompose_prompt, agent_type=lead_agent)
        raw_output = decompose_result.get("output", "")

        # Parse subtasks from lead agent output
        subtasks = self._parse_subtasks(raw_output, task, worker_agents)

        # Step 2: Dispatch subtasks in parallel
        parallel_tasks = [
            {"task": subtask, "agent_type": worker_agents[i % len(worker_agents)]}
            for i, subtask in enumerate(subtasks)
        ]
        for pt in parallel_tasks:
            self._write_signal(
                swarm_id, lead_agent, pt["agent_type"], swarm_id, "subtask",
                {"subtask": pt["task"]}
            )

        worker_results = self.orch.dispatch_parallel(parallel_tasks, max_workers=max_workers)

        # Record worker results as signals
        for wr in worker_results:
            self._write_signal(
                swarm_id,
                wr.get("agent_type", "worker"),
                lead_agent,
                swarm_id,
                "result",
                {"output": wr.get("output", "")[:500]}
            )

        # Step 3: Lead agent synthesizes results
        worker_outputs = "\n\n".join([
            f"[{wr.get('agent_type', 'worker').upper()}]: {wr.get('output', '')}"
            for wr in worker_results
        ])
        synthesis_prompt = (
            f"You coordinated a swarm to complete this task: {task}\n\n"
            f"Here are the worker results:\n{worker_outputs}\n\n"
            f"Synthesize these into a single coherent final output."
        )
        synthesis_result = self.orch.dispatch(synthesis_prompt, agent_type=lead_agent)

        self._write_signal(
            swarm_id, lead_agent, None, swarm_id, "status",
            {"phase": "complete", "synthesis_length": len(synthesis_result.get("output", ""))}
        )

        return {
            "topology":       "hierarchical",
            "swarm_id":       swarm_id,
            "task":           task,
            "lead_agent":     lead_agent,
            "subtasks":       subtasks,
            "worker_results": worker_results,
            "synthesis":      synthesis_result.get("output", ""),
        }

    def pipeline(self, task: str, steps: list) -> dict:
        """
        Sequential pipeline — each agent's output feeds the next.

        Args:
            task:  The initial task
            steps: Ordered list of agent types (e.g. ["research", "builder", "ops"])

        Returns:
            dict with keys: topology, task, steps, step_results, output
        """
        swarm_id     = str(uuid.uuid4())
        step_results = []
        current_task = task

        self._write_signal(
            swarm_id, "swarm", None, swarm_id, "status",
            {"phase": "start", "topology": "pipeline", "steps": steps, "task": task}
        )

        for i, agent_type in enumerate(steps):
            self._write_signal(
                swarm_id, "swarm", agent_type, swarm_id, "subtask",
                {"step": i + 1, "task": current_task[:300]}
            )

            result = self.orch.dispatch(current_task, agent_type=agent_type)
            step_results.append({
                "step":       i + 1,
                "agent_type": agent_type,
                "input":      current_task,
                "output":     result.get("output", ""),
            })

            self._write_signal(
                swarm_id, agent_type, steps[i + 1] if i + 1 < len(steps) else "swarm",
                swarm_id, "result",
                {"step": i + 1, "output": result.get("output", "")[:500]}
            )

            # Next agent's input = this agent's output + original task context
            if i + 1 < len(steps):
                current_task = (
                    f"Original task: {task}\n\n"
                    f"Previous step ({agent_type}) output:\n{result.get('output', '')}\n\n"
                    f"Your role: {steps[i + 1]}. Continue and build on the above."
                )

        final_output = step_results[-1]["output"] if step_results else ""
        self._write_signal(
            swarm_id, "swarm", None, swarm_id, "status",
            {"phase": "complete", "steps_completed": len(step_results)}
        )

        return {
            "topology":     "pipeline",
            "swarm_id":     swarm_id,
            "task":         task,
            "steps":        steps,
            "step_results": step_results,
            "output":       final_output,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_subtasks(self, raw: str, original_task: str, workers: list) -> list:
        """Extract subtask list from lead agent output. Falls back gracefully."""
        # Try JSON array first
        try:
            start = raw.index("[")
            end   = raw.rindex("]") + 1
            subtasks = json.loads(raw[start:end])
            if isinstance(subtasks, list) and subtasks:
                return [str(s) for s in subtasks]
        except (ValueError, json.JSONDecodeError):
            pass

        # Fall back: split by newlines, use one subtask per worker
        lines = [l.strip(" -•*123456789.") for l in raw.strip().splitlines() if l.strip()]
        lines = [l for l in lines if len(l) > 10]
        if lines:
            return lines[:len(workers)]

        # Last resort: assign original task to each worker with context
        return [
            f"{original_task} — focus: {agent}" for agent in workers
        ]

    def _write_signal(
        self,
        swarm_id: str,
        from_agent: str,
        to_agent: Optional[str],
        task_id: str,
        signal_type: str,
        payload: dict,
    ):
        try:
            conn = _get_db()
            conn.execute(
                "INSERT INTO signals (id, from_agent, to_agent, task_id, signal_type, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    from_agent,
                    to_agent,
                    task_id,
                    signal_type,
                    json.dumps(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # Signals are observability-only; never block execution


# ── Module-level singleton ────────────────────────────────────────────────────

_swarm: Optional[SwarmCoordinator] = None

def get_swarm(orchestrator=None, task_queue=None) -> SwarmCoordinator:
    global _swarm
    if _swarm is None:
        if orchestrator is None:
            from core.orchestrator import get_orchestrator
            orchestrator = get_orchestrator()
        _swarm = SwarmCoordinator(orchestrator, task_queue)
    return _swarm
