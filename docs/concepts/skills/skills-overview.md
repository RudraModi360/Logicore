---
title: Skills Overview
description: Understand what a skill is and where it fits in Logicore agents.
---
Skills are reusable capability packages that combine:
- Instructions from `SKILL.md`
- Tool schemas discovered from Python scripts
- Optional metadata like tags and dependencies

---

## What You Get with Skills

```mermaid
graph TD
    AGENT[Agent]
    AGENT --> LOAD[Load skill by name or object]
    LOAD --> DISCOVER[Skill discovery]
    DISCOVER --> REGISTER[Register skill tools]
    REGISTER --> PROMPT[Inject skill instructions]
    PROMPT --> CHAT[Run chat with enriched capabilities]
```

---

## Typical Use Cases

- Domain-specific assistants (support, analysis, research)
- Reusable team conventions and guardrails
- Faster setup for agents that need the same capability bundle

---

## Quick Start

```python
from logicore.agents.agent import Agent

agent = Agent(
    llm="ollama",
    tools=True,
    skills=["web_research"]
)

response = await agent.chat("Find and summarize updates on AI safety")
print(response)
```

---
# Good - agent knows what to expect
agent.load_skill("code_review")  # Specific skill

# Less optimal - might load more than needed
agent.load_skill("all_developer_tools")  # Broad skillset
```

### 3. Combine Complementary Skills
```python
# Good - well-rounded data team
agent = Agent(skills=[
    "web_research",     # Get data
    "data_analysis",    # Process data
    "visualization",    # Present data
    "database_ops"      # Store data
])

# Less optimal - conflicting tools
agent = Agent(skills=[
    "delete_everything",  # Dangerous
    "code_review",        # Safe
])
```

---

## Skill Lifecycle

```mermaid
graph TD
    INIT["Initialize Agent"]
    
    INIT -->|request skill| DISCOVER["Discover Skill<br/>in Registry"]
    DISCOVER -->|found| LOAD["Load Skill Definition"]
    LOAD -->|extract tools| TOOLS["Get Tool Functions"]
    TOOLS -->|check deps| CHECK["Check API Keys<br/>& Dependencies"]
    CHECK -->|valid| REGISTER["Register All Tools<br/>with Agent"]
    CHECK -->|missing| ERROR["Return Error<br/>with instruction"]
    REGISTER -->|setup| SETUP["Call skill.setup()"]
    SETUP -->|ready| READY["Skill Ready<br/>Tools Available"]
    
    ERROR -->|Fix| DISCOVER
    
    style INIT fill:#4CAF50,stroke:#2E7D32,color:#fff
    style DISCOVER fill:#2196F3,stroke:#1565C0,color:#fff
    style LOAD fill:#2196F3,stroke:#1565C0,color:#fff
    style TOOLS fill:#FF9800,stroke:#E65100,color:#fff
    style CHECK fill:#F44336,stroke:#C62828,color:#fff
    style REGISTER fill:#FF9800,stroke:#E65100,color:#fff
    style SETUP fill:#FF9800,stroke:#E65100,color:#fff
    style READY fill:#4CAF50,stroke:#2E7D32,color:#fff
    style ERROR fill:#F44336,stroke:#C62828,color:#fff
```

---


## Next Pages

- [Why Skills Are Important](./skills-why-important)
- [Build Custom Skills](./skills-build-custom)
- [Use Custom Skills in Agents](./skills-use-in-agents)
- [Skills Working Internals](./skills-working-internals)
