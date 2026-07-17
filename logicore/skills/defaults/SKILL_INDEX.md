# Skill Index
# This file is the agent's skill registry. Keep it concise.
# Full skill instructions are loaded on-demand via the load_skill tool.

## How to Use This Index

When the user's task involves Word, Excel, PowerPoint, or PDF files:
1. Read this index to find the relevant skill
2. **Call `load_skill(skill_name)` to load its full instructions** (REQUIRED before attempting the task)
3. Follow the loaded instructions exactly — they contain gotchas, code templates, and validation steps

**You MUST load the skill before attempting any document task.** Do not guess or improvise.

---

skills:
  - name: word_operations
    description: "Create, read, edit, or manipulate Word documents (.docx). Use for: reports, proposals, letters, resumes, any document with headings/tables/images."
    triggers:
      - word document, docx, .docx, report, proposal, letter, resume, template
    when_to_use: User asks to create, read, edit, or format Word documents. Mentions "Word", "DOCX", "document", "report", "proposal".
    path: word_operations/SKILL.md

  - name: excel_operations
    description: "Create, read, edit, or analyze Excel workbooks (.xlsx). Use for: data analysis, charts, spreadsheets, CSV processing, pivot tables."
    triggers:
      - excel, spreadsheet, xlsx, .xlsx, workbook, data analysis, csv, pivot table, chart
    when_to_use: User asks to create, read, edit, or analyze Excel files. Mentions "Excel", "spreadsheet", "XLSX", "workbook".
    path: excel_operations/SKILL.md

  - name: powerpoint_operations
    description: "Create or edit PowerPoint presentations (.pptx). Use for: slide decks, pitch decks, presentations, visual reports."
    triggers:
      - powerpoint, pptx, .pptx, presentation, slides, pitch deck, slide deck
    when_to_use: User asks to create, edit, or modify PowerPoint presentations. Mentions "PowerPoint", "PPT", "PPTX", "presentation", "slides".
    path: powerpoint_operations/SKILL.md

  - name: pdf_operations
    description: "Create, read, merge, split, or modify PDF files. Use for: reports, merging documents, extracting text, watermarks."
    triggers:
      - pdf, .pdf, merge pdf, split pdf, extract text from pdf, watermark
    when_to_use: User asks to create, read, merge, split, or modify PDF files. Mentions "PDF", "merge PDFs", "extract text from PDF".
    path: pdf_operations/SKILL.md
