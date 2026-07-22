import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import duckdb

from logicore import Agent

MAX_PREVIEW_ROWS = 20
MAX_SUMMARY_COLS = 50

_dataframe: Optional[pd.DataFrame] = None
_file_path: Optional[str] = None


def _df_to_json(df: pd.DataFrame, max_rows: int = MAX_PREVIEW_ROWS) -> str:
    subset = df.head(max_rows)
    return json.dumps(
        {
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "shape": {"rows": len(df), "columns": len(df.columns)},
            "preview": subset.to_dict(orient="records"),
        },
        indent=2,
        default=str,
        ensure_ascii=True,
    )


def _require_dataframe() -> Optional[str]:
    if _dataframe is None:
        return None
    return None


def load_csv(file_path: str) -> str:
    global _dataframe, _file_path
    path = Path(file_path).resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"}, indent=2)
    try:
        _dataframe = pd.read_csv(path)
        _file_path = str(path)
        return json.dumps(
            {
                "status": "loaded",
                "file": str(path),
                "shape": {"rows": len(_dataframe), "columns": len(_dataframe.columns)},
                "columns": list(_dataframe.columns),
                "dtypes": {col: str(dtype) for col, dtype in _dataframe.dtypes.items()},
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"error": f"Failed to load CSV: {exc}"}, indent=2)


def load_excel(file_path: str, sheet_name: str = "0") -> str:
    global _dataframe, _file_path
    path = Path(file_path).resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"}, indent=2)
    try:
        _dataframe = pd.read_excel(path, sheet_name=sheet_name)
        _file_path = str(path)
        return json.dumps(
            {
                "status": "loaded",
                "file": str(path),
                "sheet": sheet_name,
                "shape": {"rows": len(_dataframe), "columns": len(_dataframe.columns)},
                "columns": list(_dataframe.columns),
                "dtypes": {col: str(dtype) for col, dtype in _dataframe.dtypes.items()},
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"error": f"Failed to load Excel: {exc}"}, indent=2)


def load_dataframe(data: List[Dict[str, Any]]) -> str:
    global _dataframe, _file_path
    try:
        _dataframe = pd.DataFrame(data)
        _file_path = "<inline-data>"
        return json.dumps(
            {
                "status": "loaded",
                "source": "inline",
                "shape": {"rows": len(_dataframe), "columns": len(_dataframe.columns)},
                "columns": list(_dataframe.columns),
                "dtypes": {col: str(dtype) for col, dtype in _dataframe.dtypes.items()},
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"error": f"Failed to build DataFrame: {exc}"}, indent=2)


def preview_data(rows: int = 10) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded. Call load_csv or load_excel first."}, indent=2)
    return _df_to_json(_dataframe.head(rows), max_rows=rows)


def get_info() -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    buf: Dict[str, Any] = {
        "file": _file_path,
        "shape": {"rows": len(_dataframe), "columns": len(_dataframe.columns)},
        "columns": list(_dataframe.columns),
        "dtypes": {col: str(dtype) for col, dtype in _dataframe.dtypes.items()},
        "null_counts": _dataframe.isnull().sum().to_dict(),
        "memory_usage_bytes": int(_dataframe.memory_usage(deep=True).sum()),
    }
    return json.dumps(buf, indent=2, default=str, ensure_ascii=True)


def describe(numeric_only: bool = True) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    stats = _dataframe.describe(numeric_only=numeric_only)
    return json.dumps(
        {
            "columns": list(stats.columns),
            "index": list(stats.index),
            "stats": stats.to_dict(),
        },
        indent=2,
        default=str,
        ensure_ascii=True,
    )


def get_column(column: str, rows: int = 20) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    if column not in _dataframe.columns:
        return json.dumps(
            {"error": f"Column '{column}' not found.", "available": list(_dataframe.columns)},
            indent=2,
        )
    series = _dataframe[column]
    return json.dumps(
        {
            "column": column,
            "dtype": str(series.dtype),
            "non_null": int(series.count()),
            "null_count": int(series.isnull().sum()),
            "unique_count": int(series.nunique()),
            "preview": series.head(rows).tolist(),
        },
        indent=2,
        default=str,
        ensure_ascii=True,
    )


def filter_rows(column: str, operator: str, value: str, rows: int = 20) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    if column not in _dataframe.columns:
        return json.dumps(
            {"error": f"Column '{column}' not found.", "available": list(_dataframe.columns)},
            indent=2,
        )
    try:
        col = _dataframe[column]
        ops = {
            "eq": col == value,
            "neq": col != value,
            "gt": col > float(value),
            "gte": col >= float(value),
            "lt": col < float(value),
            "lte": col <= float(value),
            "contains": col.astype(str).str.contains(value, case=False, na=False),
            "startswith": col.astype(str).str.startswith(value, na=False),
            "endswith": col.astype(str).str.endswith(value, na=False),
        }
        mask = ops.get(operator)
        if mask is None:
            return json.dumps(
                {"error": f"Unknown operator '{operator}'.", "available": list(ops.keys())},
                indent=2,
            )
        result = _dataframe[mask]
        return json.dumps(
            {
                "filter": {"column": column, "operator": operator, "value": value},
                "matched_rows": len(result),
                "preview": result.head(rows).to_dict(orient="records"),
            },
            indent=2,
            default=str,
            ensure_ascii=True,
        )
    except Exception as exc:
        return json.dumps({"error": f"Filter failed: {exc}"}, indent=2)


def group_summary(group_by: str, agg_column: str, operation: str = "mean") -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    for col in (group_by, agg_column):
        if col not in _dataframe.columns:
            return json.dumps(
                {"error": f"Column '{col}' not found.", "available": list(_dataframe.columns)},
                indent=2,
            )
    try:
        grouped = _dataframe.groupby(group_by)[agg_column]
        ops_map = {
            "mean": grouped.mean,
            "sum": grouped.sum,
            "count": grouped.count,
            "min": grouped.min,
            "max": grouped.max,
            "median": grouped.median,
            "std": grouped.std,
        }
        func = ops_map.get(operation)
        if func is None:
            return json.dumps(
                {"error": f"Unknown operation '{operation}'.", "available": list(ops_map.keys())},
                indent=2,
            )
        result = func().reset_index()
        result.columns = [group_by, f"{agg_column}_{operation}"]
        return json.dumps(
            {
                "group_by": group_by,
                "agg_column": agg_column,
                "operation": operation,
                "result": result.to_dict(orient="records"),
            },
            indent=2,
            default=str,
            ensure_ascii=True,
        )
    except Exception as exc:
        return json.dumps({"error": f"Group summary failed: {exc}"}, indent=2)


def value_counts(column: str, top_n: int = 10) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    if column not in _dataframe.columns:
        return json.dumps(
            {"error": f"Column '{column}' not found.", "available": list(_dataframe.columns)},
            indent=2,
        )
    counts = _dataframe[column].value_counts().head(top_n)
    return json.dumps(
        {
            "column": column,
            "top_values": [
                {"value": str(val), "count": int(cnt)} for val, cnt in counts.items()
            ],
        },
        indent=2,
        ensure_ascii=True,
    )


def sort_data(column: str, ascending: bool = True, rows: int = 20) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    if column not in _dataframe.columns:
        return json.dumps(
            {"error": f"Column '{column}' not found.", "available": list(_dataframe.columns)},
            indent=2,
        )
    sorted_df = _dataframe.sort_values(by=column, ascending=ascending)
    return json.dumps(
        {
            "sorted_by": column,
            "ascending": ascending,
            "total_rows": len(sorted_df),
            "preview": sorted_df.head(rows).to_dict(orient="records"),
        },
        indent=2,
        default=str,
        ensure_ascii=True,
    )


def run_query(sql: str) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    try:
        # Use DuckDB to run SQL on the pandas DataFrame
        # DuckDB can query the variable '_dataframe' directly if it's in the local scope
        result_df = duckdb.query(sql).to_df()
        return json.dumps(
            {
                "query": sql,
                "rows": len(result_df),
                "columns": list(result_df.columns),
                "preview": result_df.head(MAX_PREVIEW_ROWS).to_dict(orient="records"),
            },
            indent=2,
            default=str,
            ensure_ascii=True,
        )
    except Exception as exc:
        return json.dumps({"error": f"Query failed: {exc}"}, indent=2)


def correlation_matrix(columns: Optional[List[str]] = None) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    try:
        numeric_df = _dataframe.select_dtypes(include=["number"])
        if columns:
            missing = [c for c in columns if c not in numeric_df.columns]
            if missing:
                return json.dumps(
                    {"error": f"Non-numeric or missing columns: {missing}", "numeric_columns": list(numeric_df.columns)},
                    indent=2,
                )
            numeric_df = numeric_df[columns]
        if numeric_df.empty:
            return json.dumps({"error": "No numeric columns found.", "available": list(_dataframe.columns)}, indent=2)
        corr = numeric_df.corr()
        return json.dumps(
            {
                "columns": list(corr.columns),
                "matrix": corr.to_dict(),
            },
            indent=2,
            default=str,
            ensure_ascii=True,
        )
    except Exception as exc:
        return json.dumps({"error": f"Correlation failed: {exc}"}, indent=2)


def export_csv(output_path: str) -> str:
    if _dataframe is None:
        return json.dumps({"error": "No data loaded."}, indent=2)
    try:
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        _dataframe.to_csv(path, index=False)
        return json.dumps({"status": "exported", "file": str(path), "rows": len(_dataframe)}, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Export failed: {exc}"}, indent=2)


SYSTEM_PROMPT = """You are a CSV Data Analyst Agent.

Your job is to help users explore, query, and analyze structured data (CSV, Excel, inline records).

Operating rules:
1. When the user provides a file path, call `load_csv` or `load_excel` to load the data first.
2. After loading, always call `get_info` or `preview_data` to understand the dataset structure before answering questions.
3. Use `filter_rows` to find rows matching specific conditions.
4. Use `group_summary` for aggregations grouped by a column.
5. Use `value_counts` to understand distribution of a column.
6. Use `describe` for statistical summary of numeric columns.
7. Use `sort_data` to order results.
8. Use `correlation_matrix` for relationships between numeric columns.
9. Use `get_column` to inspect a single column in detail.
10. Use `run_query` for SQL-like expressions on the loaded data.
11. Always present results in a clear, structured format.
12. If the user asks to export filtered results, use `export_csv`.

If no data is loaded yet, guide the user to provide a file path or paste data.
You are allowed to ask a short clarifying question if the request is underspecified.
"""


TOOLS = [
    load_csv,
    load_excel,
    load_dataframe,
    preview_data,
    get_info,
    describe,
    get_column,
    filter_rows,
    group_summary,
    value_counts,
    sort_data,
    run_query,
    correlation_matrix,
    export_csv,
]


async def main():
    agent = Agent(
        provider="ollama",
        model="lfm2.5-thinking:latest",
        api_key="a145267cdbad47e0868d72f5cc911032.dAxriNsuF0PeB7zvfnFV28ot",
        debug=True,
        telemetry=True,
        max_iterations=60,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
    print("CSV Analyst Agent ready. Type 'quit' to exit.\n")
    while (msg := input("You: ").strip()) and msg != "quit":
        await agent.chat(
            msg, stream=True, streaming_funct=lambda t: print(t, end="", flush=True)
        )
        print()


if __name__ == "__main__":
    asyncio.run(main())
