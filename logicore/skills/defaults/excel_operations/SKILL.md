---
name: excel_operations
description: Expert guidance for creating, reading, and modifying Excel workbooks across any programming language. Teaches the agent how to reason about spreadsheet tasks, select the best language/library, and produce professional output.
version: "1.0.0"
author: Logicore
tags: [excel, spreadsheet, data, formatting, charts, polyglot]
trigger: create excel, edit spreadsheet, modify xlsx, read excel, write to excel, excel file, spreadsheet, data analysis
cost_tier: low
requires: []
conflicts_with: []
---

# Excel Operations Skill

## Purpose

Guide the agent to produce well-structured, professionally formatted Excel workbooks for any complexity level. This skill teaches reasoning about spreadsheet tasks across **any programming language** — not just Python. The agent selects the best language and library based on the task, available runtimes, and output quality requirements.

## When to Activate

- User asks to create, read, edit, or analyze Excel files
- User provides tabular data needing spreadsheet format
- User mentions "Excel", "spreadsheet", "XLSX", "workbook"

## Reasoning Process

When the user requests an Excel task:

1. **Assess complexity** — Is this a simple data dump or a multi-sheet formatted report?
2. **Select language and library** — Which runtime and library best fit this task?
3. **Write code** — Generate a complete, well-structured script
4. **Execute** — Run via code execution tool
5. **Validate** — Check output against quality checklist
6. **Iterate** — If quality is low, improve and re-execute

## Language Selection Guide

**Choose the language first, then the library.** The best output comes from picking the right tool for the job — not defaulting to one language.

| Scenario | Language | Library | When to Use |
|----------|----------|---------|-------------|
| Default / rapid prototyping | Python | openpyxl | Mature, well-documented, fast iteration |
| Complex charts/formatting | Python | openpyxl + xlsxwriter | Rich formatting needed |
| Web integration / APIs | JavaScript/TypeScript | exceljs | Same language as the app, async-friendly |
| Data analysis + spreadsheets | Python | pandas + openpyxl | Heavy computation, data pipelines |
| Enterprise reporting | Python | openpyxl + Jinja2 | Template-based reports |
| Server-side / microservice | Go | excelize | Compiled binary, no runtime deps |
| JVM ecosystem | Java | Apache POI | Industry standard for Java shops |
| CLI tool / embedded | Rust | calamine | Memory-safe, read-heavy workloads |

### Decision Rules

1. **If the user's project is already in a language** — use that language's library (consistency > novelty).
2. **If the task is standalone** — prefer Python (openpyxl) or JavaScript (exceljs) for speed.
3. **If performance or deployment matters** — prefer Go (excelize) or Rust (calamine).
4. **If the user specifies a language** — always honour the request.
5. **For data-heavy tasks** — prefer Python (pandas) regardless of other constraints.

## Capability Selection Guide

| Task Complexity | Strategy | Why |
|----------------|----------|-----|
| Simple data list | Skill tool: `create_workbook` + `write_cells` | Fast, minimal code |
| Formatted report with headers | Generated code + spreadsheet library | Full control over styles |
| Charts and conditional formatting | Generated code + spreadsheet library | Rich API available |
| Cross-platform integration | Generated code (same language as app) | Consistent stack |
| Heavy data processing + formatting | Generated code + data library | Data manipulation + formatting |
| Multi-file operations | Generated script | Complex orchestration |

## Execution Strategies

### Strategy A: Use Skill Tool
When the task is simple and the skill tool covers the need.
```
Call: create_workbook(file_path, sheet_name)
Call: write_cells(file_path, sheet, row, col, data)
```
Best for: Quick data dumps, simple lists, basic tables.

### Strategy B: Generate Code
When the task needs custom formatting, complex logic, or specific libraries. Pick the language that fits the context.

#### Python — openpyxl
```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from pathlib import Path

def create_report(output_path, data):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Report"

    headers = ["Column1", "Column2", "Column3"]
    header_font = Font(bold=True, color="FFFFFF", size=12)
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row_idx, row_data in enumerate(data, 2):
        for col_idx, value in enumerate(row_data, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    for col in ws.columns:
        max_length = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max_length + 2

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
```

#### JavaScript/TypeScript — exceljs
```javascript
import ExcelJS from "exceljs";

async function createReport(outputPath, data) {
  const workbook = new ExcelJS.Workbook();
  const ws = workbook.addWorksheet("Report");

  // Headers
  ws.columns = [
    { header: "Column1", key: "col1", width: 20 },
    { header: "Column2", key: "col2", width: 20 },
    { header: "Column3", key: "col3", width: 20 },
  ];

  // Header styling
  ws.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
  ws.getRow(1).fill = {
    type: "pattern", pattern: "solid",
    fgColor: { argb: "FF2F5496" },
  };

  // Data rows
  for (const row of data) {
    ws.addRow(row);
  }

  await workbook.xlsx.writeFile(outputPath);
}
```

#### Go — excelize
```go
package main

import (
    "github.com/xuri/excelize/v2"
)

func createReport(outputPath string, headers []string, data [][]interface{}) error {
    f := excelize.NewFile()
    defer f.Close()

    sheet := f.GetSheetName(0)

    // Headers
    for i, h := range headers {
        cell, _ := excelize.CoordinatesToCellName(i+1, 1)
        f.SetCellValue(sheet, cell, h)
        f.SetCellStyle(sheet, cell, cell, 1) // header style
    }

    // Data
    for r, row := range data {
        for c, val := range row {
            cell, _ := excelize.CoordinatesToCellName(c+1, r+2)
            f.SetCellValue(sheet, cell, val)
        }
    }

    return f.SaveAs(outputPath)
}
```

### Strategy C: Hybrid
When the task combines simple operations with complex parts.
```
Skill tool for basic setup → Generated code for formatting → Validation
```

## Quality Checklist

- [ ] Headers are bold with background color
- [ ] Column widths are appropriate for content
- [ ] Data types are correct (numbers as numbers, dates as dates)
- [ ] File is saved to correct path
- [ ] No broken references or formulas
- [ ] Sheet names are descriptive
- [ ] Frozen panes for header rows (if applicable)
- [ ] Number formatting applied (currency, percentage, etc.)

## Common Pitfalls

- Saving everything as strings (loses number formatting)
- Not handling file paths correctly (relative vs absolute)
- Forgetting to close workbook (use context managers or wb.save immediately)
- Not validating output after creation
- Hardcoding column widths instead of auto-adjusting
- Not using frozen panes for large datasets

## Validation Process

After generating and executing code:

1. Verify file exists at expected path
2. Read back the file and check structure
3. Confirm formatting matches requirements
4. Check data types are preserved
5. If unsatisfied, identify specific issues and regenerate
