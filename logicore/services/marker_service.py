import subprocess
import os
import sys

class MarkerService:
    # Define output directory relative to CWD
    OUTPUT_DIR = os.path.abspath(os.path.join(os.getcwd(), "marker_outputs"))
    
    @classmethod
    def convert(cls, file_path: str) -> str:
        """
        Convert a PDF file to Markdown using the 'marker_single' CLI tool.
        Returns the content of the generated Markdown file.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")
            
        # Ensure output directory exists
        os.makedirs(cls.OUTPUT_DIR, exist_ok=True)
        
        # Build command - run directly
        cmd = [
            "marker_single",
            file_path,
            "--output_dir",
            cls.OUTPUT_DIR
        ]
        
        print(f"[MarkerService] Running conversion on {os.path.basename(file_path)}...")
        
        try:
            # Run command directly
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                error_msg = result.stderr.strip()
                print(f"[MarkerService] Error: {error_msg}")
                raise RuntimeError(f"Marker CLI Failed: {error_msg}")
            
            # Find output
            # Marker creates subdir: output_dir/filename_stem/filename_stem.md
            filename = os.path.basename(file_path)
            stem = os.path.splitext(filename)[0]
            
            # Construct expected path
            result_dir = os.path.join(cls.OUTPUT_DIR, stem)
            md_path = os.path.join(result_dir, f"{stem}.md")
            
            if os.path.exists(md_path):
                print(f"[MarkerService] Conversion successful. Reading {md_path}...")
                with open(md_path, "r", encoding="utf-8") as f:
                    return f.read()
            else:
                # Debugging: List output dir content if specific file missed
                err = f"Marker output file not found at {md_path}"
                print(f"[MarkerService] {err}")
                if os.path.exists(result_dir):
                     print(f"Contents of {result_dir}: {os.listdir(result_dir)}")
                raise FileNotFoundError(err)
                
        except Exception as e:
            # Reraise as RuntimeError for the handler to catch
            raise RuntimeError(f"Marker conversion failed: {e}")
