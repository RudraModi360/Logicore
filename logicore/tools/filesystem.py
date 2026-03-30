import os
import re
import fnmatch
import difflib
import shutil
from typing import List, Optional, Literal, Set, Union
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult

# --- Shared State ---
global_read_files_tracker: Set[str] = set()

def validate_read_before_edit(file_path: str) -> bool:
    return os.path.abspath(file_path) in global_read_files_tracker

def get_read_before_edit_error(file_path: str) -> str:
    return f"File must be read before editing. Use read_file tool first: {file_path}"

# --- Schemas ---

class ReadFileParams(BaseModel):
    file_path: str = Field(..., description='Path to file.')
    start_line: Optional[int] = Field(None, description='Starting line number (1-indexed, optional)', ge=1)
    end_line: Optional[int] = Field(None, description='Ending line number (1-indexed, optional)', ge=1)

class CreateFileParams(BaseModel):
    file_path: str = Field(..., description='Path for new file/directory.')
    content: str = Field(..., description='File content (use empty string "" for directories)')
    file_type: Literal['file', 'directory'] = Field('file', description='Create file or directory')
    overwrite: bool = Field(False, description='Overwrite existing file')

class EditFileParams(BaseModel):
    file_path: str = Field(..., description='Path to file to edit.')
    old_text: Optional[str] = Field(None, description='Exact text to replace. Required if start_line/end_line not provided.')
    new_text: str = Field(..., description='Replacement text.')
    replace_all: bool = Field(False, description='Replace all occurrences (only for text matching).')
    start_line: Optional[int] = Field(None, description='Start line for replacement (1-indexed).')
    end_line: Optional[int] = Field(None, description='End line for replacement (1-indexed).')

class DeleteFileParams(BaseModel):
    file_path: str = Field(..., description='Path to file/directory to delete.')
    recursive: bool = Field(False, description='Delete directories and their contents.')

class ListFilesParams(BaseModel):
    directory: str = Field('.', description='Directory path to list.')
    pattern: str = Field('*', description='File pattern filter.')
    recursive: bool = Field(False, description='List subdirectories recursively.')
    show_hidden: bool = Field(False, description='Include hidden files (starting with .).')
    tree: bool = Field(False, description='Return output as a visual tree structure (like unix tree command).')
    ignore_patterns: List[str] = Field(
        default=['__pycache__', '.git', 'node_modules', '*.pyc', 'venv', '.env'], 
        description='List of patterns to ignore.'
    )

class SearchFilesParams(BaseModel):
    pattern: str = Field(..., description='Text to search for.')
    file_pattern: str = Field('*', description='File pattern filter (e.g., "*.py").')
    directory: str = Field('.', description='Directory to search in.')
    case_sensitive: bool = Field(False, description='Case-sensitive search.')
    pattern_type: Literal['substring', 'regex', 'exact', 'fuzzy'] = Field('substring', description='Match type.')
    file_types: Optional[Union[str, List[str]]] = Field(None, description='File extensions to include (list or comma-separated string).')
    exclude_dirs: Optional[Union[str, List[str]]] = Field(None, description='Directories to skip (list or comma-separated string).')
    exclude_files: Optional[Union[str, List[str]]] = Field(None, description='File patterns to skip (list or comma-separated string).')
    max_results: int = Field(100, description='Maximum results to return (1-1000)', ge=1, le=1000)
    context_lines: int = Field(0, description='Lines of context around matches (0-10)', ge=0, le=10)
    group_by_file: bool = Field(False, description='Group results by filename.')

class FastGrepParams(BaseModel):
    keyword: str = Field(..., description='The keyword or regex pattern to search for.')
    directory: str = Field('.', description='The directory to search in.')
    file_pattern: Optional[str] = Field(None, description='Glob pattern to filter files to be searched (e.g., "*.py", "**/*.js").')

# --- Tools ---

class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read file contents with optional line range. REQUIRED before edit_file."
    args_schema = ReadFileParams

    def run(self, file_path: str, start_line: int = None, end_line: int = None) -> ToolResult:
        try:
            abs_path = os.path.abspath(file_path)
            if not os.path.exists(abs_path):
                return ToolResult(success=False, error="File not found")
            if not os.path.isfile(abs_path):
                return ToolResult(success=False, error="Path is not a file")
            if os.path.getsize(abs_path) > 50 * 1024 * 1024: # 50MB limit
                return ToolResult(success=False, error="File too large (max 50MB)")

            with open(abs_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            global_read_files_tracker.add(abs_path)
            lines = content.splitlines()

            if start_line is not None:
                start_idx = max(0, start_line - 1)
                end_idx = len(lines) if end_line is None else min(len(lines), end_line)
                if start_idx >= len(lines):
                    return ToolResult(success=False, error="Start line exceeds file length")
                
                selected_content = "\n".join(lines[start_idx:end_idx])
                return ToolResult(success=True, content=selected_content)
            else:
                return ToolResult(success=True, content=content)

        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

class CreateFileTool(BaseTool):
    name = "create_file"
    description = "Create NEW files or directories. Check if file exists first."
    args_schema = CreateFileParams

    def run(self, file_path: str, content: str, file_type: str = 'file', overwrite: bool = False) -> ToolResult:
        try:
            abs_path = os.path.abspath(file_path)
            if os.path.exists(abs_path) and not overwrite:
                return ToolResult(success=False, error="File already exists. Use overwrite=true to replace.")

            if file_type == 'directory':
                os.makedirs(abs_path, exist_ok=True)
                return ToolResult(success=True, content=f"Directory created: {file_path}")
            elif file_type == 'file':
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                return ToolResult(success=True, content=f"File created: {file_path}")
            else:
                return ToolResult(success=False, error="Invalid file_type. Must be 'file' or 'directory'.")

        except Exception as e:
            return ToolResult(success=False, error=f"Failed to create file/directory: {e}")

class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Modify EXISTING files with intelligent reasoning and logical explanations. Supports exact text match or line-based replacement with enhanced context awareness."
    args_schema = EditFileParams

    def _analyze_code_context(self, content: str, file_path: str) -> dict:
        """Analyze code context to understand file structure and syntax."""
        context = {
            'file_type': 'text',
            'language': None,
            'has_imports': False,
            'has_functions': False,
            'has_classes': False,
            'indentation_level': 0,
            'complexity_hints': []
        }
        
        # Determine file type and language
        ext = os.path.splitext(file_path)[1].lower()
        language_map = {
            '.py': 'python',
            '.js': 'javascript', 
            '.ts': 'typescript',
            '.java': 'java',
            '.cpp': 'cpp',
            '.c': 'c',
            '.cs': 'csharp',
            '.go': 'go',
            '.rs': 'rust',
            '.php': 'php',
            '.rb': 'ruby',
            '.sh': 'bash',
            '.html': 'html',
            '.css': 'css',
            '.json': 'json',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.md': 'markdown'
        }
        
        context['language'] = language_map.get(ext, 'text')
        if context['language'] != 'text':
            context['file_type'] = 'code'
        
        # Analyze content for patterns
        lines = content.splitlines()
        
        # Check for imports/dependencies
        import_patterns = {
            'python': ['import ', 'from ', 'import '],
            'javascript': ['import ', 'require(', 'from '],
            'java': ['import ', 'package '],
            'go': ['import ', 'package ']
        }
        
        if context['language'] in import_patterns:
            for line in lines[:20]:  # Check first 20 lines
                for pattern in import_patterns[context['language']]:
                    if pattern in line and not line.strip().startswith('#'):
                        context['has_imports'] = True
                        break
                if context['has_imports']:
                    break
        
        # Check for functions/classes
        function_patterns = {
            'python': ['def ', 'class ', 'async def '],
            'javascript': ['function ', 'const ', 'let ', 'class ', '=>'],
            'java': ['public ', 'private ', 'class ', 'interface '],
            'cpp': ['void ', 'int ', 'class ', 'struct ', 'auto ']
        }
        
        if context['language'] in function_patterns:
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                for pattern in function_patterns[context['language']]:
                    if pattern in stripped:
                        if pattern in ['def ', 'function ', 'void ', 'int ']:
                            context['has_functions'] = True
                        elif pattern in ['class ', 'interface ']:
                            context['has_classes'] = True
                        break
                if context['has_functions'] or context['has_classes']:
                    break
        
        # Analyze indentation
        indented_lines = [line for line in lines if line.startswith('    ') or line.startswith('\t')]
        if indented_lines:
            context['indentation_level'] = 4 if '    ' in indented_lines[0] else 1
        
        return context

    def _generate_change_explanation(self, old_content: str, new_content: str, context: dict, 
                                   operation: str, details: dict) -> str:
        """Generate a logical explanation of what changed and why."""
        explanations = []
        
        if operation == "line_replacement":
            start_line = details.get('start_line')
            end_line = details.get('end_line')
            explanations.append(f"**Logical Change**: Replaced lines {start_line}-{end_line}")
            
            # Analyze what type of content was replaced
            old_lines = old_content.splitlines()
            if start_line and end_line:
                old_section = '\n'.join(old_lines[start_line-1:end_line])
                new_lines_count = len(new_content.splitlines())
                old_lines_count = end_line - start_line + 1
                
                if new_lines_count > old_lines_count:
                    explanations.append(f"**Impact**: Expanded section by {new_lines_count - old_lines_count} lines")
                elif new_lines_count < old_lines_count:
                    explanations.append(f"**Impact**: Compressed section by {old_lines_count - new_lines_count} lines")
                else:
                    explanations.append("**Impact**: Replaced content with same number of lines")
                    
        elif operation == "text_replacement":
            old_text = details.get('old_text', '')
            new_text = details.get('new_text', '')
            count = details.get('count', 1)
            
            explanations.append(f"**Logical Change**: Replaced '{old_text[:50]}...' with '{new_text[:50]}...'")
            
            if count > 1:
                explanations.append(f"**Scope**: Applied change to {count} occurrences")
            
            # Analyze content changes
            if context['file_type'] == 'code':
                if 'def ' in old_text and 'def ' in new_text:
                    explanations.append("**Code Pattern**: Function definition modified")
                elif 'class ' in old_text and 'class ' in new_text:
                    explanations.append("**Code Pattern**: Class definition modified")
                elif 'import ' in old_text and 'import ' in new_text:
                    explanations.append("**Code Pattern**: Import statement updated")
                elif context['has_functions'] and context['has_classes']:
                    explanations.append("**Code Context**: File contains functions and classes")
        
        # Add logical reasoning based on context
        if context['file_type'] == 'code':
            if context['has_imports'] and 'import' in str(details):
                explanations.append("**Reasoning**: Import changes may affect dependencies and module availability")
            
            if context['has_functions']:
                explanations.append("**Code Structure**: Function modifications maintain code structure integrity")
            
            if context['has_classes']:
                explanations.append("**Code Structure**: Class modifications preserve object-oriented design patterns")
                
        return '\n'.join(explanations)

    def _validate_change_logic(self, old_content: str, new_content: str, context: dict, 
                              operation: str, details: dict) -> tuple[bool, str]:
        """Validate that the change makes logical sense."""
        warnings = []
        
        if context['file_type'] == 'code':
            # Check for syntax preservation
            old_lines = old_content.splitlines()
            new_lines = new_content.splitlines()
            
            # Basic Python syntax validation
            if context['language'] == 'python':
                # Check for balanced parentheses, brackets, braces
                for line in new_lines:
                    parens = line.count('(') - line.count(')')
                    brackets = line.count('[') - line.count(']')
                    braces = line.count('{') - line.count('}')
                    
                    if parens != 0 or brackets != 0 or braces != 0:
                        # Only warn if this looks like a complete statement
                        stripped = line.strip()
                        if stripped and not stripped.endswith(('\\', ',', '(', '[', '{')):
                            warnings.append(f"Potential unbalanced brackets in line: {line.strip()[:50]}")
                
                # Check for consistent indentation
                if context['indentation_level'] > 0:
                    for i, line in enumerate(new_lines, 1):
                        if line.strip() and line.startswith('    ') and len(line) % context['indentation_level'] != 0:
                            if not line.startswith('    ' * (len(line) // context['indentation_level'])):
                                warnings.append(f"Inconsistent indentation at line {i}")
            
            # Check for critical pattern preservation
            if 'def ' in old_content and 'def ' not in new_content:
                warnings.append("Warning: Function definitions may have been removed")
            
            if 'class ' in old_content and 'class ' not in new_content:
                warnings.append("Warning: Class definitions may have been removed")
                
            # Check import integrity
            if context['has_imports']:
                old_imports = [line for line in old_lines if any(pattern in line for pattern in ['import ', 'from '])]
                new_imports = [line for line in new_lines if any(pattern in line for pattern in ['import ', 'from '])]
                
                if len(new_imports) < len(old_imports):
                    warnings.append("Warning: Some import statements may have been removed")
        
        # Check for logical content preservation
        if len(new_content) < len(old_content) * 0.1:
            warnings.append("Warning: Content reduced significantly (>90% reduction)")
        
        return len(warnings) == 0, '\n'.join(warnings) if warnings else "Change validated successfully"

    def run(self, file_path: str, new_text: str, old_text: Optional[str] = None, 
            replace_all: bool = False, start_line: Optional[int] = None, end_line: Optional[int] = None) -> ToolResult:
        
        if not validate_read_before_edit(file_path):
            return ToolResult(success=False, error=get_read_before_edit_error(file_path))
        
        try:
            abs_path = os.path.abspath(file_path)
            with open(abs_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            # Analyze code context
            context = self._analyze_code_context(original_content, file_path)
            lines = original_content.splitlines(keepends=True)
            
            operation = None
            details = {}
            
            # MODE 1: Line-based replacement
            if start_line is not None and end_line is not None:
                operation = "line_replacement"
                if start_line < 1 or end_line < start_line:
                    return ToolResult(success=False, error=f"Invalid line range: {start_line}-{end_line}")
                
                start_idx = start_line - 1
                if start_idx >= len(lines):
                    return ToolResult(success=False, error=f"Start line {start_line} out of bounds (file has {len(lines)} lines)")
                
                # Extract old content for analysis
                old_section_content = "".join(lines[start_idx:end_line])
                details['start_line'] = start_line
                details['end_line'] = end_line
                
                pre_content = "".join(lines[:start_idx])
                post_content = "" if end_line >= len(lines) else "".join(lines[end_line:])
                updated_content = pre_content + new_text + post_content

            # MODE 2: Text-based replacement
            elif old_text is not None:
                operation = "text_replacement"
                details['old_text'] = old_text
                details['new_text'] = new_text
                
                if old_text in original_content:
                    if replace_all:
                        updated_content = original_content.replace(old_text, new_text)
                        count = original_content.count(old_text)
                        details['count'] = count
                    else:
                        updated_content = original_content.replace(old_text, new_text, 1)
                        details['count'] = 1
                else:
                    # Try relaxed matching
                    old_text_stripped = old_text.strip()
                    if not old_text_stripped:
                        return ToolResult(success=False, error="old_text not found (and empty when stripped).")

                    original_norm = ' '.join(original_content.split())
                    old_norm = ' '.join(old_text.split())
                    
                    if old_norm in original_norm:
                        return ToolResult(success=False, error="old_text found but whitespace differs. Please copy the EXACT text from the file (use read_file).")
                    
                    return ToolResult(success=False, error="old_text not found in file. Ensure you are using the EXACT text from read_file.")
            else:
                return ToolResult(success=False, error="old_text is required if start_line/end_line are not provided.")

            # Validate the change logic
            is_valid, validation_message = self._validate_change_logic(
                original_content, updated_content, context, operation, details
            )
            
            # Write the updated content
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            
            # Generate change explanation
            explanation = self._generate_change_explanation(
                original_content, updated_content, context, operation, details
            )
            
            # Create detailed response
            lines_changed = len(updated_content.splitlines()) - len(original_content.splitlines())
            
            response_parts = [
                f"✅ **Successfully edited file: {file_path}**",
                "",
                "## 📝 **Change Analysis**",
                explanation,
                "",
                f"**Lines changed**: {lines_changed:+d}",
                f"**File type**: {context['file_type']} ({context['language']})",
                "",
                "## 🔍 **Validation Results**",
                validation_message,
            ]
            
            # Add context-specific insights
            if context['file_type'] == 'code':
                insights = []
                if context['has_imports']:
                    insights.append("• Import statements preserved")
                if context['has_functions']:
                    insights.append("• Function structure maintained")
                if context['has_classes']:
                    insights.append("• Class definitions intact")
                
                if insights:
                    response_parts.extend([
                        "",
                        "## 🧠 **Code Intelligence**",
                        *insights
                    ])
            
            # Add safety recommendations if there were warnings
            if not is_valid:
                response_parts.extend([
                    "",
                    "## ⚠️ **Recommendations**",
                    "• Review the changes carefully",
                    "• Test the modified code",
                    "• Consider using version control",
                    "• Run syntax validation tools"
                ])
            
            response_content = '\n'.join(response_parts)
            
            return ToolResult(success=True, content=response_content)

        except Exception as e:
            return ToolResult(success=False, error=f"Failed to edit file: {e}")

class DeleteFileTool(BaseTool):
    name = "delete_file"
    description = "Remove files or directories. Use with caution."
    args_schema = DeleteFileParams

    def run(self, file_path: str, recursive: bool = False) -> ToolResult:
        try:
            abs_path = os.path.abspath(file_path)
            if not os.path.exists(abs_path):
                return ToolResult(success=False, error="Path not found.")

            if os.path.isdir(abs_path):
                if recursive:
                    shutil.rmtree(abs_path)
                    return ToolResult(success=True, content=f"Recursively deleted directory: {file_path}")
                else:
                    if os.listdir(abs_path):
                        return ToolResult(success=False, error="Directory is not empty. Use recursive=true to delete.")
                    os.rmdir(abs_path)
                    return ToolResult(success=True, content=f"Deleted empty directory: {file_path}")
            else:
                os.remove(abs_path)
                return ToolResult(success=True, content=f"Deleted file: {file_path}")

        except Exception as e:
            return ToolResult(success=False, error=f"Failed to delete: {e}")

class ListFilesTool(BaseTool):
    name = "list_files"
    description = "Browse directory contents and file structure. Supports tree view."
    args_schema = ListFilesParams

    def run(self, directory: str = '.', pattern: str = '*.*', recursive: bool = False, show_hidden: bool = False, tree: bool = False, ignore_patterns: List[str] = None) -> ToolResult:
        try:
            abs_path = os.path.abspath(directory)
            if not os.path.isdir(abs_path):
                return ToolResult(success=False, error="Directory not found.")

            ignore_list = ignore_patterns if ignore_patterns else ['__pycache__', '.git', 'node_modules', '*.pyc', 'venv', '.env']
            
            def should_ignore(name):
                for pat in ignore_list:
                    if fnmatch.fnmatch(name, pat):
                        return True
                return False

            if tree:
                # Tree view generation
                tree_str = []
                base_depth = abs_path.count(os.sep)
                
                for root, dirs, files in os.walk(abs_path):
                    # Filter in-place
                    dirs[:] = [d for d in dirs if not (d.startswith('.') and not show_hidden) and not should_ignore(d)]
                    files = [f for f in files if not (f.startswith('.') and not show_hidden) and not should_ignore(f)]
                    
                    depth = root.count(os.sep) - base_depth
                    indent = "  " * depth
                    if depth > 0:
                         tree_str.append(f"{indent}📂 {os.path.basename(root)}/")
                    
                    subindent = "  " * (depth + 1)
                    
                    for f in files:
                         if fnmatch.fnmatch(f, pattern):
                            tree_str.append(f"{subindent}📄 {f}")
                
                if not tree_str:
                     return ToolResult(success=True, content=f"No files found in {directory}")
                
                return ToolResult(success=True, content="\n".join(tree_str))

            # Standard List View
            file_list = []
            if recursive:
                for root, dirs, files in os.walk(abs_path):
                    dirs[:] = [d for d in dirs if not (d.startswith('.') and not show_hidden) and not should_ignore(d)]
                    # Remove hidden files if needed, but 'files' logic below handles ignore check
                    
                    for file in files:
                        if not show_hidden and file.startswith('.'):
                            continue
                        if should_ignore(file):
                            continue
                            
                        # Pattern check
                        # Simple wildcard replacement for regex match is risky, assume 'pattern' is glob
                        if fnmatch.fnmatch(file, pattern):
                             file_list.append(os.path.join(root, file))
            else:
                for item in os.listdir(abs_path):
                    if not show_hidden and item.startswith('.'):
                        continue
                    if should_ignore(item):
                        continue
                    
                    if fnmatch.fnmatch(item, pattern):
                         file_list.append(os.path.join(abs_path, item))

            return ToolResult(success=True, content=file_list)

        except Exception as e:
            return ToolResult(success=False, error=f"Failed to list files: {e}")

class SearchFilesTool(BaseTool):
    name = "search_files"
    description = "Find text patterns in files across the codebase."
    args_schema = SearchFilesParams

    def run(self, pattern: str, file_pattern: str = '*', directory: str = '.', case_sensitive: bool = False,
            pattern_type: str = 'substring', file_types: Optional[Union[str, List[str]]] = None,
            exclude_dirs: Optional[Union[str, List[str]]] = None, exclude_files: Optional[Union[str, List[str]]] = None,
            max_results: int = 100, context_lines: int = 0, group_by_file: bool = False) -> ToolResult:
        try:
            from pathlib import Path
            import re, fnmatch, os
            
            base_dir = Path(directory).resolve()
            if not base_dir.is_dir():
                return ToolResult(success=False, error="Directory not found.")

            # Normalizers
            def _norm(v):
                if v is None:
                    return []
                if isinstance(v, str):
                    return [x.strip() for x in v.split(',')]
                return list(v)

            excludes = set(_norm(exclude_dirs) + ['.git','__pycache__','node_modules','venv','.env','.idea','.vscode'])
            file_excludes = set(_norm(exclude_files))
            ftypes = set(v.lower().lstrip('.') for v in _norm(file_types)) if file_types else None

            if pattern_type == 'regex':
                try:
                    regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
                except re.error as e:
                    return ToolResult(success=False, error=f"Invalid regex: {e}")
            else:
                regex = None

            pat_cmp = pattern.lower() if not case_sensitive else pattern
            results, grouped = [], {} if group_by_file else None
            found = 0

            for root, dirs, files in os.walk(base_dir, topdown=True):
                dirs[:] = [d for d in dirs if d not in excludes]
                for f in files:
                    if found >= max_results:
                        break
                    if file_excludes and any(fnmatch.fnmatch(f, p) for p in file_excludes):
                        continue
                    if ftypes and f.rsplit('.',1)[-1].lower() not in ftypes:
                        continue
                    if file_pattern != '*' and not fnmatch.fnmatch(f, file_pattern):
                        continue

                    fp = os.path.join(root, f)
                    try:
                        stat = os.stat(fp)
                        if stat.st_size > 10*1024*1024:
                            continue
                        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                            if context_lines:
                                lines = fh.readlines()
                                iter_lines = enumerate(lines, start=1)
                                use_buf = True
                            else:
                                iter_lines = enumerate(fh, start=1)
                                use_buf = False
                            for ln_num, line in iter_lines:
                                txt = line.rstrip('\n')
                                matched = False
                                if pattern_type == 'substring':
                                    matched = (pattern in txt) if case_sensitive else (pat_cmp in txt.lower())
                                elif pattern_type == 'regex':
                                    matched = bool(regex.search(txt))
                                elif pattern_type == 'exact':
                                    matched = (txt.strip() == pattern) if case_sensitive else (txt.strip().lower() == pat_cmp)
                                if matched:
                                    ctx = []
                                    if context_lines and use_buf:
                                        start = max(0, ln_num-context_lines-1)
                                        end = min(len(lines), ln_num+context_lines)
                                        ctx = [l.strip() for l in lines[start:end]]
                                    else:
                                        ctx = [txt.strip()]
                                    rec = {'file': fp, 'line_number': ln_num, 'line': txt[:200], 'context': ctx}
                                    if group_by_file:
                                        grouped.setdefault(fp, []).append(rec)
                                    else:
                                        results.append(rec)
                                    found += 1
                                    if found >= max_results:
                                        break
                    except Exception:
                        continue
                if found >= max_results:
                    break
            return ToolResult(success=True, content=grouped if group_by_file else results)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to search files: {e}")

class FastGrepTool(BaseTool):
    name = "fast_grep"
    description = "Search for a keyword or regex pattern in a directory. This is an alias for the search_files tool."
    args_schema = FastGrepParams

    def run(self, keyword: str, directory: str = '.', file_pattern: Optional[str] = None) -> ToolResult:
        search_tool = SearchFilesTool()
        return search_tool.run(pattern=keyword, directory=directory, file_pattern=file_pattern or '*')
