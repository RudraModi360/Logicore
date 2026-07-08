import csv
import os
from .base import BaseDocumentHandler

# Maximum rows for markdown output to prevent context explosion
MAX_MARKDOWN_ROWS = 100

class CSVHandler(BaseDocumentHandler):
    """Handler for CSV files."""
    
    def load(self) -> None:
        """Load document. CSV is read on-demand, so checks existence."""
        if not os.path.exists(self.file_path):
             raise FileNotFoundError(f"File not found: {self.file_path}")

    def get_text(self) -> str:
        """Return raw text content."""
        with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
            
    def get_metadata(self) -> dict:
        """Return basic file metadata."""
        from datetime import datetime
        
        stat = os.stat(self.file_path)
        return {
            "file_name": os.path.basename(self.file_path),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        }
        
    def to_markdown(self) -> str:
        """Convert CSV to Markdown table with row limit for large files."""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                
            if not rows:
                return "*Empty CSV file*"
                
            header = rows[0]
            col_count = len(header)
            total_data_rows = len(rows) - 1
            
            md_table = f"| {' | '.join(header)} |\n"
            md_table += f"| {' | '.join(['---'] * col_count)} |\n"
            
            display_rows = rows[1:MAX_MARKDOWN_ROWS + 1]
            
            for row in display_rows:
                padded_row = row + [''] * (col_count - len(row))
                final_row = padded_row[:col_count]
                md_table += f"| {' | '.join(final_row)} |\n"
            
            result = f"# Document: {os.path.basename(self.file_path)}\n\n"
            
            if total_data_rows > MAX_MARKDOWN_ROWS:
                result += f"*Showing first {MAX_MARKDOWN_ROWS} of {total_data_rows} rows*\n\n"
            
            result += md_table
            return result
            
        except Exception as e:
            return f"# Document: {os.path.basename(self.file_path)}\n\n```csv\n{self.get_text()[:10000]}\n```"
