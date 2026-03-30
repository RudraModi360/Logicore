---
title: Build Custom Skills
description: Create your own SKILL.md package and expose tools from scripts.
---
Logicore custom skills are directory-based packages discovered by `SkillLoader`.

---

## Directory Structure

```text
my-skill/
├── SKILL.md
├── scripts/
│   └── tools.py
├── resources/
└── examples/
```

Only `SKILL.md` is required. `scripts/` is optional but needed if you want executable tools.

---

## 1) Create `SKILL.md`

Use YAML frontmatter followed by instructions.

```md
---
name: release_assistant
description: Helps with release checklist and changelog hygiene
version: 1.0.0
author: team-platform
tags: [release, docs]
requires: [git]
---

When handling release tasks:
1. Validate changed files.
2. Summarize user-facing changes.
3. Propose changelog entries.
```

Supported metadata fields map to `SkillMetadata`:
- `name`
- `description`
- `version`
- `author`
- `tags`
- `requires`

---

## 2) Add Tool Functions in `scripts/*.py`

`SkillLoader` imports functions from `scripts/` and treats functions with docstrings as tools.

```python
def summarize_release_notes(text: str) -> str:
    """Summarize release notes into concise bullet points."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(f"- {line}" for line in lines[:6])


def validate_semver(version: str) -> bool:
    """Validate whether a version follows semantic versioning."""
    parts = version.split(".")
    return len(parts) == 3 and all(p.isdigit() for p in parts)
```

Tips:
- Add docstrings (required for discovery).
- Add type hints so generated parameter schemas are useful.
- Keep tool names clear and domain-specific.

---

## 3) Place Skill in Discovery Path

Custom skills are discovered from workspace paths:
- `.agent/skills/`
- `_agent/skills/`
- `.agents/skills/`
- `_agents/skills/`

Example:

```text
<workspace>/.agent/skills/release_assistant/SKILL.md
<workspace>/.agent/skills/release_assistant/scripts/tools.py
```

---

## 4) Load and Verify

```python
from logicore.agents.agent import Agent

agent = Agent(llm="ollama", tools=True, workspace_root="D:/Scratchy")
agent.load_skills(["release_assistant"])

response = await agent.chat("Prepare release summary for v1.4.0")
print(response)
```
