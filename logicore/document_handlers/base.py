from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import os

class BaseDocumentHandler(ABC):
    """
    Abstract base class for all document handlers in Scratchy.
    """
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

    @abstractmethod
    def load(self) -> None:
        """Load the document content into memory."""
        pass

    @abstractmethod
    def get_text(self) -> str:
        """Return the full text content of the document."""
        pass

    @abstractmethod
    def get_metadata(self) -> Dict[str, Any]:
        """Return metadata about the document (author, created date, etc.)."""
        pass

    def to_markdown(self) -> str:
        """
        Convert the document content to Markdown format.
        Default implementation just returns the text content.
        Subclasses should override this to provide better formatting.
        """
        text = self.get_text()
        metadata = self.get_metadata()
        
        md_output = f"# Document: {os.path.basename(self.file_path)}\n\n"
        
        if metadata:
            md_output += "## Metadata\n\n"
            for key, value in metadata.items():
                md_output += f"- **{key}**: {value}\n"
            md_output += "\n"
            
        md_output += "## Content\n\n"
        md_output += text
        
        return md_output
        
    def convert_to(self, output_path: str) -> None:
        """
        Convert the document to another format.
        Based on the file extension, it delegates to native Python conversion utilities.
        Supported output formats: .txt, .md, .html, .docx
        """
        from ..utils.conversion import convert_to_html, convert_to_docx
        
        # Determine target extension
        _, ext = os.path.splitext(output_path)
        ext = ext.lower()
        
        # 1. Text / Markdown (Pass-through)
        if ext in ['.md', '.markdown', '.txt']:
             with open(output_path, 'w', encoding='utf-8') as f:
                if ext in ['.txt']:
                    f.write(self.get_text())
                else:
                    f.write(self.to_markdown())
             return

        # Markdown IR for conversion
        markdown_content = self.to_markdown()

        # 2. HTML
        if ext in ['.html', '.htm']:
            convert_to_html(markdown_content, output_path)
            return
        
        # 3. DOCX
        if ext in ['.docx']:
            convert_to_docx(markdown_content, output_path)
            return

        raise RuntimeError(f"Conversion to {ext} is not natively supported yet. Supported formats: .txt, .md, .html, .docx")
