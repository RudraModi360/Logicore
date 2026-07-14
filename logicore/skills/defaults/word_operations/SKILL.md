---
name: word_operations
description: Expert guidance for creating, reading, and modifying Word documents across any programming language. Teaches the agent how to reason about document structure, formatting, and professional writing — and select the best language/library for the task.
version: "1.0.0"
author: Logicore
tags: [word, docx, document, formatting, tables, styles, polyglot]
trigger: create word document, edit docx, modify word file, write document, word operations, docx file
cost_tier: low
requires: []
conflicts_with: []
---

# Word Operations Skill

## Purpose

Guide the agent to produce well-structured, professionally formatted Word documents for any complexity level. This skill teaches reasoning about document creation across **any programming language** — not just Python. The agent selects the best language and library based on the task, available runtimes, and output quality requirements.

## When to Activate

- User asks to create, read, edit, or format Word documents
- User mentions "Word", "DOCX", "document", "report", "proposal"
- User needs formatted text output with tables, headings, images

## Reasoning Process

1. **Understand the document type** — Report, proposal, letter, manual, resume?
2. **Plan the structure** — What sections? What heading hierarchy?
3. **Determine formatting** — What styles, fonts, spacing are appropriate?
4. **Select language and library** — Which runtime and library best fit this task?
5. **Generate and execute**
6. **Validate against checklist**

## Language Selection Guide

**Choose the language first, then the library.** The best output comes from picking the right tool for the job — not defaulting to one language.

| Scenario | Language | Library | Why |
|----------|----------|---------|-----|
| Default / rapid prototyping | Python | python-docx | Mature, well-documented, fast iteration |
| Web-integrated workflows | JavaScript/TypeScript | docx (npm) | Same language as the app, async-friendly |
| Server-side API / microservice | Go | unioffice | Compiled binary, no runtime deps, fast |
| Enterprise / JVM ecosystem | Java | Apache POI | Industry standard for Java shops |
| Complex templating | Python | python-docx + Jinja2 | Template-driven repetitive documents |
| Cross-platform CLI tool | Rust | docx-rs | Memory-safe, single binary output |

### Decision Rules

1. **If the user's project is already in a language** — use that language's library (consistency > novelty).
2. **If the task is standalone** — prefer Python (python-docx) or JavaScript (docx npm) for speed.
3. **If performance or deployment matters** — prefer Go (unioffice) or Rust (docx-rs).
4. **If the user specifies a language** — always honour the request.

## Capability Selection Guide

| Task Complexity | Strategy | Why |
|----------------|----------|-----|
| Simple text document | Skill tool: `create_document` + `add_paragraph` | Fast, minimal code |
| Formatted report with headings | Generated code + document library | Style control |
| Document with tables and images | Generated code + document library | Complex layout |
| Template-based documents | Generated code + templating engine | Repetitive content |
| Multi-section manual | Generated script | Complex structure |

## Execution Strategies

### Strategy A: Use Skill Tool
For simple text documents without complex formatting.
```
Call: create_document(file_path, title)
Call: add_paragraph(file_path, text, style)
Call: add_heading(file_path, text, level)
```

### Strategy B: Generate Code
For formatted, structured documents. Pick the language that fits the context.

#### Python — python-docx
```python
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

def create_professional_report(output_path, content):
    doc = Document()

    title = doc.add_heading(content["title"], level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(f"Author: {content['author']}\nDate: {content['date']}")
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()

    for section in content["sections"]:
        doc.add_heading(section["title"], level=1)
        for para in section["paragraphs"]:
            p = doc.add_paragraph(para)
            p.style.font.size = Pt(11)

        if "table" in section:
            table_data = section["table"]
            table = doc.add_table(
                rows=len(table_data["rows"]) + 1,
                cols=len(table_data["headers"])
            )
            for i, header in enumerate(table_data["headers"]):
                cell = table.rows[0].cells[i]
                cell.text = header
                cell.paragraphs[0].runs[0].bold = True
            for r, row in enumerate(table_data["rows"], 1):
                for c, val in enumerate(row):
                    table.rows[r].cells[c].text = str(val)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
```

#### JavaScript/TypeScript — docx (npm)
```javascript
import { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType } from "docx";
import fs from "fs";

async function createReport(outputPath, content) {
  const children = [];

  // Title
  children.push(new Paragraph({
    children: [new TextRun({ text: content.title, bold: true, size: 56 })],
    heading: HeadingLevel.TITLE,
    alignment: AlignmentType.CENTER,
  }));

  // Sections
  for (const section of content.sections) {
    children.push(new Paragraph({
      children: [new TextRun({ text: section.title, bold: true, size: 36 })],
      heading: HeadingLevel.HEADING_1,
    }));

    for (const para of section.paragraphs) {
      children.push(new Paragraph({
        children: [new TextRun({ text: para, size: 22 })],
        spacing: { after: 200 },
      }));
    }
  }

  const doc = new Document({ sections: [{ children }] });
  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);
}
```

#### Go — unioffice
```go
package main

import (
    "github.com/unidoc/unioffice/document"
    "github.com/unidoc/unioffice/schema/soo/wml"
)

func createReport(outputPath string, title string, sections []Section) error {
    doc := document.NewDocument()
    defer doc.Close()

    // Title
    para := doc.AddParagraph()
    para.Properties().AddRun().SetText(title)
    para.Properties().SetStyle(document.StyleIDHeading1)

    // Sections
    for _, section := range sections {
        p := doc.AddParagraph()
        p.Properties().AddRun().SetText(section.Title)
        p.Properties().SetStyle(document.StyleIDHeading2)

        for _, text := range section.Paragraphs {
            cp := doc.AddParagraph()
            cp.Properties().AddRun().SetText(text)
        }
    }

    return doc.SaveToFile(outputPath)
}
```

### Strategy C: Hybrid
Basic structure via skill tools + complex formatting via generated code in any language.

## Document Structure Guidelines

### Heading Hierarchy
```
Heading 1: Document Title
  Heading 2: Major Section
    Heading 3: Subsection
      Heading 4: Detailed topic
```

### Typical Document Sections
1. **Title Page** — Title, author, date, version
2. **Table of Contents** — Auto-generated from headings
3. **Executive Summary** — Brief overview (1 paragraph)
4. **Introduction** — Context and purpose
5. **Body** — Main content with subsections
6. **Conclusion** — Summary and next steps
7. **References** — Citations and sources
8. **Appendices** — Supporting materials

### Formatting Standards
| Element | Font | Size | Style |
|---------|------|------|-------|
| Title | Calibri | 28pt | Bold |
| Heading 1 | Calibri | 18pt | Bold |
| Heading 2 | Calibri | 14pt | Bold |
| Body | Calibri | 11pt | Regular |
| Caption | Calibri | 10pt | Italic |
| Code | Consolas | 10pt | Monospace |

## Quality Checklist

- [ ] Document has clear title and metadata
- [ ] Heading hierarchy is consistent (H1 → H2 → H3)
- [ ] Body text is 11pt Calibri (or specified font)
- [ ] Paragraphs have appropriate spacing
- [ ] Tables have headers and consistent formatting
- [ ] Images are properly sized and captioned
- [ ] Page margins are appropriate (1 inch default)
- [ ] Headers/footers are included if required
- [ ] Page numbers are present for multi-page docs
- [ ] No orphaned headings (heading at bottom of page with content on next)

## Common Pitfalls

- Using manual spacing instead of proper paragraph styles
- Inconsistent heading levels throughout document
- Tables without headers or with poor column widths
- Images that are too large or too small
- Not using page breaks between major sections
- Forgetting to set document metadata (title, author)
- Using return key for spacing instead of paragraph spacing

## Validation Process

1. Verify file exists and is valid DOCX
2. Check heading structure matches outline
3. Confirm formatting is consistent
4. Verify tables are properly formatted
5. Check page count is reasonable
6. If unsatisfied, identify specific issues and regenerate
