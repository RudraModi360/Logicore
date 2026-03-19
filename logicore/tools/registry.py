from typing import Dict, Any, List, Type
from .base import BaseTool, ToolResult
from .filesystem import (
    ReadFileTool, CreateFileTool, EditFileTool, DeleteFileTool, 
    ListFilesTool, SearchFilesTool, FastGrepTool
)
from .execution import ExecuteCommandTool, CodeExecuteTool
from .web import WebSearchTool, UrlFetchTool, ImageSearchTool
from .git import GitCommandTool
from .document import ReadDocumentTool
from .convert_document import ConvertDocumentTool
from .office_tools import (
    EditPPTXTool, CreatePPTXTool, AppendSlideTool,
    EditDOCXTool, CreateDOCXTool,
    EditExcelTool, CreateExcelTool
)
from .pdf_tools import MergePDFTool, SplitPDFTool
from .agent_tools import (
    DateTimeTool, NotesTool, MemoryTool, SmartBashTool, ThinkTool,
    get_smart_agent_tools, get_smart_agent_tool_schemas
)
from .media_search import MediaSearchTool
from .cron_tools import AddCronJobTool, ListCronJobsTool, RemoveCronJobTool, GetCronsTool

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._register_defaults()

    def register_tool(self, tool: BaseTool):
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} already registered.")
        self._tools[tool.name] = tool

    def _register_defaults(self):
        self.register_tool(ReadFileTool())
        self.register_tool(CreateFileTool())
        self.register_tool(EditFileTool())
        self.register_tool(DeleteFileTool())
        self.register_tool(ListFilesTool())
        self.register_tool(SearchFilesTool())
        self.register_tool(FastGrepTool())
        self.register_tool(ExecuteCommandTool())
        self.register_tool(CodeExecuteTool())
        self.register_tool(WebSearchTool())
        self.register_tool(ImageSearchTool())
        self.register_tool(UrlFetchTool())
        self.register_tool(GitCommandTool())
        self.register_tool(ReadDocumentTool())
        self.register_tool(ConvertDocumentTool())
        
        # Media Search for inline images/videos (Gemini-like)
        self.register_tool(MediaSearchTool())
        
        # New Office Tools
        self.register_tool(EditPPTXTool())
        self.register_tool(CreatePPTXTool())
        self.register_tool(AppendSlideTool())
        self.register_tool(EditDOCXTool())
        self.register_tool(CreateDOCXTool())
        self.register_tool(EditExcelTool())
        self.register_tool(CreateExcelTool())
        
        # New PDF Tools
        self.register_tool(MergePDFTool())
        self.register_tool(SplitPDFTool())

        # Cron Scheduling Tools
        self.register_tool(AddCronJobTool())
        self.register_tool(ListCronJobsTool())
        self.register_tool(RemoveCronJobTool())
        self.register_tool(GetCronsTool())

    def get_tool(self, name: str) -> BaseTool:
        return self._tools.get(name)

    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> ToolResult:
        tool = self.get_tool(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")
        
        try:
            # Pydantic validation
            validated_args = tool.args_schema(**tool_args).model_dump()
            return tool.run(**validated_args)
        except Exception as e:
            return ToolResult(success=False, error=f"Error executing tool {tool_name}: {e}")

    @property
    def schemas(self) -> List[Dict[str, Any]]:
        return [tool.schema for tool in self._tools.values()]

# Global registry instance
registry = ToolRegistry()

def execute_tool(tool_name: str, tool_args: Dict[str, Any]) -> ToolResult:
    return registry.execute_tool(tool_name, tool_args)

# Tool categories
SAFE_TOOLS = ['read_file', 'list_files', 'search_files', 'fast_grep', 'read_document', 'media_search', 'list_cron_jobs', 'get_crons']
APPROVAL_REQUIRED_TOOLS = [
    'create_file', 'edit_file', 'web_search', 'image_search', 'url_fetch', 'convert_document',
    'edit_pptx', 'create_pptx', 'append_slide',
    'edit_docx', 'create_docx',
    'edit_excel', 'create_excel',
    'merge_pdfs', 'split_pdf',
    'add_cron_job', 'remove_cron_job'
]
DANGEROUS_TOOLS = ['delete_file', 'execute_command', 'git_command', 'code_execute']
