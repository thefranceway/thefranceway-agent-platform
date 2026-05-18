#!/usr/bin/env python3
"""
Agent Platform — Per-Agent Input/Output Schemas
=================================================
Pydantic v2 models for every agent handoff that crosses a pipeline boundary.
Used by contracts.validate_input / validate_output.

Schema lookup:
    from core.agent_schemas import get_schema, get_tool_input_schema
    cls = get_schema("builder_agent", "input")   # returns BaseModel subclass or None
"""

from typing import Any, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from core.contracts import AgentInput, AgentOutput


# ── Shared primitives ─────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    tool:     str
    input:    dict = {}
    result:   Any  = None
    provider: str  = "anthropic"
    model_config = ConfigDict(extra="allow")


# ── Orchestrator ──────────────────────────────────────────────────────────────

class OrchestratorInput(AgentInput):
    task:        str
    agent_type:  Optional[str] = None
    context:     dict          = Field(default_factory=dict)
    provider:    Optional[str] = None
    skills:      list[str]     = Field(default_factory=list)


class OrchestratorOutput(AgentOutput):
    output:              str
    agent_type:          str
    routing_confidence:  float              = 1.0
    routing_layer:       str               = "explicit"
    tool_calls:          list[dict]         = Field(default_factory=list)
    iterations:          int               = 0
    model_config = ConfigDict(extra="allow")


# ── Swarm pipeline step ───────────────────────────────────────────────────────

class SwarmPipelineStep(BaseModel):
    step:       int
    agent_type: str
    output:     str
    model_config = ConfigDict(extra="allow")


# ── Task queue ────────────────────────────────────────────────────────────────

class TaskQueueInput(BaseModel):
    description: str
    agent_type:  Optional[str] = None
    agent_id:    Optional[str] = None
    priority:    int           = Field(default=5, ge=1, le=10)
    input_data:  dict          = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


# ── Planning agent ────────────────────────────────────────────────────────────

class PlanStep(BaseModel):
    step:        int
    task:        str
    agent_type:  str
    depends_on:  list[int] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class PlanningInput(AgentInput):
    goal:     str
    context:  dict          = Field(default_factory=dict)
    platform: Optional[str] = None


class PlanningOutput(AgentOutput):
    output:  str
    plan_id: str
    steps:   list[PlanStep]
    model_config = ConfigDict(extra="allow")


# ── Generic base agent run output ─────────────────────────────────────────────

class BaseAgentRunOutput(AgentOutput):
    output:      str
    run_id:      str
    agent_id:    str
    agent_name:  str
    tool_calls:  list[dict] = Field(default_factory=list)
    iterations:  int        = 0
    model_config = ConfigDict(extra="allow")


# ── Registry ──────────────────────────────────────────────────────────────────

# Maps (agent_type, direction) → schema class.
# "input"  → validated before agent.run() is called
# "output" → validated after agent.run() returns
_SCHEMA_REGISTRY: dict[tuple[str, str], Type[BaseModel]] = {
    # Orchestrator
    ("orchestrator",    "input"):  OrchestratorInput,
    ("orchestrator",    "output"): OrchestratorOutput,
    # Swarm
    ("swarm_step",      "output"): SwarmPipelineStep,
    # Task queue
    ("task_queue",      "input"):  TaskQueueInput,
    # Planning agent
    ("planning_agent",  "input"):  PlanningInput,
    ("planning_agent",  "output"): PlanningOutput,
    # Base agent (all agents fall back to these)
    ("base",            "output"): BaseAgentRunOutput,
}

# Minimal tool input schemas — only fields that are always required.
# Keyed by tool name. None = no validation (tool accepts arbitrary input).
_TOOL_INPUT_SCHEMAS: dict[str, Optional[Type[BaseModel]]] = {
    "remember":    None,
    "recall":      None,
    "python_exec": None,
}


def get_schema(agent_type: str, direction: str) -> Optional[Type[BaseModel]]:
    """Return the schema class for (agent_type, direction), or None if unregistered."""
    return _SCHEMA_REGISTRY.get((agent_type, direction))


def get_tool_input_schema(tool_name: str) -> Optional[Type[BaseModel]]:
    """Return the input schema for a named tool, or None if unregistered."""
    return _TOOL_INPUT_SCHEMAS.get(tool_name)
