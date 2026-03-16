---
title: Tools
description: How tools extend agents with actions and data access.
---

# Tools

Tools are callable capabilities the model can trigger. Keep each tool single-purpose and deterministic.

## Creating a tool
```python
from logicore.tools import Tool

class SearchDocs(Tool):
    name = "search_docs"
    description = "Search internal documentation"

    def run(self, query: str):
        return {"results": ["Result 1", "Result 2"]}
```

## Registering
```python
from logicore import Agent
agent = Agent(tools=[SearchDocs()])
```

## Design tips
- Describe inputs/outputs clearly so the LLM can self-select.
- Validate arguments and return friendly errors.
- Timebox external calls; add retries where needed.
- Emit metrics per tool to spot slow or failing dependencies.

## Built-in Tools
Logicore provides several built-in tools (such as file handling and execution capabilities) that agents can leverage.

### Cron Job Manager
The `CronService` allows agents to natively schedule and manage automated tasks. 

**Features:**
- Add Cron Jobs: `add_cron_job(name: str, schedule: str, task: str)`
- List Jobs: `list_cron_jobs()`
- Remove Jobs: `remove_cron_job(name: str)`

*Note: Scheduled tasks persist across sessions using robust configuration files and will recover missed intervals safely upon restart.*

## Tool vs Skill
- **Tool:** Single action.
- **Skill:** A curated bundle of tools + patterns for a domain.
