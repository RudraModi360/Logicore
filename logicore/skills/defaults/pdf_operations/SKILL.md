---
name: pdf_operations
description: Expert guidance for creating, reading, merging, and modifying PDF files across any programming language. Teaches the agent how to reason about PDF operations, select the best language/library, and produce professional output.
version: "1.0.0"
author: Logicore
tags: [pdf, document, merge, split, extract, watermark, polyglot]
trigger: create pdf, read pdf, merge pdf, split pdf, pdf operations, extract text from pdf, pdf file
cost_tier: low
requires: []
conflicts_with: []
---

# PDF Operations Skill

## Purpose

Guide the agent to produce well-formatted, professional PDF documents. This skill teaches reasoning about PDF creation and manipulation across **any programming language** — not just Python. The agent selects the best language and library based on the task, available runtimes, and output quality requirements.

## When to Activate

- User asks to create, read, merge, split, or modify PDF files
- User mentions "PDF", "merge PDFs", "extract text from PDF"
- User needs document output in PDF format

## Reasoning Process

1. **Understand the PDF task** — Create new, read existing, merge, split, modify?
2. **Assess complexity** — Simple text PDF or professionally formatted document?
3. **Select language and library** — Which runtime and library best fit this task?
4. **Generate code** — Write complete, tested script in the chosen language
5. **Execute and validate** — Run, verify output, iterate

## Language Selection Guide

**Choose the language first, then the library.** The best output comes from picking the right tool for the job — not defaulting to one language.

| Scenario | Language | Library | When to Use |
|----------|----------|---------|-------------|
| Default / rapid prototyping | Python | reportlab | Mature, full layout control |
| Simple text PDF | Python | fpdf2 / pypdf | Minimal, fast |
| HTML/web content → PDF | JavaScript/TypeScript | puppeteer / pdf-lib | Web-styled documents, same runtime as web apps |
| Template-based (invoices, reports) | Python | Jinja2 + reportlab | Repetitive, data-driven documents |
| PDF reading / extraction | Python | pypdf | Text extraction, metadata |
| Server-side / microservice | Go | go-pdf / pdfcpu | Compiled binary, no runtime deps |
| JVM ecosystem | Java | iText / OpenPDF | Industry standard for Java shops |
| Image-to-PDF | Python | pypdf / Pillow | Scanning, archiving |
| Watermark / stamp | Python | pypdf / reportlab | Batch processing |

### Decision Rules

1. **If the user's project is already in a language** — use that language's library (consistency > novelty).
2. **If the task is HTML-to-PDF** — prefer JavaScript (puppeteer) for accurate web rendering.
3. **If the task is template-driven** — prefer Python (Jinja2 + reportlab) or JavaScript (handlebars + pdfmake).
4. **If performance or deployment matters** — prefer Go (pdfcpu) or Java (iText).
5. **If the user specifies a language** — always honour the request.

## Capability Selection Guide

| Task Complexity | Strategy | Why |
|----------------|----------|-----|
| Simple text PDF | Skill tool: `create_pdf_from_text` | Quick, no dependencies |
| Formatted report as PDF | Generated code + PDF library | Full layout control |
| Merge/split existing PDFs | Skill tools: `merge_pdfs`, `split_pdf` | Direct operations |
| PDF from HTML/web content | Generated JavaScript + puppeteer | Web rendering |
| Complex branded PDF | Generated code + PDF library + templates | Template-based |

## PDF Creation Approaches

### When to Use Each Approach

| Approach | Best Libraries | Best For |
|----------|---------------|----------|
| Text-to-PDF | pypdf, fpdf2, pdf-lib | Simple text documents |
| Rich formatting | reportlab, iText, pdfmake | Complex layouts, charts, images |
| HTML-to-PDF | puppeteer, weasyprint, pdfmake | Web-styled documents |
| Template-based | Jinja2 + reportlab, handlebars + pdfmake | Invoices, reports, forms |
| Image-to-PDF | pypdf, Pillow, pdfcpu | Scanning, archiving |

## Execution Strategies

### Strategy A: Use Skill Tool
For simple text PDFs or basic merge/split operations.
```
Call: create_pdf_from_text(file_path, text, title)
Call: merge_pdfs(file_paths, output_path)
Call: split_pdf(file_path, output_dir)
```

### Strategy B: Generate Code
For formatted, designed PDFs. Pick the language that fits the context.

#### Python — reportlab
```python
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from pathlib import Path

def create_professional_pdf(output_path, content):
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=72
    )

    styles = getSampleStyleSheet()
    custom_styles = {
        "title": ParagraphStyle("CustomTitle", parent=styles["Title"],
            fontSize=28, spaceAfter=30, textColor=colors.HexColor("#2F5496")),
        "heading": ParagraphStyle("CustomHeading", parent=styles["Heading1"],
            fontSize=18, spaceAfter=12, textColor=colors.HexColor("#1F3864")),
        "body": ParagraphStyle("CustomBody", parent=styles["Normal"],
            fontSize=11, leading=16, spaceAfter=8),
    }

    story = []
    story.append(Paragraph(content["title"], custom_styles["title"]))
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2F5496")))
    story.append(Spacer(1, 24))

    for section in content["sections"]:
        story.append(Paragraph(section["title"], custom_styles["heading"]))
        for para in section.get("paragraphs", []):
            story.append(Paragraph(para, custom_styles["body"]))
            story.append(Spacer(1, 8))

        if "table" in section:
            table = Table(section["table"])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
            ]))
            story.append(table)
            story.append(Spacer(1, 12))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    doc.build(story)
```

#### JavaScript/TypeScript — pdf-lib
```javascript
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import fs from "fs";

async function createPdf(outputPath, content) {
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);
  const boldFont = await doc.embedFont(StandardFonts.HelveticaBold);

  let page = doc.addPage();
  const { width, height } = page.getSize();

  // Title
  page.drawText(content.title, {
    x: 72, y: height - 72,
    size: 28, font: boldFont, color: rgb(0.18, 0.33, 0.59),
  });

  let y = height - 120;

  // Sections
  for (const section of content.sections) {
    if (y < 100) {
      page = doc.addPage();
      y = height - 72;
    }

    page.drawText(section.title, {
      x: 72, y, size: 18, font: boldFont, color: rgb(0.12, 0.22, 0.39),
    });
    y -= 30;

    for (const para of section.paragraphs) {
      if (y < 72) {
        page = doc.addPage();
        y = height - 72;
      }
      page.drawText(para, {
        x: 72, y, size: 11, font, color: rgb(0, 0, 0),
        maxWidth: width - 144,
      });
      y -= 20;
    }
  }

  const bytes = await doc.save();
  fs.writeFileSync(outputPath, bytes);
}
```

#### Go — pdfcpu
```go
package main

import (
    "github.com/pdfcpu/pdfcpu/pkg/api"
)

func mergePdfs(inputPaths []string, outputPath string) error {
    return api.MergeCreateFile(inputPaths, outputPath, false, nil)
}

func splitPdf(inputPath string, outputDir string) error {
    _, _, err := api.SplitFile(inputPath, outputDir, 1, nil)
    return err
}
```

### Strategy C: Hybrid
For tasks that combine simple operations with complex parts.

## Quality Checklist

- [ ] PDF has proper title and metadata
- [ ] Text is readable (appropriate font size, 10-12pt)
- [ ] Headers are clearly distinguished from body
- [ ] Tables are properly formatted with borders
- [ ] Images are high-resolution and properly sized
- [ ] Page numbers are present
- [ ] Margins are appropriate (not too narrow/wide)
- [ ] No text overlapping or cut off
- [ ] File size is reasonable
- [ ] PDF opens without errors

## Common Pitfalls

- Using reportlab without proper page sizing
- Text overflow (content extending beyond margins)
- Low-resolution images that look blurry
- Missing page numbers in multi-page documents
- Not handling page breaks properly
- Forgetting to set PDF metadata (title, author)
- Tables that are too wide for the page

## Validation Process

1. Verify file exists and is valid PDF
2. Check page count matches expectations
3. Verify text content is complete
4. Confirm formatting matches requirements
5. Check file size is reasonable
6. If unsatisfied, identify specific issues and regenerate
