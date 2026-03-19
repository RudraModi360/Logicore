"""
Agentry Skills System
=====================

Skills are modular capability packages that extend the agent's functionality.
Each skill defines tools, prompts, and workflows for a specific domain.

Inspired by the Antigravity skills pattern:
- SKILL.md: Main instruction file with YAML frontmatter
- scripts/: Helper scripts and utilities
- resources/: Additional files, templates, or assets

Usage:
    from logicore.skills import SkillLoader, Skill

    # Load a skill from directory
    skill = SkillLoader.load("path/to/skill/")
    agent.load_skill(skill)

    # Or discover and load all skills
    skills = SkillLoader.discover("path/to/skills/")
    for skill in skills:
        agent.load_skill(skill)
"""

from .base import Skill, SkillMetadata
from .loader import SkillLoader

__all__ = ["Skill", "SkillMetadata", "SkillLoader"]
