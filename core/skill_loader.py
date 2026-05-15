#!/usr/bin/env python3
"""
Skills 2.0 — Skill Loader
==========================
Reads ~/.metaclaw/skills/ and makes skills available to agents at runtime.

Each skill is a directory containing:
  SKILL.md        — frontmatter (name, description, category) + content
  examples/       — optional usage examples
  reference/      — optional reference docs

Usage:
    from core.skill_loader import SkillLoader
    loader = SkillLoader()
    loader.list_skills()                    # all 45 skill names + descriptions
    loader.load("agent-creation")           # returns skill content string
    loader.load_many(["git-workflow", ...]) # returns combined block
    loader.block_for_prompt(["git-workflow"]) # formatted for system prompt injection
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

SKILLS_DIR = Path.home() / ".metaclaw" / "skills"

try:
    from core.runtime.loader import get_param
except Exception:
    def get_param(key, default=None): return default


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split SKILL.md into (frontmatter dict, body string)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, parts[2].strip()


class SkillLoader:
    """Load and inject skills from ~/.metaclaw/skills/ into agent system prompts."""

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self.skills_dir = skills_dir

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        """Return list of {name, description, category} for all available skills."""
        result = []
        if not self.skills_dir.exists():
            return result
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            fm, _ = _parse_frontmatter(skill_md.read_text())
            result.append({
                "name":        skill_dir.name,
                "description": fm.get("description", ""),
                "category":    fm.get("category", "general"),
            })
        return result

    # ── Loading ───────────────────────────────────────────────────────────────

    @lru_cache(maxsize=128)
    def load(self, name: str) -> Optional[str]:
        """
        Load a skill by name. Returns the full SKILL.md body (frontmatter stripped).
        Returns None if the skill doesn't exist.
        """
        skill_md = self.skills_dir / name / "SKILL.md"
        if not skill_md.exists():
            return None
        _, body = _parse_frontmatter(skill_md.read_text())
        return body

    def load_many(self, names: list[str]) -> list[tuple[str, str]]:
        """
        Load multiple skills. Returns list of (name, content) for found skills.
        Silently skips missing skills.
        """
        result = []
        for name in names:
            content = self.load(name)
            if content:
                result.append((name, content))
        return result

    # ── Prompt injection ──────────────────────────────────────────────────────

    def block_for_prompt(self, names: list[str]) -> str:
        """
        Build a formatted block ready to append to a system prompt.
        Returns empty string if no skills found.

        Example output:
            ── Active Skills ──
            [agent-creation]
            ...skill content...
            [git-workflow]
            ...skill content...
        """
        loaded = self.load_many(names)
        if not loaded:
            return ""
        sections = []
        for name, content in loaded:
            sections.append(f"[{name}]\n{content}")
        return "\n\n── Active Skills ──\n" + "\n\n".join(sections)

    def find_relevant(self, task: str, max_skills: int = None) -> list[str]:
        """
        Suggest skill names relevant to a task based on keyword matching
        against skill descriptions and names.
        Returns up to max_skills skill names.
        """
        if max_skills is None:
            strength = get_param("skill_loader_strength", 0.7)
            max_skills = max(1, round(strength * 5))
        task_lower = task.lower()
        scored = []
        for skill in self.list_skills():
            name  = skill["name"].lower().replace("-", " ")
            desc  = skill["description"].lower()
            score = 0
            for word in re.findall(r"\w+", task_lower):
                if len(word) < 4:
                    continue
                if word in name:
                    score += 2
                if word in desc:
                    score += 1
            if score > 0:
                scored.append((score, skill["name"]))
        scored.sort(reverse=True)
        return [name for _, name in scored[:max_skills]]


# ── Module-level singleton ─────────────────────────────────────────────────────

_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    global _loader
    if _loader is None:
        _loader = SkillLoader()
    return _loader
