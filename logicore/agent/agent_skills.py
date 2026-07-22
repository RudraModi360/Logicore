"""
AgentSkillsMixin: Skill management extracted from Agent.

Consolidates all skill-related functionality into a focused module:
loading, registering, enabling/disabling, and prompt section building.

Agent inherits from this mixin to maintain the same public API.
"""

from __future__ import annotations

import os
import logging
from typing import TYPE_CHECKING, Any, Dict, List

from logicore.skills import Skill, SkillLoader

if TYPE_CHECKING:
    from logicore.agent.agent_protocol import AgentProtocol

logger = logging.getLogger(__name__)

# Shared constant for skills defaults directory
_SKILLS_DEFAULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "defaults"
)


class AgentSkillsMixin:
    """Mixin providing skill management for Agent.

    Expects the following attributes to be set on the host class:
    - skills: List[Skill]
    - _skill_tools_registered: set
    - _skill_index_entries: Dict
    - workspace_root: Optional[str]
    - internal_tools: List[Dict]
    - tool_executor: ToolExecutor
    - supports_tools: bool
    """

    def _build_skills_prompt_section(self) -> str:
        """Build the skills section for the system prompt."""
        if not self.skills:
            return ""

        index_path = os.path.join(_SKILLS_DEFAULTS_DIR, "SKILL_INDEX.md")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                index_content = f.read().strip()
        else:
            lines = ["# Skill Index", ""]
            for skill in self.skills:
                if skill.is_enabled:
                    lines.append(f"- **{skill.name}**: {skill.description}")
            index_content = "\n".join(lines)

        return f"""

## Skills (REQUIRED for document tasks)

**IMPORTANT**: For ANY task involving Word, Excel, PowerPoint, or PDF files, you MUST:
1. First call `load_skill` with the skill name to get the full instructions
2. Then follow those instructions exactly (they include code templates, library recommendations, and quality checks)

**Do NOT attempt document tasks without loading the skill first.** The skill contains critical information about available tools, code templates, and validation steps.

Available skills:
{index_content}
"""

    def _load_default_skills(self):
        """Load skill metadata for the index."""
        if not os.path.exists(_SKILLS_DEFAULTS_DIR):
            return
        index_entries, direct_skills = SkillLoader.discover_with_index(_SKILLS_DEFAULTS_DIR)
        for skill in direct_skills:
            self._register_skill_metadata(skill)
        if not hasattr(self, '_skill_index_entries'):
            self._skill_index_entries = {}
        for entry in index_entries:
            self._skill_index_entries[entry.name] = (_SKILLS_DEFAULTS_DIR, entry)
            skill = SkillLoader.load_skill_by_index(_SKILLS_DEFAULTS_DIR, entry.name)
            if skill:
                self._register_skill_metadata(skill)

    def _load_workspace_skills(self):
        """Auto-discover skills from workspace and home directories."""
        import pathlib
        search_paths = []
        if self.workspace_root:
            ws = pathlib.Path(self.workspace_root)
            for sub in (".agents/skills", "_agents/skills", ".agent/skills", "_agent/skills"):
                search_paths.append(ws / sub)
        home = pathlib.Path.home()
        for sub in (".agents/skills", "_agents/skills"):
            search_paths.append(home / sub)

        loaded_names = {s.name for s in self.skills}
        for skill_dir in search_paths:
            if skill_dir.exists():
                discovered = SkillLoader.discover(str(skill_dir))
                for skill in discovered:
                    if skill.name not in loaded_names:
                        self._register_skill(skill)
                        loaded_names.add(skill.name)

    def load_skills(self, skills):
        """Load multiple skills by name or Skill objects."""
        for item in skills:
            if isinstance(item, Skill):
                self._register_skill(item)
            elif isinstance(item, str):
                if any(s.name == item for s in self.skills):
                    continue
                skill_path = os.path.join(_SKILLS_DEFAULTS_DIR, item)
                skill = SkillLoader.load(skill_path)
                if not skill and hasattr(self, '_skill_index_entries') and item in self._skill_index_entries:
                    skill_dir, entry = self._skill_index_entries[item]
                    skill = SkillLoader.load_skill_by_index(skill_dir, item)
                if not skill and self.workspace_root:
                    ws_skills = SkillLoader.discover_workspace_skills(self.workspace_root)
                    for ws_skill in ws_skills:
                        if ws_skill.name.lower() == item.lower():
                            skill = ws_skill
                            break
                if skill:
                    self._register_skill(skill)
        if self.skills:
            self._rebuild_system_prompt_with_tools()

    def load_skill(self, skill: Skill):
        """Load a single skill."""
        self._register_skill(skill)
        self._rebuild_system_prompt_with_tools()

    def unload_skill(self, skill_name: str) -> bool:
        """Unload a skill by name."""
        skill_to_remove = None
        for skill in self.skills:
            if skill.name == skill_name:
                skill_to_remove = skill
                break
        if not skill_to_remove:
            return False
        skill_tool_names = {
            cap.name for cap in skill_to_remove.get_registered_capabilities()
            if cap.schema
        }
        self.internal_tools = [
            t for t in self.internal_tools
            if t.get("function", {}).get("name") not in skill_tool_names
        ]
        for tool_name in skill_tool_names:
            self.tool_executor.unregister_skill_tool(tool_name)
        self.skills.remove(skill_to_remove)
        self._rebuild_system_prompt_with_tools()
        return True

    def enable_skill(self, skill_name: str) -> bool:
        for skill in self.skills:
            if skill.name == skill_name:
                skill.enable()
                self._rebuild_system_prompt_with_tools()
                return True
        return False

    def disable_skill(self, skill_name: str) -> bool:
        for skill in self.skills:
            if skill.name == skill_name:
                skill.disable()
                self._rebuild_system_prompt_with_tools()
                return True
        return False

    def load_skill_from_index(self, skill_name: str) -> bool:
        if not hasattr(self, '_skill_index_entries') or skill_name not in self._skill_index_entries:
            return False
        skills_dir, entry = self._skill_index_entries[skill_name]
        skill = SkillLoader.load_skill_by_index(skills_dir, skill_name)
        if skill:
            self._register_skill(skill)
            self._rebuild_system_prompt_with_tools()
            return True
        return False

    def list_available_skills(self) -> List[Dict[str, Any]]:
        result = []
        for skill in self.skills:
            result.append({
                "name": skill.name,
                "description": skill.description,
                "status": "loaded",
                "capabilities": len(skill.capabilities),
                "examples": len(skill.examples),
                "templates": len(skill.templates),
                "validation_rules": len(skill.validation_rules),
                "enabled": skill.is_enabled
            })
        if hasattr(self, '_skill_index_entries'):
            loaded_names = {s.name for s in self.skills}
            for name, (skills_dir, entry) in self._skill_index_entries.items():
                if name not in loaded_names:
                    result.append({
                        "name": entry.name,
                        "description": entry.description,
                        "status": "indexed",
                        "trigger": entry.trigger,
                        "cost_tier": entry.cost_tier
                    })
        return result

    def _register_skill(self, skill: Skill):
        """Register a skill and its tool capabilities."""
        if any(s.name == skill.name for s in self.skills):
            return
        self.skills.append(skill)
        self._register_skill_tools(skill)

    def _register_skill_metadata(self, skill: Skill):
        """Register a skill for metadata only (no tool registration)."""
        if any(s.name == skill.name for s in self.skills):
            return
        self.skills.append(skill)

    def _register_skill_tools(self, skill: Skill):
        """Register a skill's tool capabilities with the agent."""
        if skill.name in self._skill_tools_registered:
            return
        self._skill_tools_registered.add(skill.name)
        for cap in skill.get_registered_capabilities():
            if cap.schema:
                schema = dict(cap.schema)
                func = schema.get("function", {})
                func["x-origin"] = f"skill:{skill.name}"
                func["x-capability-type"] = cap.cap_type.value
                func["x-complexity"] = cap.complexity
                if cap.alternatives:
                    func["x-alternatives"] = cap.alternatives

                tool_name = func.get("name")
                existing_names = {
                    t.get("function", {}).get("name") for t in self.internal_tools
                    if isinstance(t, dict)
                }
                if tool_name in existing_names:
                    logger.warning(f"Tool naming conflict: skill '{skill.name}' registers '{tool_name}' — skipping")
                    continue
                self.internal_tools.append(schema)
                if cap.executor:
                    self.tool_executor.register_skill_tool(tool_name, cap.executor)

        if skill.has_capabilities():
            self.supports_tools = True
