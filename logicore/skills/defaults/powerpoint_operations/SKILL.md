---
name: powerpoint_operations
description: Expert guidance for creating professional PowerPoint presentations across any programming language. Teaches the agent how to reason about presentation design, slide structure, visual storytelling, and select the best language/library for the task.
version: "1.0.0"
author: Logicore
tags: [powerpoint, pptx, presentation, slides, design, visual, polyglot]
trigger: create powerpoint, make presentation, edit pptx, modify slides, add slide, powerpoint file, presentation
cost_tier: low
requires: []
conflicts_with: []
---

# PowerPoint Operations Skill

## Purpose

Guide the agent to produce well-designed, visually compelling PowerPoint presentations. This skill teaches reasoning about presentation design across **any programming language** — not just Python. The agent selects the best language and library based on the task, available runtimes, and output quality requirements.

## When to Activate

- User asks to create, edit, or modify PowerPoint presentations
- User mentions "PowerPoint", "PPT", "PPTX", "presentation", "slides"
- User needs to present information visually

## Reasoning Process

When the user requests a presentation:

1. **Understand the audience** — Who will view this? What's their expertise level?
2. **Plan the narrative** — What story does the presentation tell?
3. **Design the structure** — How many sections? What's the slide flow?
4. **Select language and library** — Which runtime and library best fit this task?
5. **Generate code** — Write a complete script in the chosen language
6. **Execute and validate** — Run, review, iterate

## Language Selection Guide

**Choose the language first, then the library.** The best output comes from picking the right tool for the job — not defaulting to one language.

| Scenario | Language | Library | When to Use |
|----------|----------|---------|-------------|
| Default / rapid prototyping | Python | python-pptx | Mature, well-documented, fast iteration |
| Web integration / APIs | JavaScript/TypeScript | pptxgenjs | Same language as the app, async-friendly |
| Server-side / microservice | Go | unioffice | Compiled binary, no runtime deps |
| JVM ecosystem | Java | Apache POI | Industry standard for Java shops |
| CLI tool / embedded | Rust | pptx-builder | Memory-safe, single binary |
| Data-driven decks | Python | python-pptx + pandas | Charts from datasets |

### Decision Rules

1. **If the user's project is already in a language** — use that language's library (consistency > novelty).
2. **If the task is standalone** — prefer Python (python-pptx) or JavaScript (pptxgenjs) for speed.
3. **If performance or deployment matters** — prefer Go (unioffice) or Rust (pptx-builder).
4. **If the user specifies a language** — always honour the request.
5. **For web-to-slide workflows** — prefer JavaScript (pptxgenjs) to stay in the same runtime.

## Capability Selection Guide

| Task Complexity | Strategy | Why |
|----------------|----------|-----|
| Quick 3-5 slide overview | Skill tool: `create_presentation` + `add_slide` | Fast, simple content |
| Professional themed deck | Generated code + presentation library | Full design control |
| Data-driven presentation | Generated code + presentation library + charts | Complex content |
| Branded corporate deck | Generated code with custom templates | Brand compliance |
| Interactive demo | Generated code + animations | Rich features |

## Presentation Design Principles

### Slide Hierarchy
1. **Title Slide** — Presentation title, subtitle, presenter name
2. **Agenda/Overview** — What will be covered
3. **Content Slides** — Main information (one key point per slide)
4. **Summary/Conclusion** — Key takeaways
5. **Q&A / Contact** — Closing slide

### Visual Consistency
- Use 2-3 font sizes maximum (title, subtitle, body)
- Maintain consistent spacing and margins
- Use the same color palette throughout
- Keep backgrounds uniform across slides
- Align elements to a grid

### Typography
- **Titles:** 36-44pt, bold, sans-serif
- **Subtitles:** 24-28pt, regular weight
- **Body text:** 18-24pt, regular weight
- **Captions:** 14-16pt, lighter color
- Maximum 6-8 lines of text per slide

### Color Palettes
| Theme | Primary | Secondary | Accent | Use Case |
|-------|---------|-----------|--------|----------|
| Corporate | #2F5496 | #1F3864 | #4472C4 | Business reports |
| Tech | #0078D4 | #005A9E | #50E6FF | Technology presentations |
| Creative | #FF6B6B | #4ECDC4 | #FFE66D | Creative pitches |
| Academic | #2E4057 | #048A81 | #54C6EB | Research/education |
| Minimal | #333333 | #666666 | #0066CC | Clean, professional |

## Execution Strategies

### Strategy A: Use Skill Tool
For quick, simple presentations (3-5 slides, basic content).
```
Call: create_presentation(file_path, title)
Call: add_slide(file_path, layout_index, title, content)
```
Best for: Quick overviews, simple data presentations.

### Strategy B: Generate Code
For professional, designed presentations. Pick the language that fits the context.

#### Python — python-pptx
```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

def create_professional_presentation(output_path, content):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    PRIMARY = RGBColor(0x2F, 0x54, 0x96)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)

    # Title slide
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    title = slide.shapes.title
    title.text = content["title"]
    for para in title.text_frame.paragraphs:
        para.font.size = Pt(44)
        para.font.color.rgb = WHITE
        para.font.bold = True

    # Content slides
    for section in content["sections"]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = section["title"]
        body = slide.placeholders[1]
        tf = body.text_frame
        tf.text = ""
        for point in section["points"]:
            p = tf.add_paragraph()
            p.text = point
            p.font.size = Pt(20)
            p.space_after = Pt(12)

    prs.save(output_path)
```

#### JavaScript/TypeScript — pptxgenjs
```javascript
import PptxGenJS from "pptxgenjs";

function createPresentation(outputPath, content) {
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";

  const PRIMARY = "2F5496";

  // Title slide
  pptx.addSlide().addText(content.title, {
    x: 0.5, y: 1.5, w: "90%", h: 2,
    fontSize: 44, fontFace: "Calibri", color: "FFFFFF",
    bold: true, align: "center",
    fill: { color: PRIMARY },
  });

  // Content slides
  for (const section of content.sections) {
    const slide = pptx.addSlide();
    slide.addText(section.title, {
      x: 0.5, y: 0.3, w: "90%", h: 1,
      fontSize: 32, fontFace: "Calibri", color: PRIMARY, bold: true,
    });

    slide.addText(
      section.points.map((pt) => ({ text: pt, options: { bullet: true, fontSize: 20 } })),
      { x: 0.5, y: 1.5, w: "85%", h: 4.5, fontFace: "Calibri", color: "333333" }
    );
  }

  pptx.writeFile({ fileName: outputPath });
}
```

#### Go — unioffice
```go
package main

import (
    "github.com/unidoc/unioffice/presentation"
)

func createPresentation(outputPath string, content Content) error {
    prs := presentation.New()
    defer prs.Close()

    // Title slide
    slide := prs.AddSlide()
    title := slide.AddTextContent()
    title.SetText(content.Title)

    // Content slides
    for _, section := range content.Sections {
        s := prs.AddSlide()
        s.AddTextContent().SetText(section.Title)
        body := s.AddTextContent()
        for _, pt := range section.Points {
            body.AddParagraph().AddRun().SetText("• " + pt)
        }
    }

    return prs.SaveToFile(outputPath)
}
```

### Strategy C: Hybrid
Simple structure via skill tools + complex formatting via generated code in any language.

## Quality Checklist

- [ ] Title slide is compelling and clear
- [ ] Each slide has ONE key message
- [ ] Text is concise (no paragraphs, use bullet points)
- [ ] Font sizes are appropriate (readable from back of room)
- [ ] Colors are consistent throughout
- [ ] Images are high-resolution and properly positioned
- [ ] Slide count is appropriate for time allotted (1-2 min per slide)
- [ ] No spelling or grammar errors
- [ ] Transitions are subtle and professional
- [ ] Speaker notes are included for complex slides

## Common Pitfalls

- Too much text on one slide (aim for 6-8 lines max)
- Inconsistent fonts and colors across slides
- Low-resolution images that look pixelated
- Reading slides verbatim (slides are visual aids, not scripts)
- No visual hierarchy (everything looks the same importance)
- Too many animations or transitions (distracting)
- Not including speaker notes for complex topics

## Validation Process

After generating and executing code:

1. Verify file exists and has correct slide count
2. Read back slides and check text content
3. Confirm formatting matches design requirements
4. Check that images are properly embedded
5. If unsatisfied, identify specific design issues and regenerate
