"""
Skill Loader — discovers and loads skills from directories.

Supports two-tier lazy loading via SKILL_INDEX.md:
1. Load SKILL_INDEX.md once per session (cheap, ~1 file, controlled token size)
2. Load individual SKILL.md on-demand when agent decides to use a skill
"""

import re
import ast
import os
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from .base import Skill, SkillMetadata

logger = logging.getLogger(__name__)


class SkillIndexEntry:
    """Lightweight entry from SKILL_INDEX.md — enough to decide relevance without loading full skill."""
    
    def __init__(self, name: str, description: str, trigger: str, path: str, cost_tier: str = "low", tags: List[str] = None):
        self.name = name
        self.description = description
        self.trigger = trigger
        self.path = path
        self.cost_tier = cost_tier
        self.tags = tags or []
        self._raw_metadata: Dict[str, Any] = {}  # Store raw YAML metadata
    
    @property
    def triggers(self) -> List[str]:
        """Get triggers as a list."""
        if not self.trigger:
            return []
        return [t.strip() for t in self.trigger.split(',')]
    
    @property
    def can_do(self) -> List[str]:
        """Get CAN_DO items from raw metadata."""
        return self._raw_metadata.get('can_do', [])
    
    @property
    def cannot_do(self) -> List[str]:
        """Get CANNOT_DO items from raw metadata."""
        return self._raw_metadata.get('cannot_do', [])
    
    def set_raw_metadata(self, metadata: Dict[str, Any]) -> None:
        """Store raw YAML metadata for richer queries."""
        self._raw_metadata = metadata
    
    def __repr__(self):
        return f"SkillIndexEntry(name='{self.name}', trigger='{self.trigger}', path='{self.path}')"


class SkillLoader:
    """
    Discovers and loads skills from filesystem directories.
    
    Supports:
    - Loading individual skills from a SKILL.md file
    - Discovering skills from a parent directory
    - Loading from user workspace (.agent/skills/) 
    - Loading from package defaults (logicore/skills/defaults/)
    - SKILL_INDEX.md for two-tier lazy loading (index → on-demand)
    - Skill caching for improved performance
    - Batch skill loading for multiple skills at once
    """
    
    # Class-level cache for loaded skills
    _skill_cache: Dict[str, Skill] = {}
    _index_cache: Dict[str, List[SkillIndexEntry]] = {}

    @staticmethod
    def load(skill_dir: str, use_cache: bool = True) -> Optional[Skill]:
        """
        Load a skill from a directory containing SKILL.md.
        
        Args:
            skill_dir: Path to the skill directory
            use_cache: Whether to use skill cache (default: True)
            
        Returns:
            Skill instance or None if invalid
        """
        skill_path = Path(skill_dir)
        skill_md = skill_path / "SKILL.md"
        
        if not skill_md.exists():
            return None
        
        # Check cache first
        cache_key = str(skill_path.resolve())
        if use_cache and cache_key in SkillLoader._skill_cache:
            logger.debug(f"[SkillLoader] Loading skill from cache: {skill_path.name}")
            return SkillLoader._skill_cache[cache_key]
        
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
            
            skill = Skill(
                metadata=metadata_obj,
                instructions=instructions,
                tools=tools,
                tool_executors=tool_executors,
                skill_dir=skill_path
            )
            
            # Cache the loaded skill
            if use_cache:
                SkillLoader._skill_cache[cache_key] = skill
            
            return skill
        except Exception as e:
            logger.error(f"[SkillLoader] Error loading skill from {skill_dir}: {e}")
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

    # ─── SKILL_INDEX.md Support (Two-Tier Lazy Loading) ──────────────

    @staticmethod
    def load_skill_index(skills_dir: str, use_cache: bool = True) -> List[SkillIndexEntry]:
        """
        Load SKILL_INDEX.md from a skills directory.
        
        This is the cheap first-tier load: reads 1 file instead of N individual SKILL.md files.
        Returns lightweight entries with enough info for the agent to decide which skills to load fully.
        
        The SKILL_INDEX.md format is a Markdown table:
        | skill | description | trigger | path | cost_tier |
        
        Args:
            skills_dir: Path to directory containing SKILL_INDEX.md
            use_cache: Whether to use index cache (default: True)
            
        Returns:
            List of SkillIndexEntry instances
        """
        index_path = Path(skills_dir) / "SKILL_INDEX.md"
        if not index_path.exists():
            return []
        
        # Check cache first
        cache_key = str(index_path.resolve())
        if use_cache and cache_key in SkillLoader._index_cache:
            logger.debug(f"[SkillLoader] Loading index from cache: {skills_dir}")
            return SkillLoader._index_cache[cache_key]
        
        try:
            content = index_path.read_text(encoding="utf-8")
            entries = SkillLoader._parse_skill_index(content)
            
            # Cache the parsed index
            if use_cache:
                SkillLoader._index_cache[cache_key] = entries
            
            return entries
        except Exception as e:
            logger.error(f"[SkillLoader] Error loading SKILL_INDEX.md from {skills_dir}: {e}")
            return []

    @staticmethod
    def load_skill_by_index(skills_dir: str, skill_name: str, use_cache: bool = True) -> Optional[Skill]:
        """
        Load a specific skill on-demand using the index for path resolution.
        
        Two-tier flow:
        1. Agent reads SKILL_INDEX.md → gets list of SkillIndexEntry
        2. Agent picks relevant skill by name
        3. Calls this method to load the full SKILL.md + scripts
        
        Args:
            skills_dir: Parent skills directory containing SKILL_INDEX.md
            skill_name: Name of the skill to load
            use_cache: Whether to use skill cache (default: True)
            
        Returns:
            Loaded Skill or None
        """
        logger.info(f"[SkillLoader] Using skill: {skill_name}")
        entries = SkillLoader.load_skill_index(skills_dir, use_cache=use_cache)
        for entry in entries:
            if entry.name == skill_name:
                skill_path = Path(skills_dir) / entry.path
                return SkillLoader.load(str(skill_path), use_cache=use_cache)
        
        # Fallback: try direct path resolution
        skill_path = Path(skills_dir) / skill_name
        if skill_path.is_dir():
            return SkillLoader.load(str(skill_path), use_cache=use_cache)
        
        logger.warning(f"[SkillLoader] Skill '{skill_name}' not found in index or filesystem")
        return None
    
    @staticmethod
    def load_skills_batch(skills_dir: str, skill_names: List[str], use_cache: bool = True) -> List[Skill]:
        """
        Load multiple skills at once (batch loading).
        
        This is more efficient than loading skills one at a time when you know
        you'll need multiple skills upfront.
        
        Args:
            skills_dir: Parent skills directory containing SKILL_INDEX.md
            skill_names: List of skill names to load
            use_cache: Whether to use skill cache (default: True)
            
        Returns:
            List of loaded Skill instances (may be shorter than skill_names if some fail)
        """
        logger.info(f"[SkillLoader] Using skills (batch): {', '.join(skill_names)}")
        loaded_skills = []
        
        # Pre-load index once for efficiency
        entries = SkillLoader.load_skill_index(skills_dir, use_cache=use_cache)
        entry_map = {e.name: e for e in entries}
        
        for skill_name in skill_names:
            if skill_name in entry_map:
                entry = entry_map[skill_name]
                skill_path = Path(skills_dir) / entry.path
                skill = SkillLoader.load(str(skill_path), use_cache=use_cache)
                if skill:
                    loaded_skills.append(skill)
            else:
                # Try direct path resolution
                skill_path = Path(skills_dir) / skill_name
                if skill_path.is_dir():
                    skill = SkillLoader.load(str(skill_path), use_cache=use_cache)
                    if skill:
                        loaded_skills.append(skill)
                else:
                    logger.warning(f"[SkillLoader] Skill '{skill_name}' not found in index or filesystem")
        
        return loaded_skills

    @staticmethod
    def discover_with_index(skills_dir: str) -> tuple:
        """
        Discover skills directory with index-first strategy.
        
        Returns:
            (index_entries, full_skills) tuple:
            - index_entries: List[SkillIndexEntry] from SKILL_INDEX.md (cheap load)
            - full_skills: List[Skill] from immediate discovery (for directories without index)
        """
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            return [], []
        
        # Try index first
        index_entries = SkillLoader.load_skill_index(skills_dir)
        
        # Also discover any skills not in index (fallback)
        full_skills = []
        indexed_paths = {e.path.rstrip('/') for e in index_entries}
        
        for item in skills_path.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                # Skip if already in index (will be loaded on-demand)
                rel_path = str(item.relative_to(skills_path)).replace('\\', '/')
                if rel_path not in indexed_paths:
                    skill = SkillLoader.load(str(item))
                    if skill:
                        full_skills.append(skill)
        
        return index_entries, full_skills

    @staticmethod
    def _parse_skill_index(content: str) -> List[SkillIndexEntry]:
        """
        Parse SKILL_INDEX.md content into SkillIndexEntry list.
        
        Supports multiple formats:
        1. YAML format: skills: list with name, description, triggers, etc.
        2. Markdown table: | skill | description | trigger | path | cost_tier |
        3. Simple list: - name: description (trigger: ...)
        """
        entries = []
        
        # Try YAML format first (new structured format)
        if content.strip().startswith('# Skill Index') and 'skills:' in content:
            entries = SkillLoader._parse_skill_index_yaml(content)
            if entries:
                return entries
        
        # Try Markdown table format (legacy)
        # Use [^|]+ to match content between pipes (avoids matching separator rows)
        table_pattern = r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
        for match in re.finditer(table_pattern, content):
            name = match.group(1).strip()
            description = match.group(2).strip()
            trigger = match.group(3).strip()
            path = match.group(4).strip()
            cost_tier = match.group(5).strip() or "low"
            
            # Skip header row, separator rows, and empty entries
            if name in ('skill', '---', '----', 'name', ''):
                continue
            # Skip separator rows (all dashes)
            if all(c == '-' for c in name):
                continue
            
            entries.append(SkillIndexEntry(
                name=name,
                description=description,
                trigger=trigger,
                path=path,
                cost_tier=cost_tier
            ))
        
        # If no table found, try simple list format
        if not entries:
            list_pattern = r'-\s+(\w+):\s+(.+?)(?:\s+\((.+?)\))?$'
            for match in re.finditer(list_pattern, content, re.MULTILINE):
                name = match.group(1).strip()
                description = match.group(2).strip()
                extras = match.group(3) or ""
                
                trigger = ""
                cost_tier = "low"
                if extras:
                    for part in extras.split(','):
                        part = part.strip()
                        if part.startswith('trigger:'):
                            trigger = part.split(':', 1)[1].strip()
                        elif part.startswith('cost:'):
                            cost_tier = part.split(':', 1)[1].strip()
                
                entries.append(SkillIndexEntry(
                    name=name,
                    description=description,
                    trigger=trigger,
                    path=f"{name}/",
                    cost_tier=cost_tier
                ))
        
        return entries
    
    @staticmethod
    def _parse_skill_index_yaml(content: str) -> List[SkillIndexEntry]:
        """
        Parse YAML-format SKILL_INDEX.md into SkillIndexEntry list.
        
        The YAML format provides richer metadata including:
        - Capabilities and limitations
        - Boundaries (CAN_DO / CANNOT_DO)
        - Example usage patterns
        - Detailed trigger phrases
        """
        entries = []
        
        # Simple YAML parser for our specific format
        # We don't want to depend on PyYAML for this
        current_skill = None
        in_skills_section = False
        in_triggers = False
        in_can_do = False
        in_cannot_do = False
        current_metadata = {}
        
        for line in content.split('\n'):
            stripped = line.strip()
            
            # Skip comments and empty lines
            if stripped.startswith('#') or not stripped:
                continue
            
            # Detect skills section
            if stripped == 'skills:':
                in_skills_section = True
                continue
            
            if not in_skills_section:
                continue
            
            # Detect new skill entry
            if stripped.startswith('- name:'):
                if current_skill:
                    current_skill.set_raw_metadata(current_metadata)
                    entries.append(current_skill)
                
                skill_name = stripped.split(':', 1)[1].strip()
                current_skill = SkillIndexEntry(
                    name=skill_name,
                    description="",
                    trigger="",
                    path=f"{skill_name}/",
                    cost_tier="low"
                )
                current_metadata = {'can_do': [], 'cannot_do': []}
                in_triggers = False
                in_can_do = False
                in_cannot_do = False
                continue
            
            if current_skill is None:
                continue
            
            # Parse skill fields
            if stripped.startswith('version:'):
                pass  # Version tracked but not in index entry
            elif stripped.startswith('cost_tier:'):
                current_skill.cost_tier = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('path:'):
                current_skill.path = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('description:'):
                desc = stripped.split(':', 1)[1].strip()
                if desc == '|':
                    current_skill.description = ""
                else:
                    current_skill.description = desc
            elif stripped.startswith('triggers:'):
                in_triggers = True
                in_can_do = False
                in_cannot_do = False
            elif stripped.startswith('capabilities:') or stripped.startswith('limitations:'):
                in_triggers = False
                in_can_do = False
                in_cannot_do = False
            elif stripped.startswith('boundaries:'):
                in_triggers = False
                in_can_do = False
                in_cannot_do = False
            elif stripped.startswith('CAN_DO:'):
                in_can_do = True
                in_cannot_do = False
            elif stripped.startswith('CANNOT_DO:'):
                in_can_do = False
                in_cannot_do = True
            elif stripped.startswith('example_usage:') or stripped.startswith('supported_languages:'):
                in_triggers = False
                in_can_do = False
                in_cannot_do = False
            
            # Collect triggers
            elif in_triggers and stripped.startswith('- '):
                trigger = stripped[2:].strip().strip('"')
                if current_skill.trigger:
                    current_skill.trigger += ", " + trigger
                else:
                    current_skill.trigger = trigger
            
            # Collect CAN_DO items
            elif in_can_do and stripped.startswith('- '):
                item = stripped[3:].strip().rstrip('"')
                if item:
                    current_metadata['can_do'].append(item)
            
            # Collect CANNOT_DO items
            elif in_cannot_do and stripped.startswith('- '):
                item = stripped[3:].strip().rstrip('"')
                if item:
                    current_metadata['cannot_do'].append(item)
        
        # Don't forget the last skill
        if current_skill:
            current_skill.set_raw_metadata(current_metadata)
            entries.append(current_skill)
        
        return entries

    @staticmethod
    def _validate_ast_safety(tree: ast.AST) -> bool:
        """
        Validate that an AST tree doesn't contain dangerous patterns.

        Checks for:
        - exec/eval/compile calls
        - __import__ usage
        - Dangerous attribute access patterns
        - Dangerous function calls
        - os/subprocess/sys imports

        Args:
            tree: Parsed AST tree to validate

        Returns:
            True if safe, False if dangerous patterns found
        """
        import ast

        # Dangerous function names that should never be called
        dangerous_calls = {'exec', 'eval', 'compile', '__import__'}

        # Dangerous module imports
        dangerous_modules = {'os', 'subprocess', 'sys', 'shutil', 'pathlib', 'importlib'}

        # Dangerous attribute access patterns (methods that could be used for injection)
        dangerous_attrs = {
            '__import__', '__builtins__', '__subclasses__', '__bases__',
            '__globals__', '__code__', '__dict__', '__class__',
            'system', 'popen', 'call', 'run', 'Popen',
        }

        for node in ast.walk(tree):
            # Check for dangerous function calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in dangerous_calls:
                        return False
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in dangerous_calls:
                        return False

            # Check for dangerous imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name.split('.')[0]
                    if module_name in dangerous_modules:
                        return False

            if isinstance(node, ast.ImportFrom):
                if node.module:
                    module_name = node.module.split('.')[0]
                    if module_name in dangerous_modules:
                        return False

            # Check for dangerous attribute access
            if isinstance(node, ast.Attribute):
                if node.attr in dangerous_attrs:
                    return False

            # Check for dangerous name access (e.g., __import__)
            if isinstance(node, ast.Name):
                if node.id.startswith('__') and node.id.endswith('__'):
                    if node.id not in ('True', 'False', 'None', 'self', 'cls'):
                        return False

        return True

    # ─── Index Auto-Generation ──────────────────────────────────────

    @staticmethod
    def build_skill_index(skills_dir: str, output_path: str = None) -> str:
        """
        Auto-generate SKILL_INDEX.md from individual SKILL.md files.
        
        This is the build-time script that prevents index drift.
        Should be run whenever skills are added/modified.
        
        Generates a structured YAML format with:
        - Basic metadata (name, version, cost_tier, path)
        - Detailed description
        - Trigger phrases
        - Capabilities and limitations
        - Boundaries (CAN_DO / CANNOT_DO)
        - Example usage patterns
        
        Args:
            skills_dir: Directory containing skill subdirectories
            output_path: Optional output path (defaults to skills_dir/SKILL_INDEX.md)
            
        Returns:
            Generated index content
        """
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            return ""
        
        skills = []
        for item in sorted(skills_path.iterdir()):
            if item.is_dir() and (item / "SKILL.md").exists():
                try:
                    content = (item / "SKILL.md").read_text(encoding="utf-8")
                    metadata, instructions = SkillLoader._parse_skill_md(content)
                    
                    skill_data = {
                        'name': metadata.get("name", item.name),
                        'version': metadata.get("version", "1.0.0"),
                        'description': metadata.get("description", ""),
                        'trigger': metadata.get("trigger", ""),
                        'cost_tier': metadata.get("cost_tier", "low"),
                        'path': item.name + "/",
                        'tags': metadata.get("tags", []),
                        'requires': metadata.get("requires", []),
                        'conflicts_with': metadata.get("conflicts_with", []),
                        'min_framework_version': metadata.get("min_framework_version", ""),
                    }
                    skills.append(skill_data)
                except Exception as e:
                    logger.warning(f"[SkillLoader] Error parsing {item / 'SKILL.md'}: {e}")
        
        # Generate YAML format
        index_content = """# Skill Index
# This file is AUTO-GENERATED by _build_index.py - DO NOT EDIT MANUALLY

skills:
"""
        
        for skill in skills:
            index_content += f"""  # ─────────────────────────────────────────────────────────────────────────────
  # {skill['name'].replace('_', ' ').title()} Skill
  # ─────────────────────────────────────────────────────────────────────────────
  - name: {skill['name']}
    version: "{skill['version']}"
    cost_tier: {skill['cost_tier']}
    path: {skill['path']}
    
    description: |
      {skill['description']}
    
    triggers:"""
            
            # Add triggers
            if skill['trigger']:
                for trigger in skill['trigger'].split(','):
                    index_content += f'\n      - "{trigger.strip()}"'
            else:
                index_content += f'\n      - "use {skill["name"].replace("_", " ")} skill"'
            
            index_content += "\n"
        
        if output_path is None:
            output_path = str(skills_path / "SKILL_INDEX.md")
        
        Path(output_path).write_text(index_content, encoding="utf-8")
        logger.info(f"[SkillLoader] Generated SKILL_INDEX.md with {len(skills)} skills at {output_path}")
        
        return index_content

    # ─── Core Parsing ───────────────────────────────────────────────

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
                    
                    if key in ('tags', 'requires'):
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

        Parses Google-style and Sphinx-style docstrings for parameter descriptions.

        Security: Uses AST-level validation to prevent code injection.
        Only allows safe imports and blocks dangerous operations at syntax level.

        Returns:
            List of (func_name, func, schema_dict) tuples
        """
        import importlib.util
        import inspect
        import ast

        results = []
        try:
            # Security: Read and validate file content before execution
            content = py_file.read_text(encoding="utf-8")

            # AST-level validation: Parse and check for dangerous patterns
            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                logger.warning(f"[SkillLoader] Blocked skill file {py_file.name}: syntax error - {e}")
                return []

            # Dangerous node types and patterns to reject
            if not SkillLoader._validate_ast_safety(tree):
                logger.warning(f"[SkillLoader] Blocked skill file {py_file.name}: contains dangerous AST patterns")
                return []

            # Create a restricted namespace for execution
            safe_builtins = {
                'True': True, 'False': False, 'None': None,
                'str': str, 'int': int, 'float': float, 'bool': bool,
                'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
                'len': len, 'range': range, 'enumerate': enumerate,
                'zip': zip, 'map': map, 'filter': filter,
                'print': print, 'isinstance': isinstance, 'hasattr': hasattr,
                # Note: getattr/setattr intentionally excluded to prevent reflection attacks
            }

            spec = importlib.util.spec_from_file_location(
                f"skill_script_{py_file.stem}", str(py_file)
            )
            if spec is None or spec.loader is None:
                logger.warning(
                    f"[SkillLoader] Skipping skill file {py_file.name}: "
                    f"could not create a module spec"
                )
                return []
            module = importlib.util.module_from_spec(spec)

            # Override __builtins__ with restricted set
            module.__builtins__ = {k: v for k, v in safe_builtins.items()}

            spec.loader.exec_module(module)
            
            # Find functions with docstrings (treat as tools)
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if name.startswith('_'):
                    continue
                if not obj.__doc__:
                    continue
                
                # Parse docstring for description and param docs
                raw_doc = obj.__doc__
                description, param_docs = SkillLoader._parse_function_docstring(raw_doc)
                
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
                    
                    # Use parsed docstring description, fallback to generic
                    param_desc = param_docs.get(param_name, f"Parameter: {param_name}")
                    props[param_name] = {"type": param_type, "description": param_desc}
                    
                    if param.default is inspect.Parameter.empty:
                        required.append(param_name)
                
                schema = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": required
                        }
                    }
                }
                
                results.append((name, obj, schema))
        except Exception as e:
            logger.error(f"[SkillLoader] Error importing {py_file}: {e}")
        
        return results

    @staticmethod
    def _parse_function_docstring(docstring: str) -> tuple:
        """
        Parse a function docstring to extract description and parameter docs.
        
        Supports Google-style and Sphinx-style docstrings.
        
        Returns:
            (description, param_docs_dict)
        """
        if not docstring:
            return "No description provided.", {}
        
        doc_lines = docstring.strip().split('\n')
        description_lines = []
        param_docs = {}
        
        in_args_section = False
        for line in doc_lines:
            stripped = line.strip()
            
            # Sphinx style: :param name: description
            sphinx_match = re.match(r':param\s+(\w+)\s*:(.*)', stripped)
            if sphinx_match:
                param_docs[sphinx_match.group(1)] = sphinx_match.group(2).strip()
                continue
            
            # Google style: "Args:" header
            if stripped.lower() in ('args:', 'arguments:', 'parameters:', 'params:'):
                in_args_section = True
                continue
            
            # Google style: "Returns:", "Raises:", etc. ends the Args section
            if stripped.lower().rstrip(':') in ('returns', 'raises', 'yields', 'examples', 'note', 'notes'):
                in_args_section = False
                continue
            
            if in_args_section and stripped:
                # Google style: "param_name (type): description" or "param_name: description"
                arg_match = re.match(r'(\w+)\s*(?:\([^)]*\))?\s*:(.*)', stripped)
                if arg_match:
                    param_docs[arg_match.group(1)] = arg_match.group(2).strip()
                continue
            
            if not in_args_section and stripped:
                description_lines.append(stripped)
        
        description = ' '.join(description_lines) if description_lines else docstring.strip()
        return description, param_docs

    @classmethod
    def clear_cache(cls) -> None:
        """
        Clear all cached skills and indices.
        
        This is useful when:
        - Skills have been modified on disk
        - You want to force a fresh load
        - Memory cleanup is needed
        """
        cls._skill_cache.clear()
        cls._index_cache.clear()
        logger.debug("[SkillLoader] Cache cleared")
    
    @classmethod
    def get_cache_stats(cls) -> Dict[str, int]:
        """
        Get statistics about the current cache state.
        
        Returns:
            Dictionary with cache statistics:
            - cached_skills: Number of skills in cache
            - cached_indices: Number of indices in cache
        """
        return {
            "cached_skills": len(cls._skill_cache),
            "cached_indices": len(cls._index_cache),
        }
    
    @classmethod
    def remove_from_cache(cls, skill_dir: str) -> bool:
        """
        Remove a specific skill from the cache.
        
        Args:
            skill_dir: Path to the skill directory
            
        Returns:
            True if skill was in cache and removed, False otherwise
        """
        cache_key = str(Path(skill_dir).resolve())
        if cache_key in cls._skill_cache:
            del cls._skill_cache[cache_key]
            logger.debug(f"[SkillLoader] Removed skill from cache: {skill_dir}")
            return True
        return False
    
    @staticmethod
    def resolve_dependencies(skills: List[Skill]) -> tuple:
        """
        Resolve dependencies for a list of skills.
        
        This method:
        1. Checks if all dependencies are satisfied
        2. Identifies conflicts between skills
        3. Returns sorted list (dependencies first) and any issues
        
        Args:
            skills: List of skills to resolve
            
        Returns:
            Tuple of (resolved_skills, issues) where:
            - resolved_skills: Skills sorted with dependencies first
            - issues: List of dicts describing dependency/conflict issues
        """
        issues = []
        skill_map = {s.name: s for s in skills}
        loaded_names = list(skill_map.keys())
        
        # Check dependencies for each skill
        for skill in skills:
            if not skill.check_dependencies(loaded_names):
                issues.append({
                    "type": "missing_dependency",
                    "skill": skill.name,
                    "missing": skill.missing_dependencies,
                })
            
            # Check for conflicts
            conflicts = skill.check_conflicts(loaded_names)
            if conflicts:
                issues.append({
                    "type": "conflict",
                    "skill": skill.name,
                    "conflicts_with": conflicts,
                })
        
        # Topological sort (dependencies first)
        def _sort_key(skill):
            # Skills with no dependencies come first
            return (0 if not skill.requires else 1, skill.name)
        
        resolved = sorted(skills, key=_sort_key)
        
        return resolved, issues
    
    @staticmethod
    def load_skill_graph(skills_dir: str, skill_names: List[str], use_cache: bool = True) -> tuple:
        """
        Load skills with automatic dependency resolution.
        
        This method:
        1. Loads the requested skills
        2. Automatically loads any missing dependencies
        3. Checks for conflicts
        4. Returns loaded skills and any issues
        
        Args:
            skills_dir: Parent skills directory
            skill_names: List of skill names to load
            use_cache: Whether to use cache
            
        Returns:
            Tuple of (loaded_skills, issues)
        """
        logger.info(f"[SkillLoader] Using skills (graph): {', '.join(skill_names)}")
        
        # Load requested skills
        loaded_skills = SkillLoader.load_skills_batch(skills_dir, skill_names, use_cache=use_cache)
        loaded_names = [s.name for s in loaded_skills]
        
        # Find and load missing dependencies
        missing_deps = set()
        for skill in loaded_skills:
            for dep in skill.requires:
                if dep not in loaded_names:
                    missing_deps.add(dep)
        
        # Load missing dependencies
        if missing_deps:
            logger.info(f"[SkillLoader] Loading dependencies: {', '.join(missing_deps)}")
            dep_skills = SkillLoader.load_skills_batch(skills_dir, list(missing_deps), use_cache=use_cache)
            loaded_skills.extend(dep_skills)
            loaded_names.extend([s.name for s in dep_skills])
        
        # Resolve dependencies and check for issues
        resolved, issues = SkillLoader.resolve_dependencies(loaded_skills)
        
        if issues:
            logger.warning(f"[SkillLoader] Dependency issues: {issues}")
        
        return resolved, issues
