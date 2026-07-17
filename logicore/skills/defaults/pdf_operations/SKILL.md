---
name: pdf_operations
description: "Use this skill whenever the user wants to create, read, merge, split, or modify PDF files. Triggers: any mention of 'PDF', 'merge PDFs', 'split PDF', 'extract text from PDF', 'watermark', 'PDF file', or requests to produce documents in PDF format. Also use for converting HTML/web content to PDF, adding stamps, rotating pages, or combining multiple PDFs. Do NOT use for Word docs, spreadsheets, or general document tasks not involving PDF."
version: "1.0.0"
author: Logicore
tags: [pdf, document, merge, split, extract, watermark]
trigger: create pdf, read pdf, merge pdf, split pdf, pdf operations, extract text from pdf
cost_tier: low
requires: []
conflicts_with: []
---

# PDF creation, editing, and manipulation

Choose your approach by task:

| Task | Approach |
|---|---|
| **Create** from text/code | `pdf-lib` (npm) — see gotchas below |
| **Create** from HTML/web | `puppeteer` (npm) — renders web pages to PDF |
| **Create** from data/template | `pdfmake` (npm) — declarative JSON → PDF |
| **Read** / extract text | `pypdf` (pip) or `pdf-parse` (npm) |
| **Merge** PDFs | `pdf-lib` (npm) or `pypdf` (pip) |
| **Split** PDFs | `pypdf` (pip) or `pdf-lib` (npm) |
| **Edit** existing PDF | `pdf-lib` (npm) — insert text, images, pages |
| **Watermark / stamp** | `pdf-lib` (npm) or `reportlab` (pip) |

## Creating with pdf-lib — gotchas

`pdf-lib` is preinstalled — do not run `npm install` first; write the script and `require('pdf-lib')` directly. Only if this require fails: `npm install pdf-lib`. These are the footguns:

- **Coordinates start from bottom-left** — `y` increases upward, unlike screen coords. `(72, height-72)` is 1″ from top-left.
- **No automatic text wrapping** — `drawText` places a single line. Use `wrapText` or manually split lines.
- **Font embedding is required** — `StandardFonts.Helvetica` works without embedding, but custom fonts need `doc.embedFont(fontBytes)`.
- **Page size in points** — 72 pts = 1″. A4 = 595×842 pts, Letter = 612×792 pts.
- **Existing PDFs need `PDFDocument.load()`** — not `PDFDocument.create()`.
- **Images need embedded format** — `doc.embedPng(bytes)` or `doc.embedJpg(bytes)`. No raw file paths.
- **Save returns bytes** — `const bytes = await doc.save(); fs.writeFileSync(path, bytes)`.
- **RGB colors use 0-1 range** — `rgb(0.18, 0.33, 0.59)` not hex or 0-255.
- **For multi-page:** manually manage page breaks by checking `y < margin` and calling `doc.addPage()`.

### Minimal create example

```javascript
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import fs from "fs";

const doc = await PDFDocument.create();
const font = await doc.embedFont(StandardFonts.Helvetica);
const boldFont = await doc.embedFont(StandardFonts.HelveticaBold);

const page = doc.addPage();
const { width, height } = page.getSize();

page.drawText("Hello World", {
  x: 72, y: height - 72,
  size: 28, font: boldFont, color: rgb(0.18, 0.33, 0.59),
});

page.drawText("Body text here.", {
  x: 72, y: height - 120,
  size: 11, font, color: rgb(0, 0, 0),
});

const bytes = await doc.save();
fs.writeFileSync("output.pdf", bytes);
```

### Merge PDFs

```javascript
import { PDFDocument } from "pdf-lib";
import fs from "fs";

async function mergePdfs(paths, outputPath) {
  const merged = await PDFDocument.create();
  for (const path of paths) {
    const pdf = await PDFDocument.load(fs.readFileSync(path));
    const pages = await merged.copyPages(pdf, pdf.getPageIndices());
    pages.forEach((page) => merged.addPage(page));
  }
  fs.writeFileSync(outputPath, await merged.save());
}
```

### Python fallback — reportlab

Use when pdf-lib is unavailable or for complex layouts:

```python
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch

doc = SimpleDocTemplate("output.pdf", pagesize=A4)
styles = getSampleStyleSheet()
story = [
    Paragraph("Report Title", styles["Title"]),
    Spacer(1, 12),
    Paragraph("Body text here.", styles["Normal"]),
]
doc.build(story)
```

### Read / extract text

```python
from pypdf import PdfReader

reader = PdfReader("input.pdf")
for page in reader.pages:
    print(page.extract_text())
```

```javascript
import pdfParse from "pdf-parse";
import fs from "fs";

const data = await pdfParse(fs.readFileSync("input.pdf"));
console.log(data.text);
```

## Verify the output

```bash
# Check file exists and is valid PDF
python -c "
import os
assert os.path.exists('output.pdf'), 'File not found'
with open('output.pdf', 'rb') as f:
    header = f.read(5)
    assert header == b'%PDF-', 'Not a valid PDF'
print('Valid PDF')
"

# Page count check
python -c "
from pypdf import PdfReader
r = PdfReader('output.pdf')
print(f'{len(r.pages)} pages')
"
```

## Dependencies

`pdf-lib` (npm, preinstalled) · `pypdf` (pip, read/merge/split) · `reportlab` (pip, fallback) · `puppeteer` (npm, HTML→PDF)
