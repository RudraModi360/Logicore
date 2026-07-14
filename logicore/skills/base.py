"""
Skill base classes and data models.

Three-layer architecture:
  Knowledge Layer  → SKILL.md, examples, templates, validation rules
  Cognitive Layer  → LLM reasoning (not in code)
  Execution Layer  → Capabilities (scripts, tools, MCP, generated code)

Skills are Capability Packages. They may contain:
  - Domain knowledge (reasoning guidance)
  - Executable capabilities (optional scripts, tool definitions)
  - Examples, templates, validation rules

The LLM decides which capability to use. Skills do not force execution.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class CapabilityType(str, Enum):
    """Types of executable capabilities a skill may provide."""
    SCRIPT = "script"         # Python/JS/etc. script in scripts/
    TOOL = "tool"             # Pre-defined tool function
    MCP = "mcp"               # MCP server configuration
    WORKFLOW = "workflow"      # Multi-step workflow
    TEMPLATE = "template"     # Reusable code template


@dataclass
class Capability:
    """
    An executable capability provided by a skill.

    Capabilities are registered in the Execution Layer.
    The LLM decides whether to use them based on skill guidance.
    """
    name: str
    cap_type: CapabilityType
    description: str = ""
    schema: Optional[Dict[str, Any]] = None      # OpenAI-style tool schema (for TOOL type)
    executor: Optional[Callable] = None           # Callable executor (for TOOL type)
    path: Optional[str] = None                    # File path (for SCRIPT type)
    language: str = "python"                      # Script language
    complexity: str = "simple"                    # simple | moderate | complex
    alternatives: List[str] = field(default_factory=list)  # Alternative approaches

    @property
    def is_registered(self) -> bool:
        return self.executor is not None or self.schema is not None


@dataclass
class SkillMetadata:
    """Metadata parsed from SKILL.md frontmatter."""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    trigger: str = ""
    cost_tier: str = "low"
    min_framework_version: str = ""
    conflicts_with: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillMetadata":
        """Create SkillMetadata from dict, ignoring unknown fields."""
        known_fields = {
            "name", "description", "version", "author", "tags",
            "requires", "trigger", "cost_tier", "min_framework_version", "conflicts_with"
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        extra = {k: v for k, v in data.items() if k not in known_fields}
        obj = cls(**filtered)
        obj.extra = extra
        return obj

    @property
    def version_tuple(self) -> tuple:
        try:
            parts = self.version.split('.')
            return tuple(int(p) for p in parts[:3])
        except (ValueError, AttributeError):
            return (1, 0, 0)

    def is_compatible_with(self, other_version: str) -> bool:
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
    A Capability Package for agents.

    Structure:
        skill_dir/
        ├── SKILL.md              # Instructions + YAML frontmatter (Knowledge Layer)
        ├── scripts/              # Optional executable capabilities
        ├── examples/             # Example workflows
        ├── templates/            # Reusable templates
        └── validation_rules/     # Quality checklists

    A Skill provides:
        - Knowledge: reasoning guidance, best practices, workflows
        - Capabilities: optional executable assets (scripts, tools, MCP)
        - Examples: reference implementations
        - Validation: quality checklists

    The LLM decides how to use these. Skills do not force execution.
    """

    def __init__(
        self,
        metadata: SkillMetadata,
        instructions: str,
        system_prompt_addon: str = None,
        skill_dir: Path = None,
        # Knowledge Layer fields
        examples: List[str] = None,
        templates: List[str] = None,
        validation_rules: List[str] = None,
        # Capability Layer fields
        capabilities: List[Capability] = None,
    ):
        self.metadata = metadata
        self.instructions = instructions
        self.system_prompt_addon = system_prompt_addon
        self.skill_dir = skill_dir
        self._loaded = True
        self._enabled = True
        self._dependencies_met = True
        self._missing_dependencies: List[str] = []
        # Knowledge Layer
        self.examples = examples or []
        self.templates = templates or []
        self.validation_rules = validation_rules or []
        # Capability Layer
        self.capabilities = capabilities or []

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
        self._enabled = True

    def disable(self):
        self._enabled = False

    def check_dependencies(self, loaded_skill_names: List[str]) -> bool:
        self._missing_dependencies = [
            req for req in self.requires
            if req not in loaded_skill_names
        ]
        self._dependencies_met = len(self._missing_dependencies) == 0
        return self._dependencies_met

    def check_conflicts(self, loaded_skill_names: List[str]) -> List[str]:
        return [
            conflict for conflict in self.conflicts_with
            if conflict in loaded_skill_names
        ]

    # ─── Capability Management ───────────────────────────────────────

    def get_capabilities(self) -> List[Capability]:
        """Get all executable capabilities this skill provides."""
        return self.capabilities

    def get_capability_by_name(self, name: str) -> Optional[Capability]:
        """Get a specific capability by name."""
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None

    def get_registered_capabilities(self) -> List[Capability]:
        """Get capabilities that have executors registered."""
        return [cap for cap in self.capabilities if cap.is_registered]

    def has_capabilities(self) -> bool:
        """Check if this skill provides any executable capabilities."""
        return len(self.capabilities) > 0

    # ─── Knowledge Layer ─────────────────────────────────────────────

    def get_examples(self) -> List[str]:
        return self.examples

    def get_templates(self) -> List[str]:
        return self.templates

    def get_validation_rules(self) -> List[str]:
        return self.validation_rules

    def build_validation_checklist(self) -> str:
        """Build a markdown checklist from validation rules."""
        if not self.validation_rules:
            return ""
        lines = ["## Validation Checklist"]
        for rule in self.validation_rules:
            lines.append(f"- [ ] {rule}")
        return "\n".join(lines)

    # ─── Directory Helpers ───────────────────────────────────────────

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

    def get_examples_dir(self) -> Optional[Path]:
        if self.skill_dir:
            examples = self.skill_dir / "examples"
            return examples if examples.exists() else None
        return None

    def get_templates_dir(self) -> Optional[Path]:
        if self.skill_dir:
            templates = self.skill_dir / "templates"
            return templates if templates.exists() else None
        return None

    # ─── Metadata ────────────────────────────────────────────────────

    def get_version_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "version_tuple": self.metadata.version_tuple,
            "min_framework_version": self.metadata.min_framework_version,
            "requires": self.requires,
            "conflicts_with": self.conflicts_with,
            "dependencies_met": self.dependencies_met,
            "missing_dependencies": self.missing_dependencies,
            "has_capabilities": self.has_capabilities(),
            "capability_count": len(self.capabilities),
            "example_count": len(self.examples),
            "template_count": len(self.templates),
            "validation_rule_count": len(self.validation_rules),
        }

    def __repr__(self):
        return (
            f"Skill(name='{self.name}', version='{self.version}', "
            f"capabilities={len(self.capabilities)}, "
            f"loaded={self._loaded}, enabled={self._enabled})"
        )
