---
title: Introduction to Skills
description: Core concept guide to Skills, why they matter, and how to use them.
---
Skills in Logicore package instructions + tools into reusable capability modules.

At a high level, skills give you:
- Reusable domain behavior without repeating prompts
- Structured tool bundles loaded in one step
- Cleaner agent configuration for production use

---

## Skill Concept Map

```mermaid
graph TD
	SKILL[Skill Package]
	SKILL --> MD[SKILL.md Instructions]
	SKILL --> SCRIPTS[scripts/*.py Tools]
	SKILL --> META[Metadata Frontmatter]

	LOADER[SkillLoader] --> SKILL
	AGENT[Agent] --> LOADER
	AGENT --> PROMPT[System Prompt + Skill Instructions]
	AGENT --> TOOLS[Registered Skill Tools]

	USER[User Request] --> AGENT
	TOOLS --> EXEC[Tool Execution]
	EXEC --> RESP[Final Response]
```

---

## Read Next

- [Skills Overview](./skills-overview)
- [Why Skills Are Important](./skills-why-important)
- [Build Custom Skills](./skills-build-custom)
- [Use Custom Skills in Agents](./skills-use-in-agents)
- [Skills Working Internals](./skills-working-internals)
