# Logicore Runtime: Production-Grade Agentic Loop Implementation

## Executive Summary

A comprehensive refactoring of Logicore's agentic execution architecture, implementing production-grade patterns inspired by Google's gemini-cli. This implementation addresses the core issues of **infinite/redundant loops**, **unbounded context growth**, **hardcoded thresholds**, and **poor observability**.

---

## Architecture Overview

```
logicore/runtime/
├── __init__.py              # Package exports
├── config.py                # RuntimeConfig (centralized configuration)
├── turn_manager.py          # TurnManager (bounded execution)
├── agent_runtime.py         # AgentRuntime (orchestrator)
├── loop_detection/
│   ├── __init__.py
│   ├── engine.py            # LoopDetectionEngine (orchestrator)
│   ├── detectors.py         # Pluggable detection strategies
│   └── recovery.py          # Composable recovery strategies
├── context/
│   ├── __init__.py
│   ├── manager.py           # ContextWindowManager
│   ├── token_budget.py      # TokenBudget (model-aware tracking)
│   ├── compression.py       # CompressionService (async summarization)
│   └── masking.py           # ToolOutputMaskingService (FIFO pruning)
├── scheduler/
│   ├── __init__.py
│   └── executor.py          # ToolScheduler (state machine execution)
└── telemetry/
    ├── __init__.py
    └── collector.py         # TelemetryCollector (observability)
```

---

## Components Implemented

### 1. RuntimeConfig (`config.py`)
**Purpose**: Centralized configuration eliminating 13+ hardcoded thresholds.

**Key Features**:
- Model-specific context windows (GPT-4, Claude, Gemini, Ollama models)
- Environment variable and TOML override support
- Sub-configurations: `LoopDetectionConfig`, `ContextConfig`, `ToolConfig`, `RetryConfig`, `TelemetryConfig`
- Factory method `from_settings()` for integration with existing `settings.py`

**Before vs After**:
```python
# Before (scattered hardcodes)
if len(messages) > 120:  # Magic number
    self._truncate(messages)
await asyncio.sleep(1)   # Fixed 1s retry

# After (configurable)
if len(messages) > config.max_history_messages:
    ...
delay = config.retry.base_delay_ms * (2 ** attempt)
```

---

### 2. TurnManager (`turn_manager.py`)
**Purpose**: Bounded execution with state machine tracking.

**Key Features**:
- Turn budget enforcement (default: 60 turns/session)
- State machine: `PENDING → ACTIVE → COMPLETED/FAILED`
- Async context manager for clean lifecycle management
- Lifecycle hooks (`on_turn_start`, `on_turn_end`)
- Nested turn tracking for hierarchical execution

**Usage**:
```python
async with runtime.turn(session_id) as ctx:
    ctx.tool_calls = 3
    # Turn auto-completes on exit
```

---

### 3. LoopDetectionEngine (`loop_detection/engine.py`)
**Purpose**: Multi-layer loop detection with pluggable detectors.

**Detector Types**:
| Detector | Method | Threshold | Source |
|----------|--------|-----------|--------|
| `ConsecutiveToolCallDetector` | Content hash | 5 calls | gemini-cli |
| `ContentRepetitionDetector` | Chunk analysis | 10 chunks | gemini-cli |
| `StagnantStateDetector` | Progress tracking | 5 turns | Original |
| `ToolResultSimilarityDetector` | Embeddings | 0.95 cosine | Original |

**Key Features**:
- Weighted scoring across detectors
- Session-level enable/disable
- LLM-based semantic detection fallback
- Telemetry hooks for observability

---

### 4. Recovery Strategies (`loop_detection/recovery.py`)
**Purpose**: Composable, escalating recovery actions.

**Escalation Levels**:
1. **GUIDANCE** → Inject "try a different approach" message
2. **TOOL_COOLDOWN** → Temporarily disable problematic tool (60s default)
3. **CONTEXT_RESET** → Summarize and compress context
4. **PROVIDER_FALLBACK** → Switch to alternative LLM
5. **TERMINATE** → Graceful session termination

**Usage**:
```python
result = await engine.check(event, session_id)
if result.detected:
    action = get_recovery_action(result.loop_type, result.detail, escalation_level)
    # Apply recovery...
```

---

### 5. ContextWindowManager (`context/manager.py`)
**Purpose**: Intelligent context compression and masking.

**Pipeline**:
```
Input Messages
    ↓
TokenBudget (track usage)
    ↓
ToolOutputMaskingService (if threshold exceeded)
    ↓
CompressionService (if still over budget)
    ↓
Emergency Truncate (last resort)
    ↓
Managed Messages
```

**Key Features**:
- Model-specific token limits
- **CompressionService**: Async queue-based summarization OUTSIDE main loop (fixes nested LLM risk)
- **ToolOutputMaskingService**: Backward-scanned FIFO masking with protection window
- Configurable thresholds via `ContextConfig`

---

### 6. ToolScheduler (`scheduler/executor.py`)
**Purpose**: State machine tool execution with deduplication and retry.

**State Machine**:
```
SCHEDULED → VALIDATING → EXECUTING → SUCCESS
                           ↓           ↓
                        ERROR     DEDUPLICATED
                           ↓
                       TIMEOUT/CANCELLED
```

**Key Features**:
- Content-hash deduplication (configurable TTL)
- Exponential backoff retry with jitter
- Per-tool cooldowns (integrates with loop recovery)
- Concurrent execution with semaphore limiting
- Structured execution logs

---

### 7. TelemetryCollector (`telemetry/collector.py`)
**Purpose**: Comprehensive observability for agent execution.

**Event Types**:
- Turn lifecycle (`TURN_START`, `TURN_END`, `TURN_TIMEOUT`)
- Loop events (`LOOP_DETECTED`, `LOOP_RECOVERY`)
- Tool events (`TOOL_CALL_START`, `TOOL_CALL_END`, `TOOL_COOLDOWN`, `TOOL_DEDUPLICATED`)
- Context events (`CONTEXT_COMPRESSED`, `CONTEXT_MASKED`)

**Aggregations**:
- `SessionMetrics`: turns, tool calls, loops, context ops, tokens, timing
- `LoopStatistics`: by type, recovery success rate, cooldowns

---

### 8. AgentRuntime (`agent_runtime.py`)
**Purpose**: Main orchestrator combining all components.

**Integration Points**:
```python
runtime = AgentRuntime.create(llm_provider, model_name="gpt-4")

async with runtime.turn(session_id) as ctx:
    # 1. Check for loops
    event = runtime.create_tool_call_event(tool_name, tool_args)
    loop_result = await runtime.check_loop(event, session_id)
    
    # 2. Execute tools
    results = await runtime.execute_tools(tool_calls, session_id)
    
    # 3. Manage context
    managed_result, messages = await runtime.manage_context(messages, session_id)

# Access telemetry
metrics = runtime.get_session_metrics(session_id)
```

---

## Settings Integration

New settings fields added to `logicore/config/settings.py`:

| Setting | Default | Environment Variable |
|---------|---------|---------------------|
| `RUNTIME_MAX_TURNS` | 60 | `RUNTIME_MAX_TURNS` |
| `LOOP_DETECTION_ENABLED` | True | `LOOP_DETECTION_ENABLED` |
| `LOOP_TOOL_THRESHOLD` | 5 | `LOOP_TOOL_THRESHOLD` |
| `CONTEXT_MAX_TOKENS` | 128000 | `CONTEXT_MAX_TOKENS` |
| `CONTEXT_COMPRESS_THRESHOLD` | 0.85 | `CONTEXT_COMPRESS_THRESHOLD` |
| `TOOL_EXECUTION_TIMEOUT` | 60 | `TOOL_EXECUTION_TIMEOUT` |
| `RETRY_MAX_ATTEMPTS` | 3 | `RETRY_MAX_ATTEMPTS` |
| `TELEMETRY_ENABLED` | True | `TELEMETRY_ENABLED` |

---

## Key Improvements

| Issue | Before | After |
|-------|--------|-------|
| **Infinite loops** | None | Multi-layer detection + escalating recovery |
| **Context overflow** | Manual truncation at 120 msgs | Model-aware TokenBudget + async compression |
| **Hardcoded thresholds** | 13+ scattered values | Centralized RuntimeConfig with env/TOML |
| **Retry logic** | Fixed 1s sleep | Exponential backoff with jitter |
| **Tool deduplication** | None | Content-hash with configurable TTL |
| **Observability** | Minimal logging | Comprehensive telemetry with JSON export |
| **Nested LLM risk** | Compression during loop | Async queue-based compression |

---

## Test Coverage

Unit tests created in `tests/test_runtime.py` covering:
- `TestRuntimeConfig`: Configuration loading and model context windows
- `TestTurnManager`: Turn lifecycle and budget tracking
- `TestLoopDetection`: Detector accuracy and session management
- `TestRecoveryStrategies`: Recovery action creation
- `TestToolScheduler`: Execution, deduplication, cooldowns
- `TestTelemetry`: Event recording and metrics aggregation
- `TestAgentRuntime`: Integration tests

---

## Usage Example

```python
from logicore.runtime import AgentRuntime, RuntimeConfig

# Create runtime with custom config
config = RuntimeConfig(max_turns=100)
runtime = AgentRuntime(config, llm_provider=provider, model_name="gpt-4o")

# Set tool executor
runtime.set_tool_executor(my_tool_executor)

# Execute with full runtime support
async with runtime.turn("session_123") as turn:
    # Check for loops before tool calls
    for tool_call in pending_tools:
        event = runtime.create_tool_call_event(tool_call.name, tool_call.args)
        loop_result = await runtime.check_loop(event, "session_123")
        
        if loop_result.detected:
            action = runtime.get_recovery_action(loop_result)
            if action.action_type == RecoveryActionType.TOOL_COOLDOWN:
                runtime.apply_tool_cooldown("session_123", action.tool_name)
                continue
    
    # Execute tools
    results = await runtime.execute_tools(pending_tools, "session_123")
    
    # Manage context before LLM call
    ctx_result, messages = await runtime.manage_context(history, "session_123")

# Export telemetry
print(runtime.export_telemetry("session_123"))
```

---

## Migration Path

1. **Immediate**: Existing `Agent.chat()` can continue unchanged
2. **Phase 1**: Wrap existing loop logic with `AgentRuntime.turn()` for turn budgeting
3. **Phase 2**: Add `check_loop()` calls before tool execution
4. **Phase 3**: Replace manual context truncation with `manage_context()`
5. **Phase 4**: Use `execute_tools()` for full scheduling benefits

---

## Files Created/Modified

### New Files (14)
- `logicore/runtime/__init__.py`
- `logicore/runtime/config.py`
- `logicore/runtime/turn_manager.py`
- `logicore/runtime/agent_runtime.py`
- `logicore/runtime/loop_detection/__init__.py`
- `logicore/runtime/loop_detection/engine.py`
- `logicore/runtime/loop_detection/detectors.py`
- `logicore/runtime/loop_detection/recovery.py`
- `logicore/runtime/context/__init__.py`
- `logicore/runtime/context/manager.py`
- `logicore/runtime/context/token_budget.py`
- `logicore/runtime/context/compression.py`
- `logicore/runtime/context/masking.py`
- `logicore/runtime/scheduler/__init__.py`
- `logicore/runtime/scheduler/executor.py`
- `logicore/runtime/telemetry/__init__.py`
- `logicore/runtime/telemetry/collector.py`
- `tests/test_runtime.py`

### Modified Files (1)
- `logicore/config/settings.py` (added runtime configuration section)

---

## Future Enhancements

1. **Multi-Agent Orchestration**: AgentRuntime architecture supports future supervisor/worker patterns
2. **OpenTelemetry Integration**: TelemetryCollector can be extended for distributed tracing
3. **Adaptive Thresholds**: Machine learning-based threshold adjustment based on session history
4. **Provider Failover**: ProviderFallbackStrategy groundwork for multi-provider resilience
