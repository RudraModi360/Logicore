"""
Load Skill Tool — on-demand skill instruction loading.

When the agent determines a task requires a specific skill, it calls this tool
to load the full skill instructions into the conversation context. The tool
returns the complete SKILL.md content as its result, making it immediately
available to the model for reasoning and execution.
"""

from typing import Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


class LoadSkillParams(BaseModel):
    skill_name: str = Field(
        ...,
        description=(
            "Name of the skill to load (e.g. 'word_operations', 'excel_operations'). "
            "Use the skill index to find available skills and their names."
        ),
    )


class LoadSkillTool(BaseTool):
    name = "load_skill"
    description = (
        "Load a skill's full instructions into context. Call this when the user's "
        "task requires specialized document/file operations (Word, Excel, PowerPoint, PDF). "
        "The result contains the complete skill guide — follow it to complete the task."
    )
    args_schema = LoadSkillParams

    def __init__(self, skills_dir: str = None):
        """
        Args:
            skills_dir: Path to the skills directory containing skill subdirectories.
                       If None, uses the default skills/defaults/ directory.
        """
        if skills_dir is None:
            import os
            skills_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "skills", "defaults"
            )
        self._skills_dir = skills_dir

    def run(self, skill_name: str) -> ToolResult:
        import os

        skill_path = os.path.join(self._skills_dir, skill_name, "SKILL.md")
        if not os.path.exists(skill_path):
            # Try to find it via index
            index_path = os.path.join(self._skills_dir, "SKILL_INDEX.md")
            if os.path.exists(index_path):
                from logicore.skills.loader import SkillLoader
                entries = SkillLoader.load_skill_index(self._skills_dir)
                for entry in entries:
                    if entry.name == skill_name:
                        skill_path = os.path.join(self._skills_dir, entry.path, "SKILL.md")
                        break

        if not os.path.exists(skill_path):
            available = []
            if os.path.isdir(self._skills_dir):
                for d in os.listdir(self._skills_dir):
                    if os.path.isfile(os.path.join(self._skills_dir, d, "SKILL.md")):
                        available.append(d)
            return ToolResult(
                success=False,
                error=f"Skill '{skill_name}' not found. Available skills: {', '.join(sorted(available))}"
            )

        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Strip YAML frontmatter — agent only needs the instructions
            import re
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
            if match:
                instructions = match.group(2).strip()
            else:
                instructions = content.strip()

            return ToolResult(
                success=True,
                content=f"# Skill: {skill_name}\n\n{instructions}"
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to load skill '{skill_name}': {e}")
