---
title: Built-in Tools
description: Built-in Logicore tools, their categories, and how to use them from agents.
---

Logicore ships with a default registry of tools for files, shell/code execution, web retrieval, documents, office/PDF handling, media search, and cron scheduling.

## Enable built-in tools

```python
from logicore import Agent

agent = Agent(
    llm="ollama",
    tools=True
)
```

You can also enable them after initialization:

```python
agent = Agent(llm="ollama")
agent.load_default_tools()
```

---

## Tool Categories

### Default registry tools (`registry.py`)
These are loaded by `Agent(..., tools=True)` and `agent.load_default_tools()`.

### Filesystem
- `read_file`, `create_file`, `edit_file`, `delete_file`, `list_files`, `search_files`, `fast_grep`

### Execution
- `execute_command`, `code_execute`

### Git
- `git_command`

### Web + Fetch
- `web_search`, `url_fetch`, `image_search`

### Documents and Conversion
- `read_document`, `convert_document`

### Office and PDF
- `edit_pptx`, `create_pptx`, `append_slide`
- `edit_docx`, `create_docx`
- `edit_excel`, `create_excel`
- `merge_pdfs`, `split_pdf`

### Media
- `media_search`

### Scheduling (Cron)
- `add_cron_job`, `list_cron_jobs`, `remove_cron_job`, `get_crons`

---

## Smart Agent tools (`agent_tools.py`)

These are defined in `logicore/tools/agent_tools.py` and exported from `logicore.tools`:
- `datetime`
- `notes`
- `memory`
- `bash`
- `think`

Notes:
- `SmartAgent` loads a curated set from these tools plus `web_search`, `image_search`, and cron tools.
- Current `SmartAgent` code intentionally skips loading `think` by default.

---

## File-to-Tool Map (`logicore/tools`)

| File | Tool names |
| --- | --- |
| `filesystem.py` | `read_file`, `create_file`, `edit_file`, `delete_file`, `list_files`, `search_files`, `fast_grep` |
| `execution.py` | `execute_command`, `code_execute` |
| `web.py` | `web_search`, `url_fetch`, `image_search` |
| `git.py` | `git_command` |
| `document.py` | `read_document` |
| `convert_document.py` | `convert_document` |
| `office_tools.py` | `edit_pptx`, `create_pptx`, `append_slide`, `edit_docx`, `create_docx`, `edit_excel`, `create_excel` |
| `pdf_tools.py` | `merge_pdfs`, `split_pdf` |
| `media_search.py` | `media_search` |
| `cron_tools.py` | `add_cron_job`, `list_cron_jobs`, `remove_cron_job`, `get_crons` |
| `agent_tools.py` | `datetime`, `notes`, `memory`, `bash`, `think` |

---

## Built-in Tool Usage in Agents

### Example: File operations
```python
from logicore import Agent

agent = Agent(llm="ollama", tools=True)

reply = await agent.chat(
    "Read docs/quickstart.md and create a 5-point setup checklist in docs/setup-checklist.md"
)
print(reply)
```

### Example: Web research + synthesis
```python
reply = await agent.chat(
    "Use web_search in detailed mode for 'python async best practices' and give me a short summary"
)
```

### Example: Cron scheduling
```python
reply = await agent.chat(
    "Create a cron job named 'daily-standup' with cron '0 9 * * 1-5' and message 'Post standup reminder'"
)
```

For cron tools, these key arguments are used internally:
- `add_cron_job(name, message, cron_expression)`
- `remove_cron_job(job_id)`

---

## Approval and Safety Model

Built-in tools are grouped by risk:

- **Safe tools:** read/list/search-style operations.
- **Approval-required tools:** write/edit/network/scheduling operations.
- **Dangerous tools:** destructive or command execution operations.

You can bypass approvals for trusted environments:

```python
agent.set_auto_approve_all(True)
```

---

## Environment Notes

- `web_search` and `image_search` require `GOOGLE_API_KEY` and `GOOGLE_CX`.
- `execute_command` behavior depends on host OS and shell availability.
- Cron jobs persist through the framework cron service storage.

## Next Steps

- [How Tools Work Internally](./tools-overview.md)
- [Ways of Making Tools](./tools-ways.md)
- [Custom Tools](./tools-custom.md)
