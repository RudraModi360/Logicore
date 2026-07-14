# Example: Simple Sales Data Excel

## Input
"Create an Excel file with Q1 sales data"

## Strategy
Use skill tools for simple data dump.

## Approach
```
1. create_workbook(file_path="q1_sales.xlsx", sheet_name="Sales")
2. write_cells(file_path, "Sales", 1, 1, [["Month", "Revenue", "Units"], ["Jan", 15000, 120], ["Feb", 18000, 145], ["Mar", 22000, 180]])
3. read_sheet(file_path, "Sales") — verify output
```

## Expected Output
A 3x3 spreadsheet with headers and Q1 data.
