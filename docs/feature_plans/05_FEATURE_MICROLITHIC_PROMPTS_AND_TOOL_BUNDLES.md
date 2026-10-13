# Feature Plan 05: Microlithic Prompt Architecture & Tool Bundles (Part E)

## Overview & Objective
Transform the hardcoded 44,402-character monolithic system prompt in [`logicore/config/prompts.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/config/prompts.py) into a clean, **microlithic (micro-component-based) architecture**.

Key design requirements:
1. **Curated Default Tool Families (No Runtime Tool Searching):** By default, the agent ships with only the core essential tool families (OS/CMD + Filesystem + Web). Specialized tools (Document AI, PPT, Task DAG, Scheduler) are available as explicit on-demand bundles.
2. **Eliminate Dual Tool Payload:** Deprecate the Markdown parameter schema dumper in `_format_tools()`. Tool schemas are exclusively provided via the standard API parameter `tools=[...]`. The prompt only provides concise behavioral guidance for enabled tool families.
3. **Prefix KV Cache Stability:** Structure prompt sections with a stable byte prefix (Identity -> Safety -> Environment -> Tool Guidance) so that GPU KV caches in Ollama, vLLM, SGLang, OpenAI, and Anthropic hit automatically.

---

## Status & Tracking
- **Status:** `READY FOR DEV`
- **Owner:** Developer Agent Team
- **Reviewer:** Product Owner
- **Target Files:**
  - `logicore/config/prompts/base_section.py` [NEW]
  - `logicore/config/prompts/tool_bundles.py` [NEW]
  - `logicore/config/prompts/sections/identity.py` [NEW]
  - `logicore/config/prompts/sections/safety.py` [NEW]
  - `logicore/config/prompts/sections/environment.py` [NEW]
  - `logicore/config/prompts/sections/tool_guidance.py` [NEW]
  - `logicore/config/prompt_builder.py` [MODIFY]
  - `logicore/config/prompts.py` [MODIFY]
  - `tests/test_prompt_builder.py` [NEW]

---

## Detailed Implementation Plan

### 1. [NEW] `logicore/config/prompts/tool_bundles.py`
Defines curated tool sets to prevent tool bloat out-of-the-box:

```python
"""
logicore/config/prompts/tool_bundles.py
Curated tool families for clean out-of-the-box defaults.
"""
from typing import List

class ToolBundles:
    # Default out-of-the-box bundle (Only 10 tools instead of 41!)
    CORE: List[str] = [
        # OS / Execution
        "bash", "code_execute",
        # Filesystem
        "read_file", "create_file", "edit_file", "list_files", "search_files", "fast_grep",
        # Web Access
        "web_search", "url_fetch",
    ]

    # Specialized On-Demand Bundles
    DOCUMENT_AI: List[str] = [
        "read_document", "convert_document", "load_skill"
    ]

    TASK_DAG: List[str] = [
        "task_create", "task_update", "task_list", "task_next", "task_get"
    ]

    PLAN_MODE: List[str] = [
        "enter_plan_mode", "submit_plan", "exit_plan_mode", "view_plan", "update_plan_progress"
    ]

    SCHEDULER: List[str] = [
        "add_cron_job", "list_cron_jobs", "remove_cron_job", "get_crons"
    ]
```

---

### 2. [NEW] `logicore/config/prompts/base_section.py`
Abstract interface for modular prompt components:

```python
"""
logicore/config/prompts/base_section.py
Abstract base class for microlithic prompt components.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any

class BasePromptSection(ABC):
    name: str
    cacheable: bool = True  # Belongs to the static prefix block

    @abstractmethod
    def should_render(self, context: Dict[str, Any]) -> bool:
        """Evaluate if section is needed based on active tools and config."""
        pass

    @abstractmethod
    def render(self, context: Dict[str, Any]) -> str:
        """Render section markdown text."""
        pass
```

---

### 3. [NEW] Micro-Sections in `logicore/config/prompts/sections/`

#### `identity.py` (~150 chars):
```python
from logicore.config.prompts.base_section import BasePromptSection

class CoreIdentitySection(BasePromptSection):
    name = "identity"
    cacheable = True
    def should_render(self, context): return True
    def render(self, context):
        agent_name = context.get("agent_name", "SmartAgent")
        return f"You are {agent_name}, an autonomous AI developer assistant powered by Logicore. Deliver verified, accurate, and direct results."
```

#### `safety.py` (~250 chars):
```python
from logicore.config.prompts.base_section import BasePromptSection

class SafetySection(BasePromptSection):
    name = "safety"
    cacheable = True
    def should_render(self, context): return True
    def render(self, context):
        return """## Operational Guardrails
- Block dangerous commands (e.g. rm -rf /, formatting disks).
- Confirm before overwriting un-backed-up production files.
- Inspect tool execution errors and self-heal before requesting user intervention."""
```

#### `environment.py` (~200 chars):
```python
import platform
import os
from logicore.config.prompts.base_section import BasePromptSection

class EnvironmentSection(BasePromptSection):
    name = "environment"
    cacheable = True
    def should_render(self, context): return True
    def render(self, context):
        os_name = "Windows (PowerShell)" if platform.system() == "Windows" else f"{platform.system()} (Bash)"
        cwd = os.getcwd()
        return f"## Runtime Environment\n- Operating System: {os_name}\n- Working Directory: `{cwd}`"
```

#### `tool_guidance.py` (~600 chars — Behavioral Tips ONLY for Active Tools):
```python
from logicore.config.prompts.base_section import BasePromptSection

class ToolGuidanceSection(BasePromptSection):
    name = "tool_guidance"
    cacheable = True
    def should_render(self, context): return bool(context.get("active_tools"))
    def render(self, context):
        active = set(context.get("active_tools", []))
        parts = ["## Tool Usage Directives"]
        
        if "web_search" in active:
            parts.append("- **Web Search:** Use specific, concise keywords (2-4 terms). Verify company identities against directories.")
        if "read_file" in active or "edit_file" in active:
            parts.append("- **Filesystem:** Always read a file before editing it. Use targeted edits over complete file rewrites.")
        if "bash" in active:
            parts.append("- **Command Execution:** Run non-interactive commands. Check exit codes before proceeding.")
        if "task_create" in active:
            parts.append("- **Task Tracking:** For complex requests (3+ steps), create tracked tasks and mark them completed as you finish.")
            
        return "\n".join(parts)
```

---

### 4. [MODIFY] [`logicore/config/prompts.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/config/prompts.py)
**Deprecate Markdown Tool Parameter Dumping:**
Remove lines 59–105 where `_format_tools` loops through `properties.items()` and formats parameter schemas as Markdown text.  
Re-route `get_system_prompt()` to instantiate and call `PromptBuilder`.

---

## Verification Plan

### Automated Test: `tests/test_prompt_builder.py`
```python
from logicore.config.prompt_builder import PromptBuilder
from logicore.config.prompts.tool_bundles import ToolBundles

def test_microlithic_prompt_assembly():
    builder = PromptBuilder.create_default()
    prompt = builder.build({
        "active_tools": ToolBundles.CORE,
        "agent_name": "SmartAgent"
    })
    
    assert "You are SmartAgent" in prompt
    assert "## Runtime Environment" in prompt
    assert "## Tool Usage Directives" in prompt
    # CRUCIAL: Ensure no raw JSON schema parameter dumps exist in markdown!
    assert "*(required)*" not in prompt
    assert len(prompt) < 2500  # Less than 2,500 chars (down from 44,400 chars!)
```

### Verification Command
```powershell
pytest tests/test_prompt_builder.py -v
```

---

## Acceptance Criteria
- [ ] System prompt size drops from 44,402 chars to under 2,500 chars for default core runs.
- [ ] No tool parameters are duplicated in Markdown text when `tools=[...]` is provided.
- [ ] Default agent initializes with `ToolBundles.CORE` (10 tools instead of 41).
- [ ] Specialized bundles (`ToolBundles.DOCS`, `ToolBundles.TASK`) can be added seamlessly via `tools=...`.
