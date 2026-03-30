from .base import BaseDocumentHandler
from .registry import get_handler, DocumentHandlerRegistry
from .pdf import PDFHandler
from .docx import DocxHandler
from .pptx import PPTXHandler
from .excel import ExcelHandler
from .text import TextHandler

__all__ = [
    "BaseDocumentHandler",
    "get_handler",
    "DocumentHandlerRegistry",
    "PDFHandler",
    "DocxHandler",
    "PPTXHandler",
    "ExcelHandler",
    "TextHandler",
]
