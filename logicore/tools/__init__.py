from .registry import (
    registry, 
    execute_tool, 
    SAFE_TOOLS, 
    DANGEROUS_TOOLS, 
    APPROVAL_REQUIRED_TOOLS
)
from .base import ToolResult, BaseTool
from .agent_tools import (
    DateTimeTool, NotesTool, MemoryTool, SmartBashTool, ThinkTool,
    get_smart_agent_tools, get_smart_agent_tool_schemas
)
from .scheduler import (
    ToolScheduler,
    ToolExecution,
    ExecutionStatus,
    SchedulerConfig,
    schedule_tool,
)

# New tracker and plan tools
from .tracker_tools import (
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
from .plan_tools import (
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
    'MemoryTool',
    'SmartBashTool',
    'ThinkTool',
    'get_smart_agent_tools',
    'get_smart_agent_tool_schemas',
    # Scheduler
    'ToolScheduler',
    'ToolExecution',
    'ExecutionStatus',
    'SchedulerConfig',
    'schedule_tool',
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

