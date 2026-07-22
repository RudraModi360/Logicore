---
name: powerpoint_operations
description: "Use this skill whenever the user wants to create, edit, or modify PowerPoint presentations (.pptx files). Triggers: any mention of 'PowerPoint', 'PPT', 'PPTX', 'presentation', 'slides', 'pitch deck', 'slide deck', or requests to present information visually. Do NOT use for Word docs, PDFs, or image-only tasks."
version: "1.2.0"
author: Logicore
tags: [powerpoint, pptx, presentation, slides, design]
trigger: create powerpoint, make presentation, edit pptx, modify slides, presentation
cost_tier: low
requires: []
conflicts_with: []
---

# PowerPoint (.pptx) creation and editing

A `.pptx` is a ZIP archive of XML files (like `.docx`). Choose your approach by task:

| Task | Approach |
|---|---|
| **Create** a new presentation | `pptxgenjs` (npm, recommended) — better design control |
| **Edit** an existing presentation | `python-pptx` (pip) — pptxgenjs cannot open existing files |
| **Read** content | `python-pptx` extraction or `unzip` → parse XML |

## Creating with pptxgenjs (Recommended)

`pptxgenjs` is preinstalled in the project. Use `require('pptxgenjs')` directly.

**IMPORTANT:** Use `require()` not `import` — the bash tool runs JavaScript via Node.js temp files.

### Two ways to run JavaScript

**Option 1: `node -e` (recommended for inline code)**
```bash
node -e "const PptxGenJS = require('pptxgenjs'); const pptx = new PptxGenJS(); pptx.layout = 'LAYOUT_WIDE'; pptx.addSlide().addText('Hello'); pptx.writeFile({fileName: 'output.pptx'}).then(() => console.log('Done'));"
```

**Option 2: Write to file and execute**
```bash
cat > script.js << 'EOF'
const PptxGenJS = require("pptxgenjs");
const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";
pptx.addSlide().addText("Hello");
await pptx.writeFile({ fileName: "output.pptx" });
console.log("Done");
EOF
node script.js
```

### Gotchas
- **Layout must be set first** — `pptx.layout = "LAYOUT_WIDE"` (13.33×7.5″) or `LAYOUT_16x9` before adding slides.
- **Slide dimensions are in inches** — `x`, `y`, `w`, `h` all in inches, not pixels or EMU.
- **Text boxes need explicit positioning** — no auto-layout. Always set `x`, `y`, `w`, `h`.
- **Bullets need array format** — `slide.addText([{ text: "Point", options: { bullet: true } }], { ... })`.
- **Images need base64 or file path** — `slide.addImage({ path: "logo.png" })` or `data: "image/png;base64,..."`.
- **Charts require structured data** — `chartData` is an array of `{ name, labels, values }` objects.
- **Colors are hex strings without `#`** — `"2F5496"` not `"#2F5496"` or `0x2F5496`.
- **Speaker notes:** `slide.addNotes("Speaker notes text")`.
- **Write is async** — `await pptx.writeFile({ fileName: "output.pptx" })`.

### Minimal create example

```javascript
const PptxGenJS = require("pptxgenjs");

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";

const PRIMARY = "2F5496";

// Title slide
const titleSlide = pptx.addSlide();
titleSlide.addText("Presentation Title", {
  x: 0.5, y: 1.5, w: "90%", h: 2,
  fontSize: 44, fontFace: "Calibri", color: "FFFFFF",
  bold: true, align: "center",
  fill: { color: PRIMARY },
});

// Content slide
const contentSlide = pptx.addSlide();
contentSlide.addText("Key Points", {
  x: 0.5, y: 0.3, w: "90%", h: 1,
  fontSize: 32, fontFace: "Calibri", color: PRIMARY, bold: true,
});
contentSlide.addText(
  [
    { text: "First point", options: { bullet: true, fontSize: 20 } },
    { text: "Second point", options: { bullet: true, fontSize: 20 } },
    { text: "Third point", options: { bullet: true, fontSize: 20 } },
  ],
  { x: 0.5, y: 1.5, w: "85%", h: 4.5, fontFace: "Calibri", color: "333333" }
);
contentSlide.addNotes("Speaker notes for this slide.");

await pptx.writeFile({ fileName: "output.pptx" });
console.log("Presentation created: output.pptx");
```

### Advanced: Shapes, images, tables

```javascript
const PptxGenJS = require("pptxgenjs");

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";

const PRIMARY = "1A237E"; // Deep Indigo
const SECONDARY = "00BFA5"; // Teal

// Slide with shapes
const slide = pptx.addSlide();

// Add a colored rectangle
slide.addShape(pptx.ShapeType.rect, {
  x: 1, y: 1, w: 4, h: 2,
  fill: { color: PRIMARY },
});

// Add text
slide.addText("Custom Shape", {
  x: 1, y: 1, w: 4, h: 2,
  fontSize: 24, fontFace: "Calibri", color: "FFFFFF",
  align: "center", valign: "middle",
});

// Add image
// slide.addImage({ path: "logo.png", x: 6, y: 1, w: 5, h: 3 });

// Add table
const tableRows = [
  [{ text: "Header 1", options: { bold: true } }, { text: "Header 2", options: { bold: true } }],
  ["Row 1 Col 1", "Row 1 Col 2"],
  ["Row 2 Col 1", "Row 2 Col 2"],
];
slide.addTable(tableRows, {
  x: 1, y: 4, w: 10,
  border: { type: "solid", pt: 1, color: "CCCCCC" },
});

await pptx.writeFile({ fileName: "output.pptx" });
console.log("Created presentation with shapes and table");
```

## Python fallback — python-pptx

Use when editing existing presentations or if Node.js is unavailable:

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

# Title slide
slide = prs.slides.add_slide(prs.slide_layouts[0])
title = slide.shapes.title
title.text = "Presentation Title"
for para in title.text_frame.paragraphs:
    para.font.size = Pt(44)
    para.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    para.font.bold = True

# Content slide
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = "Key Points"
body = slide.placeholders[1]
tf = body.text_frame
tf.text = ""
for point in ["First point", "Second point", "Third point"]:
    p = tf.add_paragraph()
    p.text = point
    p.font.size = Pt(20)
    p.space_after = Pt(12)

prs.save("output.pptx")
print(f"Created presentation with {len(prs.slides)} slides")
```

## Editing existing presentations

```python
from pptx import Presentation

prs = Presentation("existing.pptx")
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if "old text" in run.text:
                        run.text = run.text.replace("old text", "new text")
prs.save("modified.pptx")
```

### Editing existing presentations

```python
from pptx import Presentation

prs = Presentation("existing.pptx")
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if "old text" in run.text:
                        run.text = run.text.replace("old text", "new text")
prs.save("modified.pptx")
```

## Verify the output

```bash
# Check file exists and is valid ZIP
python -c "import zipfile; zipfile.ZipFile('output.pptx').testzip()"

# Quick content check
python -c "
from pptx import Presentation
prs = Presentation('output.pptx')
print(f'{len(prs.slides)} slides')
for i, slide in enumerate(prs.slides, 1):
    title = slide.shapes.title.text if slide.shapes.title else '(no title)'
    print(f'  Slide {i}: {title}')
"
```

## Design quick-reference

| Element | Font size | Weight | Color |
|---|---|---|---|
| Title | 36-44pt | Bold | White on primary |
| Slide heading | 28-32pt | Bold | Primary color |
| Body text | 18-20pt | Regular | Dark gray |
| Captions | 14-16pt | Light | Medium gray |

**Rule of thumb:** max 6-8 lines of text per slide, one key message per slide.

## Dependencies

- **pptxgenjs** (npm, preinstalled) — for creating new presentations
- **python-pptx** (pip) — for editing existing presentations
