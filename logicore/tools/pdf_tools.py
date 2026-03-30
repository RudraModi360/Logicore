from typing import List, Optional
from pydantic import BaseModel, Field
import os
from .base import BaseTool, ToolResult
from pypdf import PdfReader, PdfWriter

class MergePDFParams(BaseModel):
    output_path: str = Field(..., description='Path for the merged PDF.')
    input_paths: List[str] = Field(..., description='List of input PDF paths in order.')

class SplitPDFParams(BaseModel):
    input_path: str = Field(..., description='Path to source PDF.')
    output_dir: str = Field(..., description='Directory to save split pages.')

class MergePDFTool(BaseTool):
    name = "merge_pdfs"
    description = "Merge multiple PDF files into one."
    args_schema = MergePDFParams
    
    def run(self, output_path: str, input_paths: List[str]) -> ToolResult:
        try:
            merger = PdfWriter()
            for path in input_paths:
                if not os.path.exists(path):
                     return ToolResult(success=False, error=f"File not found: {path}")
                merger.append(path)
            
            merger.write(output_path)
            merger.close()
            return ToolResult(success=True, content=f"Merged {len(input_paths)} PDFs into {output_path}")
        except Exception as e:
            return ToolResult(success=False, error=f"Merge Error: {e}")

class SplitPDFTool(BaseTool):
    name = "split_pdf"
    description = "Split a PDF into individual pages."
    args_schema = SplitPDFParams
    
    def run(self, input_path: str, output_dir: str) -> ToolResult:
        try:
            if not os.path.exists(input_path): return ToolResult(success=False, error="File not found")
            os.makedirs(output_dir, exist_ok=True)
            
            reader = PdfReader(input_path)
            created_files = []
            
            for i, page in enumerate(reader.pages):
                writer = PdfWriter()
                writer.add_page(page)
                
                out_name = f"page_{i+1}.pdf"
                out_path = os.path.join(output_dir, out_name)
                
                with open(out_path, "wb") as f:
                    writer.write(f)
                created_files.append(out_name)
                
            return ToolResult(success=True, content=f"Split into {len(created_files)} pages in {output_dir}")
        except Exception as e:
            return ToolResult(success=False, error=f"Split Error: {e}")
