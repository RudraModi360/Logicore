# Feature Plan 04: Action Transparency & Walkthrough Decoupling (Part D)

## Overview & Objective
Refactor the walkthrough summary generator so that it provides action transparency for users (especially when using small/dumb models) **without polluting `session.messages` with synthetic user/assistant conversation turns**.

Under the previous implementation, every single turn injected two permanent messages into session memory:
1. Fake User: `"Task execution is complete! Please review your execution details..."`
2. Fake Assistant: `"### Walkthrough Summary: ..."`

This doubled message count growth, burned a redundant full-history LLM call, and confused small models on subsequent turns.

---

## Status & Tracking
- **Status:** `READY FOR DEV`
- **Owner:** Developer Agent Team
- **Reviewer:** Product Owner
- **Target Files:**
  - [`logicore/agent/base.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/base.py) [MODIFY]
  - [`logicore/agent/chat_orchestrator.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py) [MODIFY]
  - `tests/test_walkthrough.py` [NEW]

---

## The Root Bugs & Line Citations

### Bug 1: Synthetic Messages Injected into Persistent Memory
- **Location:** [`logicore/agent/base.py:1467`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/base.py#L1467) & [`base.py:1476`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/base.py#L1476)
- **Problem:**
  ```python
  session.add_message({"role": "user", "content": walkthrough_prompt})
  ...
  session.add_message({"role": "assistant", "content": content})
  ```
  Both the synthetic prompt and the walkthrough summary were added directly into the live conversation history! On Turn 2, the LLM saw this fake exchange as if the user had literally asked for a walkthrough.

### Bug 2: Redundant LLM Call Even When Model Already Summarized
- **Location:** [`logicore/agent/chat_orchestrator.py:1603`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py#L1603)
- **Problem:**
  `should_generate_walkthrough = generate_walkthrough or successful_tools_this_chat > 0`
  It unconditionally fired on every single turn where a tool succeeded, even if the model's primary response was already an exhaustive walkthrough table!

---

## Detailed Implementation Diff Plan

### 1. [`logicore/agent/base.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/base.py)
**Diff in `_generate_walkthrough_summary` (around lines 1458–1480):**

```python
<<<<
    async def _generate_walkthrough_summary(self, session_id: str, active_callbacks: dict, stream: bool = False) -> str:
        if not self.execution_log:
            return ""
        execution_records = "\n".join(self.execution_log)
        walkthrough_prompt = (
            "Task execution is complete! Please review your execution details below and generate a 'Walkthrough Summary'.\n\n"
            f"Execution Records:\n{execution_records}"
        )
        session = self.get_session(session_id)
        session.add_message({"role": "user", "content": walkthrough_prompt})
        try:
            _walk_ctx, _walk_msgs = await self.context_engine.prepare_messages(session.messages, session_id=session_id)
            response = await self.gateway.chat(_walk_msgs, tools=None)
            from logicore.gateway.gateway import NormalizedMessage
            if isinstance(response, NormalizedMessage):
                content = response.content
            else:
                content = getattr(response, 'content', str(response))
            session.add_message({"role": "assistant", "content": content})
            return content
        except Exception as e:
            return f"Walkthrough unavailable. error={e}"
====
    async def _generate_walkthrough_summary(
        self,
        session_id: str,
        active_callbacks: dict,
        final_content: str = "",
        stream: bool = False,
    ) -> str:
        """
        Generate an action walkthrough summary using the Hybrid Adaptive pattern:
        1. If the model's final response already explains its actions, skip the extra call.
        2. If the model was silent/dumb, generate an out-of-band ephemeral summary.
        3. NEVER add the walkthrough prompt or summary to session.messages!
        """
        if not self.execution_log or len(self.execution_log) <= 1:
            return ""

        # Step 1: Hybrid check — did the model already summarize its actions?
        content_lower = final_content.lower()
        has_summary = any(
            marker in content_lower
            for marker in ["walkthrough", "summary", "actions taken", "here is what i did", "findings:", "verified contact table"]
        )
        if has_summary and len(final_content) > 150:
            return ""  # Model already provided full context, skip extra call!

        # Step 2: Ephemeral Out-of-Band Scratchpad LLM Call
        execution_records = "\n".join(self.execution_log[-20:])
        scratchpad = [
            {
                "role": "system",
                "content": "You are an action summarizer. State in 2-3 concise sentences what tools/actions were executed and the final outcome. Do not use tools."
            },
            {
                "role": "user",
                "content": f"User Request: {str(self.execution_log[0])[:200]}\n\nActions Taken:\n{execution_records}"
            }
        ]

        try:
            # Query gateway with scratchpad ONLY (never session.messages)
            response = await self.gateway.chat(scratchpad, tools=None)
            from logicore.gateway.gateway import NormalizedMessage
            if isinstance(response, NormalizedMessage):
                content = response.content
            else:
                content = getattr(response, 'content', str(response))
            # DO NOT CALL session.add_message()!
            return content
        except Exception as e:
            # Deterministic fallback ledger (0 tokens, 0ms latency)
            return f"Actions executed: {len(self.execution_log)} steps completed."
>>>>
```

---

### 2. [`logicore/agent/chat_orchestrator.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py)
**Diff in `_finalize_response` (around lines 1602–1615):**

```python
<<<<
        should_generate_walkthrough = generate_walkthrough or successful_tools_this_chat > 0
        if should_generate_walkthrough:
            walkthrough = await self.agent._generate_walkthrough_summary(session_id, active_callbacks)
            if walkthrough:
                content += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"
                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.WALKTHROUGH, {"content": walkthrough}))
====
        should_generate_walkthrough = generate_walkthrough and successful_tools_this_chat > 0
        if should_generate_walkthrough:
            # Pass final_content to allow hybrid skipping
            walkthrough = await self.agent._generate_walkthrough_summary(
                session_id, active_callbacks, final_content=content
            )
            if walkthrough:
                content += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"
                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.WALKTHROUGH, {"content": walkthrough}))
>>>>
```

---

## Verification Plan

### Automated Test: `tests/test_walkthrough.py`
```python
import pytest
from logicore.agent import Agent

@pytest.mark.asyncio
async def test_walkthrough_does_not_mutate_session():
    agent = Agent(model="mock", supports_tools=True)
    # Simulate a run with tools
    agent.execution_log = ["Agent Started", "Executed web_search", "Found results"]
    session = agent.get_session("test_session")
    session.add_message({"role": "user", "content": "Search for Acme Corp"})
    session.add_message({"role": "assistant", "content": "Acme Corp found."})

    initial_count = len(session.messages)
    walkthrough = await agent._generate_walkthrough_summary("test_session", {}, final_content="Acme Corp found.")

    # CRUCIAL ASSERTION: Message history count must NOT increase!
    assert len(session.messages) == initial_count
    for m in session.messages:
        assert "Task execution is complete" not in m.get("content", "")
```

### Verification Command
```powershell
pytest tests/test_walkthrough.py -v
```

---

## Acceptance Criteria
- [ ] No synthetic `"Task execution is complete..."` user messages exist in `session.messages`.
- [ ] Walkthrough summaries are emitted as `StreamEvent(WALKTHROUGH)` and appended to the final response text delivered to the user.
- [ ] If the model's response already explains its actions, the extra LLM call is skipped.
- [ ] Multi-turn session message count grows strictly by 2 per turn (`1 user + 1 assistant`), plus tool results.
