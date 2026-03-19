from typing import Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
from ..document_handlers import get_handler

class ConvertDocumentParams(BaseModel):
    input_path: str = Field(..., description="Absolute path to the input document.")
    output_path: str = Field(..., description="Absolute path to the output document. Extension determines the format.")

class ConvertDocumentTool(BaseTool):
    name = "convert_document"
    description = "Convert a document from one format to another (e.g., PDF to DOCX, DOCX to HTML). Uses Pandoc where applicable."
    args_schema = ConvertDocumentParams

    def run(self, input_path: str, output_path: str) -> ToolResult:
        try:
            handler = get_handler(input_path)
            handler.convert_to(output_path)
            return ToolResult(success=True, content=f"Successfully converted {input_path} to {output_path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
