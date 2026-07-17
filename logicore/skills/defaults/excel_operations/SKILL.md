---
name: excel_operations
description: "Use this skill whenever the user wants to create, read, edit, or analyze Excel workbooks (.xlsx files). Triggers: any mention of 'Excel', 'spreadsheet', '.xlsx', 'workbook', 'data analysis', 'csv', 'pivot table', 'chart', 'formula', or requests to work with tabular data in spreadsheet format. Do NOT use for Word docs, PDFs, or general data tasks not involving spreadsheets."
version: "1.0.0"
author: Logicore
tags: [excel, spreadsheet, data, formatting, charts, xlsx]
trigger: create excel, edit spreadsheet, modify xlsx, read excel, spreadsheet, data analysis
cost_tier: low
requires: []
conflicts_with: []
---

# Excel (.xlsx) creation, editing, and analysis

An `.xlsx` is a ZIP of XML files (similar to `.docx`). Choose your approach by task:

| Task | Approach |
|---|---|
| **Create** a new workbook | Write an `exceljs` (npm) script — see gotchas below |
| **Edit** an existing workbook | `exceljs` can read+modify+write, or `unzip` → edit XML → `zip` |
| **Read** content | `exceljs` read, or `python -c "import openpyxl; ..."` |
| **Data analysis** | Python with `pandas` + `openpyxl` |

## Creating with exceljs — gotchas

`exceljs` is preinstalled — do not run `npm install` first; write the script and `require('exceljs')` directly. Only if this require fails: `npm install exceljs`. These are the footguns:

- **Column widths must be set explicitly** — exceljs does not auto-size. Set `ws.getColumn(n).width = N` or columns will be narrow and data truncated.
- **Header styling requires row object** — `ws.getRow(1).font = { bold: true }` works, but `ws.getRow(1).fill` needs the full pattern object.
- **Number formats are strings** — `numFmt: '#,##0.00'` for currency, `numFmt: '0.00%'` for percentage.
- **Merged cells lose data** — only the top-left cell retains value. Set value before merging.
- **Formulas are strings** — `ws.getCell('C1').value = { formula: 'A1+B1' }` not a raw string.
- **Conditional formatting needs rule objects** — not just cell styles.
- **Freeze panes:** `ws.views = [{ state: 'frozen', ySplit: 1 }]` to freeze header row.
- **Sheets must have unique names** — max 31 chars, no `[]:\\/?*`.

### Minimal create example

```javascript
import ExcelJS from "exceljs";

const wb = new ExcelJS.Workbook();
const ws = wb.addWorksheet("Report");

// Headers with styling
ws.columns = [
  { header: "Name", key: "name", width: 25 },
  { header: "Revenue", key: "revenue", width: 15 },
  { header: "Growth %", key: "growth", width: 12 },
];
ws.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
ws.getRow(1).fill = {
  type: "pattern", pattern: "solid",
  fgColor: { argb: "FF2F5496" },
};

// Data
ws.addRow({ name: "Product A", revenue: 50000, growth: 0.12 });
ws.addRow({ name: "Product B", revenue: 35000, growth: 0.08 });

// Number formatting
ws.getColumn("revenue").numFmt = '#,##0';
ws.getColumn("growth").numFmt = '0.0%';

// Freeze header
ws.views = [{ state: "frozen", ySplit: 1 }];

await wb.xlsx.writeFile("output.xlsx");
```

### Python fallback — openpyxl

Use when exceljs is unavailable or for data-heavy tasks:

```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Report"

headers = ["Name", "Revenue", "Growth %"]
header_font = Font(bold=True, color="FFFFFF", size=11)
header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")

for col, header in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=header)
    cell.font = header_font
    cell.fill = header_fill

data = [("Product A", 50000, 0.12), ("Product B", 35000, 0.08)]
for row_idx, (name, rev, growth) in enumerate(data, 2):
    ws.cell(row=row_idx, column=1, value=name)
    ws.cell(row=row_idx, column=2, value=rev).number_format = '#,##0'
    ws.cell(row=row_idx, column=3, value=growth).number_format = '0.0%'

wb.save("output.xlsx")
```

### Data analysis — pandas

For heavy data processing, filtering, pivoting, or aggregation:

```python
import pandas as pd

df = pd.read_excel("input.xlsx")
summary = df.groupby("Category").agg({"Revenue": "sum", "Growth": "mean"})
summary.to_excel("output.xlsx", sheet_name="Summary")
```

## Editing existing workbooks

```bash
# Quick read to inspect structure
node -e "const ExcelJS = require('exceljs'); const wb = new ExcelJS.Workbook(); wb.xlsx.readFile('file.xlsx').then(() => wb.eachSheet((s,sid) => console.log(sid, s.name, s.rowCount + ' rows')))"
```

For XML-level edits: `unzip -q file.xlsx -d unpacked/` → edit XML → re-zip.

## Verify the output

```bash
# Check file exists and is valid ZIP
python -c "import zipfile; zipfile.ZipFile('output.xlsx').testzip()"

# Quick content check
node -e "const ExcelJS = require('exceljs'); const wb = new ExcelJS.Workbook(); wb.xlsx.readFile('output.xlsx').then(() => wb.eachSheet((s) => { console.log(s.name + ': ' + s.rowCount + ' rows, ' + s.columnCount + ' cols'); }))"
```

## Dependencies

`exceljs` (npm, preinstalled) · `openpyxl` (pip, fallback) · `pandas` (pip, data analysis)
