from typing import Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
from ..document_handlers import get_handler

class ReadDocumentParams(BaseModel):
    file_path: str = Field(..., description="Absolute path to the document file.")
    output_format: str = Field("markdown", description="Output format: 'text', 'markdown', or 'metadata'. Default is 'markdown'.")

class ReadDocumentTool(BaseTool):
    name = "read_document"
    description = "Read and extract content from various document formats (PDF, DOCX, PPTX, XLSX, etc.)"
    args_schema = ReadDocumentParams

    def run(self, file_path: str, output_format: str = "markdown") -> ToolResult:
        try:
            handler = get_handler(file_path)
            
            if output_format == "metadata":
                content = str(handler.get_metadata())
            elif output_format == "text":
                content = handler.get_text()
            else: # markdown
                content = handler.to_markdown()
                
            return ToolResult(success=True, content=content)
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))
