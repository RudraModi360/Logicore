---
title: Skills
description: Bundle related tools and prompts for repeatable capabilities.
---

# Skills

Skills package multiple tools plus guidance so agents can perform a domain task reliably (e.g., "triage support tickets").

## Creating a skill
1. Group 2–5 tools that belong together.
2. Add a short skill description and usage hints.
3. Provide guardrails: limits, required inputs, and success criteria.

## Registering
```python
from logicore import Agent
from logicore.skills import SupportSkill

agent = Agent(skills=[SupportSkill()])
```

## Good patterns
- Keep skills focused; avoid mega-skills that do everything.
- Include example prompts in the skill docstring so the LLM knows when to use it.
- Version skills; note breaking changes in the changelog.
