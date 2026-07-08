"""
Logicore Skills System
=====================

Skills are modular capability packages that extend the agent's functionality.
Each skill defines tools, prompts, and workflows for a specific domain.

Two-Tier Lazy Loading Pattern:
- SKILL_INDEX.md: Lightweight index loaded once per session (cheap, controlled tokens)
- Individual SKILL.md: Loaded on-demand when agent decides to use a skill

Inspired by the Antigravity skills pattern:
- SKILL.md: Main instruction file with YAML frontmatter
- scripts/: Helper scripts and utilities
- resources/: Additional files, templates, or assets

Usage:
    from logicore.skills import SkillLoader, Skill

    # Tier 1: Load index (cheap, once per session)
    entries = SkillLoader.load_skill_index("skills/")
    
    # Tier 2: Load specific skill on-demand
    skill = SkillLoader.load_skill_by_index("skills/", "excel_skill")
    agent.load_skill(skill)

    # Or discover all at once (legacy approach)
    skills = SkillLoader.discover("skills/")
    for skill in skills:
        agent.load_skill(skill)

    # Auto-generate SKILL_INDEX.md from individual skills
    SkillLoader.build_skill_index("skills/")
"""

from .base import Skill, SkillMetadata
from .loader import SkillLoader, SkillIndexEntry

__all__ = ["Skill", "SkillMetadata", "SkillLoader", "SkillIndexEntry"]
