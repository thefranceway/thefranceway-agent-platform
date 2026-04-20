"""
Research Platform — FastAPI Router
All /research-api/* endpoints.
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent))

from research.models     import ProjectStore, CreateProjectRequest, SaveStepRequest
from research.steps      import STEP_KEYS, build_analysis_prompt
from research.report     import render_report

router = APIRouter()


# ── Projects ──────────────────────────────────────────────────────────────────

@router.post("/projects")
async def create_project(body: CreateProjectRequest):
    if not body.title.strip():
        raise HTTPException(400, "title is required")
    project = ProjectStore.create(body.title.strip())
    return project


@router.get("/projects")
async def list_projects():
    return ProjectStore.list_all()


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    project = ProjectStore.get(project_id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    return project


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    ok = ProjectStore.delete(project_id)
    if not ok:
        raise HTTPException(404, f"Project {project_id} not found")
    return {"deleted": project_id}


# ── Steps ─────────────────────────────────────────────────────────────────────

@router.put("/projects/{project_id}/step/{step_key}")
async def save_step(project_id: str, step_key: str, body: SaveStepRequest):
    if step_key not in STEP_KEYS:
        raise HTTPException(400, f"Unknown step key: {step_key}. Valid: {STEP_KEYS}")
    if step_key in ("agent_analysis", "final_report"):
        raise HTTPException(400, f"Step '{step_key}' is an action step — use the dedicated endpoint")

    project = ProjectStore.save_step(project_id, step_key, body.data)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    return project


# ── Agent Analysis ────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/analyze")
async def run_analysis(project_id: str):
    project = ProjectStore.get(project_id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")

    # Check prerequisites
    required = ["research_question", "data_collection", "analysis_method"]
    missing  = [k for k in required if not project["steps"].get(k, {}).get("completed")]
    if missing:
        raise HTTPException(400, f"Complete these steps first: {missing}")

    prompt = build_analysis_prompt(project)

    # Run agent in thread pool (agent.run() is synchronous)
    def _run():
        from agents.data_analytics_agent import DataAnalyticsAgent
        agent = DataAnalyticsAgent()
        return agent.run(prompt)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(500, f"Agent error: {e}")

    updated = ProjectStore.save_agent_output(
        project_id  = project_id,
        output      = result.get("output", ""),
        run_id      = result.get("run_id", ""),
        tool_calls  = result.get("tool_calls", []),
        iterations  = result.get("iterations", 0),
    )
    return {
        "output":     result.get("output", ""),
        "run_id":     result.get("run_id", ""),
        "tool_calls": len(result.get("tool_calls", [])),
        "iterations": result.get("iterations", 0),
        "project":    updated,
    }


# ── Report ────────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/report")
async def generate_report(project_id: str):
    project = ProjectStore.get(project_id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")

    html        = render_report(project)
    report_path = str(Path(__file__).parent / "projects" / f"{project_id}_report.html")
    Path(report_path).write_text(html)
    ProjectStore.save_report(project_id, report_path)

    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'attachment; filename="{project_id}_report.html"'},
    )


@router.get("/projects/{project_id}/report")
async def download_report(project_id: str):
    report_path = Path(__file__).parent / "projects" / f"{project_id}_report.html"
    if not report_path.exists():
        raise HTTPException(404, "Report not yet generated. POST to /report first.")
    return HTMLResponse(
        content=report_path.read_text(),
        headers={"Content-Disposition": f'attachment; filename="{project_id}_report.html"'},
    )
