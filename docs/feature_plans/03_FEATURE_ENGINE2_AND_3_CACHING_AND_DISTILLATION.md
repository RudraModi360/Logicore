# Feature Plan 03: Engines 2 & 3 Fixes — FileStateCache, Distillation & Result Cache (Part C)

## Overview & Objective
Fix the integration bugs in **Engine 2 (In-Turn File Deduplication)** and **Engine 3 (Per-Result Distillation & Result Cache)**. 

Currently, [`FileStateCache`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/tools/dedup/file_state_cache.py) is implemented in `logicore/tools/dedup/` but completely unwired in [`read_file()`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/tools/filesystem.py). Additionally, in [`chat_orchestrator.py:1403`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py#L1403), the `reused` flag was omitted when serializing tool results, meaning `reused_cached_result` was hardcoded to `false` in every single trace.

---

## Status & Tracking
- **Status:** `READY FOR DEV`
- **Owner:** Developer Agent Team
- **Reviewer:** Product Owner
- **Target Files:**
  - [`logicore/tools/filesystem.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/tools/filesystem.py) [MODIFY]
  - [`logicore/agent/chat_orchestrator.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py) [MODIFY]
  - [`logicore/runtime/context/tool_output_distiller.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/tool_output_distiller.py) [MODIFY]
  - `tests/test_file_cache.py` [NEW]
  - `tests/test_tool_cache.py` [NEW]

---

## The Root Bugs & Line Citations

### Bug 1: `FileStateCache` Unwired in `read_file()`
- **Location:** [`logicore/tools/filesystem.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/tools/filesystem.py)
- **Problem:** `FileStateCache` exists in `logicore/tools/dedup/file_state_cache.py` with full mtime, offset, and limit tracking. However, `read_file()` in `filesystem.py` never checks or populates this cache! Every file re-read during an agent's multi-step editing workflow re-reads disk and bloats the prompt.

### Bug 2: Dropped `reused` Flag in Tool Serialization
- **Location:** [`logicore/agent/chat_orchestrator.py:1403`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py#L1403)
- **Problem:**
  ```python
  tool_msg = {
      "role": "tool",
      "name": name,
      "content": self.agent._serialize_tool_result_for_model(name, result) # reused is NOT passed!
  }
  ```
  `_serialize_tool_result_for_model` takes `reused: bool = False`. Since it is omitted, it always defaults to `False`. All message payloads in traces show `"reused_cached_result": false`, even when served from `ResultCache`!

### Bug 3: No Cross-Turn Pruning for Old Tool Outputs
- **Location:** [`logicore/runtime/context/tool_output_distiller.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/tool_output_distiller.py)
- **Problem:** While `ToolOutputDistiller` caps individual tool results at 6,000 characters when created, raw tool outputs from 3–4 turns ago remain in `session.messages` forever, wasting tens of thousands of tokens on stale queries.

---

## Detailed Implementation Diff Plan

### 1. Wire `FileStateCache` in [`logicore/tools/filesystem.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/tools/filesystem.py)

In `read_file()`:
```python
<<<<
def read_file(file_path: str, offset: Optional[int] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    path = os.path.abspath(file_path)
    if not os.path.exists(path):
        return {"success": False, "error": f"File not found: {file_path}"}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        # reads content...
====
from logicore.tools.dedup import file_state_cache

def read_file(file_path: str, offset: Optional[int] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    path = os.path.abspath(file_path)
    if not os.path.exists(path):
        return {"success": False, "error": f"File not found: {file_path}"}
    
    # Engine 2: Check in-turn file state cache
    cached_entry = file_state_cache.get(path, offset, limit)
    if cached_entry is not None:
        return {
            "success": True,
            "cached": True,
            "content": f"[FILE CACHED — UNCHANGED: {file_path}]",
            "content_hash": cached_entry.content_hash,
            "_note": "File content unchanged since last read in this session. Reusing prior context."
        }
    
    # Cache miss: read from disk
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        # apply offset/limit...
        content = "".join(lines)
    
    # Store in FileStateCache
    file_state_cache.set(path, content, offset, limit)
    return {"success": True, "content": content}
>>>>
```

---

### 2. Pass `reused` Flag in [`logicore/agent/chat_orchestrator.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/chat_orchestrator.py)

**Line 1403 Diff:**
```python
<<<<
        tool_msg = {
            "role": "tool",
            "name": name,
            "content": self.agent._serialize_tool_result_for_model(name, result)
        }
====
        tool_msg = {
            "role": "tool",
            "name": name,
            "content": self.agent._serialize_tool_result_for_model(
                name, result, reused=reused_cached_result
            )
        }
>>>>
```
Also update line 789 (the hook skip path) to pass `reused=False` explicitly.

---

### 3. Cross-Turn Pruning in [`logicore/runtime/context/tool_output_distiller.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/tool_output_distiller.py)

Add method `prune_older_turns()` to prune tool results older than `keep_turns` (default: 2):

```python
    @staticmethod
    def prune_older_turns(
        messages: List[Dict[str, Any]],
        current_turn: int,
        keep_turns: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Replace tool output payloads from older turns with 1-line summary receipts.
        Prevents raw JSON outputs from turns 1-3 from polluting turn 5.
        """
        pruned_messages = messages.copy()
        for idx, msg in enumerate(pruned_messages):
            if msg.get("role") != "tool":
                continue
            
            msg_turn = msg.get("_turn", current_turn)
            # If tool is older than keep_turns, compress to receipt
            if current_turn - msg_turn > keep_turns:
                tool_name = msg.get("name", "tool")
                content = msg.get("content", "")
                if "[RECEIPT]" not in content and "[OUTPUT_MASKED]" not in content:
                    summary = content[:120].replace("\n", " ").strip()
                    pruned_messages[idx] = {
                        **msg,
                        "content": json.dumps({
                            "tool": tool_name,
                            "status": "completed",
                            "summary": f"[RECEIPT] Prior turn output: {summary}...",
                            "_pruned_older_turn": True
                        })
                    }
        return pruned_messages
```

---

## Verification Plan

### Automated Test: `tests/test_file_cache.py`
```python
import os
import pytest
from logicore.tools.filesystem import read_file
from logicore.tools.dedup import file_state_cache

def test_file_read_caching(tmp_path):
    p = tmp_path / "test.txt"
    p.write_text("hello world", encoding="utf-8")
    
    file_state_cache.clear()
    
    # First read: miss
    res1 = read_file(str(p))
    assert res1["success"] is True
    assert res1.get("cached") is not True
    assert res1["content"] == "hello world"
    
    # Second read: hit
    res2 = read_file(str(p))
    assert res2["success"] is True
    assert res2["cached"] is True
    assert "[FILE CACHED" in res2["content"]
```

### Verification Command
```powershell
pytest tests/test_file_cache.py tests/test_tool_cache.py -v
```

---

## Acceptance Criteria
- [ ] Consecutive `read_file` calls on an unchanged file return the cached stub.
- [ ] Modifying a file invalidates its `FileStateCache` entry and serves fresh content on the next read.
- [ ] Reused tool executions have `"reused_cached_result": true` in the tool message JSON.
- [ ] Tool results older than 2 turns are pruned to 1-line receipts.
