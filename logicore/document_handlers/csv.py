import csv
import os
from .base import BaseDocumentHandler

class CSVHandler(BaseDocumentHandler):
    """Handler for CSV files."""
    
    def load(self) -> None:
        """Load document. CSV is read on-demand, so checks existence."""
        import os
        if not os.path.exists(self.file_path):
             raise FileNotFoundError(f"File not found: {self.file_path}")

    def get_text(self) -> str:
        """Return raw text content."""
        with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
            
    def get_metadata(self) -> dict:
        """Return basic file metadata."""
        import os
        from datetime import datetime
        
        stat = os.stat(self.file_path)
        return {
            "file_name": os.path.basename(self.file_path),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        }
        
    def to_markdown(self) -> str:
        """Convert CSV to Markdown table."""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                
            if not rows:
                return "*Empty CSV file*"
                
            # Header
            header = rows[0]
            md_table = f"| {' | '.join(header)} |\n"
            md_table += f"| {' | '.join(['---'] * len(header))} |\n"
            
            # Rows (limit to first 50 rows to avoid massive context for now)
            # Or should we include all? The user mentioned "real time data files", so maybe all is better.
            # But for large CSVs, Markdown is bad.
            # Let's verify size. If it's massive, maybe truncate.
            # For now, let's include all.
            for row in rows[1:]:
                # Handle possible mismatch in column count
                # Pad with empty strings if row is shorter
                padded_row = row + [''] * (len(header) - len(row))
                # If row is longer, truncate (or just join)
                final_row = padded_row[:len(header)]
                
                md_table += f"| {' | '.join(final_row)} |\n"
                
            return f"# Document: {os.path.basename(self.file_path)}\n\n{md_table}"
            
        except Exception as e:
            # Fallback to plain text if CSV parsing fails
            return f"# Document: {os.path.basename(self.file_path)}\n\n```csv\n{self.get_text()}\n```"
