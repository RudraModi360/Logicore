---
name: word_operations
description: "Use this skill whenever the user wants to create, read, edit, or manipulate Word documents (.docx files). Triggers: any mention of 'Word doc', 'word document', '.docx', reports, proposals, letters, resumes, or requests to produce professional documents with formatting like tables of contents, headings, page numbers, or letterheads. Also use when extracting content from .docx files, inserting images, find-and-replace, or converting content into a polished Word document. Do NOT use for PDFs, spreadsheets, or general coding tasks."
version: "1.0.0"
author: Logicore
tags: [word, docx, document, formatting, tables, styles]
trigger: create word document, edit docx, modify word file, write document, docx file
cost_tier: low
requires: []
conflicts_with: []
---

# DOCX creation, editing, and analysis

A `.docx` is a ZIP archive of XML files. Choose your approach by task:

| Task | Approach |
|---|---|
| **Create** a new document | Write a `docx` (npm) script — see gotchas below |
| **Edit** an existing document | `unzip` → edit `word/document.xml` → `zip` (docx-js cannot open existing files) |
| **Read** content | `pandoc -t markdown file.docx` or `python-docx` extraction |

## Creating with docx-js — gotchas

`docx` is preinstalled — do not run `npm install` first; write the script and `require('docx')` directly. Only if this require fails: `npm install docx`. The model knows the API; these are the footguns:

- **Page size defaults to A4.** For US Letter set `page: { size: { width: 12240, height: 15840 } }` (DXA; 1440 = 1″).
- **Landscape:** pass portrait dimensions and `orientation: PageOrientation.LANDSCAPE` — docx-js swaps width/height internally.
- **Tables need dual widths:** set `columnWidths` on the table AND `width` on every cell, both in `WidthType.DXA` (PERCENTAGE breaks in Google Docs). Column widths must sum to the table width.
- **Table shading:** use `ShadingType.CLEAR`, never `SOLID` (renders black).
- **Lists:** never insert `•` literally; use a `numbering` config with `LevelFormat.BULLET`.
- **`ImageRun` requires `type:`** (`"png"`, `"jpg"`, …).
- **`PageBreak` must be inside a `Paragraph`.**
- **Never use `\n`** — use separate `Paragraph` elements.
- **TOC:** headings must use built-in `HeadingLevel.*`; custom heading styles need `outlineLevel` set or they won't appear.
- **Don't use a table as a horizontal rule** — use a paragraph bottom border instead.
- **Dot-leader / right-aligned-on-same-line:** use `PositionalTab` (`alignment: PositionalTabAlignment.RIGHT`, `leader: PositionalTabLeader.DOT`) inside a `TextRun`, not literal `.` or space padding.

### Minimal create example

```javascript
import { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
         Table, TableRow, TableCell, WidthType, ShadingType } from "docx";
import fs from "fs";

const doc = new Document({
  sections: [{
    children: [
      new Paragraph({
        children: [new TextRun({ text: "Title", bold: true, size: 56 })],
        heading: HeadingLevel.TITLE,
        alignment: AlignmentType.CENTER,
      }),
      new Paragraph({
        children: [new TextRun({ text: "Body text here", size: 22 })],
        spacing: { after: 200 },
      }),
    ],
  }],
});

const buffer = await Packer.toBuffer(doc);
fs.writeFileSync("output.docx", buffer);
```

### Python fallback — python-docx

Use when docx-js is unavailable or for template-driven documents:

```python
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
title = doc.add_heading("Report Title", level=0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER

doc.add_paragraph("Body text here.")
doc.add_heading("Section 1", level=1)
doc.add_paragraph("Section content.")

doc.save("output.docx")
```

## Editing existing documents

Legacy `.doc` files must be converted first: `libreoffice --headless --convert-to docx file.doc`.

```bash
unzip -q doc.docx -d unpacked/
# Edit unpacked/word/document.xml in place — do NOT reformat or pretty-print
(cd unpacked && rm -f ../out.docx && zip -Xr ../out.docx .)
```

**Key:** Word splits text across many `<w:r>` runs. A phrase you see in the document may not exist as a contiguous string in XML. Use `grep` to find the right runs, then edit precisely.

**Tracked changes:** wrap runs in `<w:ins>`/`<w:del>` with `w:id`, `w:author`, `w:date` attributes. Inside `<w:del>`, use `<w:delText>` instead of `<w:t>`.

## Verify the output

After writing a `.docx`, validate it:

```bash
# Check file exists and is valid ZIP
python -c "import zipfile; zipfile.ZipFile('output.docx').testzip()"

# Convert to PDF for visual inspection (optional)
libreoffice --headless --convert-to pdf output.docx
```

## Dependencies

`docx` (npm, preinstalled) · `python-docx` (pip, fallback) · `pandoc` (read/convert) · `libreoffice` (convert legacy .doc)
