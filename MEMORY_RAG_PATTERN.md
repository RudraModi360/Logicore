# Memory as RAG (Retrieval-Augmented Generation)

## Overview

Persistent memory in the logicore agent system is now **explicit and on-demand** rather than auto-injected. This means:

- **Memory context is NOT automatically injected** at chat start
- **Agents explicitly request memory** when they need context (via the `memory` tool)
- **Casual conversation is filtered** from auto-capture (no more "hello" polluting memory)
- **Clean context** - no hallucinations from stale, irrelevant information

## The Problem (Solved)

### What Was Wrong

```python
# OLD BEHAVIOR (PROBLEMATIC) ❌
agent = Agent(memory=True)

await agent.chat("hello")
# SimpleMem would auto-retrieve and inject:
# "Remember you asked about Python syntax last week..."
# "You wanted to learn about Docker..."
# "We discussed API design patterns..."
#
# Even though user just said "hello"!
# Result: Hallucinations, agent confused with irrelevant context
```

### Why It Failed

1. **Casual conversation was stored**: "hello", "thanks", "remind me to X"
2. **Everything was auto-injected**: At chat start, memory contexts were blindly injected
3. **No filtering**: Stale, outdated, irrelevant context polluted every request
4. **Context bloat**: Large context window filled with useless "dusty garbage"
5. **Hallucinations**: Agent tried to use irrelevant context, made false connections

## The Solution

### New Behavior (WORKING) ✅

```python
# NEW BEHAVIOR (CLEAN & EXPLICIT) ✅
agent = Agent(memory=True)

await agent.chat("hello")
# SimpleMem queues message for indexing, but NO context injection
# Clean session start, agent responds naturally
# Result: No hallucinations, focused responses

await agent.chat("help me with Python - I remember we solved a syntax issue before")
# Agent recognizes memory is relevant
# Agent calls memory tool explicitly:
#
#   agent.memory(action="search", query="Python syntax issue", limit=5)
#
# SimpleMem searches and returns matching memories
# Agent uses returned results in response
# Result: Personalized, contextual response based on actual memory
```

## How to Use Memory Explicitly

The `memory` tool is the primary interface for memory interaction:

### 1. Search for Relevant Memory (RAG)

```python
# Agent explicitly searches when needed
result = await agent.memory(
    action="search",
    query="Python syntax patterns we discussed",
    limit=5  # Get top 5 results
)

# Returns formatted memory blocks from past conversations
```

**When agents use this:**
- User asks: "Help me with Python - what did we discover before?"
- Agent recognizes opportunity → calls memory.search("Python discoveries")
- Gets relevant context → includes in response

### 2. Store Significant Learning

```python
# Auto-captured for significant responses
result = await agent.memory(
    action="store",
    memory_type="learning",  # or "approach", "pattern", "decision"
    title="Python f-string performance",
    content="F-strings are significantly faster than .format() method...",
    tags=["python", "performance"]
)

# Returns: Memory stored: [learning] Python f-string performance (ID: xyz)
```

**Automatic capture filtered:**
- ✅ Captures: Solutions, patterns, insights, recommendations
- ❌ Ignores: Greetings, casual chat, simple confirmations

### 3. List Recent Memories

```python
result = await agent.memory(
    action="list",
    memory_type="learning",  # Filter by type
    limit=10
)

# Returns: List of recent learning memories
```

## Memory Types

The `memory` tool supports different memory types for organization:

| Type | Purpose | Example |
|------|---------|---------|
| `approach` | Strategy or method | "Use dependency injection for better testing" |
| `learning` | Knowledge gained | "Docker images are built in layers" |
| `key_step` | Important step | "Always run tests before committing" |
| `pattern` | Recurring pattern | "Use middleware for cross-cutting concerns" |
| `preference` | User preference | "I prefer explicit over implicit" |
| `decision` | Project decision | "Use PostgreSQL instead of MongoDB" |
| `context` | Project/domain context | "Team follows PEP 8 style guide" |

## System Prompts Integration

### For SmartAgent (Project Mode)

The `SmartAgent` automatically injects **project context** (not casual memory) to maintain focus:

```python
agent = SmartAgent(mode="project", project_id="my-project")

await agent.chat("What's our current focus?")
# Agent has project context injected (title, goal, key_files)
# But NOT random casual memory
# Result: Focused response within project scope
```

### Memory Tool Available

Even in project mode, agents can explicitly search project-specific memories:

```python
result = await agent.memory(
    action="search",
    query="API authentication patterns",
    project_id="my-project",  # Filter to project
    limit=5
)
```

## Learning Capture Filter

### What Gets Captured

The system now only captures **significant learnings**, filtering out noise:

**✅ Captured Responses**
- "The solution is to use context managers"
- "I found that async/await improves performance"
- "Here's a pattern: always validate input before processing"
- "Best practice: use type hints for better IDE support"

**❌ Ignored Responses**
- "Hello! How can I help?"
- "Sure, I understand"
- "Thanks for asking"
- "Sounds good to me"

### Filter Logic

Response is captured only if it:
1. Contains value indicators ("solution is", "found that", "pattern", "approach")
2. Is substantive (not just casual greeting)
3. Has actionable content (not just acknowledgement)

## Example Workflows

### Workflow 1: Casual Chat (Clean)

```python
agent = Agent(memory=True)

await agent.chat("hello, how are you?")
# ✅ SimpleMem queues message (indexed for future searches)
# ✅ No context injected
# ✅ Agent responds naturally
# ✅ Response filtered: casual, not captured to memory

# Result: Clean conversation, no memory pollution
```

### Workflow 2: Explicit Learning Query (RAG)

```python
agent = Agent(memory=True)

# User provides a problem
await agent.chat("Help me optimize this SQL query")

# Agent automatically:
# 1. Recognizes this is problem-solving
# 2. Calls memory tool to search for relevant past solutions
# 3. memory.search("SQL optimization patterns")
# 4. Receives matching memories from past (RAG)
# 5. Incorporates results in response
# 6. Response contains solution → auto-captured

# Result: Personalized, context-aware solution
```

### Workflow 3: Project-Focused Work (Context + Memory)

```python
agent = SmartAgent(mode="project", project_id="web-app")

await agent.chat("How should we structure the database?")

# Agent has:
# ✅ Project context injected (controlled, non-polluting)
# ✅ Access to memory tool for explicit queries if needed
# ✅ Can search project-specific memories
#
# Agent calls:
#   memory.search("database architecture decisions", project_id="web-app")
#
# Returns remembered patterns from THIS project

# Result: Context-aware + personalized response
```

## Migration Guide

If you had code relying on auto-injected memory:

### Before (Auto-Injection)
```python
agent = Agent(memory=True)
await agent.chat("Tell me about Python")
# Magically got all past Python-related memory injected
```

### After (Explicit RAG)
```python
agent = Agent(memory=True)
await agent.chat("Tell me about Python")
# Agent internally recognizes topic
# Agent calls memory.search("Python topics")
# Gets relevant memory and includes it

# OR you manually call:
context = await agent.memory(action="search", query="Python topics")
await agent.chat(f"Tell me about Python:\n{context}")
```

## Best Practices

1. **Enable memory intentionally**: `Agent(memory=True)` only when you need personalization
2. **Let agents decide**: Don't force memory injection; let agents call the tool when appropriate
3. **Use typed memory**: Store memories with appropriate `memory_type` for better organization
4. **Tag appropriately**: Use `tags` parameter to categorize memories
5. **Keep content clean**: Only store genuinely significant learnings
6. **Project context**: Use project mode for project-specific context (separate from memory)

## Troubleshooting

### "Agent ignores memory"
- **Expected**: Agents now call memory tool explicitly when needed
- No auto-injection means memory is intentional, not automatic

### "Old memory not showing up"
- **Check**: Did agent explicitly call memory.search()?
- **Search yourself**: `await agent.memory(action="search", query="your-topic")`
- **Casual chat not captured**: Only significant learnings are stored now

### "Too much irrelevant old memory"
- **This is fixed**: Auto-injection removed, only explicit queries now
- **Clean it up**: Query and review with `memory.list()`
- **Archive old**: Memories are persistent; consider periodic cleanup

## Technical Details

### Modified Files
- `logicore/agents/agent.py`: Removed auto-injection block, SimpleMem now one-way (queue only)
- `logicore/agents/agent_smart.py`: Improved learning capture filter, removed memory judge
- `logicore/tools/agent_tools.py`: MemoryTool already supports RAG pattern (no changes needed)

### SimpleMem Integration
- `on_user_message()` still called for indexing (queues message)
- No context is extracted and injected anymore
- Memory search via tool remains fully functional
- Session isolation preserved

### Performance Impact
- ✅ Faster initial response (no memory search at chat start)
- ✅ Reduced context size (no auto-injected bloat)
- ✅ Memory only retrieved when explicitly requested
- ✅ Better token efficiency
