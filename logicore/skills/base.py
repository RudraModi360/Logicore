"""
Skill base classes and data models.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Metadata parsed from SKILL.md frontmatter."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)  # Dependencies
    trigger: str = ""  # When this skill should be activated (for SKILL_INDEX.md)
    cost_tier: str = "low"  # Token cost tier: low, medium, high
    min_framework_version: str = ""  # Minimum Logicore version required
    conflicts_with: List[str] = field(default_factory=list)  # Skills that cannot be loaded together
    
    @property
    def version_tuple(self) -> tuple:
        """Parse version string into comparable tuple (major, minor, patch)."""
        try:
            parts = self.version.split('.')
            return tuple(int(p) for p in parts[:3])
        except (ValueError, AttributeError):
            return (1, 0, 0)
    
    def is_compatible_with(self, other_version: str) -> bool:
        """Check if this skill is compatible with a given framework version."""
        if not self.min_framework_version:
            return True
        
        try:
            required = tuple(int(p) for p in self.min_framework_version.split('.'))
            current = tuple(int(p) for p in other_version.split('.'))
            return current >= required
        except (ValueError, AttributeError):
            return True


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
        self._loaded = True  # Mark as loaded upon construction
        self._enabled = True  # Enable/disable toggle
        self._dependencies_met = True  # Track if dependencies are satisfied
        self._missing_dependencies: List[str] = []  # List of missing dependencies

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description
    
    @property
    def version(self) -> str:
        return self.metadata.version
    
    @property
    def requires(self) -> List[str]:
        return self.metadata.requires
    
    @property
    def conflicts_with(self) -> List[str]:
        return self.metadata.conflicts_with
    
    @property
    def dependencies_met(self) -> bool:
        return self._dependencies_met
    
    @property
    def missing_dependencies(self) -> List[str]:
        return self._missing_dependencies

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def enable(self):
        """Enable this skill."""
        self._enabled = True

    def disable(self):
        """Disable this skill."""
        self._enabled = False
    
    def check_dependencies(self, loaded_skill_names: List[str]) -> bool:
        """
        Check if all dependencies are satisfied.
        
        Args:
            loaded_skill_names: List of currently loaded skill names
            
        Returns:
            True if all dependencies are met, False otherwise
        """
        self._missing_dependencies = [
            req for req in self.requires 
            if req not in loaded_skill_names
        ]
        self._dependencies_met = len(self._missing_dependencies) == 0
        return self._dependencies_met
    
    def check_conflicts(self, loaded_skill_names: List[str]) -> List[str]:
        """
        Check for conflicts with other loaded skills.
        
        Args:
            loaded_skill_names: List of currently loaded skill names
            
        Returns:
            List of conflicting skill names that are loaded
        """
        return [
            conflict for conflict in self.conflicts_with 
            if conflict in loaded_skill_names
        ]
    
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

    def get_tool_names(self) -> List[str]:
        """Get list of tool names this skill provides."""
        return [t.get("function", {}).get("name", "") for t in self.tools if isinstance(t, dict)]
    
    def get_version_info(self) -> Dict[str, Any]:
        """Get detailed version information."""
        return {
            "name": self.name,
            "version": self.version,
            "version_tuple": self.metadata.version_tuple,
            "min_framework_version": self.metadata.min_framework_version,
            "requires": self.requires,
            "conflicts_with": self.conflicts_with,
            "dependencies_met": self.dependencies_met,
            "missing_dependencies": self.missing_dependencies,
        }

    def __repr__(self):
        return f"Skill(name='{self.name}', version='{self.version}', tools={len(self.tools)}, loaded={self._loaded}, enabled={self._enabled}, dir={self.skill_dir})"
