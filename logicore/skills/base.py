"""
Skill base classes and data models.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path


@dataclass
class SkillMetadata:
    """Metadata parsed from SKILL.md frontmatter."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)  # Dependencies
    

class Skill:
    """
    A modular capability package for agents.
    
    Structure:
        skill_dir/
        ├── SKILL.md          # Instructions + YAML frontmatter
        ├── scripts/          # Helper scripts
        ├── resources/        # Templates, assets
        └── examples/         # Reference implementations
    """
    
    def __init__(
        self,
        metadata: SkillMetadata,
        instructions: str,
        tools: List[Dict[str, Any]] = None,
        tool_executors: Dict[str, Callable] = None,
        system_prompt_addon: str = None,
        skill_dir: Path = None
    ):
        self.metadata = metadata
        self.instructions = instructions
        self.tools = tools or []
        self.tool_executors = tool_executors or {}
        self.system_prompt_addon = system_prompt_addon
        self.skill_dir = skill_dir
        self._loaded = False

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    def get_scripts_dir(self) -> Optional[Path]:
        if self.skill_dir:
            scripts = self.skill_dir / "scripts"
            return scripts if scripts.exists() else None
        return None

    def get_resources_dir(self) -> Optional[Path]:
        if self.skill_dir:
            resources = self.skill_dir / "resources"
            return resources if resources.exists() else None
        return None

    def __repr__(self):
        return f"Skill(name='{self.name}', tools={len(self.tools)}, dir={self.skill_dir})"
