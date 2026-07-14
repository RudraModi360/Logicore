"""
Skill Loader — discovers and loads skills from directories.

Three-layer architecture support:
1. Knowledge Layer: SKILL.md, examples, templates, validation rules
2. Cognitive Layer: LLM reasoning (injected via system prompt)
3. Execution Layer: Optional capabilities (scripts, tools)

Supports two-tier lazy loading via SKILL_INDEX.md:
1. Load SKILL_INDEX.md once per session (cheap, controlled token size)
2. Load individual SKILL.md on-demand when agent decides to use a skill
"""

import re
import ast
import os
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from .base import Skill, SkillMetadata, Capability, CapabilityType

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
        self._raw_metadata: Dict[str, Any] = {}

    @property
    def triggers(self) -> List[str]:
        if not self.trigger:
            return []
        return [t.strip() for t in self.trigger.split(',')]

    @property
    def can_do(self) -> List[str]:
        return self._raw_metadata.get('can_do', [])

    @property
    def cannot_do(self) -> List[str]:
        return self._raw_metadata.get('cannot_do', [])

    def set_raw_metadata(self, metadata: Dict[str, Any]) -> None:
        self._raw_metadata = metadata

    def __repr__(self):
        return f"SkillIndexEntry(name='{self.name}', trigger='{self.trigger}', path='{self.path}')"


class SkillLoader:
    """
    Discovers and loads skills from filesystem directories.

    Supports:
    - Knowledge Layer: SKILL.md, examples/, templates/, validation_rules/
    - Execution Layer: scripts/ (optional capabilities)
    - SKILL_INDEX.md for two-tier lazy loading
    - Skill caching for improved performance
    - Batch skill loading with dependency resolution
    """

    _skill_cache: Dict[str, Skill] = {}
    _index_cache: Dict[str, List[SkillIndexEntry]] = {}

    # ─── Core Loading ────────────────────────────────────────────────

    @staticmethod
    def load(skill_dir: str, use_cache: bool = True) -> Optional[Skill]:
        """
        Load a skill from a directory containing SKILL.md.

        Discovers:
        - SKILL.md → metadata + instructions (Knowledge Layer)
        - examples/ → example workflows
        - templates/ → reusable templates
        - validation_rules/ → quality checklists
        - scripts/ → optional executable capabilities (Execution Layer)

        Args:
            skill_dir: Path to the skill directory
            use_cache: Whether to use skill cache

        Returns:
            Skill instance or None if invalid
        """
        skill_path = Path(skill_dir)
        skill_md = skill_path / "SKILL.md"

        if not skill_md.exists():
            return None

        cache_key = str(skill_path.resolve())
        if use_cache and cache_key in SkillLoader._skill_cache:
            logger.debug(f"[SkillLoader] Loading skill from cache: {skill_path.name}")
            return SkillLoader._skill_cache[cache_key]

        try:
            content = skill_md.read_text(encoding="utf-8")
            metadata, instructions = SkillLoader._parse_skill_md(content)
            metadata_obj = SkillMetadata.from_dict(metadata)

            # ─── Knowledge Layer Discovery ──────────────────────────
            examples = SkillLoader._discover_examples(skill_path)
            templates = SkillLoader._discover_templates(skill_path)
            validation_rules = SkillLoader._discover_validation_rules(skill_path)

            # ─── Execution Layer Discovery (Optional) ───────────────
            capabilities = SkillLoader._discover_capabilities(skill_path)

            skill = Skill(
                metadata=metadata_obj,
                instructions=instructions,
                skill_dir=skill_path,
                examples=examples,
                templates=templates,
                validation_rules=validation_rules,
                capabilities=capabilities,
            )

            if use_cache:
                SkillLoader._skill_cache[cache_key] = skill

            return skill
        except Exception as e:
            logger.error(f"[SkillLoader] Error loading skill from {skill_dir}: {e}")
            return None

    # ─── Knowledge Layer Discovery ───────────────────────────────────

    @staticmethod
    def _discover_examples(skill_path: Path) -> List[str]:
        """Discover example files in examples/ directory."""
        examples_dir = skill_path / "examples"
        if not examples_dir.exists():
            return []

        examples = []
        for f in sorted(examples_dir.iterdir()):
            if f.is_file() and f.suffix in ('.md', '.txt', '.py', '.json'):
                try:
                    examples.append(f.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[SkillLoader] Could not read example {f}: {e}")
        return examples

    @staticmethod
    def _discover_templates(skill_path: Path) -> List[str]:
        """Discover template files in templates/ directory."""
        templates_dir = skill_path / "templates"
        if not templates_dir.exists():
            return []

        templates = []
        for f in sorted(templates_dir.iterdir()):
            if f.is_file():
                try:
                    templates.append(f.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[SkillLoader] Could not read template {f}: {e}")
        return templates

    @staticmethod
    def _discover_validation_rules(skill_path: Path) -> List[str]:
        """Discover validation rules from validation_rules/ directory or SKILL.md frontmatter."""
        rules = []

        # Check validation_rules/ directory
        rules_dir = skill_path / "validation_rules"
        if rules_dir.exists():
            for f in sorted(rules_dir.iterdir()):
                if f.is_file() and f.suffix in ('.md', '.txt'):
                    try:
                        content = f.read_text(encoding="utf-8")
                        # Parse as list of rules (one per line or bullet point)
                        for line in content.split('\n'):
                            line = line.strip()
                            if line and not line.startswith('#'):
                                rules.append(line.lstrip('- ').lstrip('* '))
                    except Exception as e:
                        logger.warning(f"[SkillLoader] Could not read rule {f}: {e}")

        return rules

    # ─── Execution Layer Discovery (Optional Capabilities) ───────────

    @staticmethod
    def _discover_capabilities(skill_path: Path) -> List[Capability]:
        """
        Discover executable capabilities in a skill directory.

        Looks for:
        - scripts/*.py → Python script capabilities
        - scripts/*.js → JavaScript capabilities

        Returns list of Capability objects (may be empty for knowledge-only skills).
        """
        capabilities = []
        scripts_dir = skill_path / "scripts"

        if not scripts_dir.exists():
            return capabilities

        for script_file in sorted(scripts_dir.iterdir()):
            if not script_file.is_file():
                continue

            if script_file.suffix == '.py':
                caps = SkillLoader._discover_python_capabilities(script_file, skill_path.name)
                capabilities.extend(caps)
            elif script_file.suffix in ('.js', '.mjs'):
                caps = SkillLoader._discover_script_capabilities(
                    script_file, skill_path.name, language="javascript"
                )
                capabilities.extend(caps)

        return capabilities

    @staticmethod
    def _discover_python_capabilities(py_file: Path, skill_name: str) -> List[Capability]:
        """
        Discover tool functions from a Python file and wrap as Capabilities.

        Functions with docstrings and type hints become TOOL capabilities.
        """
        import importlib.util
        import inspect

        capabilities = []
        try:
            content = py_file.read_text(encoding="utf-8")

            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                logger.warning(f"[SkillLoader] Blocked skill file {py_file.name}: syntax error - {e}")
                return []

            if not SkillLoader._validate_ast_safety(tree):
                logger.warning(f"[SkillLoader] Blocked skill file {py_file.name}: contains dangerous AST patterns")
                return []

            spec = importlib.util.spec_from_file_location(
                f"skill_script_{py_file.stem}", str(py_file)
            )
            if spec is None or spec.loader is None:
                return []

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if name.startswith('_'):
                    continue
                if not obj.__doc__:
                    continue

                description, param_docs = SkillLoader._parse_function_docstring(obj.__doc__)
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
                        },
                        "x-origin": f"skill:{skill_name}",
                        "x-complexity": "simple",
                    }
                }

                cap = Capability(
                    name=name,
                    cap_type=CapabilityType.TOOL,
                    description=description,
                    schema=schema,
                    executor=obj,
                    path=str(py_file),
                    language="python",
                    complexity="simple",
                )
                capabilities.append(cap)

        except Exception as e:
            logger.error(f"[SkillLoader] Error importing {py_file}: {e}")

        return capabilities

    @staticmethod
    def _discover_script_capabilities(script_file: Path, skill_name: str, language: str = "javascript") -> List[Capability]:
        """
        Register a script file as a SCRIPT capability.
        The script is not executed at load time — it's available for the LLM to invoke.
        """
        cap = Capability(
            name=script_file.stem,
            cap_type=CapabilityType.SCRIPT,
            description=f"Script: {script_file.name}",
            path=str(script_file),
            language=language,
            complexity="moderate",
        )
        return [cap]

    # ─── Discovery (Directory Scanning) ──────────────────────────────

    @staticmethod
    def discover(skills_dir: str) -> List[Skill]:
        """Discover all skills in a parent directory."""
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
        """Discover skills from the user's workspace."""
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
        """Load SKILL_INDEX.md from a skills directory."""
        index_path = Path(skills_dir) / "SKILL_INDEX.md"
        if not index_path.exists():
            return []

        cache_key = str(index_path.resolve())
        if use_cache and cache_key in SkillLoader._index_cache:
            return SkillLoader._index_cache[cache_key]

        try:
            content = index_path.read_text(encoding="utf-8")
            entries = SkillLoader._parse_skill_index(content)
            if use_cache:
                SkillLoader._index_cache[cache_key] = entries
            return entries
        except Exception as e:
            logger.error(f"[SkillLoader] Error loading SKILL_INDEX.md from {skills_dir}: {e}")
            return []

    @staticmethod
    def load_skill_by_index(skills_dir: str, skill_name: str, use_cache: bool = True) -> Optional[Skill]:
        """Load a specific skill on-demand using the index for path resolution."""
        logger.info(f"[SkillLoader] Using skill: {skill_name}")
        entries = SkillLoader.load_skill_index(skills_dir, use_cache=use_cache)
        for entry in entries:
            if entry.name == skill_name:
                # entry.path may be "skill_name/SKILL.md" — strip filename to get directory
                entry_dir = str(Path(entry.path).parent)
                skill_path = Path(skills_dir) / entry_dir
                return SkillLoader.load(str(skill_path), use_cache=use_cache)

        skill_path = Path(skills_dir) / skill_name
        if skill_path.is_dir():
            return SkillLoader.load(str(skill_path), use_cache=use_cache)

        logger.warning(f"[SkillLoader] Skill '{skill_name}' not found in index or filesystem")
        return None

    @staticmethod
    def load_skills_batch(skills_dir: str, skill_names: List[str], use_cache: bool = True) -> List[Skill]:
        """Load multiple skills at once."""
        logger.info(f"[SkillLoader] Using skills (batch): {', '.join(skill_names)}")
        loaded_skills = []
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
                skill_path = Path(skills_dir) / skill_name
                if skill_path.is_dir():
                    skill = SkillLoader.load(str(skill_path), use_cache=use_cache)
                    if skill:
                        loaded_skills.append(skill)
                else:
                    logger.warning(f"[SkillLoader] Skill '{skill_name}' not found")

        return loaded_skills

    @staticmethod
    def discover_with_index(skills_dir: str) -> tuple:
        """Discover skills directory with index-first strategy."""
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            return [], []

        index_entries = SkillLoader.load_skill_index(skills_dir)
        full_skills = []
        indexed_paths = {e.path.rstrip('/') for e in index_entries}

        for item in skills_path.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                rel_path = str(item.relative_to(skills_path)).replace('\\', '/')
                if rel_path not in indexed_paths:
                    skill = SkillLoader.load(str(item))
                    if skill:
                        full_skills.append(skill)

        return index_entries, full_skills

    # ─── Index Parsing ───────────────────────────────────────────────

    @staticmethod
    def _parse_skill_index(content: str) -> List[SkillIndexEntry]:
        """Parse SKILL_INDEX.md content into SkillIndexEntry list."""
        entries = []

        if content.strip().startswith('# Skill Index') and 'skills:' in content:
            entries = SkillLoader._parse_skill_index_yaml(content)
            if entries:
                return entries

        table_pattern = r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
        for match in re.finditer(table_pattern, content):
            name = match.group(1).strip()
            description = match.group(2).strip()
            trigger = match.group(3).strip()
            path = match.group(4).strip()
            cost_tier = match.group(5).strip() or "low"

            if name in ('skill', '---', '----', 'name', ''):
                continue
            if all(c == '-' for c in name):
                continue

            entries.append(SkillIndexEntry(
                name=name, description=description,
                trigger=trigger, path=path, cost_tier=cost_tier
            ))

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
                    name=name, description=description,
                    trigger=trigger, path=f"{name}/", cost_tier=cost_tier
                ))

        return entries

    @staticmethod
    def _parse_skill_index_yaml(content: str) -> List[SkillIndexEntry]:
        """Parse YAML-format SKILL_INDEX.md."""
        entries = []
        current_skill = None
        in_skills_section = False
        in_triggers = False
        in_can_do = False
        in_cannot_do = False
        current_metadata = {}

        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#') or not stripped:
                continue
            if stripped == 'skills:':
                in_skills_section = True
                continue
            if not in_skills_section:
                continue

            if stripped.startswith('- name:'):
                if current_skill:
                    current_skill.set_raw_metadata(current_metadata)
                    entries.append(current_skill)
                skill_name = stripped.split(':', 1)[1].strip()
                current_skill = SkillIndexEntry(
                    name=skill_name, description="", trigger="",
                    path=f"{skill_name}/", cost_tier="low"
                )
                current_metadata = {'can_do': [], 'cannot_do': []}
                in_triggers = False
                in_can_do = False
                in_cannot_do = False
                continue

            if current_skill is None:
                continue

            if stripped.startswith('version:'):
                pass
            elif stripped.startswith('cost_tier:'):
                current_skill.cost_tier = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('path:'):
                current_skill.path = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('description:'):
                desc = stripped.split(':', 1)[1].strip()
                current_skill.description = "" if desc == '|' else desc
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
            elif in_triggers and stripped.startswith('- '):
                trigger = stripped[2:].strip().strip('"')
                current_skill.trigger = f"{current_skill.trigger}, {trigger}" if current_skill.trigger else trigger
            elif in_can_do and stripped.startswith('- '):
                item = stripped[3:].strip().rstrip('"')
                if item:
                    current_metadata['can_do'].append(item)
            elif in_cannot_do and stripped.startswith('- '):
                item = stripped[3:].strip().rstrip('"')
                if item:
                    current_metadata['cannot_do'].append(item)

        if current_skill:
            current_skill.set_raw_metadata(current_metadata)
            entries.append(current_skill)

        return entries

    # ─── AST Safety Validation ───────────────────────────────────────

    @staticmethod
    def _validate_ast_safety(tree: ast.AST) -> bool:
        """Validate that an AST tree doesn't contain dangerous patterns."""
        import ast

        dangerous_calls = {'exec', 'eval', 'compile', '__import__'}
        dangerous_modules = {'subprocess', 'sys', 'shutil', 'importlib', 'socket', 'pickle', 'multiprocessing'}
        dangerous_attrs = {
            '__import__', '__builtins__', '__subclasses__', '__bases__',
            '__globals__', '__code__', '__dict__', '__class__',
            'system', 'popen', 'Popen',
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in dangerous_calls:
                        return False
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in dangerous_calls:
                        return False

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

            if isinstance(node, ast.Attribute):
                if node.attr in dangerous_attrs:
                    return False

            if isinstance(node, ast.Name):
                if node.id.startswith('__') and node.id.endswith('__'):
                    if node.id not in ('True', 'False', 'None', 'self', 'cls'):
                        return False

        return True

    # ─── Index Auto-Generation ───────────────────────────────────────

    @staticmethod
    def build_skill_index(skills_dir: str, output_path: str = None) -> str:
        """Auto-generate SKILL_INDEX.md from individual SKILL.md files."""
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

        index_content = """# Skill Index
# This file is AUTO-GENERATED by _build_index.py - DO NOT EDIT MANUALLY

skills:
"""
        for skill in skills:
            index_content += f"""  # {skill['name'].replace('_', ' ').title()} Skill
  - name: {skill['name']}
    version: "{skill['version']}"
    cost_tier: {skill['cost_tier']}
    path: {skill['path']}

    description: |
      {skill['description']}

    triggers:"""
            if skill['trigger']:
                for trigger in skill['trigger'].split(','):
                    index_content += f'\n      - "{trigger.strip()}"'
            else:
                index_content += f'\n      - "use {skill["name"].replace("_", " ")} skill"'
            index_content += "\n"

        if output_path is None:
            output_path = str(skills_path / "SKILL_INDEX.md")

        Path(output_path).write_text(index_content, encoding="utf-8")
        logger.info(f"[SkillLoader] Generated SKILL_INDEX.md with {len(skills)} skills")
        return index_content

    # ─── Core Parsing ────────────────────────────────────────────────

    @staticmethod
    def _parse_skill_md(content: str) -> tuple:
        """Parse SKILL.md with YAML frontmatter."""
        metadata = {"name": "Unknown", "description": "No description"}
        instructions = content

        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
        if frontmatter_match:
            frontmatter = frontmatter_match.group(1)
            instructions = frontmatter_match.group(2).strip()

            for line in frontmatter.strip().split('\n'):
                line = line.strip()
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key in ('tags', 'requires'):
                        value = value.strip('[]')
                        metadata[key] = [v.strip().strip('"').strip("'") for v in value.split(',') if v.strip()]
                    else:
                        metadata[key] = value

        return metadata, instructions

    @staticmethod
    def _parse_function_docstring(docstring: str) -> tuple:
        """Parse a function docstring to extract description and parameter docs."""
        if not docstring:
            return "No description provided.", {}

        doc_lines = docstring.strip().split('\n')
        description_lines = []
        param_docs = {}
        in_args_section = False

        for line in doc_lines:
            stripped = line.strip()
            sphinx_match = re.match(r':param\s+(\w+)\s*:(.*)', stripped)
            if sphinx_match:
                param_docs[sphinx_match.group(1)] = sphinx_match.group(2).strip()
                continue
            if stripped.lower() in ('args:', 'arguments:', 'parameters:', 'params:'):
                in_args_section = True
                continue
            if stripped.lower().rstrip(':') in ('returns', 'raises', 'yields', 'examples', 'note', 'notes'):
                in_args_section = False
                continue
            if in_args_section and stripped:
                arg_match = re.match(r'(\w+)\s*(?:\([^)]*\))?\s*:(.*)', stripped)
                if arg_match:
                    param_docs[arg_match.group(1)] = arg_match.group(2).strip()
                continue
            if not in_args_section and stripped:
                description_lines.append(stripped)

        description = ' '.join(description_lines) if description_lines else docstring.strip()
        return description, param_docs

    # ─── Cache Management ────────────────────────────────────────────

    @classmethod
    def clear_cache(cls) -> None:
        cls._skill_cache.clear()
        cls._index_cache.clear()

    @classmethod
    def get_cache_stats(cls) -> Dict[str, int]:
        return {
            "cached_skills": len(cls._skill_cache),
            "cached_indices": len(cls._index_cache),
        }

    @classmethod
    def remove_from_cache(cls, skill_dir: str) -> bool:
        cache_key = str(Path(skill_dir).resolve())
        if cache_key in cls._skill_cache:
            del cls._skill_cache[cache_key]
            return True
        return False

    # ─── Dependency Resolution ───────────────────────────────────────

    @staticmethod
    def resolve_dependencies(skills: List[Skill]) -> tuple:
        """Resolve dependencies and check for conflicts."""
        issues = []
        skill_map = {s.name: s for s in skills}
        loaded_names = list(skill_map.keys())

        for skill in skills:
            if not skill.check_dependencies(loaded_names):
                issues.append({
                    "type": "missing_dependency",
                    "skill": skill.name,
                    "missing": skill.missing_dependencies,
                })
            conflicts = skill.check_conflicts(loaded_names)
            if conflicts:
                issues.append({
                    "type": "conflict",
                    "skill": skill.name,
                    "conflicts_with": conflicts,
                })

        def _sort_key(skill):
            return (0 if not skill.requires else 1, skill.name)

        resolved = sorted(skills, key=_sort_key)
        return resolved, issues

    @staticmethod
    def load_skill_graph(skills_dir: str, skill_names: List[str], use_cache: bool = True) -> tuple:
        """Load skills with automatic dependency resolution."""
        logger.info(f"[SkillLoader] Using skills (graph): {', '.join(skill_names)}")
        loaded_skills = SkillLoader.load_skills_batch(skills_dir, skill_names, use_cache=use_cache)
        loaded_names = [s.name for s in loaded_skills]

        missing_deps = set()
        for skill in loaded_skills:
            for dep in skill.requires:
                if dep not in loaded_names:
                    missing_deps.add(dep)

        if missing_deps:
            dep_skills = SkillLoader.load_skills_batch(skills_dir, list(missing_deps), use_cache=use_cache)
            loaded_skills.extend(dep_skills)
            loaded_names.extend([s.name for s in dep_skills])

        resolved, issues = SkillLoader.resolve_dependencies(loaded_skills)
        return resolved, issues
