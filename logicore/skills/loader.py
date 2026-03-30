"""
Skill Loader — discovers and loads skills from directories.
"""

import os
import re
from pathlib import Path
from typing import List, Optional, Dict, Any

from .base import Skill, SkillMetadata


class SkillLoader:
    """
    Discovers and loads skills from filesystem directories.
    
    Supports:
    - Loading individual skills from a SKILL.md file
    - Discovering skills from a parent directory
    - Loading from user workspace (.agent/skills/) 
    - Loading from package defaults (logicore/skills/defaults/)
    """

    @staticmethod
    def load(skill_dir: str) -> Optional[Skill]:
        """
        Load a skill from a directory containing SKILL.md.
        
        Args:
            skill_dir: Path to the skill directory
            
        Returns:
            Skill instance or None if invalid
        """
        skill_path = Path(skill_dir)
        skill_md = skill_path / "SKILL.md"
        
        if not skill_md.exists():
            return None
        
        try:
            content = skill_md.read_text(encoding="utf-8")
            metadata, instructions = SkillLoader._parse_skill_md(content)
            metadata_obj = SkillMetadata(**metadata)
            
            # Look for tool definitions in scripts/
            tools = []
            tool_executors = {}
            scripts_dir = skill_path / "scripts"
            if scripts_dir.exists():
                for py_file in scripts_dir.glob("*.py"):
                    # Import and discover tool functions
                    tool_funcs = SkillLoader._discover_tool_functions(py_file)
                    for func_name, func, schema in tool_funcs:
                        tools.append(schema)
                        tool_executors[func_name] = func
            
            return Skill(
                metadata=metadata_obj,
                instructions=instructions,
                tools=tools,
                tool_executors=tool_executors,
                skill_dir=skill_path
            )
        except Exception as e:
            print(f"[SkillLoader] Error loading skill from {skill_dir}: {e}")
            return None

    @staticmethod
    def discover(skills_dir: str) -> List[Skill]:
        """
        Discover all skills in a parent directory.
        Each subdirectory with a SKILL.md is treated as a skill.
        
        Args:
            skills_dir: Parent directory containing skill subdirectories
            
        Returns:
            List of loaded Skill instances
        """
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            return []
        
        skills = []
        for item in skills_path.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                skill = SkillLoader.load(str(item))
                if skill:
                    skills.append(skill)
        
        return skills

    @staticmethod  
    def discover_workspace_skills(workspace_root: str) -> List[Skill]:
        """
        Discover skills from the user's workspace.
        Looks in .agent/skills/ and _agent/skills/ directories.
        
        Args:
            workspace_root: Root of the user's workspace
            
        Returns:
            List of loaded Skill instances
        """
        workspace = Path(workspace_root)
        skill_dirs = [
            workspace / ".agent" / "skills",
            workspace / "_agent" / "skills",
            workspace / ".agents" / "skills",
            workspace / "_agents" / "skills",
        ]
        
        skills = []
        for sd in skill_dirs:
            if sd.exists():
                skills.extend(SkillLoader.discover(str(sd)))
        
        return skills

    @staticmethod
    def _parse_skill_md(content: str) -> tuple:
        """
        Parse SKILL.md with YAML frontmatter.
        
        Returns:
            (metadata_dict, instructions_str)
        """
        metadata = {"name": "Unknown", "description": "No description"}
        instructions = content
        
        # Check for YAML frontmatter (between --- delimiters)
        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
        if frontmatter_match:
            frontmatter = frontmatter_match.group(1)
            instructions = frontmatter_match.group(2).strip()
            
            # Simple YAML parsing (no PyYAML dependency)
            for line in frontmatter.strip().split('\n'):
                line = line.strip()
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    
                    if key == 'tags' or key == 'requires':
                        # Parse as list: [item1, item2] or comma-separated
                        value = value.strip('[]')
                        metadata[key] = [v.strip().strip('"').strip("'") for v in value.split(',') if v.strip()]
                    else:
                        metadata[key] = value
        
        return metadata, instructions

    @staticmethod
    def _discover_tool_functions(py_file: Path) -> list:
        """
        Discover and import tool functions from a Python file.
        Functions with docstrings and type hints are treated as tools.
        
        Returns:
            List of (func_name, func, schema_dict) tuples
        """
        import importlib.util
        
        results = []
        try:
            spec = importlib.util.spec_from_file_location(
                f"skill_script_{py_file.stem}", str(py_file)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find functions with docstrings (treat as tools)
            import inspect
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if name.startswith('_'):
                    continue
                if not obj.__doc__:
                    continue
                    
                # Build schema from function signature
                sig = inspect.signature(obj)
                props = {}
                required = []
                
                type_map = {
                    str: "string", int: "integer", float: "number",
                    bool: "boolean", list: "array", dict: "object"
                }
                
                for param_name, param in sig.parameters.items():
                    if param_name in ('self', 'cls'):
                        continue
                    
                    annotation = param.annotation
                    param_type = type_map.get(annotation, "string") if annotation != inspect.Parameter.empty else "string"
                    props[param_name] = {"type": param_type, "description": f"Parameter: {param_name}"}
                    
                    if param.default is inspect.Parameter.empty:
                        required.append(param_name)
                
                schema = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": obj.__doc__.strip(),
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": required
                        }
                    }
                }
                
                results.append((name, obj, schema))
        except Exception as e:
            print(f"[SkillLoader] Error importing {py_file}: {e}")
        
        return results
