"""
experiments/prototype_agent.py
Simple standalone agent loop (Plan 00 sandbox), Plan 01 profile wiring.

Deliberately minimal: provider client + 5 tools + chat loop, plus the
class-based ModelProfile strategy from logicore/runtime/model_profile.py
(Plan 01) for context-window/tier resolution. No context management, no
walkthrough - heavier features get layered on later via framework classes.

Usage:
    python experiments/prototype_agent.py --provider ollama --model gemma4:cloud
    python experiments/prototype_agent.py --model gemini/gemini-2.5-flash
    python experiments/prototype_agent.py --model-tier small --context-length 16000
    python experiments/prototype_agent.py --turns "create hello.py that prints hi" "run it"

Every run writes a JSONL trace (debug/traces/prototype/, override with
--trace-dir/--trace-file, disable with --no-trace) in the same record format
as scripts/session_trace.py, so scripts/format_trace.py can pretty-print it.
Traces include a profile_resolved record plus context_pct on every llm_request
so the profile strategy's real behavior can be analyzed afterwards.

Provider prefixes understood by --model (or use --provider):
    gemini/<model>   GEMINI_API_KEY
    groq/<model>     GROQ_API_KEY
    openai/<model>   OPENAI_API_KEY
    ollama/<model>   OLLAMA_API_KEY, ":cloud" models -> https://ollama.com/v1,
                     otherwise OLLAMA_URL (default http://localhost:11434/v1)
"""

from __future__ import annotations

import argparse
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from logicore.runtime.model_profile import ModelProfile  # noqa: E402

MAX_TOOL_OUTPUT_CHARS = 20000

SYSTEM_PROMPT = (
    "You are PrototypeAgent, a compact coding/ops assistant.\n"
    "Rules:\n"
    "1. Use the provided tools for every filesystem, shell, or web action. Never pretend.\n"
    "2. Prefer one tool call per step; read before you edit.\n"
    "3. Keep answers short: state what you did, then the result.\n"
    "4. If a tool fails, say so and adapt - never invent a success.\n"
    "5. On Windows, use PowerShell commands (for example Select-String) rather than "
    "Unix-only commands such as grep, sed, or awk.\n"
)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def tool_bash(args: Dict[str, Any]) -> str:
    command = args.get("command", "")
    if not command:
        return "ERROR: empty command"
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60s"
    stderr = proc.stderr or ""
    if os.name == "nt" and proc.returncode != 0 and re.search(
        r"(^|\s)(grep|sed|awk|cat)\b", command
    ):
        stderr += (
            "\nWindows shell detected: use PowerShell equivalents such as "
            "Select-String, Get-Content, or ForEach-Object."
        )
    out = (proc.stdout or "") + (("\n[stderr]\n" + stderr) if stderr else "")
    out = out.strip() or "(no output)"
    return f"exit_code={proc.returncode}\n{out}"[:MAX_TOOL_OUTPUT_CHARS]


def tool_read_file(args: Dict[str, Any]) -> str:
    path = args.get("file_path", "")
    if not os.path.exists(path):
        return f"ERROR: file not found: {path}"
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read(MAX_TOOL_OUTPUT_CHARS + 1)
    truncated = len(content) > MAX_TOOL_OUTPUT_CHARS
    if truncated:
        content = content[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
    return f"({len(content)} chars{' , truncated' if truncated else ''})\n{content}"


def tool_create_file(args: Dict[str, Any]) -> str:
    path = args.get("file_path", "")
    content = args.get("content", "")
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"Created {path} ({len(content)} chars)"


def tool_edit_file(args: Dict[str, Any]) -> str:
    path = args.get("file_path", "")
    old = args.get("old_str", "")
    new = args.get("new_str", "")
    if not os.path.exists(path):
        return f"ERROR: file not found: {path}"
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    if old not in content:
        return "ERROR: old_str not found in file"
    count = content.count(old)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content.replace(old, new, 1))
    return f"Edited {path}" + (" (multiple matches, replaced first)" if count > 1 else "")


class _DuckDuckGoResultParser(HTMLParser):
    """Extract result links and snippets from DuckDuckGo's HTML endpoint."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._capture: Optional[str] = None
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            if self._current:
                self.results.append(self._current)
            self._current = {
                "href": attributes.get("href", ""),
                "title": "",
                "snippet": "",
            }
            self._capture = "title"
            self._buffer = []
        elif self._current and (
            "result__snippet" in classes or "result__snippet" in (attributes.get("id") or "")
        ):
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._current and self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._current or not self._capture:
            return
        if (self._capture == "title" and tag == "a") or (
            self._capture == "snippet" and tag in {"a", "div"}
        ):
            self._current[self._capture] = " ".join("".join(self._buffer).split())
            self._capture = None
            self._buffer = []

    def close(self) -> None:
        super().close()
        if self._current:
            self.results.append(self._current)
            self._current = None


def _parse_duckduckgo_results(html: str) -> List[Dict[str, str]]:
    parser = _DuckDuckGoResultParser()
    parser.feed(html)
    parser.close()
    return parser.results


class _BingResultParser(HTMLParser):
    """Extract result links and snippets from Bing's HTML endpoint."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._capture: Optional[str] = None
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "li" and "b_algo" in classes:
            if self._current:
                self.results.append(self._current)
            self._current = {"href": "", "title": "", "snippet": ""}
        elif self._current and tag == "a" and self._capture is None:
            self._current["href"] = attributes.get("href", "")
            self._capture = "title"
            self._buffer = []
        elif self._current and tag == "p":
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._current and self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._current or not self._capture:
            return
        if (self._capture == "title" and tag == "a") or (
            self._capture == "snippet" and tag == "p"
        ):
            self._current[self._capture] = " ".join("".join(self._buffer).split())
            self._capture = None
            self._buffer = []

    def close(self) -> None:
        super().close()
        if self._current:
            self.results.append(self._current)
            self._current = None


def _parse_bing_results(html: str) -> List[Dict[str, str]]:
    parser = _BingResultParser()
    parser.feed(html)
    parser.close()
    return parser.results


def _normalise_duckduckgo_url(href: str) -> str:
    """Unwrap DuckDuckGo redirect links while preserving direct result URLs."""
    parsed = urllib.parse.urlparse(unescape(href))
    query = urllib.parse.parse_qs(parsed.query)
    return query.get("uddg", [unescape(href)])[0]


def tool_web_search(args: Dict[str, Any]) -> str:
    query = args.get("query", "")
    if not query:
        return "ERROR: empty query"
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (prototype-agent)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return f"web_search unavailable ({exc.__class__.__name__}); continue with local tools."
    hits = _parse_duckduckgo_results(html)
    if not hits:
        fallback_url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
        fallback_req = urllib.request.Request(
            fallback_url, headers={"User-Agent": "Mozilla/5.0 (prototype-agent)"}
        )
        try:
            with urllib.request.urlopen(fallback_req, timeout=20) as resp:
                fallback_html = resp.read().decode("utf-8", "replace")
            hits = _parse_bing_results(fallback_html)
        except Exception:
            hits = []
    if not hits:
        return f"No results parsed for: {query}"
    lines = [
        f"{i}. {hit['title'] or '(untitled)'}\n"
        f"   {hit['snippet'] or '(no snippet)'}\n"
        f"   {_normalise_duckduckgo_url(hit['href'])[:160]}"
        for i, hit in enumerate(hits[:5], 1)
    ]
    return "\n".join(lines)[:MAX_TOOL_OUTPUT_CHARS]


TOOL_IMPLS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "bash": tool_bash,
    "read_file": tool_read_file,
    "create_file": tool_create_file,
    "edit_file": tool_edit_file,
    "web_search": tool_web_search,
}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the current working directory (60s timeout).",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file and return its content.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}},
                       "required": ["file_path"]}}},
    {"type": "function", "function": {
        "name": "create_file",
        "description": "Create (or overwrite) a file with the given content.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"},
                                                        "content": {"type": "string"}},
                       "required": ["file_path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace the first occurrence of old_str with new_str in a file.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"},
                                                        "old_str": {"type": "string"},
                                                        "new_str": {"type": "string"}},
                       "required": ["file_path", "old_str", "new_str"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web and return the top few results with snippets.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
]


# --------------------------------------------------------------------------- #
# Model client (OpenAI-compatible chat completions over urllib, no deps)
# --------------------------------------------------------------------------- #
PROVIDER_ENDPOINTS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}
PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}


class ModelClient:
    def __init__(self, provider: str, model: str, timeout: int = 120):
        self.provider = provider
        self.model = model
        self.timeout = timeout
        self.base_url = self._resolve_base_url()
        self.api_key = os.environ.get(PROVIDER_KEYS.get(provider, ""), "")

    def _resolve_base_url(self) -> str:
        if self.provider == "ollama":
            if self.model.endswith(":cloud"):
                return "https://ollama.com/v1"
            base = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
            return base if base.endswith("/v1") else base + "/v1"
        base = PROVIDER_ENDPOINTS.get(self.provider)
        if not base:
            raise ValueError(f"Unknown provider '{self.provider}' (use --model provider/model)")
        return base

    def chat(self, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None,
             max_tokens: int = 2048) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages,
                                   "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
        message = data["choices"][0]["message"]
        return {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "usage": data.get("usage") or {},
        }


# --------------------------------------------------------------------------- #
# Tracing
# --------------------------------------------------------------------------- #
# Emits the same JSONL record types as scripts/session_trace.py (the main
# framework tracer), so scripts/format_trace.py reads prototype traces with no
# changes: session_start, turn_start, llm_request, model_response, tool_call,
# turn_end, error.
#
# Default dir: <repo>/debug/traces/prototype/  Override with --trace-dir, or
# PROTOTYPE_TRACE_DIR. Disable with --no-trace or PROTOTYPE_TRACE=0.
TRACE_MAX_FIELD = int(os.environ.get("PROTOTYPE_TRACE_MAX_FIELD", "60000"))


def _clip(value: Any, limit: int = TRACE_MAX_FIELD) -> Any:
    """Truncate long strings/objects so a trace stays readable and bounded."""
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            text = json.dumps(value, default=str)
        except Exception:
            text = str(value)
        if len(text) <= limit:
            return value
        return text[:limit] + f"...[trace truncated {len(text) - limit} chars]"
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[trace truncated {len(value) - limit} chars]"


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Rough char/4 token estimate - enough to spot context blowups in tests."""
    total = 0
    for msg in messages:
        total += 4  # per-message framing overhead
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content) // 4
        elif content is not None:
            total += len(json.dumps(content, default=str)) // 4
        for call in msg.get("tool_calls") or []:
            total += len(json.dumps(call, default=str)) // 4
    return total


class PrototypeTracer:
    """Append-only JSONL tracer for the prototype agent loop.

    When an ``estimator`` (framework TokenEstimator) is provided, every
    ``est_tokens`` field is computed with it (the single source of truth)
    instead of the rough char/4 heuristic, so trace numbers are authoritative.
    """

    def __init__(self, path: Optional[str] = None, enabled: Optional[bool] = None,
                 provider: str = "", model: str = "", trace_dir: Optional[str] = None,
                 profile: Optional[Dict[str, Any]] = None, estimator: Optional[Any] = None):
        if enabled is None:
            enabled = os.environ.get("PROTOTYPE_TRACE", "1") != "0"
        self.enabled = bool(enabled)
        self.provider = provider
        self.model = model
        self.profile = dict(profile or {})
        self._estimator = estimator
        self.path: Optional[str] = None
        self._file = None
        self._seq = 0
        self._turn = 0
        self._iteration = 0
        self.counters = {"turns": 0, "llm_calls": 0, "tool_calls": 0, "tool_errors": 0}
        if not self.enabled:
            return
        if path is None:
            root = trace_dir or os.environ.get("PROTOTYPE_TRACE_DIR") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "debug", "traces", "prototype",
            )
            os.makedirs(root, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = os.path.join(root, f"prototype_{stamp}_{os.getpid()}.jsonl")
        self.path = path
        self._file = open(path, "a", encoding="utf-8")

    def _count(self, messages: List[Dict[str, Any]]) -> int:
        """Authoritative token count: framework estimator when present, else char/4."""
        if self._estimator is not None:
            return self._estimator.count_messages_tokens(messages)
        return estimate_tokens(messages)

    # -- plumbing ---------------------------------------------------------- #
    def _record(self, kind: str, **payload: Any) -> None:
        if not self.enabled or self._file is None:
            return
        self._seq += 1
        record = {"type": kind, "seq": self._seq, "ts": round(time.time(), 3),
                  "turn": self._turn, "iteration": self._iteration, **payload}
        try:
            self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._file.flush()
        except Exception:
            pass

    def _turn_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{
            "role": m.get("role"),
            "name": m.get("name"),
            "tool_call_id": m.get("tool_call_id"),
            "chars": len(str(m.get("content") or "")),
            "content": _clip(m.get("content")),
            "tool_calls": _jsonable(m.get("tool_calls")) if m.get("tool_calls") else None,
        } for m in messages]

    # -- lifecycle events -------------------------------------------------- #
    def profile_resolved(self) -> None:
        """Record which ModelProfile strategy ran and what it decided."""
        self._record("profile_resolved", profile=_jsonable(self.profile))

    def session_start(self, system_prompt: str, max_iterations: int = 8) -> None:
        self._record("session_start",
                     provider=self.provider,
                     model=self.model,
                     profile=_jsonable(self.profile),
                     max_iterations=max_iterations,
                     tool_count=len(TOOL_SCHEMAS),
                     tools=[t["function"]["name"] for t in TOOL_SCHEMAS],
                     system_prompt=_clip(system_prompt))

    def turn_start(self, user_input: str, messages: List[Dict[str, Any]]) -> None:
        if not self.enabled:
            return
        self._turn += 1
        self._iteration = 0
        self.counters["turns"] += 1
        self._record("turn_start",
                     user_input=_clip(user_input, 20000),
                     session_messages=len(messages),
                     est_tokens=self._count(messages))

    def llm_request(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
        self.counters["llm_calls"] += 1
        est = self._count(messages)
        limit = int(self.profile.get("context_length") or 0)
        self._record("llm_request",
                     message_count=len(messages),
                     est_tokens=est,
                     context_limit=limit or None,
                     context_pct=round(est / limit * 100, 2) if limit else None,
                     tool_count=len(tools),
                     tools=[t.get("function", {}).get("name", "") for t in tools],
                     messages=self._turn_messages(messages))

    def model_response(self, response: Dict[str, Any]) -> None:
        self._record("model_response",
                     content=_clip(response.get("content")),
                     tool_calls=_jsonable(response.get("tool_calls") or []),
                     usage=_jsonable(response.get("usage") or {}))

    def tool_call(self, name: str, args: Dict[str, Any], result: str,
                  duration_ms: float, parsed_args: bool = True) -> None:
        success = not str(result).startswith("ERROR")
        self.counters["tool_calls"] += 1
        if not success:
            self.counters["tool_errors"] += 1
        self._record("tool_call",
                     name=name,
                     args=_clip(_jsonable(args), 4000),
                     args_parsed=parsed_args,
                     success=success,
                     duration_ms=round(duration_ms, 1),
                     result_chars=len(result),
                     result=_clip(result))

    def turn_end(self, final_response: str, messages: List[Dict[str, Any]],
                 iterations: int) -> None:
        self._record("turn_end",
                     final_response=_clip(final_response, 20000),
                     iterations=iterations,
                     session_messages=len(messages),
                     est_tokens=self._count(messages),
                     transcript=_jsonable(messages))

    def context_manage(self, result: Any) -> None:
        """Record what the context manager did before an LLM request (A5).

        Captures tokens-before/after-manage, the action flags, tokens saved,
        and the estimator's accuracy class so a trace is self-explanatory and
        has exactly one token authority per event.
        """
        masking = getattr(result, "masking_result", None)
        compression = getattr(result, "compression_result", None)
        self._record(
            "context_manage",
            original_tokens=getattr(result, "original_tokens", 0),
            final_tokens=getattr(result, "final_tokens", 0),
            tokens_saved=getattr(result, "tokens_saved", 0),
            masked=bool(getattr(result, "masked", False)),
            masked_count=(masking.masked_count if masking else 0),
            compressed=bool(getattr(result, "compressed", False)),
            compressed_status=(compression.status.value if compression else None),
            truncated=bool(getattr(result, "truncated", False)),
            hard_enforced=bool(getattr(result, "hard_enforced", False)),
            hard_limit=getattr(result, "hard_limit", None),
            tokens_before_enforce=getattr(result, "tokens_before_enforce", None),
            accuracy_class=(self._estimator.get_accuracy_class()
                            if self._estimator is not None else None),
        )

    def error(self, exc: BaseException, phase: str = "turn") -> None:
        self._record("error", phase=phase,
                     error_type=exc.__class__.__name__, message=_clip(str(exc), 4000))

    def summary(self) -> Dict[str, Any]:
        return dict(self.counters, trace_file=self.path)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
def resolve_provider_model(model_arg: str, provider_arg: Optional[str]) -> Tuple[str, str]:
    if "/" in model_arg:
        provider, model = model_arg.split("/", 1)
        return provider.lower(), model
    if provider_arg:
        return provider_arg.lower(), model_arg
    if model_arg.endswith(":cloud"):  # ollama cloud tag (e.g. gemma4:cloud)
        return "ollama", model_arg
    for provider in ("gemini", "groq", "openai", "ollama"):
        if os.environ.get(PROVIDER_KEYS[provider]):
            return provider, model_arg
    return "ollama", model_arg


def run_turn(model: ModelClient, session: List[Dict[str, Any]], user_input: str,
             max_iterations: int = 8, tracer: Optional[PrototypeTracer] = None) -> str:
    session.append({"role": "user", "content": user_input})
    if tracer is not None:
        tracer.turn_start(user_input, session)

    for iteration in range(1, max_iterations + 1):
        if tracer is not None:
            tracer._iteration = iteration
            tracer.llm_request(session, TOOL_SCHEMAS)
        response = model.chat(session, tools=TOOL_SCHEMAS)
        if tracer is not None:
            tracer.model_response(response)
        tool_calls = response.get("tool_calls") or []

        if not tool_calls:
            final_text = response.get("content") or "(no response)"
            session.append({"role": "assistant", "content": final_text})
            print(f"\n[agent] {final_text}")
            if tracer is not None:
                tracer.turn_end(final_text, session, iteration)
            return final_text

        session.append({"role": "assistant", "content": response.get("content") or "",
                        "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments", "{}")
            parsed_args = True
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (json.JSONDecodeError, ValueError):
                args = {"_raw": str(raw)}
                parsed_args = False
            impl = TOOL_IMPLS.get(name)
            started = time.time()
            try:
                result = impl(args) if impl else f"ERROR: unknown tool {name}"
            except Exception as exc:
                result = f"ERROR: {name} raised {exc.__class__.__name__}: {exc}"
            elapsed_ms = (time.time() - started) * 1000
            preview = result[:120].replace("\n", " ")
            print(f"    [tool] {name} -> {preview}{'...' if len(result) > 120 else ''}")
            if tracer is not None:
                tracer.tool_call(name, args, result, elapsed_ms, parsed_args)
            session.append({
                "role": "tool",
                "name": name,
                "tool_call_id": call.get("id") or "",
                "content": json.dumps({
                    "tool": name,
                    "success": not result.startswith("ERROR"),
                    "reused_cached_result": False,
                    "content": result,
                }, ensure_ascii=False),
            })

    final_text = "(max iterations reached)"
    session.append({"role": "assistant", "content": final_text})
    print(f"\n[agent] {final_text}")
    if tracer is not None:
        tracer.turn_end(final_text, session, max_iterations)
    return final_text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Plan 00 simple prototype agent")
    parser.add_argument("--model", default="gemma4:cloud",
                        help="provider/model or bare model name (with --provider)")
    parser.add_argument("--provider", default=None,
                        help="gemini | groq | openai | ollama")
    parser.add_argument("--context-length", type=int, default=None,
                        help="override the context window (goes into model_config; "
                             "default: resolved from ModelProfile registry/heuristics)")
    parser.add_argument("--model-tier", default=None,
                        choices=["small", "standard", "medium", "large"],
                        help="override the model tier (goes into model_config; "
                             "default: resolved automatically from the model name)")
    parser.add_argument("--turns", nargs="*", default=None, help="non-interactive turns")
    parser.add_argument("--turns-file", default=None, help="file with one turn per line")
    parser.add_argument("--trace", dest="trace", action="store_true", default=None,
                        help="write a JSONL trace (default)")
    parser.add_argument("--no-trace", dest="trace", action="store_false",
                        help="disable tracing (or set PROTOTYPE_TRACE=0)")
    parser.add_argument("--trace-dir", default=None,
                        help="directory for the trace file (default debug/traces/prototype)")
    parser.add_argument("--trace-file", default=None, help="explicit trace file path")
    args = parser.parse_args(argv)

    provider, model_name = resolve_provider_model(args.model, args.provider)
    print(f"PrototypeAgent | provider={provider} model={model_name} "
          f"tools={', '.join(TOOL_SCHEMAS[i]['function']['name'] for i in range(len(TOOL_SCHEMAS)))}")

    model_config: Dict[str, Any] = {}
    if args.context_length is not None:
        model_config["context_length"] = args.context_length
    if args.model_tier is not None:
        model_config["model_tier"] = args.model_tier
    profile = ModelProfile.resolve(model_name, model_config or None)
    print(f"Profile | tier={profile.tier} window={profile.context_length} "
          f"source={profile.source} masking={profile.masking_ratio} "
          f"compression={profile.compression_ratio}")

    tracer = PrototypeTracer(path=args.trace_file,
                             enabled=args.trace,
                             provider=provider, model=model_name,
                             trace_dir=args.trace_dir,
                             profile=profile.to_dict())
    tracer.profile_resolved()

    try:
        model = ModelClient(provider, model_name)
    except ValueError as exc:
        print(f"Model client init failed: {exc}", file=sys.stderr)
        tracer.error(exc, phase="model_init")
        tracer.close()
        return 2

    session: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    tracer.session_start(SYSTEM_PROMPT, max_iterations=8)

    scripted: List[str] = list(args.turns) if args.turns else []
    if args.turns_file:
        with open(args.turns_file, "r", encoding="utf-8") as fh:
            scripted += [line.strip() for line in fh if line.strip()]

    index = 0
    while True:
        if index < len(scripted):
            user_input = scripted[index]
            print(f"\n[user] {user_input}")
        else:
            try:
                user_input = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input:
                continue
        index += 1

        if user_input.lower() in {"exit", "quit", "q"}:
            break
        try:
            run_turn(model, session, user_input, tracer=tracer)
        except Exception as exc:
            print(f"[agent error] {exc}", file=sys.stderr)
            session.append({"role": "assistant", "content": f"(turn failed: {exc})"})
            tracer.error(exc, phase="turn")
        if scripted and index >= len(scripted):
            break

    summary = tracer.summary()
    tracer.close()
    print(f"\nsession.messages: {len(session)} messages")
    if summary.get("trace_file"):
        print(f"trace: {summary['trace_file']} | turns={summary['turns']} "
              f"llm_calls={summary['llm_calls']} tool_calls={summary['tool_calls']} "
              f"tool_errors={summary['tool_errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
