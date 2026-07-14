# Example: Merged Research PDF

## Input
"Merge these 3 research paper PDFs into one document"

## Strategy
Use skill tool (simple merge operation).

## Approach
```
1. merge_pdfs(file_paths=["paper1.pdf", "paper2.pdf", "paper3.pdf"], output_path="merged_research.pdf")
2. get_pdf_info(output_path) — verify page count
```

## Expected Output
A single PDF with all 3 papers combined, page count = sum of all inputs.
