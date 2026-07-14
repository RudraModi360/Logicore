# Skill Index
# This file is the agent's skill registry. Keep it concise.
# Full skill instructions are loaded on-demand via the load_skill tool.

## How to Use This Index

When the user's task involves Word, Excel, PowerPoint, or PDF files:
1. Read this index to find the relevant skill
2. **Call `load_skill(skill_name)` to load its full instructions** (REQUIRED before attempting the task)
3. Follow the loaded instructions exactly — they contain code templates, library recommendations, and quality checks

**You MUST load the skill before attempting any document task.** Do not guess or improvise.

---

skills:
  - name: word_operations
    description: Create, read, edit, and format Word documents (DOCX) with professional styling.
    triggers:
      - word document, docx, word file, report, proposal, letter, manual, resume
    when_to_use: User asks to create, read, edit, or format Word documents. Mentions "Word", "DOCX", "document", "report", "proposal".
    capabilities:
      - Create documents with headings, tables, images
      - Read and extract content from DOCX files
      - Apply formatting (fonts, styles, margins)
      - Template-based document generation
    languages: [Python, JavaScript/TypeScript, Go, Java, Rust]
    path: word_operations/SKILL.md

  - name: excel_operations
    description: Create, read, edit, and analyze Excel workbooks (XLSX) with formatting and charts.
    triggers:
      - excel, spreadsheet, xlsx, workbook, data analysis, csv, pivot table
    when_to_use: User asks to create, read, edit, or analyze Excel files. Mentions "Excel", "spreadsheet", "XLSX", "workbook".
    capabilities:
      - Create workbooks with formatted headers and data
      - Read and extract sheet data
      - Apply cell formatting (colors, fonts, borders)
      - Charts and conditional formatting
      - Multi-sheet operations
    languages: [Python, JavaScript/TypeScript, Go, Java, Rust]
    path: excel_operations/SKILL.md

  - name: powerpoint_operations
    description: Create and edit professional PowerPoint presentations (PPTX) with themes and layouts.
    triggers:
      - powerpoint, pptx, presentation, slides, pitch deck, slideshare
    when_to_use: User asks to create, edit, or modify PowerPoint presentations. Mentions "PowerPoint", "PPT", "PPTX", "presentation", "slides".
    capabilities:
      - Create presentations with title and content slides
      - Apply themes, colors, and layouts
      - Add text boxes and shapes
      - Speaker notes
      - Slide duplication and deletion
    languages: [Python, JavaScript/TypeScript, Go, Java]
    path: powerpoint_operations/SKILL.md

  - name: pdf_operations
    description: Create, read, merge, split, and modify PDF files with professional formatting.
    triggers:
      - pdf, merge pdf, split pdf, extract text from pdf, watermark, pdf file
    when_to_use: User asks to create, read, merge, split, or modify PDF files. Mentions "PDF", "merge PDFs", "extract text from PDF".
    capabilities:
      - Create PDFs from text or HTML
      - Read and extract text from PDFs
      - Merge multiple PDFs into one
      - Split PDFs into individual pages
      - Add watermarks and rotate pages
    languages: [Python, JavaScript/TypeScript, Go, Java]
    path: pdf_operations/SKILL.md
