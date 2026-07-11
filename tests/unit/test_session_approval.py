"""
Tests for per-session approval caching in ToolExecutor.

Verifies that network-egress tools (web_search / image_search / url_fetch)
are gated by a single per-session decision, while destructive tools keep
prompting on every call.
"""

import asyncio
import pytest

from logicore.agent.tool_executor import ToolExecutor


async def _run(executor, name, session_id, args=None):
    return await executor.execute(name, args or {"user_input": "x"}, session_id=session_id)


def test_egress_tools_prompt_once_per_session():
    ex = ToolExecutor()
    calls = {"n": 0}

    async def cb(session_id, name, args):
        calls["n"] += 1
        return True

    ex.set_callbacks(on_tool_approval=cb)

    async def main():
        # Same session: 3 web calls + 1 sibling egress call
        await _run(ex, "web_search", "s1")
        await _run(ex, "web_search", "s1")
        await _run(ex, "image_search", "s1", {"query": "x"})
        await _run(ex, "url_fetch", "s1", {"url": "http://e.com"})
        # New session: should prompt again
        await _run(ex, "web_search", "s2")

    asyncio.run(main())
    # 1 for session s1 + 1 for session s2
    assert calls["n"] == 2


def test_destructive_tools_always_prompt():
    ex = ToolExecutor()
    calls = {"n": 0}

    async def cb(session_id, name, args):
        calls["n"] += 1
        return True

    ex.set_callbacks(on_tool_approval=cb)

    async def main():
        await ex.execute("delete_file", {"file_path": "a"}, session_id="s1")
        await ex.execute("execute_command", {"command": "ls"}, session_id="s1")

    asyncio.run(main())
    assert calls["n"] == 2


def test_denial_is_cached_for_session():
    ex = ToolExecutor()
    calls = {"n": 0}

    async def cb(session_id, name, args):
        calls["n"] += 1
        return False

    ex.set_callbacks(on_tool_approval=cb)

    async def main():
        r1 = await ex.execute("web_search", {"user_input": "x"}, session_id="s9")
        r2 = await ex.execute("url_fetch", {"url": "http://x"}, session_id="s9")
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert calls["n"] == 1
    # Second egress call denied without re-prompting
    assert "Denied" in str(r2.get("error", ""))


def test_clear_session_approvals_resets_cache():
    ex = ToolExecutor()
    calls = {"n": 0}

    async def cb(session_id, name, args):
        calls["n"] += 1
        return True

    ex.set_callbacks(on_tool_approval=cb)

    async def main():
        await ex.execute("web_search", {"user_input": "x"}, session_id="s1")
        ex.clear_session_approvals("s1")
        await ex.execute("web_search", {"user_input": "x"}, session_id="s1")

    asyncio.run(main())
    assert calls["n"] == 2
