from .registry import (
    registry, 
    lightweight_registry,
    smart_registry,
    copilot_registry,
    execute_tool, 
    execute_tool_lightweight,
    execute_tool_smart,
    ToolRegistry,
    TOOL_PRESETS,
    SAFE_TOOLS, 
    DANGEROUS_TOOLS, 
    APPROVAL_REQUIRED_TOOLS
)
from .base import ToolResult, BaseTool
from .notes import NotesTool
from .datetime import DateTimeTool, get_smart_agent_tools, get_smart_agent_tool_schemas
from .think import ThinkTool
from .bash import SmartBashTool
from .execution import ListProcessesTool, KillProcessTool, GetProcessInfoTool, GetProcessOutputTool, TailProcessOutputTool, WatchProcessTool, background_manager

# Tracker and plan tools
from .tracker import (
    TrackerCreateTool,
    TrackerUpdateTool,
    TrackerListTool,
    TrackerGetTool,
    TrackerAddDependencyTool,
    TrackerVisualizeTool,
    TrackerCloseTool,
    get_tracker_tools,
    get_tracker_tool_schemas,
)
from .plan import (
    EnterPlanModeTool,
    SubmitPlanTool,
    ExitPlanModeTool,
    UpdatePlanProgressTool,
    ViewPlanTool,
    get_plan_tools,
    get_plan_tool_schemas,
)

# Convenience for getting all schemas
ALL_TOOL_SCHEMAS = registry.schemas
LIGHTWEIGHT_TOOL_SCHEMAS = lightweight_registry.schemas
SMART_TOOL_SCHEMAS = smart_registry.schemas
COPILOT_TOOL_SCHEMAS = copilot_registry.schemas

__all__ = [
    'registry', 
    'lightweight_registry',
    'smart_registry',
    'copilot_registry',
    'execute_tool', 
    'execute_tool_lightweight',
    'execute_tool_smart',
    'ToolRegistry',
    'TOOL_PRESETS',
    'ToolResult', 
    'BaseTool',
    'SAFE_TOOLS',
    'DANGEROUS_TOOLS',
    'APPROVAL_REQUIRED_TOOLS',
    'ALL_TOOL_SCHEMAS',
    'LIGHTWEIGHT_TOOL_SCHEMAS',
    'SMART_TOOL_SCHEMAS',
    'COPILOT_TOOL_SCHEMAS',
    # Process Management
    'ListProcessesTool',
    'KillProcessTool',
    'GetProcessInfoTool',
    'GetProcessOutputTool',
    'TailProcessOutputTool',
    'WatchProcessTool',
    'background_manager',
    # Smart Agent Tools
    'DateTimeTool',
    'NotesTool', 
    'SmartBashTool',
    'ThinkTool',
    'get_smart_agent_tools',
    'get_smart_agent_tool_schemas',
    # Tracker Tools
    'TrackerCreateTool',
    'TrackerUpdateTool',
    'TrackerListTool',
    'TrackerGetTool',
    'TrackerAddDependencyTool',
    'TrackerVisualizeTool',
    'TrackerCloseTool',
    'get_tracker_tools',
    'get_tracker_tool_schemas',
    # Plan Tools
    'EnterPlanModeTool',
    'SubmitPlanTool',
    'ExitPlanModeTool',
    'UpdatePlanProgressTool',
    'ViewPlanTool',
    'get_plan_tools',
    'get_plan_tool_schemas',
]
