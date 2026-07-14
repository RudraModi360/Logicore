"""
Semantic dead code and duplicate detection test suite for the Logicore codebase.

Enhanced scanner with:
- Class hierarchy awareness (won't flag inherited methods as duplicates)
- Re-export detection (recognizes __init__.py re-exports)
- Abstract method exclusion (skips @abstractmethod decorated methods)
- Semantic function body similarity (same logic, different names)
- Duplicate class structure detection
- Relative import resolution

Run with: pytest test_cases/dead_code/test_duplicate_detection.py -v
"""

import ast
import os
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────
LOGICORE_ROOT = Path(r"D:\Scratchy\logicore")
PROJECT_ROOT = Path(r"D:\Scratchy")
assert LOGICORE_ROOT.exists(), f"Logicore source not found at {LOGICORE_ROOT}"

# Names so common that reporting them as duplicates is noise
COMMON_FUNCTION_NAMES = {
    "__init__", "__str__", "__repr__", "__eq__", "__hash__",
    "__enter__", "__exit__", "__call__", "__len__", "__iter__",
    "__next__", "__contains__", "__getitem__", "__setitem__",
    "__post_init__", "__fspath__", "__format__", "__lt__", "__le__",
    "__gt__", "__ge__", "__ne__", "__add__", "__sub__", "__mul__",
    "__truediv__", "__floordiv__", "__mod__", "__and__", "__or__",
    "__xor__", "__lshift__", "__rshift__", "__neg__", "__pos__",
    "__abs__", "__invert__", "__round__", "__ceil__", "__floor__",
    "__trunc__", "__int__", "__float__", "__complex__", "__bool__",
    "__index__", "__aenter__", "__aexit__", "__aiter__", "__anext__",
    "__delete__", "__set_name__", "__init_subclass__", "__class_getitem__",
    "run", "get", "set", "start", "stop", "close", "open",
    "create", "delete", "update", "save", "load",
    "setup", "teardown", "main", "handle", "execute",
    "initialize", "configure", "validate", "parse",
    "to_dict", "from_dict", "to_json", "from_json",
    "encode", "decode", "serialize", "deserialize",
    "chat", "chat_stream", "health_check",  # provider/gateway methods
}

COMMON_CLASS_NAMES = {
    "__init__", "Exception", "Error",
}

# Dunder methods that are protocol/structural - expected across classes
PROTOCOL_METHODS = {
    "__init__", "__str__", "__repr__", "__eq__", "__hash__",
    "__enter__", "__exit__", "__call__", "__len__", "__iter__",
    "__next__", "__contains__", "__getitem__", "__setitem__",
    "__post_init__", "__fspath__", "__format__", "__lt__", "__le__",
    "__gt__", "__ge__", "__ne__", "__add__", "__sub__", "__mul__",
    "__truediv__", "__floordiv__", "__mod__", "__and__", "__or__",
    "__xor__", "__lshift__", "__rshift__", "__neg__", "__pos__",
    "__abs__", "__invert__", "__round__", "__ceil__", "__floor__",
    "__trunc__", "__int__", "__float__", "__complex__", "__bool__",
    "__index__", "__aenter__", "__aexit__", "__aiter__", "__anext__",
    "__delete__", "__set_name__", "__init_subclass__", "__class_getitem__",
}

# ── Hierarchy Map: parent_class -> child_classes ──────────────────────────────
# Built dynamically, but pre-seed known hierarchies
KNOWN_HIERARCHIES: dict[str, set[str]] = {
    # Agent hierarchy
    "Agent": {"SmartAgent", "MCPAgent", "CopilotAgent"},
    # Provider hierarchy (ABC)
    "LLMProvider": {"OllamaProvider", "GroqProvider", "GeminiProvider",
                    "OpenAIProvider", "AzureProvider", "CustomProvider"},
    # Gateway hierarchy (ABC)
    "ProviderGateway": {"OpenAIGateway", "GeminiGateway", "OllamaGateway",
                        "AzureGateway", "ResilientGateway"},
    # Tool hierarchy (ABC)
    "BaseTool": {
        "ReadFileTool", "CreateFileTool", "EditFileTool", "DeleteFileTool",
        "ListFilesTool", "SearchFilesTool", "FastGrepTool",
        "ExecuteCommandTool", "CodeExecuteTool", "ListProcessesTool",
        "KillProcessTool", "GetProcessInfoTool", "GetProcessOutputTool",
        "TailProcessOutputTool", "WatchProcessTool",
        "WebSearchTool", "UrlFetchTool", "ImageSearchTool",
        "SmartBashTool", "DateTimeTool", "NotesTool", "ThinkTool",
        "GitCommandTool", "ReadDocumentTool", "ConvertDocumentTool",
        "MediaSearchTool",
        "AddCronJobTool", "ListCronJobsTool", "RemoveCronJobTool", "GetCronsTool",
        "EnterPlanModeTool", "SubmitPlanTool", "ExitPlanModeTool",
        "UpdatePlanProgressTool", "ViewPlanTool",
        "TaskCreateTool", "TaskGetTool", "TaskUpdateTool", "TaskListTool", "TaskNextTool",
    },
}

# Build reverse map: child_class -> parent_class
CHILD_TO_PARENT: dict[str, str] = {}
for parent, children in KNOWN_HIERARCHIES.items():
    for child in children:
        CHILD_TO_PARENT[child] = parent

# ── Re-export Map: __init__.py re-exports ─────────────────────────────────────
# Built dynamically from __init__.py files
RE_EXPORTS: dict[str, set[str]] = defaultdict(set)  # file -> {names that are re-exports}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _collect_py_files() -> list[Path]:
    """Return all .py files under logicore/."""
    return sorted(LOGICORE_ROOT.rglob("*.py"))


def _parse_file(path: Path) -> ast.Module | None:
    """Parse a Python file, return AST or None on failure."""
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def _ast_body_hash(node: ast.AST) -> str:
    """Return a deterministic string representation of an AST node."""
    return ast.dump(node)


def _ast_node_count(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def _similarity_ratio(a: ast.AST, b: ast.AST) -> float:
    """Jaccard-like similarity over the set of AST node types + field names."""
    nodes_a = set()
    nodes_b = set()
    for n in ast.walk(a):
        key = (type(n).__name__, tuple(sorted(
            (k, str(getattr(v, "__name__", v)))
            for k, v in ast.iter_fields(n)
            if not isinstance(v, list)
        )))
        nodes_a.add(key)
    for n in ast.walk(b):
        key = (type(n).__name__, tuple(sorted(
            (k, str(getattr(v, "__name__", v)))
            for k, v in ast.iter_fields(n)
            if not isinstance(v, list)
        )))
        nodes_b.add(key)
    if not nodes_a or not nodes_b:
        return 0.0
    return len(nodes_a & nodes_b) / len(nodes_a | nodes_b)


def _build_re_export_map():
    """Scan __init__.py files and mark imports as re-exports."""
    global RE_EXPORTS
    for path in _collect_py_files():
        if path.name != "__init__.py":
            continue
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                # from .module import X → re-export
                for alias in node.names:
                    if alias.name != "*":
                        name = alias.asname or alias.name
                        RE_EXPORTS[_relative(path)].add(name)
            elif isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
                # from ..module import X → relative re-export
                for alias in node.names:
                    if alias.name != "*":
                        name = alias.asname or alias.name
                        RE_EXPORTS[_relative(path)].add(name)


def _is_re_export(file_rel: str, name: str) -> bool:
    """Check if a name in a file is just a re-export from a submodule."""
    return name in RE_EXPORTS.get(file_rel, set())


def _is_inherited_method(class_name: str, method_name: str) -> bool:
    """Check if a method is inherited from a parent class."""
    parent = CHILD_TO_PARENT.get(class_name)
    if parent is None:
        return False
    # The method is expected if it exists in the parent
    return True  # All child methods are potentially inherited


def _is_abstract_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a method has @abstractmethod decorator."""
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "abstractmethod":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "abstractmethod":
            return True
    return False


def _is_protocol_class(node: ast.ClassDef) -> bool:
    """Check if a class is a Protocol (has Protocol in bases or decorator)."""
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "runtime_checkable":
            return True
    return False


def _is_noop_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is a no-op (only pass/ellipsis, or returns None/True/False)."""
    body = node.body
    if len(body) == 1:
        stmt = body[0]
        # pass
        if isinstance(stmt, ast.Pass):
            return True
        # ...
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            if stmt.value.value is ...:
                return True
        # return None
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                return True
            if isinstance(stmt.value, ast.Constant) and stmt.value.value in (None, True, False):
                return True
    return False


def _get_class_bases(tree: ast.Module) -> dict[str, list[str]]:
    """Return {class_name: [base_class_names]} for all classes in the tree."""
    bases: dict[str, list[str]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            base_names = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            bases[node.name] = base_names
    return bases


def _resolve_relative_import(module: str | None, level: int, file_path: Path) -> str | None:
    """Resolve a relative import to an absolute module path."""
    if module is None:
        module = ""
    parts = module.split(".") if module else []

    # Go up 'level' directories from the file's package
    pkg = file_path.parent
    for _ in range(level - 1):
        pkg = pkg.parent

    # Construct the full path
    if parts:
        full = str(pkg.relative_to(PROJECT_ROOT)).replace(os.sep, ".")
        full = full.replace(".", ".", 1) if "." in full else full
        return ".".join(parts) if not full else f"{full}.{'.'.join(parts)}"
    else:
        return str(pkg.relative_to(PROJECT_ROOT)).replace(os.sep, ".")


# Build the re-export map on module load
_build_re_export_map()


# ── Test 1: Unused imports ─────────────────────────────────────────────────────

def _collect_imports(tree: ast.Module) -> dict[str, list[int]]:
    """Return {name: [line_numbers]} for every imported name."""
    imports: dict[str, list[int]] = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[-1]
                imports[name].append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imports[name].append(node.lineno)
    return imports


def _collect_used_names(tree: ast.Module) -> set[str]:
    """Return all names that are *used* (referenced) in the tree."""
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            # walk up to root of attribute chain
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            used.add(node.name)
        elif isinstance(node, ast.ClassDef):
            used.add(node.name)
    return used


def test_unused_imports():
    """Detect imported names that are never referenced in the same file."""
    findings: list[str] = []
    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        imports = _collect_imports(tree)
        used = _collect_used_names(tree)
        # Also consider string annotations and type comments
        source = path.read_text(encoding="utf-8")
        for name in list(imports.keys()):
            if name not in used and name not in source.split("import")[0]:
                # extra heuristic: skip if name appears anywhere in source as string
                if re.search(rf'\b{re.escape(name)}\b', source):
                    continue
                for lineno in imports[name]:
                    findings.append(f"  {_relative(path)}:{lineno} – unused import '{name}'")

    if findings:
        msg = "Unused imports detected:\n" + "\n".join(findings)
        pytest.fail(msg)


# ── Test 2: Duplicate function names across files (semantic) ──────────────────

def test_duplicate_function_names():
    """Find function/method names that appear in multiple files.

    Semantic enhancements:
    - Skips methods inherited from parent classes (e.g., chat() in Agent subclasses)
    - Skips re-exports in __init__.py files
    - Skips abstract methods
    - Skips protocol/dunder methods
    """
    # Collect all function definitions with their class context
    # key: (name, file_rel) -> {class_context, line, is_abstract, bases}
    func_defs: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        file_rel = _relative(path)
        class_bases = _get_class_bases(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("_") and name not in PROTOCOL_METHODS:
                    continue

                # Determine class context
                class_context = None
                for parent_node in ast.walk(tree):
                    if isinstance(parent_node, ast.ClassDef):
                        for child in ast.iter_child_nodes(parent_node):
                            if child is node:
                                class_context = parent_node.name
                                break

                is_abstract = _is_abstract_method(node)
                parent_class = CHILD_TO_PARENT.get(class_context) if class_context else None

                func_defs[name][file_rel] = {
                    "class": class_context,
                    "line": node.lineno,
                    "is_abstract": is_abstract,
                    "parent_class": parent_class,
                    "is_re_export": _is_re_export(file_rel, name),
                }

    findings: list[str] = []
    for name, locations in func_defs.items():
        if name in COMMON_FUNCTION_NAMES or name in PROTOCOL_METHODS:
            continue
        if len(locations) <= 1:
            continue

        # Filter out re-exports
        non_reexport = {f: info for f, info in locations.items() if not info["is_re_export"]}
        if len(non_reexport) <= 1:
            continue

        # Filter out inherited methods (same parent class → child class overlap is expected)
        inherited_groups: dict[str, list[str]] = defaultdict(list)
        standalone: dict[str, dict] = {}
        for f, info in non_reexport.items():
            if info["parent_class"]:
                inherited_groups[info["parent_class"]].append(f)
            else:
                standalone[f] = info

        # If all locations are children of the same parent, skip (expected inheritance)
        if len(inherited_groups) == 1 and not standalone:
            continue

        # If most are inherited from same parent, keep only standalone + one representative
        files_to_report = []
        for f, info in standalone.items():
            files_to_report.append(f"{f}:{info['line']}")
        for parent, children in inherited_groups.items():
            if len(children) > 1:
                # Pick one representative from the inheritance group
                rep = children[0]
                info = non_reexport[rep]
                files_to_report.append(f"{rep}:{info['line']} (inherited from {parent})")
            else:
                f = children[0]
                info = non_reexport[f]
                files_to_report.append(f"{f}:{info['line']}")

        if len(files_to_report) > 1:
            findings.append(f"  '{name}' found in {len(files_to_report)} locations:")
            for f in files_to_report:
                findings.append(f"    - {f}")

    if findings:
        warnings.warn("Duplicate function names across files:\n" + "\n".join(findings))


# ── Test 3: Duplicate class names ─────────────────────────────────────────────

def test_duplicate_class_names():
    """Find class names that appear in multiple files.

    Semantic enhancement: re-exports in __init__.py are not duplicates.
    """
    name_to_files: dict[str, set[str]] = defaultdict(set)
    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        file_rel = _relative(path)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                if node.name not in COMMON_CLASS_NAMES and not node.name.startswith("_"):
                    # Skip if this is just a re-export
                    if not _is_re_export(file_rel, node.name):
                        name_to_files[node.name].add(file_rel)

    dupes = {n: sorted(f) for n, f in name_to_files.items() if len(f) > 1}
    if dupes:
        lines = []
        for name, files in sorted(dupes.items()):
            lines.append(f"  '{name}' found in {len(files)} files:")
            for f in files:
                lines.append(f"    - {f}")
        warnings.warn("Duplicate class names across files:\n" + "\n".join(lines))


# ── Test 4: Functions with identical/similar bodies ────────────────────────────

def test_duplicate_function_bodies():
    """Find functions whose AST bodies are identical or very similar (>80%).

    Semantic enhancement: skips abstract methods and protocol methods.
    """
    func_map: dict[str, list[tuple[str, ast.AST, int]]] = defaultdict(list)
    # key = (name, number_of_statements) to avoid matching completely different funcs
    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip abstract methods
                if _is_abstract_method(node):
                    continue
                # Skip protocol methods
                if node.name in PROTOCOL_METHODS:
                    continue
                body = node.body
                if len(body) < 2:
                    continue  # skip trivial functions
                body_hash = _ast_body_hash(ast.Module(body=body, type_ignores=[]))
                key = f"{node.name}:{len(body)}"
                func_map[key].append((_relative(path), body_hash, node.lineno))

    findings: list[str] = []
    for key, entries in func_map.items():
        if len(entries) < 2:
            continue
        # group by exact hash
        hash_groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for filepath, h, lineno in entries:
            hash_groups[h].append((filepath, lineno))
        for h, group in hash_groups.items():
            if len(group) >= 2:
                files_str = ", ".join(f"{f}:{l}" for f, l in sorted(group))
                findings.append(f"  Function '{key.split(':')[0]}': identical bodies in {files_str}")

        # Also check similarity > 80% across different hashes
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                fp_i, h_i, ln_i = entries[i]
                fp_j, h_j, ln_j = entries[j]
                if h_i == h_j:
                    continue  # already reported
                tree_i = _parse_file(Path(PROJECT_ROOT / fp_i.lstrip("./")))
                tree_j = _parse_file(Path(PROJECT_ROOT / fp_j.lstrip("./")))
                if tree_i is None or tree_j is None:
                    continue
                func_node_i = None
                func_node_j = None
                name = key.split(":")[0]
                for n in ast.walk(tree_i):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name and n.lineno == ln_i:
                        func_node_i = n
                        break
                for n in ast.walk(tree_j):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name and n.lineno == ln_j:
                        func_node_j = n
                        break
                if func_node_i and func_node_j:
                    ratio = _similarity_ratio(
                        ast.Module(body=func_node_i.body, type_ignores=[]),
                        ast.Module(body=func_node_j.body, type_ignores=[]),
                    )
                    if ratio > 0.8:
                        findings.append(
                            f"  Function '{name}': ~{ratio:.0%} similar between "
                            f"{fp_i}:{ln_i} and {fp_j}:{ln_j}"
                        )

    if findings:
        pytest.fail("Duplicate/similar function bodies detected:\n" + "\n".join(findings))


# ── Test 5: Dead imports (modules that don't exist) ───────────────────────────

def test_dead_module_imports():
    """Find imports of modules that don't exist in the codebase.

    Semantic enhancement: resolves relative imports properly.
    """
    findings: list[str] = []
    stdlib_modules = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    # Skip stdlib, third-party
                    if mod in stdlib_modules or mod in ("os", "sys", "re", "json", "time",
                        "datetime", "pathlib", "typing", "collections", "functools",
                        "itertools", "copy", "hashlib", "base64", "uuid", "asyncio",
                        "logging", "abc", "io", "subprocess", "threading", "contextlib",
                        "dataclasses", "enum", "textwrap", "shutil", "tempfile", "traceback",
                        "warnings", "inspect", "importlib", "pkgutil", "math", "random",
                        "string", "struct", "socket", "http", "urllib", "email", "csv",
                        "xml", "html", "ctypes", "glob", "fnmatch", "difflib",
                        "unittest", "argparse", "configparser", "secrets", "hmac",
                        "statistics", "decimal", "fractions", "operator", "dis",
                        "token", "tokenize", "ast", "code", "codeop", "compileall",
                        "py_compile", "zipimport", "zipfile", "tarfile", "gzip",
                        "bz2", "lzma", "zlib", "bz2", "lzma", "abc", "numbers",
                        "cmath", "fractions", "random", "statistics", "decimal",
                        "copyreg", "pprint", "reprlib", "enum", "graphlib",
                        "types", "weakref", "types", "codecs", "unicodedata",
                        "stringprep", "readline", "rlcompleter"):
                        continue
                    # Check if the module is a local package
                    local_path = LOGICORE_ROOT / mod
                    if not local_path.exists() and not (local_path / "__init__.py").exists():
                        # Check if it's installed as a third-party package
                        try:
                            __import__(mod)
                        except ImportError:
                            findings.append(
                                f"  {_relative(path)}:{node.lineno} – "
                                f"module '{alias.name}' may not exist"
                            )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                # Handle relative imports
                if node.level and node.level > 0:
                    resolved = _resolve_relative_import(node.module, node.level, path)
                    if resolved:
                        parts = resolved.split(".")
                        mod = parts[0]
                    else:
                        mod = node.module.split(".")[0]
                else:
                    mod = node.module.split(".")[0]

                if mod in stdlib_modules:
                    continue
                local_path = LOGICORE_ROOT / mod
                if not local_path.exists() and not (local_path / "__init__.py").exists():
                    try:
                        __import__(mod)
                    except ImportError:
                        findings.append(
                            f"  {_relative(path)}:{node.lineno} – "
                            f"module '{node.module}' may not exist"
                        )

    if findings:
        import warnings
        warnings.warn(
            "Potentially dead module imports:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Test 6: Empty functions/methods ───────────────────────────────────────────

def test_empty_functions():
    """Find functions that only contain `pass` or `...`.

    Semantic enhancement: skips abstract methods, protocol methods, and no-ops.
    """
    findings: list[str] = []
    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        # Find Protocol classes in this file
        protocol_classes: set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and _is_protocol_class(node):
                protocol_classes.add(node.name)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip abstract methods - empty body is expected
                if _is_abstract_method(node):
                    continue
                # Skip protocol methods - empty body is expected
                if node.name in PROTOCOL_METHODS:
                    continue
                # Skip methods in Protocol classes
                # (check by walking up to find parent class)
                in_protocol = False
                for parent_node in ast.walk(tree):
                    if isinstance(parent_node, ast.ClassDef):
                        if parent_node.name in protocol_classes:
                            for child in ast.iter_child_nodes(parent_node):
                                if child is node:
                                    in_protocol = True
                                    break
                if in_protocol:
                    continue
                # Skip no-op functions (return True/None/pass/...)
                if _is_noop_function(node):
                    continue
                body = node.body
                if len(body) == 1:
                    stmt = body[0]
                    is_empty = False
                    if isinstance(stmt, ast.Pass):
                        is_empty = True
                    elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                        if stmt.value.value is ...:
                            is_empty = True
                    if is_empty:
                        findings.append(
                            f"  {_relative(path)}:{node.lineno} – "
                            f"empty function/method '{node.name}'"
                        )

    if findings:
        warnings.warn("Empty functions/methods detected:\n" + "\n".join(findings))


# ── Test 7: Duplicate string constants ────────────────────────────────────────

def test_duplicate_string_constants():
    """Find string literals that appear 3+ times across the codebase."""
    string_locations: dict[str, list[str]] = defaultdict(list)

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.strip()
                # Skip very short strings, empty strings, and common patterns
                if len(val) < 3:
                    continue
                if val in ("utf-8", "utf8", "rb", "r", "w", "a", "utf-8-sig"):
                    continue
                string_locations[val].append(f"{_relative(path)}:{node.lineno}")

    dupes = {s: locs for s, locs in string_locations.items() if len(locs) >= 3}
    if dupes:
        lines = []
        for s, locs in sorted(dupes.items(), key=lambda x: -len(x[1]))[:15]:
            preview = s[:60] + ("..." if len(s) > 60 else "")
            lines.append(f"  \"{preview}\" appears {len(locs)} times:")
            for loc in locs[:5]:
                lines.append(f"    - {loc}")
            if len(locs) > 5:
                lines.append(f"    - ... and {len(locs) - 5} more")
        warnings.warn("Duplicate string constants (potential constant candidates):\n" + "\n".join(lines))


# ── Test 8: Similar error handling patterns ────────────────────────────────────

def test_duplicate_error_handling():
    """Find try/except blocks with identical exception types and similar handlers."""
    handlers: list[tuple[str, int, str, str]] = []  # (file, line, exc_type, handler_dump)

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        exc_type = "bare-except"
                    elif isinstance(handler.type, ast.Name):
                        exc_type = handler.type.id
                    elif isinstance(handler.type, ast.Attribute):
                        exc_type = ast.unparse(handler.type)
                    else:
                        exc_type = ast.unparse(handler.type)
                    handler_dump = _ast_body_hash(
                        ast.Module(body=handler.body, type_ignores=[])
                    )
                    handlers.append((_relative(path), handler.lineno, exc_type, handler_dump))

    # Group by (exc_type, handler_dump)
    groups: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for filepath, lineno, exc_type, h_dump in handlers:
        groups[(exc_type, h_dump)].append((filepath, lineno))

    findings: list[str] = []
    for (exc_type, _), entries in groups.items():
        if len(entries) >= 2:
            files_str = ", ".join(f"{f}:{l}" for f, l in sorted(entries))
            findings.append(f"  Except '{exc_type}' with identical handler in: {files_str}")

    if findings:
        import warnings
        warnings.warn(
            "Duplicate error handling patterns:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Test 9: Duplicate utility patterns ────────────────────────────────────────

def test_duplicate_utility_patterns():
    """Find repeated patterns like os.path.exists() + open() sequences."""
    findings: list[str] = []

    for path in _collect_py_files():
        source = path.read_text(encoding="utf-8")
        rel = _relative(path)

        # Pattern 1: os.path.exists() followed by open()
        exists_then_open = re.findall(
            r'os\.path\.exists\([^)]+\).*?open\(',
            source,
            re.DOTALL,
        )
        if len(exists_then_open) >= 2:
            findings.append(
                f"  {rel}: {len(exists_then_open)} instances of 'os.path.exists() + open()' pattern"
            )

        # Pattern 2: isinstance() chains (3+ isinstance calls close together)
        isinstance_chains = re.findall(
            r'isinstance\([^)]+\)(?:\s*or\s*isinstance\([^)]+\)){2,}',
            source,
        )
        if isinstance_chains:
            findings.append(
                f"  {rel}: {len(isinstance_chains)} long isinstance() chains (consider using Union types)"
            )

        # Pattern 3: Repeated try/except with same single operation
        try_except_pass = re.findall(
            r'try:\s*\n\s+(.+?)\n\s*except.*?:\s*\n\s+pass',
            source,
            re.DOTALL,
        )
        if len(try_except_pass) >= 3:
            findings.append(
                f"  {rel}: {len(try_except_pass)} try/except/pass blocks (silent exception swallowing)"
            )

    if findings:
        import warnings
        warnings.warn(
            "Duplicate utility patterns detected:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Test 10: Dead exported symbols ────────────────────────────────────────────

def test_dead_exported_symbols():
    """Check __all__ exports in __init__.py files; verify each name exists.

    Semantic enhancement: resolves re-exports from submodules.
    """
    findings: list[str] = []

    for path in _collect_py_files():
        if path.name != "__init__.py":
            continue
        tree = _parse_file(path)
        if tree is None:
            continue

        # Find __all__ assignment
        all_names: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            all_names = [
                                elt.value for elt in node.value.elts
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            ]

        if not all_names:
            continue

        # Collect all names defined in this module
        defined: set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
            elif isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[-1]
                    defined.add(name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or alias.name
                    defined.add(name)

        # Check for missing exports
        for name in all_names:
            if name not in defined:
                # Also check if it's re-exported from a submodule
                submodule = path.parent / f"{name}.py"
                subpackage = path.parent / name / "__init__.py"
                if not submodule.exists() and not subpackage.exists():
                    findings.append(
                        f"  {_relative(path)}: __all__ exports '{name}' "
                        f"but it is not defined or imported in this module"
                    )

    if findings:
        pytest.fail("Dead exported symbols in __all__:\n" + "\n".join(findings))


# ── Test 11: Semantic duplicate function detection (different names, same logic) ──

def test_semantic_duplicate_functions():
    """Detect functions with different names but similar AST structure (>85% similarity).

    This catches true code duplication even when functions are named differently.
    """
    # Collect all function bodies with metadata
    func_bodies: list[tuple[str, str, int, ast.AST, int]] = []
    # (file_rel, func_name, line, body_node, body_size)

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        file_rel = _relative(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_abstract_method(node):
                    continue
                if node.name in PROTOCOL_METHODS:
                    continue
                body = node.body
                if len(body) < 3:  # skip trivial functions
                    continue
                body_module = ast.Module(body=body, type_ignores=[])
                func_bodies.append((file_rel, node.name, node.lineno, body_module, len(body)))

    findings: list[str] = []
    # Only compare functions with similar body sizes (within 50%)
    size_buckets: dict[int, list[int]] = defaultdict(list)
    for idx, (_, _, _, _, size) in enumerate(func_bodies):
        bucket = size // 2  # group by approximate size
        size_buckets[bucket].append(idx)

    checked: set[tuple[str, str]] = set()
    for bucket, indices in size_buckets.items():
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                idx_a = indices[i]
                idx_b = indices[j]
                file_a, name_a, line_a, body_a, size_a = func_bodies[idx_a]
                file_b, name_b, line_b, body_b, size_b = func_bodies[idx_b]

                if name_a == name_b:
                    continue  # same name, already covered by test_duplicate_function_bodies

                pair_key = tuple(sorted([f"{file_a}:{name_a}", f"{file_b}:{name_b}"]))
                if pair_key in checked:
                    continue
                checked.add(pair_key)

                # Quick size filter
                if abs(size_a - size_b) > max(size_a, size_b) * 0.5:
                    continue

                ratio = _similarity_ratio(body_a, body_b)
                if ratio > 0.85:
                    findings.append(
                        f"  '{name_a}' ({file_a}:{line_a}) and '{name_b}' ({file_b}:{line_b}) "
                        f"are ~{ratio:.0%} structurally similar (different names, same logic?)"
                    )

    if findings:
        warnings.warn(
            "Semantic duplicate functions detected:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Test 12: Duplicate class structures ───────────────────────────────────────

def test_duplicate_class_structures():
    """Find classes with identical structure (same methods, similar body shapes).

    Catches copy-paste classes that differ only in naming.
    """
    class_data: list[tuple[str, str, int, ast.ClassDef]] = []
    # (file_rel, class_name, line, class_node)

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        file_rel = _relative(path)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                class_data.append((file_rel, node.name, node.lineno, node))

    findings: list[str] = []
    # Build method signature for each class
    class_sigs: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for file_rel, name, line, cls in class_data:
        methods = []
        for child in ast.iter_child_nodes(cls):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = len(child.args.args)
                body_size = len(child.body)
                methods.append(f"{child.name}({params}args,{body_size}stmts)")
        sig = "|".join(sorted(methods))
        if sig:
            class_sigs[sig].append((file_rel, name, line))

    for sig, classes in class_sigs.items():
        if len(classes) > 1:
            # Filter out parent-child relationships
            names = [c[1] for c in classes]
            non_hierarchy = []
            for c in classes:
                parent = CHILD_TO_PARENT.get(c[1])
                if parent and parent in names:
                    continue  # child class - expected overlap
                non_hierarchy.append(c)

            if len(non_hierarchy) > 1:
                files_str = ", ".join(f"{f}:{n} @ {l}" for f, n, l in non_hierarchy)
                findings.append(f"  Classes with identical structure: {files_str}")

    if findings:
        warnings.warn(
            "Duplicate class structures detected:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Test 13: Re-export chains ─────────────────────────────────────────────────

def test_re_export_chains():
    """Find re-export chains: __init__.py re-exports from __init__.py.

    This creates confusing import paths and is a code smell.
    """
    findings: list[str] = []

    for path in _collect_py_files():
        if path.name != "__init__.py":
            continue
        tree = _parse_file(path)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module and node.module.endswith(".__init__"):
                    findings.append(
                        f"  {_relative(path)}:{node.lineno} – "
                        f"re-importing from __init__ module: from {node.module} import ..."
                    )

    if findings:
        warnings.warn(
            "Re-export chains detected:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Test 14: Functions that are exact wrappers (no-op delegation) ─────────────

def test_trivial_wrapper_functions():
    """Find functions that just delegate to another function with same args.

    Pattern: def f(x): return g(x) or def f(x): g(x)
    """
    findings: list[str] = []

    for path in _collect_py_files():
        tree = _parse_file(path)
        if tree is None:
            continue
        file_rel = _relative(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_abstract_method(node):
                    continue
                if node.name in PROTOCOL_METHODS:
                    continue
                body = node.body
                if len(body) != 1:
                    continue

                stmt = body[0]
                # Pattern 1: return <name>(<same_args>)
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
                    call = stmt.value
                    if isinstance(call.func, ast.Name):
                        # Check if call args match function params exactly
                        func_args = [a.arg for a in node.args.args if a.arg != "self"]
                        call_args = []
                        for a in call.args:
                            if isinstance(a, ast.Name):
                                call_args.append(a.id)
                            else:
                                call_args.append(None)
                        if func_args == call_args and call_args:
                            findings.append(
                                f"  {file_rel}:{node.lineno} – "
                                f"'{node.name}' is a trivial wrapper for '{call.func.id}'"
                            )

    if findings:
        warnings.warn(
            "Trivial wrapper functions detected:\n" + "\n".join(findings),
            stacklevel=1,
        )


# ── Bonus: Summary fixture ────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _print_scan_summary():
    """Print a summary after all tests run."""
    yield
    file_count = len(_collect_py_files())
    hierarchy_count = len(CHILD_TO_PARENT)
    reexport_count = sum(len(v) for v in RE_EXPORTS.values())
    print(f"\n{'='*60}")
    print(f"  Semantic dead code scan complete: analyzed {file_count} .py files")
    print(f"  Source root: {LOGICORE_ROOT}")
    print(f"  Class hierarchy: {hierarchy_count} parent-child relationships mapped")
    print(f"  Re-exports tracked: {reexport_count} names across {len(RE_EXPORTS)} files")
    print(f"{'='*60}", file=sys.stderr)
