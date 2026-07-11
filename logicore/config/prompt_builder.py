"""
Section-Based Dynamic Prompt Builder.

Based on Claude Code's prompt construction pattern:
- Static sections (cacheable across sessions)
- Dynamic sections (session-specific, recomputed)
- Boundary marker separating cacheable from non-cacheable content
- Tool availability affects prompt content

Key insight from Claude Code:
- getSystemPrompt() assembles sections
- Static content before boundary marker
- Dynamic content after boundary marker
- Tool-specific guidance injected based on available tools
"""

from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
import os
import platform


class PromptSection:
    """
    A section of the system prompt.
    
    Based on Claude Code's systemPromptSection() pattern:
    - name: Unique identifier for caching
    - compute: Function that generates the section content
    - cache_break: Whether this section recomputes every turn
    """
    
    def __init__(
        self,
        name: str,
        compute: Callable[[], str],
        cache_break: bool = False
    ):
        self.name = name
        self.compute = compute
        self.cache_break = cache_break
        self._cached_value: Optional[str] = None
    
    def resolve(self, force: bool = False) -> Optional[str]:
        """
        Resolve the section content.
        
        Based on Claude Code's resolveSystemPromptSections():
        - Cache hit: return cached value
        - Cache miss: compute and cache
        - cache_break: always recompute
        """
        if not force and not self.cache_break and self._cached_value is not None:
            return self._cached_value
        
        value = self.compute()
        if not self.cache_break:
            self._cached_value = value
        return value


class PromptBuilder:
    """
    Builds system prompts from sections.
    
    Based on Claude Code's getSystemPrompt() pattern:
    - Static sections (cacheable)
    - Dynamic boundary marker
    - Dynamic sections (session-specific)
    - Tool availability affects prompt content
    """
    
    # Boundary marker from Claude Code
    DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"
    
    def __init__(self):
        """Initialize the prompt builder."""
        self._sections: List[PromptSection] = []
        self._section_cache: Dict[str, str] = {}
    
    def add_static_section(self, name: str, compute: Callable[[], str]):
        """
        Add a static section (cacheable across sessions).
        
        Based on Claude Code's static content pattern:
        - getSimpleIntroSection()
        - getSimpleSystemSection()
        - getSimpleDoingTasksSection()
        - getActionsSection()
        - getUsingYourToolsSection()
        - getSimpleToneAndStyleSection()
        - getOutputEfficiencySection()
        """
        self._sections.append(PromptSection(name, compute, cache_break=False))
    
    def add_dynamic_section(self, name: str, compute: Callable[[], str]):
        """
        Add a dynamic section (session-specific, recomputed).
        
        Based on Claude Code's dynamic content pattern:
        - getSessionSpecificGuidanceSection()
        - getMemoryPrompt()
        - getAntModelOverrideSection()
        """
        self._sections.append(PromptSection(name, compute, cache_break=True))
    
    def build(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Build the system prompt from sections.
        
        Based on Claude Code's getSystemPrompt() pattern:
        1. Static sections (cacheable)
        2. Dynamic boundary marker
        3. Dynamic sections (session-specific)
        """
        if context is None:
            context = {}
        
        # Separate static and dynamic sections
        static_sections = []
        dynamic_sections = []
        
        for section in self._sections:
            if section.cache_break:
                dynamic_sections.append(section)
            else:
                static_sections.append(section)
        
        # Build prompt
        parts = []
        
        # Static sections
        for section in static_sections:
            content = section.resolve()
            if content:
                parts.append(content)
        
        # Dynamic boundary marker
        parts.append(self.DYNAMIC_BOUNDARY)
        
        # Dynamic sections
        for section in dynamic_sections:
            content = section.resolve(force=True)
            if content:
                parts.append(content)
        
        return "\n\n".join(parts)
    
    def clear_cache(self):
        """Clear the section cache."""
        for section in self._sections:
            section._cached_value = None
        self._section_cache.clear()


def get_tool_guidance_section(enabled_tools: List[str]) -> str:
    """
    Generate tool-specific guidance based on available tools.
    
    Based on Claude Code's getUsingYourToolsSection() pattern:
    - Adapts based on which tools are available
    - Provides specific guidance for each tool type
    """
    guidance_parts = ["## Tool-Specific Guidance\n"]
    
    # Planning tools
    planning_tools = ["enter_plan_mode", "submit_plan", "exit_plan_mode", "task_create"]
    if any(tool in enabled_tools for tool in planning_tools):
        guidance_parts.append("### Planning & Task Management")
        if "task_create" in enabled_tools:
            guidance_parts.append("- Use `task_create` to break complex work into subtasks")
            guidance_parts.append("- Always use `active_form` for live UI status")
            guidance_parts.append("- Use `blocked_by` for dependencies between tasks")
        if "task_list" in enabled_tools:
            guidance_parts.append("- Use `task_list` to see all tasks and progress")
        if "task_next" in enabled_tools:
            guidance_parts.append("- Use `task_next` to get the next available task")
        if "enter_plan_mode" in enabled_tools:
            guidance_parts.append("- Use `enter_plan_mode` for complex tasks requiring user approval")
            guidance_parts.append("- Follow the 5-phase workflow: explore → design → write plan → get approval → execute")
        guidance_parts.append("")
    
    # File tools
    file_tools = ["read_file", "file_edit", "file_write", "list_files", "search_files"]
    if any(tool in enabled_tools for tool in file_tools):
        guidance_parts.append("### File Operations")
        if "read_file" in enabled_tools:
            guidance_parts.append("- Always read files before editing them")
            guidance_parts.append("- Use `read_file` to understand current state")
        if "file_edit" in enabled_tools:
            guidance_parts.append("- Use `file_edit` for targeted changes (preferred)")
            guidance_parts.append("- Read the file first to understand the context")
        if "file_write" in enabled_tools:
            guidance_parts.append("- Use `file_write` for creating new files or complete rewrites")
            guidance_parts.append("- File writes are irreversible - confirm with user if unsure")
        if "list_files" in enabled_tools:
            guidance_parts.append("- Use `list_files` to explore directory structure")
        if "search_files" in enabled_tools:
            guidance_parts.append("- Use `search_files` to find files by pattern")
        guidance_parts.append("")
    
    # Search tools
    search_tools = ["fast_grep", "search_files"]
    if any(tool in enabled_tools for tool in search_tools):
        guidance_parts.append("### Search & Discovery")
        if "fast_grep" in enabled_tools:
            guidance_parts.append("- Use `fast_grep` to search file contents with regex")
            guidance_parts.append("- Be specific with patterns for better results")
        guidance_parts.append("")
    
    # Execution tools
    exec_tools = ["bash", "code_execute"]
    if any(tool in enabled_tools for tool in exec_tools):
        guidance_parts.append("### Command Execution")
        if "bash" in enabled_tools:
            guidance_parts.append("- Use `bash` for system commands and file operations")
            guidance_parts.append("- Set appropriate timeouts (10-15s for quick, 60-120s for installs)")
            guidance_parts.append("- On Windows, use PowerShell commands")
        guidance_parts.append("")
    
    # Web tools
    web_tools = ["web_search", "web_fetch"]
    if any(tool in enabled_tools for tool in web_tools):
        guidance_parts.append("### Web Access")
        if "web_search" in enabled_tools:
            guidance_parts.append("- Use `web_search` for current/recent information")
            guidance_parts.append("- Use web tools autonomously when the task benefits — you do not need to ask the user before each search")
            guidance_parts.append("- Use specific, narrow search queries (2-4 keywords)")
        if "web_fetch" in enabled_tools:
            guidance_parts.append("- Use `web_fetch` to retrieve specific URLs")
        guidance_parts.append("")
    
    return "\n".join(guidance_parts)


def get_session_guidance_section(session_state: Dict[str, Any]) -> str:
    """
    Generate session-specific guidance.
    
    Based on Claude Code's getSessionSpecificGuidanceSection() pattern:
    - Adapts based on session state
    - Provides contextual reminders
    """
    guidance_parts = []
    
    # Plan mode guidance
    if session_state.get("in_plan_mode"):
        guidance_parts.append("### Plan Mode Active")
        guidance_parts.append("- You are in plan mode - read-only except for the plan file")
        guidance_parts.append("- Use read-only tools to explore and understand the codebase")
        guidance_parts.append("- Write your plan to the plan file")
        guidance_parts.append("- Use `ask_user_question` if you need clarification")
        guidance_parts.append("- Use `exit_plan_mode` when your plan is ready for approval")
        guidance_parts.append("")
    
    # Task progress guidance
    pending_tasks = session_state.get("pending_tasks", 0)
    completed_tasks = session_state.get("completed_tasks", 0)
    if pending_tasks > 0 or completed_tasks > 0:
        guidance_parts.append("### Task Progress")
        guidance_parts.append(f"- Pending: {pending_tasks} tasks")
        guidance_parts.append(f"- Completed: {completed_tasks} tasks")
        if pending_tasks > 0:
            guidance_parts.append("- Use `task_next` to get the next available task")
        guidance_parts.append("")
    
    # File change guidance
    files_changed = session_state.get("files_changed", 0)
    if files_changed > 3:
        guidance_parts.append("### Verification Reminder")
        guidance_parts.append(f"- You've made {files_changed} file changes")
        guidance_parts.append("- Consider verifying your work before proceeding")
        guidance_parts.append("- Use `verify_plan_execution` to report verification status")
        guidance_parts.append("")
    
    return "\n".join(guidance_parts)


def build_system_prompt(
    model_name: str = "Unknown Model",
    role: str = "general",
    tools: Optional[List[Dict[str, Any]]] = None,
    reasoning_level: str = "medium",
    plan_mode: bool = True,
    session_state: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build a complete system prompt using the section-based pattern.
    
    This is the main entry point for building prompts.
    
    Args:
        model_name: The name of the model
        role: Agent role ('general', 'engineer', 'copilot')
        tools: List of available tools
        reasoning_level: Reasoning depth
        plan_mode: Whether plan mode is enabled
        session_state: Current session state
    
    Returns:
        Complete system prompt
    """
    if tools is None:
        tools = []
    if session_state is None:
        session_state = {}
    
    # Extract tool names
    enabled_tools = []
    for tool in tools:
        if isinstance(tool, dict) and "function" in tool:
            enabled_tools.append(tool["function"].get("name", ""))
        elif hasattr(tool, "name"):
            enabled_tools.append(tool.name)
    
    # Build prompt using PromptBuilder
    builder = PromptBuilder()
    
    # Add static sections
    builder.add_static_section("identity", lambda: f"""You are an AI Assistant from the Logicore Framework. You are powered by {model_name}.

## Identity
You are a versatile AI assistant designed to help with a wide range of tasks. You combine strong reasoning with practical tool access and thoughtful analysis.

Core traits:
- Helpful - you genuinely try to understand and address what users need
- Capable - you have tools available for various tasks
- Adaptive - you match the user's communication style
- Thoughtful - you explain your reasoning before taking action""")
    
    builder.add_static_section("tool_guidance", lambda: get_tool_guidance_section(enabled_tools))
    
    # Add dynamic sections
    if session_state:
        builder.add_dynamic_section("session_guidance", lambda: get_session_guidance_section(session_state))
    
    # Build and return
    return builder.build(context=session_state)
