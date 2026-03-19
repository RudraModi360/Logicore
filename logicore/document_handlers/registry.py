import os
from typing import Type
from .base import BaseDocumentHandler
from .pdf import PDFHandler
from .docx import DocxHandler
from .pptx import PPTXHandler
from .excel import ExcelHandler
from .text import TextHandler
from .csv import CSVHandler
from .image import ImageHandler

class DocumentHandlerRegistry:
    """Registry to map file extensions to document handlers."""
    
    _handlers: dict[str, Type[BaseDocumentHandler]] = {
        ".pdf": PDFHandler,
        ".docx": DocxHandler,
        ".doc": DocxHandler, # python-docx might handle .doc if it's actually xml, otherwise might fail, but mapping for now
        ".pptx": PPTXHandler,
        ".ppt": PPTXHandler, # similar caveat
        ".xlsx": ExcelHandler,
        ".xls": ExcelHandler, 
        ".csv": CSVHandler,
        ".txt": TextHandler,
        ".md": TextHandler,
        ".py": TextHandler,
        ".json": TextHandler,
        ".xml": TextHandler,
        ".html": TextHandler,
        ".css": TextHandler,
        ".js": TextHandler,
        ".png": ImageHandler,
        ".jpg": ImageHandler,
        ".jpeg": ImageHandler,
        ".webp": ImageHandler,
    }

    @classmethod
    def get_handler(cls, file_path: str) -> BaseDocumentHandler:
        """
        Return an instance of the appropriate handler for the given file path.
        Raises ValueError if no handler is found.
        """
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        
        handler_cls = cls._handlers.get(ext)
        if handler_cls:
            return handler_cls(file_path)
            
        # Fallback for text-like files not explicitly listed? 
        # Maybe safer to return TextHandler for unknown types if they look like text?
        # For now, strictly follow registry or default to TextHandler if we want "general purpose".
        # Let's default to TextHandler but maybe warn.
        
        # Actually, let's just default to TextHandler. It handles read errors gracefully-ish.
        return TextHandler(file_path)

def get_handler(file_path: str) -> BaseDocumentHandler:
    """Convenience function to get a handler instance."""
    return DocumentHandlerRegistry.get_handler(file_path)
