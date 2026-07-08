"""
Document Analysis Helper Scripts

Utility functions for the document_analysis skill.
These scripts provide common operations for processing various document types.
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any


def get_file_info(file_path: str) -> Dict[str, Any]:
    """
    Get basic information about a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Dictionary with file metadata
    """
    path = Path(file_path)
    
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    
    stat = path.stat()
    
    return {
        "name": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "modified": stat.st_mtime,
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
    }


def detect_document_type(file_path: str) -> str:
    """
    Detect document type from file extension.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Document type string (pdf, excel, csv, image, text, unknown)
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    
    type_mapping = {
        ".pdf": "pdf",
        ".xlsx": "excel",
        ".xls": "excel",
        ".csv": "csv",
        ".tsv": "csv",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
        ".gif": "image",
        ".bmp": "image",
        ".tiff": "image",
        ".txt": "text",
        ".md": "text",
        ".json": "text",
        ".xml": "text",
        ".html": "text",
    }
    
    return type_mapping.get(ext, "unknown")


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: File size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB", "256 KB")
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def validate_file_access(file_path: str) -> tuple[bool, str]:
    """
    Validate that a file exists and is accessible.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Tuple of (is_valid, message)
    """
    path = Path(file_path)
    
    if not path.exists():
        return False, f"File does not exist: {file_path}"
    
    if not path.is_file():
        return False, f"Path is not a file: {file_path}"
    
    if not os.access(file_path, os.R_OK):
        return False, f"File is not readable: {file_path}"
    
    return True, "File is accessible"


def get_document_summary(file_path: str) -> Dict[str, Any]:
    """
    Get a summary of document information.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Dictionary with document summary
    """
    info = get_file_info(file_path)
    
    if "error" in info:
        return info
    
    doc_type = detect_document_type(file_path)
    
    summary = {
        "file_info": info,
        "document_type": doc_type,
        "can_process": doc_type != "unknown",
    }
    
    # Add type-specific metadata
    if doc_type == "pdf":
        summary["suggested_handler"] = "PDFHandler"
    elif doc_type == "excel":
        summary["suggested_handler"] = "ExcelHandler"
    elif doc_type == "csv":
        summary["suggested_handler"] = "CSVHandler"
    elif doc_type == "image":
        summary["suggested_handler"] = "ImageHandler"
    elif doc_type == "text":
        summary["suggested_handler"] = "TextHandler"
    else:
        summary["suggested_handler"] = None
        summary["warning"] = f"Unsupported document type: {file_path}"
    
    return summary


# Example usage for testing
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        summary = get_document_summary(file_path)
        
        print("Document Summary:")
        print(f"  Type: {summary.get('document_type', 'unknown')}")
        print(f"  Size: {summary.get('file_info', {}).get('size_mb', 'N/A')} MB")
        print(f"  Handler: {summary.get('suggested_handler', 'None')}")
        print(f"  Can Process: {summary.get('can_process', False)}")
    else:
        print("Usage: python document_helper.py <file_path>")
