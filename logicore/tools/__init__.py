from .registry import (
    registry, 
    execute_tool, 
    SAFE_TOOLS, 
    DANGEROUS_TOOLS, 
    APPROVAL_REQUIRED_TOOLS
)
from .base import ToolResult, BaseTool
from .notes import NotesTool
from .datetime import DateTimeTool, get_smart_agent_tools, get_smart_agent_tool_schemas
from .think import ThinkTool
from .bash import SmartBashTool

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

__all__ = [
    'registry', 
    'execute_tool', 
    'ToolResult', 
    'BaseTool',
    'SAFE_TOOLS',
    'DANGEROUS_TOOLS',
    'APPROVAL_REQUIRED_TOOLS',
    'ALL_TOOL_SCHEMAS',
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

