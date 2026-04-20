"""
Research Platform — Project Storage
Handles all project CRUD as JSON files in research/projects/.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

RESEARCH_DIR  = Path(__file__).parent
PROJECTS_DIR  = RESEARCH_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Per-project write locks
_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def _get_lock(project_id: str) -> threading.Lock:
    with _locks_mu:
        if project_id not in _locks:
            _locks[project_id] = threading.Lock()
        return _locks[project_id]


# ── Pydantic request models ───────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    title: str

class SaveStepRequest(BaseModel):
    data: dict


# ── Default project template ──────────────────────────────────────────────────

def _empty_step():
    return {"completed": False, "saved_at": None, "data": {}}

def _new_project(project_id: str, title: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id":           project_id,
        "title":        title,
        "status":       "draft",
        "current_step": 1,
        "created_at":   now,
        "updated_at":   now,
        "steps": {
            "research_question": _empty_step(),
            "hypothesis":        _empty_step(),
            "data_collection":   _empty_step(),
            "data_profile":      _empty_step(),
            "analysis_method":   _empty_step(),
            "agent_analysis":    _empty_step(),
            "findings":          _empty_step(),
            "conclusions":       _empty_step(),
            "final_report":      _empty_step(),
        },
    }


# ── ProjectStore ──────────────────────────────────────────────────────────────

class ProjectStore:

    @staticmethod
    def _path(project_id: str) -> Path:
        return PROJECTS_DIR / f"{project_id}.json"

    @staticmethod
    def create(title: str) -> dict:
        project_id = "proj_" + uuid.uuid4().hex[:8]
        project    = _new_project(project_id, title)
        ProjectStore._write(project)
        return project

    @staticmethod
    def get(project_id: str) -> Optional[dict]:
        path = ProjectStore._path(project_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    @staticmethod
    def list_all() -> list[dict]:
        projects = []
        for p in sorted(PROJECTS_DIR.glob("proj_*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text())
                # Return summary only (no step data)
                projects.append({
                    "id":           data["id"],
                    "title":        data["title"],
                    "status":       data["status"],
                    "current_step": data["current_step"],
                    "created_at":   data["created_at"],
                    "updated_at":   data["updated_at"],
                    "steps_done":   sum(
                        1 for s in data["steps"].values() if s.get("completed")
                    ),
                })
            except Exception:
                continue
        return projects

    @staticmethod
    def save_step(project_id: str, step_key: str, data: dict) -> Optional[dict]:
        with _get_lock(project_id):
            project = ProjectStore.get(project_id)
            if not project:
                return None

            now = datetime.now(timezone.utc).isoformat()
            project["steps"][step_key] = {
                "completed": True,
                "saved_at":  now,
                "data":      data,
            }
            project["updated_at"] = now

            # Advance current_step if this step completed it
            from research.steps import STEP_KEYS
            step_num = STEP_KEYS.index(step_key) + 1
            if step_num >= project["current_step"]:
                project["current_step"] = min(step_num + 1, 9)

            # Update status
            total_done = sum(1 for s in project["steps"].values() if s.get("completed"))
            if total_done == 9:
                project["status"] = "complete"
            elif total_done > 0:
                project["status"] = "in_progress"

            ProjectStore._write(project)
            return project

    @staticmethod
    def save_agent_output(project_id: str, output: str, run_id: str,
                          tool_calls: list, iterations: int) -> Optional[dict]:
        charts = []
        import re
        for match in re.finditer(r'/tmp/[\w\-\.]+\.png', output):
            charts.append(match.group(0))

        data = {
            "raw_output": output,
            "run_id":     run_id,
            "tool_calls": len(tool_calls),
            "iterations": iterations,
            "charts":     charts,
        }
        return ProjectStore.save_step(project_id, "agent_analysis", data)

    @staticmethod
    def save_report(project_id: str, report_path: str) -> Optional[dict]:
        data = {
            "report_path":  report_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        return ProjectStore.save_step(project_id, "final_report", data)

    @staticmethod
    def delete(project_id: str) -> bool:
        path = ProjectStore._path(project_id)
        if path.exists():
            path.unlink()
            return True
        return False

    @staticmethod
    def _write(project: dict):
        path = ProjectStore._path(project["id"])
        path.write_text(json.dumps(project, indent=2))
