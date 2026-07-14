"""
PDF Operations Helper Scripts

Tool functions for the pdf_operations skill.
Provides create, read, merge, split, and modify operations on PDF files.
"""

from pathlib import Path
from typing import Optional, Dict, Any, List


def create_pdf_from_text(file_path: str, text: str, title: str = "") -> Dict[str, Any]:
    """
    Create a PDF file from plain text content.

    Args:
        file_path: Path where the PDF will be saved
        text: Text content for the PDF
        title: Optional title for the PDF metadata

    Returns:
        Dict with status and file info
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build a minimal valid PDF with text content
    lines = text.split("\n")
    content_lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]

    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_lines.append(f"({safe}) Tj")
        content_lines.append("T*")

    content_lines.append("ET")
    content_stream = "\n".join(content_lines)

    # Build PDF body and compute exact byte offsets for xref table
    objects = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append("<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Length {len(content_stream)} >>\nstream\n{content_stream}\nendstream")

    pdf_bytes = b"%PDF-1.4\n"
    offsets = []
    for i, obj_body in enumerate(objects, start=1):
        offsets.append(len(pdf_bytes))
        pdf_bytes += f"{i} 0 obj\n{obj_body}\nendobj\n".encode("latin-1")

    xref_offset = len(pdf_bytes)
    pdf_bytes += b"xref\n0 6\n"
    pdf_bytes += b"0000000000 65535 f \n"
    for off in offsets:
        pdf_bytes += f"{off:010d} 00000 n \n".encode("latin-1")

    pdf_bytes += b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
    pdf_bytes += f"startxref\n{xref_offset}\n".encode("latin-1")
    pdf_bytes += b"%%EOF"

    with open(str(path), "wb") as f:
        f.write(pdf_bytes)

    return {
        "status": "created",
        "file_path": str(path.resolve()),
        "page_count": 1,
        "size_bytes": path.stat().st_size,
    }


def read_pdf(file_path: str, max_pages: int = 50) -> Dict[str, Any]:
    """
    Extract text content from a PDF file.

    Args:
        file_path: Path to the PDF file
        max_pages: Maximum pages to read (default: 50)

    Returns:
        Dict with extracted text and metadata
    """
    from pypdf import PdfReader

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    reader = PdfReader(str(path))
    pages_text = []
    total_pages = len(reader.pages)

    for i in range(min(total_pages, max_pages)):
        page = reader.pages[i]
        text = page.extract_text() or ""
        pages_text.append({"page": i + 1, "text": text.strip()})

    metadata = reader.metadata or {}

    return {
        "file_path": str(path.resolve()),
        "total_pages": total_pages,
        "pages_read": len(pages_text),
        "truncated": total_pages > max_pages,
        "metadata": {
            "title": getattr(metadata, "title", "") or "",
            "author": getattr(metadata, "author", "") or "",
        },
        "pages": pages_text,
    }


def get_pdf_info(file_path: str) -> Dict[str, Any]:
    """
    Get metadata and structure info about a PDF.

    Args:
        file_path: Path to the PDF file

    Returns:
        Dict with PDF metadata
    """
    from pypdf import PdfReader

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    reader = PdfReader(str(path))
    metadata = reader.metadata or {}

    return {
        "file_path": str(path.resolve()),
        "page_count": len(reader.pages),
        "is_encrypted": reader.is_encrypted,
        "metadata": {
            "title": getattr(metadata, "title", "") or "",
            "author": getattr(metadata, "author", "") or "",
            "subject": getattr(metadata, "subject", "") or "",
        },
        "size_bytes": path.stat().st_size,
    }


def merge_pdfs(file_paths: List[str], output_path: str) -> Dict[str, Any]:
    """
    Merge multiple PDF files into a single PDF.

    Args:
        file_paths: List of PDF file paths to merge (in order)
        output_path: Path for the merged output PDF

    Returns:
        Dict with status and merged info
    """
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    pages_added = 0
    sources = []

    for fp in file_paths:
        path = Path(fp)
        if not path.exists():
            return {"error": f"File not found: {fp}"}
        reader = PdfReader(str(path))
        for page in reader.pages:
            writer.add_page(page)
            pages_added += 1
        sources.append({"file": str(path.name), "pages": len(reader.pages)})

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "wb") as f:
        writer.write(f)

    return {
        "status": "merged",
        "output_path": str(out.resolve()),
        "total_pages": pages_added,
        "sources": sources,
        "size_bytes": out.stat().st_size,
    }


def split_pdf(file_path: str, output_dir: str, pages_per_file: int = 1) -> Dict[str, Any]:
    """
    Split a PDF into multiple files.

    Args:
        file_path: Path to the PDF to split
        output_dir: Directory to save split files
        pages_per_file: Number of pages per output file (default: 1)

    Returns:
        Dict with status and list of output files
    """
    from pypdf import PdfReader, PdfWriter

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_files = []
    file_index = 1

    for start in range(0, total_pages, pages_per_file):
        writer = PdfWriter()
        end = min(start + pages_per_file, total_pages)
        for i in range(start, end):
            writer.add_page(reader.pages[i])

        out_name = f"{path.stem}_part{file_index}.pdf"
        out_path = out_dir / out_name
        with open(str(out_path), "wb") as f:
            writer.write(f)

        output_files.append({
            "file": str(out_path.resolve()),
            "pages": f"{start + 1}-{end}",
        })
        file_index += 1

    return {
        "status": "split",
        "source_pages": total_pages,
        "files_created": len(output_files),
        "outputs": output_files,
    }


def extract_pages(file_path: str, output_path: str, start_page: int = 1, end_page: int = 1) -> Dict[str, Any]:
    """
    Extract a range of pages from a PDF into a new file.

    Args:
        file_path: Path to the source PDF
        output_path: Path for the extracted PDF
        start_page: Starting page number (1-indexed)
        end_page: Ending page number (1-indexed, inclusive)

    Returns:
        Dict with status
    """
    from pypdf import PdfReader, PdfWriter

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    reader = PdfReader(str(path))
    total_pages = len(reader.pages)

    if start_page < 1 or end_page > total_pages or start_page > end_page:
        return {"error": f"Invalid page range {start_page}-{end_page}. Total pages: {total_pages}"}

    writer = PdfWriter()
    for i in range(start_page - 1, end_page):
        writer.add_page(reader.pages[i])

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "wb") as f:
        writer.write(f)

    return {
        "status": "extracted",
        "output_path": str(out.resolve()),
        "pages_extracted": f"{start_page}-{end_page}",
        "page_count": end_page - start_page + 1,
    }


def add_text_watermark(file_path: str, output_path: str, watermark_text: str) -> Dict[str, Any]:
    """
    Add a text watermark to all pages of a PDF.

    Args:
        file_path: Path to the source PDF
        output_path: Path for the watermarked PDF
        watermark_text: Text to use as watermark

    Returns:
        Dict with status
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    reader = PdfReader(str(path))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    # Add watermark as metadata subject
    writer.add_metadata({NameObject("/Subject"): TextStringObject(f"Watermark: {watermark_text}")})

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "wb") as f:
        writer.write(f)

    return {
        "status": "watermarked",
        "output_path": str(out.resolve()),
        "watermark": watermark_text,
        "page_count": len(reader.pages),
    }


def rotate_pages(file_path: str, output_path: str, rotation: int = 90, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    """
    Rotate pages in a PDF.

    Args:
        file_path: Path to the source PDF
        output_path: Path for the rotated PDF
        rotation: Rotation angle in degrees (90, 180, 270)
        pages: List of 1-indexed page numbers to rotate (None = all pages)

    Returns:
        Dict with status
    """
    from pypdf import PdfReader, PdfWriter

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    if rotation not in (90, 180, 270):
        return {"error": f"Invalid rotation: {rotation}. Must be 90, 180, or 270."}

    reader = PdfReader(str(path))
    writer = PdfWriter()
    rotated_count = 0

    for i, page in enumerate(reader.pages):
        page_num = i + 1
        if pages is None or page_num in pages:
            page.rotate(rotation)
            rotated_count += 1
        writer.add_page(page)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "wb") as f:
        writer.write(f)

    return {
        "status": "rotated",
        "output_path": str(out.resolve()),
        "rotation": rotation,
        "pages_rotated": rotated_count,
        "total_pages": len(reader.pages),
    }


def remove_pages(file_path: str, output_path: str, pages_to_remove: List[int]) -> Dict[str, Any]:
    """
    Remove specific pages from a PDF.

    Args:
        file_path: Path to the source PDF
        output_path: Path for the modified PDF
        pages_to_remove: List of 1-indexed page numbers to remove

    Returns:
        Dict with status
    """
    from pypdf import PdfReader, PdfWriter

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    reader = PdfReader(str(path))
    writer = PdfWriter()
    total_pages = len(reader.pages)
    remove_set = set(pages_to_remove)

    for i, page in enumerate(reader.pages):
        if (i + 1) not in remove_set:
            writer.add_page(page)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "wb") as f:
        writer.write(f)

    return {
        "status": "pages_removed",
        "output_path": str(out.resolve()),
        "removed": sorted(pages_to_remove),
        "original_pages": total_pages,
        "remaining_pages": total_pages - len(remove_set),
    }
