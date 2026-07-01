# Native Kotlin Agentic Runtime - Developer Implementation Guide

> **Version**: 1.0  
> **Target**: gemini-cli Android App  
> **Architecture**: Self-contained on-device agentic runtime (no Python backend)

---

## Table of Contents

1. [Phase 1: Runtime Core Foundation](#phase-1-runtime-core-foundation)
2. [Phase 2: Tool System](#phase-2-tool-system)
3. [Phase 3: Memory System](#phase-3-memory-system)
4. [Phase 4: Planning & Tracking](#phase-4-planning--tracking)
5. [Phase 5: Provider Abstraction](#phase-5-provider-abstraction)
6. [Phase 6: Skills System](#phase-6-skills-system)
7. [Phase 7: UI Integration](#phase-7-ui-integration)
8. [Phase 8: Wire AgentRuntime to ChatViewModel](#phase-8-wire-agentruntime-to-chatviewmodel)

---

# Phase 1: Runtime Core Foundation

## Overview

Create the core runtime package that orchestrates agent execution with bounded turns, loop detection, and reasoning control.

---

## Step 1.1: Create Package Structure

### What To Do
Create the following directory structure under `app/src/main/java/com/example/`:

```
runtime/
├── AgentRuntime.kt
├── config/
│   └── RuntimeConfig.kt
├── turn/
│   ├── TurnManager.kt
│   ├── TurnContext.kt
│   └── TurnStatus.kt
├── loop/
│   ├── LoopDetectionEngine.kt
│   ├── AgentEvent.kt
│   ├── LoopType.kt
│   └── detectors/
│       ├── HashDetector.kt
│       ├── ContentDetector.kt
│       └── StagnantDetector.kt
├── reasoning/
│   ├── ReasoningController.kt
│   ├── ReasoningLevel.kt
│   └── ReasoningState.kt
└── telemetry/
    └── RuntimeTelemetry.kt
```

### Why Do This
- The current app has NO runtime orchestration - it's a direct LLM call without turn limits, loop detection, or reasoning control
- This mirrors the Python `logicore/runtime/` architecture but implemented natively in Kotlin
- Provides a modular, testable architecture where each component can be unit tested independently

### Previous State (Before)
```kotlin
// ChatViewModel.kt - Current "dumb" implementation
private suspend fun executeLocalAILoop(userText: String) {
    val response = OpenCodeApiClient.api.chatCompletions(requestUrl, "Bearer $key", request)
    val aiText = response.choices?.firstOrNull()?.message?.content
    // No turn limits, no loop detection, no reasoning control
}
```

### New State (After)
```kotlin
// ChatViewModel.kt - Using AgentRuntime
private suspend fun executeLocalAILoop(userText: String) {
    val result = agentRuntime.executeTurn(userText, sessionId)
    // Runtime handles: turn limits, loop detection, reasoning, tool scheduling
}
```

### Test Cases

```kotlin
// RuntimePackageStructureTest.kt
class RuntimePackageStructureTest {
    
    @Test
    fun `verify all runtime classes exist`() {
        // Verify classes can be instantiated
        assertDoesNotThrow { RuntimeConfig() }
        assertDoesNotThrow { TurnManager(RuntimeConfig()) }
        assertDoesNotThrow { LoopDetectionEngine(RuntimeConfig()) }
        assertDoesNotThrow { ReasoningController() }
    }
    
    @Test
    fun `verify package hierarchy`() {
        // Use reflection to verify package structure
        val runtimePackage = "com.example.runtime"
        assertTrue(Class.forName("$runtimePackage.AgentRuntime") != null)
        assertTrue(Class.forName("$runtimePackage.turn.TurnManager") != null)
        assertTrue(Class.forName("$runtimePackage.loop.LoopDetectionEngine") != null)
    }
}
```

---

## Step 1.2: Implement RuntimeConfig

### What To Do
Create `runtime/config/RuntimeConfig.kt`:

```kotlin
package com.example.runtime.config

data class RuntimeConfig(
    // Turn Management
    val maxTurns: Int = 40,
    val turnTimeoutMs: Long = 120_000L, // 2 minutes per turn
    
    // Loop Detection
    val loopDetectionEnabled: Boolean = true,
    val toolCallThreshold: Int = 3,  // Same tool called 3x = loop
    val contentRepetitionThreshold: Int = 3,
    val contentChunkSize: Int = 100,
    val maxContentHistory: Int = 20,
    val stagnantTurnsThreshold: Int = 5,
    
    // Reasoning
    val defaultReasoningLevel: ReasoningLevel = ReasoningLevel.MEDIUM,
    val autoEscalate: Boolean = true,
    val autoEscalateKeywords: List<String> = listOf(
        "analyze", "debug", "architect", "design", "complex",
        "investigate", "optimize", "refactor", "security"
    ),
    
    // Tool Execution
    val toolTimeoutMs: Long = 30_000L,
    val maxRetryAttempts: Int = 3,
    val deduplicationTtlMs: Long = 300_000L, // 5 minutes
    
    // Telemetry
    val telemetryEnabled: Boolean = true
) {
    companion object {
        fun default() = RuntimeConfig()
        
        fun minimal() = RuntimeConfig(
            maxTurns = 10,
            loopDetectionEnabled = false,
            autoEscalate = false
        )
        
        fun deep() = RuntimeConfig(
            maxTurns = 100,
            defaultReasoningLevel = ReasoningLevel.HIGH,
            autoEscalate = true
        )
    }
}
```

### Why Do This
- Centralizes all runtime configuration in one place
- Allows different presets (minimal, default, deep) for different use cases
- Makes configuration testable and mockable
- Maps to Python's `RuntimeConfig` class in `logicore/runtime/config.py`

### Previous State (Before)
- No configuration - hardcoded values scattered across ChatViewModel
- No turn limits (infinite loops possible)
- No reasoning level control

### New State (After)
- Single source of truth for all runtime behavior
- Configurable limits prevent runaway execution
- Factory methods for common configurations

### Test Cases

```kotlin
// RuntimeConfigTest.kt
class RuntimeConfigTest {
    
    @Test
    fun `default config has sensible values`() {
        val config = RuntimeConfig.default()
        assertEquals(40, config.maxTurns)
        assertEquals(ReasoningLevel.MEDIUM, config.defaultReasoningLevel)
        assertTrue(config.loopDetectionEnabled)
    }
    
    @Test
    fun `minimal config disables advanced features`() {
        val config = RuntimeConfig.minimal()
        assertEquals(10, config.maxTurns)
        assertFalse(config.loopDetectionEnabled)
        assertFalse(config.autoEscalate)
    }
    
    @Test
    fun `custom config overrides work`() {
        val config = RuntimeConfig(
            maxTurns = 100,
            toolCallThreshold = 5
        )
        assertEquals(100, config.maxTurns)
        assertEquals(5, config.toolCallThreshold)
    }
}
```

---

## Step 1.3: Implement TurnManager

### What To Do
Create turn management classes:

**File: `runtime/turn/TurnStatus.kt`**
```kotlin
package com.example.runtime.turn

enum class TurnStatus {
    PENDING,    // Turn created but not started
    ACTIVE,     // Turn currently executing
    COMPLETED,  // Turn finished successfully
    FAILED,     // Turn finished with error
    CANCELLED,  // Turn was cancelled
    TIMEOUT     // Turn exceeded time limit
}
```

**File: `runtime/turn/TurnContext.kt`**
```kotlin
package com.example.runtime.turn

import java.time.Instant
import java.util.UUID

data class TurnContext(
    val turnId: String = UUID.randomUUID().toString(),
    val sessionId: String,
    val turnNumber: Int,
    var status: TurnStatus = TurnStatus.PENDING,
    
    // Timing
    val createdAt: Instant = Instant.now(),
    var startedAt: Instant? = null,
    var endedAt: Instant? = null,
    
    // Execution metadata
    var toolCalls: Int = 0,
    var tokensInput: Int = 0,
    var tokensOutput: Int = 0,
    
    // Error information
    var error: String? = null,
    var errorType: String? = null,
    
    // Recovery
    var recoveryAttempts: Int = 0,
    val recoveryActions: MutableList<String> = mutableListOf()
) {
    val durationMs: Long?
        get() = if (startedAt != null && endedAt != null) {
            java.time.Duration.between(startedAt, endedAt).toMillis()
        } else null
    
    val isTerminal: Boolean
        get() = status in listOf(
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
            TurnStatus.TIMEOUT
        )
    
    fun start() {
        status = TurnStatus.ACTIVE
        startedAt = Instant.now()
    }
    
    fun complete() {
        status = TurnStatus.COMPLETED
        endedAt = Instant.now()
    }
    
    fun fail(errorMessage: String, type: String? = null) {
        status = TurnStatus.FAILED
        error = errorMessage
        errorType = type
        endedAt = Instant.now()
    }
    
    fun timeout() {
        status = TurnStatus.TIMEOUT
        endedAt = Instant.now()
    }
}
```

**File: `runtime/turn/TurnManager.kt`**
```kotlin
package com.example.runtime.turn

import com.example.runtime.config.RuntimeConfig
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.TimeoutCancellationException
import java.time.Instant

class TurnManager(private val config: RuntimeConfig) {
    
    private val sessions = mutableMapOf<String, SessionState>()
    private val mutex = Mutex()
    
    // Lifecycle hooks
    private val onTurnStartCallbacks = mutableListOf<suspend (TurnContext) -> Unit>()
    private val onTurnEndCallbacks = mutableListOf<suspend (TurnContext) -> Unit>()
    private val onBudgetExceededCallbacks = mutableListOf<suspend (TurnContext) -> Unit>()
    
    data class SessionState(
        val sessionId: String,
        var turnsUsed: Int = 0,
        var budgetAdjustments: Int = 0,
        val createdAt: Instant = Instant.now(),
        var lastActivity: Instant = Instant.now(),
        var activeTurn: TurnContext? = null,
        val turnHistory: MutableList<TurnContext> = mutableListOf()
    )
    
    // --- Public API ---
    
    fun registerOnTurnStart(callback: suspend (TurnContext) -> Unit) {
        onTurnStartCallbacks.add(callback)
    }
    
    fun registerOnTurnEnd(callback: suspend (TurnContext) -> Unit) {
        onTurnEndCallbacks.add(callback)
    }
    
    fun registerOnBudgetExceeded(callback: suspend (TurnContext) -> Unit) {
        onBudgetExceededCallbacks.add(callback)
    }
    
    fun getRemainingTurns(sessionId: String): Int {
        val session = getOrCreateSession(sessionId)
        val effectiveBudget = config.maxTurns + session.budgetAdjustments
        return maxOf(0, effectiveBudget - session.turnsUsed)
    }
    
    fun isBudgetExceeded(sessionId: String): Boolean {
        return getRemainingTurns(sessionId) <= 0
    }
    
    suspend fun adjustBudget(sessionId: String, delta: Int): Int {
        return mutex.withLock {
            val session = getOrCreateSession(sessionId)
            session.budgetAdjustments += delta
            getRemainingTurns(sessionId)
        }
    }
    
    /**
     * Execute a block within a managed turn context.
     * 
     * Usage:
     * ```
     * turnManager.withTurn(sessionId) { turn ->
     *     // Your execution logic
     *     turn.toolCalls++
     * }
     * ```
     */
    suspend fun <T> withTurn(
        sessionId: String,
        parentTurnId: String? = null,
        block: suspend (TurnContext) -> T
    ): T {
        // Check budget before starting
        if (isBudgetExceeded(sessionId)) {
            val turn = createTurn(sessionId, parentTurnId)
            turn.fail("Turn budget exceeded", "BudgetExceeded")
            onBudgetExceededCallbacks.forEach { it(turn) }
            throw TurnBudgetExceededException(sessionId, config.maxTurns)
        }
        
        val turn = mutex.withLock {
            val session = getOrCreateSession(sessionId)
            val turnNumber = session.turnsUsed + 1
            TurnContext(
                sessionId = sessionId,
                turnNumber = turnNumber,
                parentTurnId = parentTurnId
            ).also {
                session.activeTurn = it
            }
        }
        
        // Notify start
        turn.start()
        onTurnStartCallbacks.forEach { it(turn) }
        
        return try {
            // Execute with timeout
            withTimeout(config.turnTimeoutMs) {
                block(turn)
            }.also {
                turn.complete()
            }
        } catch (e: TimeoutCancellationException) {
            turn.timeout()
            throw TurnTimeoutException(turn.turnId, config.turnTimeoutMs)
        } catch (e: Exception) {
            turn.fail(e.message ?: "Unknown error", e::class.simpleName)
            throw e
        } finally {
            // Record turn completion
            mutex.withLock {
                val session = getOrCreateSession(sessionId)
                session.turnsUsed++
                session.lastActivity = Instant.now()
                session.activeTurn = null
                session.turnHistory.add(turn)
            }
            
            // Notify end
            onTurnEndCallbacks.forEach { it(turn) }
        }
    }
    
    // --- Private Helpers ---
    
    private fun getOrCreateSession(sessionId: String): SessionState {
        return sessions.getOrPut(sessionId) { SessionState(sessionId) }
    }
    
    private fun createTurn(sessionId: String, parentTurnId: String?): TurnContext {
        val session = getOrCreateSession(sessionId)
        return TurnContext(
            sessionId = sessionId,
            turnNumber = session.turnsUsed + 1,
            parentTurnId = parentTurnId
        )
    }
}

// Custom exceptions
class TurnBudgetExceededException(
    sessionId: String, 
    maxTurns: Int
) : Exception("Session $sessionId exceeded turn budget of $maxTurns")

class TurnTimeoutException(
    turnId: String,
    timeoutMs: Long
) : Exception("Turn $turnId timed out after ${timeoutMs}ms")
```

### Why Do This
- **Turn Limits**: Current app has no limits - an agent could run forever
- **Timeout Protection**: Prevents hung operations from blocking the UI
- **Budget Tracking**: Allows dynamic adjustment if user wants more turns
- **Lifecycle Hooks**: Enables telemetry collection and debugging
- **Maps to**: Python `logicore/runtime/turn_manager.py`

### Previous State (Before)
```kotlin
// No turn management - unlimited execution
while (hasMoreToolCalls) {
    executeTools()  // Could run forever!
}
```

### New State (After)
```kotlin
// Bounded execution with timeout
turnManager.withTurn(sessionId) { turn ->
    executeTools()
    turn.toolCalls++
}  // Auto-completes, times out, or fails gracefully
```

### Test Cases

```kotlin
// TurnManagerTest.kt
@OptIn(ExperimentalCoroutinesApi::class)
class TurnManagerTest {
    
    private lateinit var config: RuntimeConfig
    private lateinit var manager: TurnManager
    
    @Before
    fun setup() {
        config = RuntimeConfig(maxTurns = 5, turnTimeoutMs = 1000)
        manager = TurnManager(config)
    }
    
    @Test
    fun `first turn has correct turn number`() = runTest {
        manager.withTurn("session1") { turn ->
            assertEquals(1, turn.turnNumber)
        }
    }
    
    @Test
    fun `turns increment correctly`() = runTest {
        manager.withTurn("session1") { turn -> assertEquals(1, turn.turnNumber) }
        manager.withTurn("session1") { turn -> assertEquals(2, turn.turnNumber) }
        manager.withTurn("session1") { turn -> assertEquals(3, turn.turnNumber) }
    }
    
    @Test
    fun `budget exceeded throws exception`() = runTest {
        repeat(5) {
            manager.withTurn("session1") { /* use up budget */ }
        }
        
        assertThrows<TurnBudgetExceededException> {
            manager.withTurn("session1") { /* should fail */ }
        }
    }
    
    @Test
    fun `remaining turns decrements`() = runTest {
        assertEquals(5, manager.getRemainingTurns("session1"))
        manager.withTurn("session1") { }
        assertEquals(4, manager.getRemainingTurns("session1"))
    }
    
    @Test
    fun `turn timeout works`() = runTest {
        assertThrows<TurnTimeoutException> {
            manager.withTurn("session1") {
                delay(2000)  // Exceeds 1000ms timeout
            }
        }
    }
    
    @Test
    fun `lifecycle hooks are called`() = runTest {
        var startCalled = false
        var endCalled = false
        
        manager.registerOnTurnStart { startCalled = true }
        manager.registerOnTurnEnd { endCalled = true }
        
        manager.withTurn("session1") { }
        
        assertTrue(startCalled)
        assertTrue(endCalled)
    }
    
    @Test
    fun `budget adjustment works`() = runTest {
        repeat(5) { manager.withTurn("session1") { } }
        
        // Should fail
        assertThrows<TurnBudgetExceededException> {
            manager.withTurn("session1") { }
        }
        
        // Adjust budget
        manager.adjustBudget("session1", 5)
        
        // Should succeed now
        assertDoesNotThrow {
            manager.withTurn("session1") { }
        }
    }
}
```

---

## Step 1.4: Implement LoopDetectionEngine

### What To Do

**File: `runtime/loop/LoopType.kt`**
```kotlin
package com.example.runtime.loop

enum class LoopType {
    CONSECUTIVE_TOOL_CALLS,  // Same tool called repeatedly
    CONTENT_REPETITION,      // Same output text repeated
    SEMANTIC_LOOP,           // LLM detects conversation cycling
    STAGNANT_STATE,          // No progress for N turns
    TOOL_RESULT_SIMILARITY   // Identical tool results
}
```

**File: `runtime/loop/AgentEvent.kt`**
```kotlin
package com.example.runtime.loop

import org.json.JSONObject
import java.security.MessageDigest
import java.time.Instant

enum class AgentEventType {
    TOOL_CALL,
    TOOL_RESULT,
    CONTENT,
    TURN_START,
    TURN_END
}

data class AgentEvent(
    val type: AgentEventType,
    val timestamp: Instant = Instant.now(),
    
    // Tool call event data
    val toolName: String? = null,
    val toolArgs: Map<String, Any?>? = null,
    val toolResult: String? = null,
    val toolSuccess: Boolean? = null,
    
    // Content event data
    val content: String? = null,
    
    // Turn event data
    val turnId: String? = null,
    val turnNumber: Int? = null
) {
    fun getToolCallHash(): String? {
        if (type != AgentEventType.TOOL_CALL || toolName == null) return null
        
        val argsJson = JSONObject(toolArgs ?: emptyMap<String, Any?>()).toString()
        val key = "$toolName:$argsJson"
        return MessageDigest.getInstance("SHA-256")
            .digest(key.toByteArray())
            .joinToString("") { "%02x".format(it) }
    }
}
```

**File: `runtime/loop/LoopDetectionResult.kt`**
```kotlin
package com.example.runtime.loop

data class LoopDetectionResult(
    val detected: Boolean = false,
    val loopType: LoopType? = null,
    val confidence: Float = 0f,
    val detail: String? = null,
    val repetitionCount: Int = 0,
    val suggestedRecoveryAction: RecoveryAction? = null
)

enum class RecoveryAction {
    ESCALATE_REASONING,    // Request deeper thinking
    PROVIDE_CONTEXT_HINT,  // Supply additional information
    SUGGEST_ALTERNATIVES,  // Try different approach
    INTERRUPT_EXECUTION,   // Ask user for guidance
    CLEAR_STATE           // Reset conversation context
}
```

**File: `runtime/loop/detectors/HashDetector.kt`**
```kotlin
package com.example.runtime.loop.detectors

import com.example.runtime.loop.*

/**
 * Detects consecutive identical tool calls by hashing tool name + args.
 */
class HashDetector(private val threshold: Int = 3) {
    
    private val recentHashes = mutableListOf<String>()
    private val maxHistory = 20
    
    fun analyze(event: AgentEvent): LoopDetectionResult {
        if (event.type != AgentEventType.TOOL_CALL) {
            return LoopDetectionResult(detected = false)
        }
        
        val hash = event.getToolCallHash() ?: return LoopDetectionResult(detected = false)
        
        // Add to history
        recentHashes.add(hash)
        if (recentHashes.size > maxHistory) {
            recentHashes.removeAt(0)
        }
        
        // Count consecutive occurrences
        var consecutiveCount = 0
        for (i in recentHashes.indices.reversed()) {
            if (recentHashes[i] == hash) {
                consecutiveCount++
            } else {
                break
            }
        }
        
        return if (consecutiveCount >= threshold) {
            LoopDetectionResult(
                detected = true,
                loopType = LoopType.CONSECUTIVE_TOOL_CALLS,
                confidence = minOf(1f, consecutiveCount / threshold.toFloat()),
                detail = "Tool '${event.toolName}' called $consecutiveCount times consecutively",
                repetitionCount = consecutiveCount,
                suggestedRecoveryAction = RecoveryAction.ESCALATE_REASONING
            )
        } else {
            LoopDetectionResult(detected = false)
        }
    }
    
    fun reset() {
        recentHashes.clear()
    }
}
```

**File: `runtime/loop/detectors/ContentDetector.kt`**
```kotlin
package com.example.runtime.loop.detectors

import com.example.runtime.loop.*

/**
 * Detects repeated content chunks in LLM output.
 */
class ContentDetector(
    private val threshold: Int = 3,
    private val chunkSize: Int = 100,
    private val maxHistory: Int = 20
) {
    private data class ContentChunk(val hash: Int, val content: String)
    
    private val recentChunks = mutableListOf<ContentChunk>()
    
    fun analyze(event: AgentEvent): LoopDetectionResult {
        if (event.type != AgentEventType.CONTENT || event.content.isNullOrBlank()) {
            return LoopDetectionResult(detected = false)
        }
        
        val content = event.content
        
        // Extract chunks
        val chunks = content.chunked(chunkSize).map { chunk ->
            ContentChunk(chunk.hashCode(), chunk)
        }
        
        // Check for repetition
        for (chunk in chunks) {
            val occurrences = recentChunks.count { it.hash == chunk.hash }
            
            if (occurrences >= threshold) {
                return LoopDetectionResult(
                    detected = true,
                    loopType = LoopType.CONTENT_REPETITION,
                    confidence = minOf(1f, (occurrences + 1) / threshold.toFloat()),
                    detail = "Content chunk repeated ${occurrences + 1} times",
                    repetitionCount = occurrences + 1,
                    suggestedRecoveryAction = RecoveryAction.PROVIDE_CONTEXT_HINT
                )
            }
        }
        
        // Add chunks to history
        recentChunks.addAll(chunks)
        while (recentChunks.size > maxHistory * chunkSize / 100) {
            recentChunks.removeAt(0)
        }
        
        return LoopDetectionResult(detected = false)
    }
    
    fun reset() {
        recentChunks.clear()
    }
}
```

**File: `runtime/loop/detectors/StagnantDetector.kt`**
```kotlin
package com.example.runtime.loop.detectors

import com.example.runtime.loop.*

/**
 * Detects when no progress is being made across multiple turns.
 */
class StagnantDetector(private val threshold: Int = 5) {
    
    private var turnsWithoutProgress = 0
    private var lastKnownState: String? = null
    
    fun analyze(event: AgentEvent): LoopDetectionResult {
        if (event.type != AgentEventType.TURN_END) {
            return LoopDetectionResult(detected = false)
        }
        
        // Simple heuristic: if content hasn't meaningfully changed
        val currentState = event.content?.take(200) ?: ""
        
        if (currentState == lastKnownState) {
            turnsWithoutProgress++
        } else {
            turnsWithoutProgress = 0
            lastKnownState = currentState
        }
        
        return if (turnsWithoutProgress >= threshold) {
            LoopDetectionResult(
                detected = true,
                loopType = LoopType.STAGNANT_STATE,
                confidence = minOf(1f, turnsWithoutProgress / threshold.toFloat()),
                detail = "No progress detected for $turnsWithoutProgress turns",
                repetitionCount = turnsWithoutProgress,
                suggestedRecoveryAction = RecoveryAction.INTERRUPT_EXECUTION
            )
        } else {
            LoopDetectionResult(detected = false)
        }
    }
    
    fun reset() {
        turnsWithoutProgress = 0
        lastKnownState = null
    }
}
```

**File: `runtime/loop/LoopDetectionEngine.kt`**
```kotlin
package com.example.runtime.loop

import com.example.runtime.config.RuntimeConfig
import com.example.runtime.loop.detectors.*

/**
 * Multi-layer loop detection engine with pluggable detectors.
 * 
 * Combines multiple detection strategies:
 * 1. Hash-based: Identical tool calls (fast, exact)
 * 2. Content-based: Repeated output chunks (streaming-safe)
 * 3. Stagnant: No progress detection (state-based)
 */
class LoopDetectionEngine(private val config: RuntimeConfig) {
    
    private val hashDetector = HashDetector(config.toolCallThreshold)
    private val contentDetector = ContentDetector(
        threshold = config.contentRepetitionThreshold,
        chunkSize = config.contentChunkSize,
        maxHistory = config.maxContentHistory
    )
    private val stagnantDetector = StagnantDetector(config.stagnantTurnsThreshold)
    
    private val disabledSessions = mutableSetOf<String>()
    private val onDetectionCallbacks = mutableListOf<suspend (LoopDetectionResult) -> Unit>()
    
    fun registerOnDetection(callback: suspend (LoopDetectionResult) -> Unit) {
        onDetectionCallbacks.add(callback)
    }
    
    fun disableForSession(sessionId: String) {
        disabledSessions.add(sessionId)
    }
    
    fun enableForSession(sessionId: String) {
        disabledSessions.remove(sessionId)
    }
    
    fun isDisabled(sessionId: String): Boolean {
        return !config.loopDetectionEnabled || sessionId in disabledSessions
    }
    
    /**
     * Check an event for loop patterns.
     * Returns the highest-confidence detection result.
     */
    suspend fun check(event: AgentEvent, sessionId: String): LoopDetectionResult {
        if (isDisabled(sessionId)) {
            return LoopDetectionResult(detected = false)
        }
        
        // Run all detectors
        val results = listOf(
            hashDetector.analyze(event),
            contentDetector.analyze(event),
            stagnantDetector.analyze(event)
        )
        
        // Find highest confidence detection
        val detected = results
            .filter { it.detected }
            .maxByOrNull { it.confidence }
        
        if (detected != null) {
            onDetectionCallbacks.forEach { it(detected) }
        }
        
        return detected ?: LoopDetectionResult(detected = false)
    }
    
    /**
     * Get appropriate recovery action for a detection result.
     */
    fun getRecoveryAction(result: LoopDetectionResult): RecoveryAction {
        return result.suggestedRecoveryAction ?: when (result.loopType) {
            LoopType.CONSECUTIVE_TOOL_CALLS -> RecoveryAction.ESCALATE_REASONING
            LoopType.CONTENT_REPETITION -> RecoveryAction.PROVIDE_CONTEXT_HINT
            LoopType.SEMANTIC_LOOP -> RecoveryAction.CLEAR_STATE
            LoopType.STAGNANT_STATE -> RecoveryAction.INTERRUPT_EXECUTION
            LoopType.TOOL_RESULT_SIMILARITY -> RecoveryAction.SUGGEST_ALTERNATIVES
            null -> RecoveryAction.INTERRUPT_EXECUTION
        }
    }
    
    fun resetSession(sessionId: String) {
        hashDetector.reset()
        contentDetector.reset()
        stagnantDetector.reset()
    }
}
```

### Why Do This
- **Prevent Infinite Loops**: Current app has NO loop detection - an LLM can get stuck calling the same tool forever
- **Multiple Strategies**: Different loop types require different detection approaches
- **Recovery Actions**: Provides actionable suggestions when a loop is detected
- **Maps to**: Python `logicore/runtime/loop_detection/engine.py`

### Previous State (Before)
```kotlin
// No loop detection - infinite loops possible
while (aiResponse.containsToolCall()) {
    executeTool()  // Could be the same tool forever!
    aiResponse = callLLM()
}
```

### New State (After)
```kotlin
// Loop detection integrated
val result = loopEngine.check(AgentEvent(
    type = AgentEventType.TOOL_CALL,
    toolName = "shell_exec",
    toolArgs = mapOf("command" to "ls")
), sessionId)

if (result.detected) {
    val recovery = loopEngine.getRecoveryAction(result)
    // Handle recovery: escalate reasoning, provide hints, etc.
}
```

### Test Cases

```kotlin
// LoopDetectionEngineTest.kt
@OptIn(ExperimentalCoroutinesApi::class)
class LoopDetectionEngineTest {
    
    private lateinit var config: RuntimeConfig
    private lateinit var engine: LoopDetectionEngine
    
    @Before
    fun setup() {
        config = RuntimeConfig(
            toolCallThreshold = 3,
            contentRepetitionThreshold = 3,
            stagnantTurnsThreshold = 3
        )
        engine = LoopDetectionEngine(config)
    }
    
    @Test
    fun `detects consecutive tool calls`() = runTest {
        val event = AgentEvent(
            type = AgentEventType.TOOL_CALL,
            toolName = "shell_exec",
            toolArgs = mapOf("command" to "ls")
        )
        
        // First two calls - no detection
        assertFalse(engine.check(event, "session1").detected)
        assertFalse(engine.check(event, "session1").detected)
        
        // Third call - should detect
        val result = engine.check(event, "session1")
        assertTrue(result.detected)
        assertEquals(LoopType.CONSECUTIVE_TOOL_CALLS, result.loopType)
        assertEquals(3, result.repetitionCount)
    }
    
    @Test
    fun `different tools dont trigger detection`() = runTest {
        val event1 = AgentEvent(
            type = AgentEventType.TOOL_CALL,
            toolName = "shell_exec",
            toolArgs = mapOf("command" to "ls")
        )
        val event2 = AgentEvent(
            type = AgentEventType.TOOL_CALL,
            toolName = "read_file",
            toolArgs = mapOf("path" to "test.txt")
        )
        
        engine.check(event1, "session1")
        engine.check(event2, "session1")
        engine.check(event1, "session1")
        
        // Different tools interleaved - no detection
        val result = engine.check(event2, "session1")
        assertFalse(result.detected)
    }
    
    @Test
    fun `detects content repetition`() = runTest {
        val repeatedContent = "This is repeated content that keeps appearing."
        val event = AgentEvent(
            type = AgentEventType.CONTENT,
            content = repeatedContent
        )
        
        // First two - no detection
        assertFalse(engine.check(event, "session1").detected)
        assertFalse(engine.check(event, "session1").detected)
        
        // Third - should detect
        val result = engine.check(event, "session1")
        assertTrue(result.detected)
        assertEquals(LoopType.CONTENT_REPETITION, result.loopType)
    }
    
    @Test
    fun `disabled sessions bypass detection`() = runTest {
        engine.disableForSession("session1")
        
        val event = AgentEvent(
            type = AgentEventType.TOOL_CALL,
            toolName = "shell_exec",
            toolArgs = mapOf("command" to "ls")
        )
        
        repeat(5) {
            assertFalse(engine.check(event, "session1").detected)
        }
    }
    
    @Test
    fun `recovery action is appropriate for loop type`() {
        val toolLoopResult = LoopDetectionResult(
            detected = true,
            loopType = LoopType.CONSECUTIVE_TOOL_CALLS
        )
        assertEquals(
            RecoveryAction.ESCALATE_REASONING,
            engine.getRecoveryAction(toolLoopResult)
        )
        
        val stagnantResult = LoopDetectionResult(
            detected = true,
            loopType = LoopType.STAGNANT_STATE
        )
        assertEquals(
            RecoveryAction.INTERRUPT_EXECUTION,
            engine.getRecoveryAction(stagnantResult)
        )
    }
    
    @Test
    fun `callbacks are invoked on detection`() = runTest {
        var callbackInvoked = false
        var detectedResult: LoopDetectionResult? = null
        
        engine.registerOnDetection { result ->
            callbackInvoked = true
            detectedResult = result
        }
        
        val event = AgentEvent(
            type = AgentEventType.TOOL_CALL,
            toolName = "test",
            toolArgs = mapOf("arg" to "value")
        )
        
        repeat(3) { engine.check(event, "session1") }
        
        assertTrue(callbackInvoked)
        assertNotNull(detectedResult)
        assertTrue(detectedResult!!.detected)
    }
}
```

---

## Step 1.5: Implement ReasoningController

### What To Do

**File: `runtime/reasoning/ReasoningLevel.kt`**
```kotlin
package com.example.runtime.reasoning

enum class ReasoningLevel(
    val value: Int,
    val thinkingBudgetMs: Long,
    val tokenBudget: Int,
    val systemPromptAddon: String
) {
    MINIMAL(
        value = 1,
        thinkingBudgetMs = 0,
        tokenBudget = 512,
        systemPromptAddon = "Be concise. Give direct answers."
    ),
    LOW(
        value = 2,
        thinkingBudgetMs = 5_000,
        tokenBudget = 1024,
        systemPromptAddon = "Think briefly before answering."
    ),
    MEDIUM(
        value = 3,
        thinkingBudgetMs = 15_000,
        tokenBudget = 2048,
        systemPromptAddon = "Think step by step. Consider alternatives."
    ),
    HIGH(
        value = 4,
        thinkingBudgetMs = 60_000,
        tokenBudget = 4096,
        systemPromptAddon = "Think deeply. Analyze from multiple angles. Consider edge cases."
    ),
    DEEP(
        value = 5,
        thinkingBudgetMs = 300_000,
        tokenBudget = 8192,
        systemPromptAddon = "Think exhaustively. Explore all possibilities. Verify your reasoning."
    );
    
    companion object {
        fun fromValue(value: Int): ReasoningLevel {
            return values().find { it.value == value } ?: MEDIUM
        }
    }
}
```

**File: `runtime/reasoning/ReasoningState.kt`**
```kotlin
package com.example.runtime.reasoning

import java.time.Instant

data class ReasoningState(
    var currentLevel: ReasoningLevel,
    val originalLevel: ReasoningLevel,
    var escalationCount: Int = 0,
    var deEscalationCount: Int = 0,
    var lastAdjustment: Instant? = null,
    val adjustmentHistory: MutableList<ReasoningAdjustment> = mutableListOf()
)

data class ReasoningAdjustment(
    val from: ReasoningLevel,
    val to: ReasoningLevel,
    val reason: String,
    val timestamp: Instant = Instant.now()
)
```

**File: `runtime/reasoning/ReasoningController.kt`**
```kotlin
package com.example.runtime.reasoning

import com.example.runtime.config.RuntimeConfig
import java.time.Instant

/**
 * Controls reasoning level dynamically during agent execution.
 * 
 * Features:
 * - Set reasoning level programmatically
 * - Auto-escalate on complex queries
 * - Track reasoning adjustments
 * - Generate appropriate system prompt addons
 */
class ReasoningController(
    private val config: RuntimeConfig = RuntimeConfig()
) {
    private var state = ReasoningState(
        currentLevel = config.defaultReasoningLevel,
        originalLevel = config.defaultReasoningLevel
    )
    
    private val levelChangeCallbacks = mutableListOf<(ReasoningLevel, ReasoningLevel) -> Unit>()
    
    val currentLevel: ReasoningLevel
        get() = state.currentLevel
    
    val thinkingBudget: Long
        get() = state.currentLevel.thinkingBudgetMs
    
    val tokenBudget: Int
        get() = state.currentLevel.tokenBudget
    
    fun onLevelChange(callback: (ReasoningLevel, ReasoningLevel) -> Unit) {
        levelChangeCallbacks.add(callback)
    }
    
    /**
     * Set reasoning level explicitly.
     */
    fun setLevel(level: ReasoningLevel, reason: String = "manual") {
        val oldLevel = state.currentLevel
        if (oldLevel == level) return
        
        state.currentLevel = level
        state.lastAdjustment = Instant.now()
        state.adjustmentHistory.add(ReasoningAdjustment(
            from = oldLevel,
            to = level,
            reason = reason
        ))
        
        levelChangeCallbacks.forEach { it(oldLevel, level) }
    }
    
    /**
     * Escalate reasoning level by one step.
     */
    fun escalate(reason: String = "complexity_detected"): ReasoningLevel {
        val levels = ReasoningLevel.values()
        val currentIdx = levels.indexOf(state.currentLevel)
        
        if (currentIdx < levels.size - 1) {
            val newLevel = levels[currentIdx + 1]
            setLevel(newLevel, reason)
            state.escalationCount++
        }
        
        return state.currentLevel
    }
    
    /**
     * De-escalate reasoning level by one step.
     */
    fun deEscalate(reason: String = "simple_query"): ReasoningLevel {
        val levels = ReasoningLevel.values()
        val currentIdx = levels.indexOf(state.currentLevel)
        
        if (currentIdx > 0) {
            val newLevel = levels[currentIdx - 1]
            setLevel(newLevel, reason)
            state.deEscalationCount++
        }
        
        return state.currentLevel
    }
    
    /**
     * Reset to original reasoning level.
     */
    fun reset() {
        setLevel(state.originalLevel, "reset")
        state.escalationCount = 0
        state.deEscalationCount = 0
    }
    
    /**
     * Automatically adjust reasoning level based on query complexity.
     */
    fun adjustForQuery(query: String): ReasoningLevel {
        if (!config.autoEscalate) return state.currentLevel
        
        if (shouldEscalate(query)) {
            return escalate("auto_escalation_query_complexity")
        }
        
        if (shouldDeEscalate(query)) {
            return deEscalate("auto_de_escalation_simple_query")
        }
        
        return state.currentLevel
    }
    
    /**
     * Get system prompt addon for current reasoning level.
     */
    fun getSystemPromptAddon(): String {
        return state.currentLevel.systemPromptAddon
    }
    
    /**
     * Get current state for telemetry/debugging.
     */
    fun getState(): ReasoningState = state.copy()
    
    // --- Private Helpers ---
    
    private fun shouldEscalate(query: String): Boolean {
        val queryLower = query.lowercase()
        
        // Check configured keywords
        if (config.autoEscalateKeywords.any { it in queryLower }) {
            return true
        }
        
        // Check for complexity patterns
        val complexPatterns = listOf(
            Regex("why does.+not work"),
            Regex("how (can|do|should) (i|we).+multiple"),
            Regex("what('s| is) the (best|optimal|right) (way|approach)"),
            Regex("debug.+(error|issue|problem|bug)"),
            Regex("(analyze|investigate|diagnose)"),
            Regex("step.?by.?step"),
            Regex("(comprehensive|thorough|detailed) (analysis|review|audit)")
        )
        
        return complexPatterns.any { it.containsMatchIn(queryLower) }
    }
    
    private fun shouldDeEscalate(query: String): Boolean {
        val queryLower = query.lowercase()
        
        val simplePatterns = listOf(
            Regex("^(what|who|when|where) is \\w+\\??$"),
            Regex("^(hi|hello|hey|thanks|thank you)"),
            Regex("^yes$|^no$|^ok$|^okay$"),
            Regex("^\\d+$")
        )
        
        return simplePatterns.any { it.matches(queryLower) }
    }
}
```

### Why Do This
- **Adaptive Reasoning**: Simple questions get fast answers, complex ones get deep analysis
- **User Control**: Users can manually set reasoning depth via UI slider
- **Cost Optimization**: Lower reasoning = fewer tokens = lower API costs
- **Maps to**: Python `logicore/runtime/reasoning/controller.py`

### Previous State (Before)
```kotlin
// No reasoning control - same behavior for all queries
val response = api.generateContent(messages)
```

### New State (After)
```kotlin
// Dynamic reasoning based on query
reasoningController.adjustForQuery(userQuery)
val systemAddon = reasoningController.getSystemPromptAddon()
val response = api.generateContent(
    messages = messages,
    systemInstruction = basePrompt + systemAddon
)
```

### Test Cases

```kotlin
// ReasoningControllerTest.kt
class ReasoningControllerTest {
    
    private lateinit var controller: ReasoningController
    
    @Before
    fun setup() {
        controller = ReasoningController(RuntimeConfig())
    }
    
    @Test
    fun `default level is MEDIUM`() {
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `manual level setting works`() {
        controller.setLevel(ReasoningLevel.HIGH, "test")
        assertEquals(ReasoningLevel.HIGH, controller.currentLevel)
    }
    
    @Test
    fun `escalation increases level`() {
        controller.setLevel(ReasoningLevel.LOW)
        controller.escalate()
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `de-escalation decreases level`() {
        controller.setLevel(ReasoningLevel.HIGH)
        controller.deEscalate()
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `cannot escalate beyond DEEP`() {
        controller.setLevel(ReasoningLevel.DEEP)
        controller.escalate()
        assertEquals(ReasoningLevel.DEEP, controller.currentLevel)
    }
    
    @Test
    fun `cannot de-escalate below MINIMAL`() {
        controller.setLevel(ReasoningLevel.MINIMAL)
        controller.deEscalate()
        assertEquals(ReasoningLevel.MINIMAL, controller.currentLevel)
    }
    
    @Test
    fun `auto-escalates on complex query`() {
        controller.setLevel(ReasoningLevel.LOW)
        controller.adjustForQuery("Can you debug this error in my code?")
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `auto-escalates on analyze keyword`() {
        controller.setLevel(ReasoningLevel.LOW)
        controller.adjustForQuery("Analyze this architecture")
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `no escalation on simple query`() {
        controller.setLevel(ReasoningLevel.MEDIUM)
        controller.adjustForQuery("What is Python?")
        // Should not change
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `reset returns to original level`() {
        controller.setLevel(ReasoningLevel.DEEP)
        controller.reset()
        assertEquals(ReasoningLevel.MEDIUM, controller.currentLevel)
    }
    
    @Test
    fun `system prompt addon varies by level`() {
        controller.setLevel(ReasoningLevel.MINIMAL)
        assertTrue(controller.getSystemPromptAddon().contains("concise"))
        
        controller.setLevel(ReasoningLevel.DEEP)
        assertTrue(controller.getSystemPromptAddon().contains("exhaustively"))
    }
    
    @Test
    fun `level change callback is invoked`() {
        var callbackInvoked = false
        var fromLevel: ReasoningLevel? = null
        var toLevel: ReasoningLevel? = null
        
        controller.onLevelChange { from, to ->
            callbackInvoked = true
            fromLevel = from
            toLevel = to
        }
        
        controller.setLevel(ReasoningLevel.HIGH)
        
        assertTrue(callbackInvoked)
        assertEquals(ReasoningLevel.MEDIUM, fromLevel)
        assertEquals(ReasoningLevel.HIGH, toLevel)
    }
    
    @Test
    fun `adjustment history is recorded`() {
        controller.setLevel(ReasoningLevel.HIGH, "test1")
        controller.setLevel(ReasoningLevel.LOW, "test2")
        
        val state = controller.getState()
        assertEquals(2, state.adjustmentHistory.size)
        assertEquals("test1", state.adjustmentHistory[0].reason)
        assertEquals("test2", state.adjustmentHistory[1].reason)
    }
}
```

---

## Step 1.6: Implement AgentRuntime

### What To Do

**File: `runtime/AgentRuntime.kt`**
```kotlin
package com.example.runtime

import com.example.runtime.config.RuntimeConfig
import com.example.runtime.turn.*
import com.example.runtime.loop.*
import com.example.runtime.reasoning.*
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * Production-grade orchestrator for agent execution.
 * 
 * Combines all runtime components into a unified interface:
 * - Bounded turn execution
 * - Multi-layer loop detection with recovery
 * - Dynamic reasoning control
 * - Comprehensive telemetry
 */
class AgentRuntime(
    val config: RuntimeConfig = RuntimeConfig()
) {
    // Components
    val turnManager = TurnManager(config)
    val loopEngine = LoopDetectionEngine(config)
    val reasoningController = ReasoningController(config)
    
    // Event stream for UI
    private val _events = MutableSharedFlow<RuntimeEvent>(extraBufferCapacity = 64)
    val events: SharedFlow<RuntimeEvent> = _events.asSharedFlow()
    
    init {
        setupTelemetryHooks()
    }
    
    private fun setupTelemetryHooks() {
        // Turn lifecycle events
        turnManager.registerOnTurnStart { turn ->
            _events.emit(RuntimeEvent.TurnStarted(turn))
        }
        
        turnManager.registerOnTurnEnd { turn ->
            _events.emit(RuntimeEvent.TurnCompleted(turn))
        }
        
        turnManager.registerOnBudgetExceeded { turn ->
            _events.emit(RuntimeEvent.BudgetExceeded(turn))
        }
        
        // Loop detection events
        loopEngine.registerOnDetection { result ->
            _events.emit(RuntimeEvent.LoopDetected(result))
        }
        
        // Reasoning change events
        reasoningController.onLevelChange { from, to ->
            _events.tryEmit(RuntimeEvent.ReasoningLevelChanged(from, to))
        }
    }
    
    /**
     * Execute an agentic turn with full orchestration.
     */
    suspend fun <T> executeTurn(
        sessionId: String,
        block: suspend TurnExecutionContext.() -> T
    ): T {
        return turnManager.withTurn(sessionId) { turn ->
            val context = TurnExecutionContext(
                turn = turn,
                runtime = this@AgentRuntime
            )
            block(context)
        }
    }
    
    /**
     * Check for loop patterns in an event.
     */
    suspend fun checkLoop(event: AgentEvent, sessionId: String): LoopDetectionResult {
        return loopEngine.check(event, sessionId)
    }
    
    /**
     * Adjust reasoning based on query.
     */
    fun adjustReasoningForQuery(query: String): ReasoningLevel {
        return reasoningController.adjustForQuery(query)
    }
    
    /**
     * Get system prompt with reasoning addon.
     */
    fun getEnhancedSystemPrompt(basePrompt: String): String {
        val addon = reasoningController.getSystemPromptAddon()
        return "$basePrompt\n\n$addon"
    }
    
    /**
     * Get remaining turn budget.
     */
    fun getRemainingTurns(sessionId: String): Int {
        return turnManager.getRemainingTurns(sessionId)
    }
    
    /**
     * Reset session state.
     */
    fun resetSession(sessionId: String) {
        loopEngine.resetSession(sessionId)
        reasoningController.reset()
    }
    
    companion object {
        fun create(config: RuntimeConfig = RuntimeConfig()): AgentRuntime {
            return AgentRuntime(config)
        }
    }
}

/**
 * Context available during turn execution.
 */
class TurnExecutionContext(
    val turn: TurnContext,
    private val runtime: AgentRuntime
) {
    fun recordToolCall() {
        turn.toolCalls++
    }
    
    fun recordTokens(input: Int, output: Int) {
        turn.tokensInput += input
        turn.tokensOutput += output
    }
    
    suspend fun checkLoop(event: AgentEvent): LoopDetectionResult {
        return runtime.checkLoop(event, turn.sessionId)
    }
    
    val currentReasoningLevel: ReasoningLevel
        get() = runtime.reasoningController.currentLevel
}

/**
 * Events emitted by the runtime for UI/telemetry.
 */
sealed class RuntimeEvent {
    data class TurnStarted(val turn: TurnContext) : RuntimeEvent()
    data class TurnCompleted(val turn: TurnContext) : RuntimeEvent()
    data class BudgetExceeded(val turn: TurnContext) : RuntimeEvent()
    data class LoopDetected(val result: LoopDetectionResult) : RuntimeEvent()
    data class ReasoningLevelChanged(
        val from: ReasoningLevel,
        val to: ReasoningLevel
    ) : RuntimeEvent()
    data class ToolExecuted(
        val toolName: String,
        val success: Boolean,
        val durationMs: Long
    ) : RuntimeEvent()
    data class ProgressUpdate(
        val message: String,
        val percent: Int
    ) : RuntimeEvent()
}
```

### Why Do This
- **Single Entry Point**: ChatViewModel interacts with ONE class instead of many
- **Event Stream**: UI can observe runtime events for real-time updates
- **Coordinated Components**: All components work together seamlessly
- **Maps to**: Python `logicore/runtime/agent_runtime.py`

### Previous State (Before)
```kotlin
// Direct API calls, no orchestration
val response = OpenCodeApiClient.api.chatCompletions(...)
```

### New State (After)
```kotlin
// Full orchestration via runtime
agentRuntime.executeTurn(sessionId) { turn ->
    val response = callLLM()
    recordTokens(response.inputTokens, response.outputTokens)
    
    if (response.hasToolCall()) {
        recordToolCall()
        val loopResult = checkLoop(AgentEvent(...))
        if (loopResult.detected) {
            // Handle recovery
        }
    }
}
```

### Test Cases

```kotlin
// AgentRuntimeTest.kt
@OptIn(ExperimentalCoroutinesApi::class)
class AgentRuntimeTest {
    
    private lateinit var runtime: AgentRuntime
    
    @Before
    fun setup() {
        runtime = AgentRuntime.create(RuntimeConfig(maxTurns = 5))
    }
    
    @Test
    fun `executeTurn tracks turn number`() = runTest {
        runtime.executeTurn("session1") { turn ->
            assertEquals(1, turn.turnNumber)
        }
        runtime.executeTurn("session1") { turn ->
            assertEquals(2, turn.turnNumber)
        }
    }
    
    @Test
    fun `events are emitted during execution`() = runTest {
        val collectedEvents = mutableListOf<RuntimeEvent>()
        val job = launch {
            runtime.events.collect { collectedEvents.add(it) }
        }
        
        runtime.executeTurn("session1") { }
        
        delay(100) // Allow event propagation
        job.cancel()
        
        assertTrue(collectedEvents.any { it is RuntimeEvent.TurnStarted })
        assertTrue(collectedEvents.any { it is RuntimeEvent.TurnCompleted })
    }
    
    @Test
    fun `reasoning level affects system prompt`() {
        runtime.reasoningController.setLevel(ReasoningLevel.DEEP)
        val prompt = runtime.getEnhancedSystemPrompt("Base prompt")
        assertTrue(prompt.contains("exhaustively"))
    }
    
    @Test
    fun `remaining turns decrements`() = runTest {
        assertEquals(5, runtime.getRemainingTurns("session1"))
        runtime.executeTurn("session1") { }
        assertEquals(4, runtime.getRemainingTurns("session1"))
    }
    
    @Test
    fun `loop detection integrated`() = runTest {
        val event = AgentEvent(
            type = AgentEventType.TOOL_CALL,
            toolName = "test_tool",
            toolArgs = mapOf("arg" to "value")
        )
        
        runtime.executeTurn("session1") {
            checkLoop(event)
            checkLoop(event)
            val result = checkLoop(event)
            assertTrue(result.detected)
        }
    }
    
    @Test
    fun `turn context tracks tool calls`() = runTest {
        runtime.executeTurn("session1") { turn ->
            assertEquals(0, turn.toolCalls)
            recordToolCall()
            assertEquals(1, turn.toolCalls)
            recordToolCall()
            assertEquals(2, turn.toolCalls)
        }
    }
    
    @Test
    fun `turn context tracks tokens`() = runTest {
        runtime.executeTurn("session1") { turn ->
            recordTokens(100, 50)
            recordTokens(200, 100)
            assertEquals(300, turn.tokensInput)
            assertEquals(150, turn.tokensOutput)
        }
    }
}
```

---

# Phase 1 Summary

## Files Created

| File | Purpose |
|------|---------|
| `runtime/config/RuntimeConfig.kt` | Centralized configuration |
| `runtime/turn/TurnStatus.kt` | Turn state enum |
| `runtime/turn/TurnContext.kt` | Turn execution context |
| `runtime/turn/TurnManager.kt` | Bounded turn execution |
| `runtime/loop/LoopType.kt` | Loop type enum |
| `runtime/loop/AgentEvent.kt` | Events for loop detection |
| `runtime/loop/LoopDetectionResult.kt` | Detection result + recovery |
| `runtime/loop/detectors/HashDetector.kt` | Tool call hash detection |
| `runtime/loop/detectors/ContentDetector.kt` | Content repetition detection |
| `runtime/loop/detectors/StagnantDetector.kt` | Progress stagnation detection |
| `runtime/loop/LoopDetectionEngine.kt` | Multi-layer detection engine |
| `runtime/reasoning/ReasoningLevel.kt` | 5-level reasoning enum |
| `runtime/reasoning/ReasoningState.kt` | Reasoning state tracking |
| `runtime/reasoning/ReasoningController.kt` | Dynamic reasoning control |
| `runtime/AgentRuntime.kt` | Main orchestrator |

## Dependencies

Add to `app/build.gradle.kts`:
```kotlin
dependencies {
    // Existing dependencies...
    
    // For Instant/Duration
    implementation("org.jetbrains.kotlinx:kotlinx-datetime:0.4.0")
    
    // Testing
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.mockito.kotlin:mockito-kotlin:5.1.0")
}
```

## Verification Checklist

- [ ] All 15 files created with correct package structure
- [ ] RuntimeConfig can be instantiated with default values
- [ ] TurnManager enforces turn limits
- [ ] TurnManager times out long operations
- [ ] LoopDetectionEngine detects consecutive tool calls
- [ ] LoopDetectionEngine detects content repetition
- [ ] ReasoningController escalates on complex queries
- [ ] ReasoningController de-escalates on simple queries
- [ ] AgentRuntime emits events during execution
- [ ] All test cases pass

---

**Continue to Phase 2 →** (Tool System implementation)

---

# Phase 2: Tool System

## Overview

Implement the tool abstraction layer, registry, scheduler, and built-in tools for real command execution.

---

## Step 2.1: Create Tool Package Structure

### What To Do
Create the following structure under `app/src/main/java/com/example/`:

```
tools/
├── BaseTool.kt           # Tool interface
├── ToolResult.kt         # Execution result
├── ToolRegistry.kt       # Central registration
├── RiskLevel.kt          # Safety classification
├── scheduler/
│   ├── ToolScheduler.kt      # Execution orchestration
│   ├── ToolCallRequest.kt    # Request model
│   ├── ToolCallResult.kt     # Result model
│   └── ToolCallStatus.kt     # Status enum
└── builtin/
    ├── ShellExecTool.kt      # Command execution
    ├── FileReadTool.kt       # File reading
    ├── FileWriteTool.kt      # File writing
    ├── WebSearchTool.kt      # Web search
    ├── DateTimeTool.kt       # Date/time operations
    └── NotesTool.kt          # Note management
```

### Why Do This
- Current app has **mocked tool execution** - tools return fake results
- Need real execution via ProcessBuilder for shell commands
- Tool registry provides a central place for all available tools
- Scheduler handles deduplication, retry, and cooldowns
- Maps to: Python `logicore/tools/base.py`, `logicore/runtime/scheduler/executor.py`

### Previous State (Before)
```kotlin
// ChatViewModel.kt - Mocked tool execution
if (toolName == "shell_exec") {
    // Fake execution - not real
    val fakeResult = "Command executed successfully"
}
```

### New State (After)
```kotlin
// Real tool execution via scheduler
val result = toolScheduler.execute(ToolCallRequest(
    name = "shell_exec",
    args = mapOf("command" to "ls -la")
))
if (result.success) {
    // Real command output
}
```

---

## Step 2.2: Implement Core Tool Interfaces

**File: `tools/RiskLevel.kt`**
```kotlin
package com.example.tools

enum class RiskLevel {
    SAFE,              // Read-only operations, no approval needed
    APPROVAL_REQUIRED, // May modify state, user approval needed
    DANGEROUS          // Can delete/execute code, always confirm
}
```

**File: `tools/ToolResult.kt`**
```kotlin
package com.example.tools

data class ToolResult(
    val success: Boolean,
    val content: String? = null,
    val error: String? = null,
    val metadata: Map<String, Any> = emptyMap()
) {
    companion object {
        fun success(content: String, metadata: Map<String, Any> = emptyMap()) = 
            ToolResult(success = true, content = content, metadata = metadata)
        
        fun error(error: String) = 
            ToolResult(success = false, error = error)
    }
}
```

**File: `tools/BaseTool.kt`**
```kotlin
package com.example.tools

import kotlinx.serialization.json.JsonObject

/**
 * Base interface for all agent tools.
 */
interface BaseTool {
    /** Unique tool name (snake_case) */
    val name: String
    
    /** Human-readable description for LLM */
    val description: String
    
    /** JSON Schema for arguments */
    val argsSchema: JsonObject
    
    /** Risk level for approval flow */
    val riskLevel: RiskLevel
    
    /**
     * Execute the tool with given arguments.
     */
    suspend fun execute(args: Map<String, Any?>): ToolResult
    
    /**
     * Get OpenAI-compatible function schema.
     */
    fun toFunctionSchema(): Map<String, Any> {
        return mapOf(
            "type" to "function",
            "function" to mapOf(
                "name" to name,
                "description" to description,
                "parameters" to argsSchema
            )
        )
    }
}
```

**File: `tools/ToolRegistry.kt`**
```kotlin
package com.example.tools

import kotlinx.serialization.json.JsonObject

/**
 * Central registry for all available tools.
 */
object ToolRegistry {
    private val tools = mutableMapOf<String, BaseTool>()
    
    fun register(tool: BaseTool) {
        tools[tool.name] = tool
    }
    
    fun unregister(name: String) {
        tools.remove(name)
    }
    
    fun get(name: String): BaseTool? = tools[name]
    
    fun all(): List<BaseTool> = tools.values.toList()
    
    fun allByRisk(risk: RiskLevel): List<BaseTool> = 
        tools.values.filter { it.riskLevel == risk }
    
    fun schemas(): List<Map<String, Any>> = 
        tools.values.map { it.toFunctionSchema() }
    
    fun safeTools(): List<BaseTool> = 
        allByRisk(RiskLevel.SAFE)
    
    fun dangerousTools(): List<BaseTool> = 
        allByRisk(RiskLevel.DANGEROUS)
    
    fun clear() {
        tools.clear()
    }
    
    /**
     * Initialize with default built-in tools.
     */
    fun initializeDefaults() {
        register(ShellExecTool())
        register(FileReadTool())
        register(FileWriteTool())
        register(DateTimeTool())
        register(NotesTool())
    }
}
```

### Test Cases

```kotlin
// ToolRegistryTest.kt
class ToolRegistryTest {
    
    @Before
    fun setup() {
        ToolRegistry.clear()
    }
    
    @Test
    fun `register and retrieve tool`() {
        val tool = DateTimeTool()
        ToolRegistry.register(tool)
        assertEquals(tool, ToolRegistry.get("datetime"))
    }
    
    @Test
    fun `all returns registered tools`() {
        ToolRegistry.initializeDefaults()
        assertTrue(ToolRegistry.all().isNotEmpty())
    }
    
    @Test
    fun `schemas returns OpenAI format`() {
        ToolRegistry.register(DateTimeTool())
        val schemas = ToolRegistry.schemas()
        assertEquals(1, schemas.size)
        assertEquals("function", schemas[0]["type"])
    }
    
    @Test
    fun `filter by risk level`() {
        ToolRegistry.initializeDefaults()
        val safe = ToolRegistry.safeTools()
        val dangerous = ToolRegistry.dangerousTools()
        
        assertTrue(safe.all { it.riskLevel == RiskLevel.SAFE })
        assertTrue(dangerous.all { it.riskLevel == RiskLevel.DANGEROUS })
    }
}
```

---

## Step 2.3: Implement ToolScheduler

**File: `tools/scheduler/ToolCallStatus.kt`**
```kotlin
package com.example.tools.scheduler

enum class ToolCallStatus {
    SCHEDULED,     // Queued for execution
    VALIDATING,    // Validating arguments
    EXECUTING,     // Currently running
    SUCCESS,       // Completed successfully
    ERROR,         // Failed with error
    CANCELLED,     // Cancelled before completion
    TIMEOUT,       // Timed out
    DEDUPLICATED   // Skipped as duplicate
}
```

**File: `tools/scheduler/ToolCallRequest.kt`**
```kotlin
package com.example.tools.scheduler

import java.time.Instant
import java.util.UUID

data class ToolCallRequest(
    val callId: String = UUID.randomUUID().toString(),
    val name: String,
    val args: Map<String, Any?>,
    val sessionId: String = "default",
    val turnId: String? = null,
    val createdAt: Instant = Instant.now(),
    val timeoutSeconds: Int = 30,
    val allowRetry: Boolean = true
) {
    /**
     * Get signature for deduplication.
     */
    fun getSignature(): String {
        val argsJson = args.entries
            .sortedBy { it.key }
            .joinToString(",") { "${it.key}=${it.value}" }
        return "$name:$argsJson".hashCode().toString(16)
    }
}
```

**File: `tools/scheduler/ToolCallResult.kt`**
```kotlin
package com.example.tools.scheduler

import java.time.Instant

data class ToolCallResult(
    val callId: String,
    val name: String,
    val status: ToolCallStatus,
    val result: String? = null,
    val error: String? = null,
    val errorType: String? = null,
    val startedAt: Instant? = null,
    val endedAt: Instant? = null,
    val attempts: Int = 1,
    val reusedFrom: String? = null  // callId if deduplicated
) {
    val durationMs: Long?
        get() = if (startedAt != null && endedAt != null) {
            java.time.Duration.between(startedAt, endedAt).toMillis()
        } else null
    
    val success: Boolean
        get() = status == ToolCallStatus.SUCCESS
}
```

**File: `tools/scheduler/ToolScheduler.kt`**
```kotlin
package com.example.tools.scheduler

import com.example.runtime.config.RuntimeConfig
import com.example.tools.ToolRegistry
import com.example.tools.ToolResult
import kotlinx.coroutines.*
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap

/**
 * Coordinates tool execution with deduplication, retry, and cooldowns.
 */
class ToolScheduler(
    private val config: RuntimeConfig = RuntimeConfig()
) {
    // Deduplication cache: signature -> (callId, result, timestamp)
    private val dedupCache = ConcurrentHashMap<String, Triple<String, String, Long>>()
    
    // Cooldowns: tool_name -> cooldown_until timestamp
    private val cooldowns = ConcurrentHashMap<String, Long>()
    
    // Active executions
    private val activeExecutions = ConcurrentHashMap<String, Job>()
    
    // Execution history
    private val history = mutableListOf<ToolCallResult>()
    
    // Concurrency limit
    private val semaphore = kotlinx.coroutines.sync.Semaphore(10)
    
    /**
     * Execute a single tool call.
     */
    suspend fun execute(request: ToolCallRequest): ToolCallResult {
        // Check cooldown
        val cooldownUntil = cooldowns[request.name]
        if (cooldownUntil != null && System.currentTimeMillis() < cooldownUntil) {
            return ToolCallResult(
                callId = request.callId,
                name = request.name,
                status = ToolCallStatus.ERROR,
                error = "Tool on cooldown until ${Instant.ofEpochMilli(cooldownUntil)}"
            )
        }
        
        // Check deduplication
        val signature = request.getSignature()
        val cached = dedupCache[signature]
        if (cached != null && 
            System.currentTimeMillis() - cached.third < config.deduplicationTtlMs) {
            return ToolCallResult(
                callId = request.callId,
                name = request.name,
                status = ToolCallStatus.DEDUPLICATED,
                result = cached.second,
                reusedFrom = cached.first
            )
        }
        
        // Get tool
        val tool = ToolRegistry.get(request.name)
            ?: return ToolCallResult(
                callId = request.callId,
                name = request.name,
                status = ToolCallStatus.ERROR,
                error = "Tool '${request.name}' not found"
            )
        
        // Execute with timeout and retry
        var attempts = 0
        var lastError: String? = null
        var lastErrorType: String? = null
        val startedAt = Instant.now()
        
        return semaphore.withPermit {
            while (attempts < config.maxRetryAttempts) {
                attempts++
                
                try {
                    val toolResult = withTimeout(config.toolTimeoutMs) {
                        tool.execute(request.args)
                    }
                    
                    val endedAt = Instant.now()
                    
                    val result = if (toolResult.success) {
                        // Cache successful result
                        dedupCache[signature] = Triple(
                            request.callId,
                            toolResult.content ?: "",
                            System.currentTimeMillis()
                        )
                        
                        ToolCallResult(
                            callId = request.callId,
                            name = request.name,
                            status = ToolCallStatus.SUCCESS,
                            result = toolResult.content,
                            startedAt = startedAt,
                            endedAt = endedAt,
                            attempts = attempts
                        )
                    } else {
                        ToolCallResult(
                            callId = request.callId,
                            name = request.name,
                            status = ToolCallStatus.ERROR,
                            error = toolResult.error,
                            startedAt = startedAt,
                            endedAt = endedAt,
                            attempts = attempts
                        )
                    }
                    
                    history.add(result)
                    return@withPermit result
                    
                } catch (e: TimeoutCancellationException) {
                    lastError = "Timeout after ${config.toolTimeoutMs}ms"
                    lastErrorType = "Timeout"
                } catch (e: Exception) {
                    lastError = e.message
                    lastErrorType = e::class.simpleName
                    
                    if (!request.allowRetry) break
                    
                    // Exponential backoff
                    delay(1000L * attempts)
                }
            }
            
            // All attempts failed
            ToolCallResult(
                callId = request.callId,
                name = request.name,
                status = if (lastErrorType == "Timeout") 
                    ToolCallStatus.TIMEOUT else ToolCallStatus.ERROR,
                error = lastError,
                errorType = lastErrorType,
                startedAt = startedAt,
                endedAt = Instant.now(),
                attempts = attempts
            ).also { history.add(it) }
        }
    }
    
    /**
     * Execute multiple tool calls.
     */
    suspend fun executeAll(requests: List<ToolCallRequest>): List<ToolCallResult> {
        return coroutineScope {
            requests.map { request ->
                async { execute(request) }
            }.awaitAll()
        }
    }
    
    /**
     * Set cooldown for a tool.
     */
    fun setCooldown(toolName: String, durationMs: Long) {
        cooldowns[toolName] = System.currentTimeMillis() + durationMs
    }
    
    /**
     * Clear deduplication cache.
     */
    fun clearCache() {
        dedupCache.clear()
    }
    
    /**
     * Get execution history.
     */
    fun getHistory(): List<ToolCallResult> = history.toList()
}
```

### Test Cases

```kotlin
// ToolSchedulerTest.kt
@OptIn(ExperimentalCoroutinesApi::class)
class ToolSchedulerTest {
    
    private lateinit var scheduler: ToolScheduler
    
    @Before
    fun setup() {
        ToolRegistry.clear()
        ToolRegistry.register(DateTimeTool())
        scheduler = ToolScheduler(RuntimeConfig())
    }
    
    @Test
    fun `executes tool successfully`() = runTest {
        val request = ToolCallRequest(name = "datetime", args = emptyMap())
        val result = scheduler.execute(request)
        
        assertTrue(result.success)
        assertEquals(ToolCallStatus.SUCCESS, result.status)
    }
    
    @Test
    fun `returns error for unknown tool`() = runTest {
        val request = ToolCallRequest(name = "unknown_tool", args = emptyMap())
        val result = scheduler.execute(request)
        
        assertFalse(result.success)
        assertEquals(ToolCallStatus.ERROR, result.status)
        assertTrue(result.error!!.contains("not found"))
    }
    
    @Test
    fun `deduplicates identical requests`() = runTest {
        val request1 = ToolCallRequest(name = "datetime", args = emptyMap())
        val request2 = ToolCallRequest(name = "datetime", args = emptyMap())
        
        val result1 = scheduler.execute(request1)
        val result2 = scheduler.execute(request2)
        
        assertEquals(ToolCallStatus.SUCCESS, result1.status)
        assertEquals(ToolCallStatus.DEDUPLICATED, result2.status)
        assertEquals(result1.callId, result2.reusedFrom)
    }
    
    @Test
    fun `respects cooldown`() = runTest {
        scheduler.setCooldown("datetime", 5000)
        
        val request = ToolCallRequest(name = "datetime", args = emptyMap())
        val result = scheduler.execute(request)
        
        assertEquals(ToolCallStatus.ERROR, result.status)
        assertTrue(result.error!!.contains("cooldown"))
    }
    
    @Test
    fun `records execution history`() = runTest {
        val request = ToolCallRequest(name = "datetime", args = emptyMap())
        scheduler.execute(request)
        
        assertEquals(1, scheduler.getHistory().size)
    }
}
```

---

## Step 2.4: Implement Built-in Tools

**File: `tools/builtin/ShellExecTool.kt`**
```kotlin
package com.example.tools.builtin

import com.example.tools.*
import kotlinx.serialization.json.*
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

/**
 * Executes shell commands.
 * DANGEROUS: Can execute arbitrary commands.
 */
class ShellExecTool : BaseTool {
    override val name = "shell_exec"
    override val description = "Execute a shell command and return output"
    override val riskLevel = RiskLevel.DANGEROUS
    
    override val argsSchema = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("command") {
                put("type", "string")
                put("description", "Shell command to execute")
            }
            putJsonObject("timeout_seconds") {
                put("type", "integer")
                put("description", "Timeout in seconds (default 30)")
            }
        }
        putJsonArray("required") {
            add("command")
        }
    }
    
    override suspend fun execute(args: Map<String, Any?>): ToolResult {
        val command = args["command"] as? String
            ?: return ToolResult.error("Missing 'command' argument")
        
        val timeoutSeconds = (args["timeout_seconds"] as? Number)?.toLong() ?: 30L
        
        return try {
            val process = ProcessBuilder("/bin/sh", "-c", command)
                .redirectErrorStream(true)
                .start()
            
            val completed = process.waitFor(timeoutSeconds, TimeUnit.SECONDS)
            
            if (!completed) {
                process.destroyForcibly()
                return ToolResult.error("Command timed out after ${timeoutSeconds}s")
            }
            
            val output = BufferedReader(InputStreamReader(process.inputStream))
                .use { it.readText() }
            
            val exitCode = process.exitValue()
            
            if (exitCode == 0) {
                ToolResult.success(output, mapOf("exitCode" to exitCode))
            } else {
                ToolResult.error("Command failed with exit code $exitCode: $output")
            }
        } catch (e: Exception) {
            ToolResult.error("Execution failed: ${e.message}")
        }
    }
}
```

**File: `tools/builtin/FileReadTool.kt`**
```kotlin
package com.example.tools.builtin

import com.example.tools.*
import kotlinx.serialization.json.*
import java.io.File

/**
 * Reads file contents.
 * SAFE: Read-only operation.
 */
class FileReadTool : BaseTool {
    override val name = "read_file"
    override val description = "Read contents of a file"
    override val riskLevel = RiskLevel.SAFE
    
    override val argsSchema = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") {
                put("type", "string")
                put("description", "Path to file")
            }
            putJsonObject("max_lines") {
                put("type", "integer")
                put("description", "Maximum lines to read (default all)")
            }
        }
        putJsonArray("required") {
            add("path")
        }
    }
    
    override suspend fun execute(args: Map<String, Any?>): ToolResult {
        val path = args["path"] as? String
            ?: return ToolResult.error("Missing 'path' argument")
        
        val maxLines = (args["max_lines"] as? Number)?.toInt()
        
        return try {
            val file = File(path)
            if (!file.exists()) {
                return ToolResult.error("File not found: $path")
            }
            
            val content = if (maxLines != null) {
                file.useLines { lines ->
                    lines.take(maxLines).joinToString("\n")
                }
            } else {
                file.readText()
            }
            
            ToolResult.success(content, mapOf(
                "path" to path,
                "size" to file.length()
            ))
        } catch (e: Exception) {
            ToolResult.error("Failed to read file: ${e.message}")
        }
    }
}
```

**File: `tools/builtin/DateTimeTool.kt`**
```kotlin
package com.example.tools.builtin

import com.example.tools.*
import kotlinx.serialization.json.*
import java.time.*
import java.time.format.DateTimeFormatter

/**
 * Date and time operations.
 * SAFE: Read-only.
 */
class DateTimeTool : BaseTool {
    override val name = "datetime"
    override val description = "Get current date/time or perform date calculations"
    override val riskLevel = RiskLevel.SAFE
    
    override val argsSchema = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("operation") {
                put("type", "string")
                put("enum", listOf("now", "add_days", "format", "parse"))
                put("description", "Operation to perform")
            }
            putJsonObject("timezone") {
                put("type", "string")
                put("description", "Timezone (default UTC)")
            }
            putJsonObject("format") {
                put("type", "string")
                put("description", "Date format pattern")
            }
        }
    }
    
    override suspend fun execute(args: Map<String, Any?>): ToolResult {
        val operation = args["operation"] as? String ?: "now"
        val timezone = args["timezone"] as? String ?: "UTC"
        val format = args["format"] as? String ?: "yyyy-MM-dd HH:mm:ss z"
        
        return try {
            val zone = ZoneId.of(timezone)
            val now = ZonedDateTime.now(zone)
            val formatter = DateTimeFormatter.ofPattern(format)
            
            val result = when (operation) {
                "now" -> now.format(formatter)
                else -> now.format(formatter)
            }
            
            ToolResult.success(result, mapOf(
                "timezone" to timezone,
                "epoch" to now.toEpochSecond()
            ))
        } catch (e: Exception) {
            ToolResult.error("DateTime operation failed: ${e.message}")
        }
    }
}
```

---

## Phase 2 Summary

| File | Purpose |
|------|---------|
| `tools/RiskLevel.kt` | Safety classification enum |
| `tools/ToolResult.kt` | Execution result model |
| `tools/BaseTool.kt` | Tool interface |
| `tools/ToolRegistry.kt` | Central tool registration |
| `tools/scheduler/ToolCallStatus.kt` | Execution status enum |
| `tools/scheduler/ToolCallRequest.kt` | Execution request model |
| `tools/scheduler/ToolCallResult.kt` | Execution result model |
| `tools/scheduler/ToolScheduler.kt` | Execution orchestrator |
| `tools/builtin/ShellExecTool.kt` | Shell command execution |
| `tools/builtin/FileReadTool.kt` | File reading |
| `tools/builtin/DateTimeTool.kt` | Date/time operations |

---

# Phase 3: Memory System

## Overview

Implement multi-tier memory: session memory (Room), short-term memory (embeddings), and long-term memory (FTS).

---

## Step 3.1: Extend Room Entities

### What To Do
Add new entities to `data/Entities.kt`:

```kotlin
// Add to existing Entities.kt

@Entity(tableName = "memory_entries")
data class MemoryEntry(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val memoryType: String,  // approach, learning, pattern, preference
    val title: String,
    val content: String,
    val tags: String,        // JSON array as string
    val projectId: String?,  // null = global
    val createdAt: Long = System.currentTimeMillis(),
    val relevanceScore: Float = 1.0f,
    val usageCount: Int = 0
)

@Entity(tableName = "plans")
data class PlanEntity(
    @PrimaryKey val id: String,
    val title: String,
    val description: String = "",
    val status: String = "draft",  // draft, pending, approved, in_progress, completed, rejected
    val reason: String = "",
    val createdAt: Long = System.currentTimeMillis(),
    val approvedAt: Long? = null,
    val completedAt: Long? = null,
    val rejectionReason: String? = null
)

@Entity(tableName = "plan_steps")
data class PlanStepEntity(
    @PrimaryKey val id: String,
    val planId: String,
    val description: String,
    val stepOrder: Int,
    val status: String = "pending",  // pending, in_progress, completed, skipped, failed
    val estimatedTurns: Int = 1,
    val actualTurns: Int = 0,
    val startedAt: Long? = null,
    val completedAt: Long? = null
)

@Entity(tableName = "tracker_tasks")
data class TrackerTaskEntity(
    @PrimaryKey val id: String,
    val title: String,
    val description: String = "",
    val taskType: String = "task",  // epic, task, subtask, bug
    val status: String = "open",    // open, in_progress, blocked, closed
    val priority: String = "medium", // low, medium, high, critical
    val parentId: String? = null,
    val dependencies: String = "[]", // JSON array
    val progressPercent: Int = 0,
    val createdAt: Long = System.currentTimeMillis()
)
```

### Why Do This
- Memory entries enable long-term learning across sessions
- Plans and steps support the planning workflow
- Tasks enable hierarchical task tracking
- Maps to: Python `logicore/memory/project_memory.py`, `logicore/runtime/tracker/types.py`

### Previous State (Before)
- Only `ChatSession` and `ChatMessage` entities
- No memory persistence
- No plan/task storage

### New State (After)
- Full entity hierarchy for all agentic features
- Persistent storage for learnings, plans, tasks

---

## Step 3.2: Add Memory DAOs

Add to `data/Daos.kt`:

```kotlin
@Dao
interface MemoryDao {
    @Query("SELECT * FROM memory_entries ORDER BY relevanceScore DESC")
    fun getAllMemories(): Flow<List<MemoryEntry>>
    
    @Query("SELECT * FROM memory_entries WHERE projectId = :projectId OR projectId IS NULL ORDER BY relevanceScore DESC")
    fun getMemoriesForProject(projectId: String?): Flow<List<MemoryEntry>>
    
    @Query("SELECT * FROM memory_entries WHERE content LIKE '%' || :query || '%' OR title LIKE '%' || :query || '%' ORDER BY relevanceScore DESC LIMIT :limit")
    suspend fun searchMemories(query: String, limit: Int = 10): List<MemoryEntry>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertMemory(memory: MemoryEntry): Long
    
    @Update
    suspend fun updateMemory(memory: MemoryEntry)
    
    @Query("UPDATE memory_entries SET usageCount = usageCount + 1 WHERE id = :id")
    suspend fun incrementUsageCount(id: Long)
    
    @Query("DELETE FROM memory_entries WHERE id = :id")
    suspend fun deleteMemory(id: Long)
}

@Dao
interface PlanDao {
    @Query("SELECT * FROM plans ORDER BY createdAt DESC")
    fun getAllPlans(): Flow<List<PlanEntity>>
    
    @Query("SELECT * FROM plans WHERE id = :planId")
    suspend fun getPlanById(planId: String): PlanEntity?
    
    @Query("SELECT * FROM plans WHERE status = :status")
    fun getPlansByStatus(status: String): Flow<List<PlanEntity>>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertPlan(plan: PlanEntity)
    
    @Update
    suspend fun updatePlan(plan: PlanEntity)
    
    @Query("DELETE FROM plans WHERE id = :planId")
    suspend fun deletePlan(planId: String)
    
    // Steps
    @Query("SELECT * FROM plan_steps WHERE planId = :planId ORDER BY stepOrder")
    fun getStepsForPlan(planId: String): Flow<List<PlanStepEntity>>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertStep(step: PlanStepEntity)
    
    @Update
    suspend fun updateStep(step: PlanStepEntity)
}

@Dao
interface TrackerDao {
    @Query("SELECT * FROM tracker_tasks ORDER BY createdAt DESC")
    fun getAllTasks(): Flow<List<TrackerTaskEntity>>
    
    @Query("SELECT * FROM tracker_tasks WHERE status != 'closed' ORDER BY priority DESC")
    fun getOpenTasks(): Flow<List<TrackerTaskEntity>>
    
    @Query("SELECT * FROM tracker_tasks WHERE parentId = :parentId")
    fun getChildTasks(parentId: String): Flow<List<TrackerTaskEntity>>
    
    @Query("SELECT * FROM tracker_tasks WHERE id = :taskId")
    suspend fun getTaskById(taskId: String): TrackerTaskEntity?
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertTask(task: TrackerTaskEntity)
    
    @Update
    suspend fun updateTask(task: TrackerTaskEntity)
    
    @Query("DELETE FROM tracker_tasks WHERE id = :taskId")
    suspend fun deleteTask(taskId: String)
}
```

---

## Step 3.3: Create Memory Service

**File: `memory/MemoryService.kt`**
```kotlin
package com.example.memory

import com.example.data.*
import kotlinx.coroutines.flow.Flow
import org.json.JSONArray

enum class MemoryType(val value: String) {
    APPROACH("approach"),
    LEARNING("learning"),
    KEY_STEP("key_step"),
    PATTERN("pattern"),
    PREFERENCE("preference"),
    DECISION("decision"),
    CONTEXT("context")
}

class MemoryService(private val memoryDao: MemoryDao) {
    
    fun getAllMemories(): Flow<List<MemoryEntry>> = memoryDao.getAllMemories()
    
    fun getMemoriesForProject(projectId: String?): Flow<List<MemoryEntry>> = 
        memoryDao.getMemoriesForProject(projectId)
    
    suspend fun search(query: String, limit: Int = 10): List<MemoryEntry> =
        memoryDao.searchMemories(query, limit)
    
    suspend fun addMemory(
        type: MemoryType,
        title: String,
        content: String,
        tags: List<String> = emptyList(),
        projectId: String? = null
    ): Long {
        val memory = MemoryEntry(
            memoryType = type.value,
            title = title,
            content = content,
            tags = JSONArray(tags).toString(),
            projectId = projectId
        )
        return memoryDao.insertMemory(memory)
    }
    
    suspend fun recordUsage(id: Long) {
        memoryDao.incrementUsageCount(id)
    }
    
    suspend fun deleteMemory(id: Long) {
        memoryDao.deleteMemory(id)
    }
    
    /**
     * Get relevant memories for a query (for RAG).
     */
    suspend fun getRelevantMemories(query: String, projectId: String? = null, limit: Int = 5): List<MemoryEntry> {
        // Simple keyword search - can be enhanced with embeddings later
        return memoryDao.searchMemories(query, limit)
    }
    
    /**
     * Format memories for injection into system prompt.
     */
    fun formatForPrompt(memories: List<MemoryEntry>): String {
        if (memories.isEmpty()) return ""
        
        return buildString {
            appendLine("\n## Relevant Memories")
            memories.forEach { memory ->
                appendLine("- **${memory.title}** (${memory.memoryType}): ${memory.content}")
            }
        }
    }
}
```

### Test Cases

```kotlin
// MemoryServiceTest.kt
class MemoryServiceTest {
    
    private lateinit var service: MemoryService
    private lateinit var db: AppDatabase
    
    @Before
    fun setup() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java).build()
        service = MemoryService(db.memoryDao())
    }
    
    @After
    fun teardown() {
        db.close()
    }
    
    @Test
    fun `add and retrieve memory`() = runTest {
        val id = service.addMemory(
            type = MemoryType.LEARNING,
            title = "Test Learning",
            content = "This is what I learned"
        )
        
        val memories = service.search("learned")
        assertTrue(memories.isNotEmpty())
        assertEquals("Test Learning", memories[0].title)
    }
    
    @Test
    fun `usage count increments`() = runTest {
        val id = service.addMemory(
            type = MemoryType.PATTERN,
            title = "Test Pattern",
            content = "A reusable pattern"
        )
        
        service.recordUsage(id)
        service.recordUsage(id)
        
        // Would need to fetch and verify usageCount = 2
    }
    
    @Test
    fun `format for prompt works`() {
        val memories = listOf(
            MemoryEntry(
                id = 1,
                memoryType = "learning",
                title = "Test",
                content = "Content",
                tags = "[]",
                projectId = null
            )
        )
        
        val formatted = service.formatForPrompt(memories)
        assertTrue(formatted.contains("Relevant Memories"))
        assertTrue(formatted.contains("Test"))
    }
}
```

---

# Phase 4: Planning & Tracking

## Step 4.1: Implement PlanService

**File: `planner/PlanService.kt`**
```kotlin
package com.example.planner

import com.example.data.*
import kotlinx.coroutines.flow.Flow
import java.util.UUID

enum class PlanStatus(val value: String) {
    DRAFT("draft"),
    PENDING("pending"),
    APPROVED("approved"),
    IN_PROGRESS("in_progress"),
    COMPLETED("completed"),
    REJECTED("rejected")
}

enum class StepStatus(val value: String) {
    PENDING("pending"),
    IN_PROGRESS("in_progress"),
    COMPLETED("completed"),
    SKIPPED("skipped"),
    FAILED("failed")
}

class PlanService(private val planDao: PlanDao) {
    
    private var planModeActive = false
    private var currentPlanId: String? = null
    
    val isInPlanMode: Boolean
        get() = planModeActive
    
    fun getAllPlans(): Flow<List<PlanEntity>> = planDao.getAllPlans()
    
    fun getStepsForPlan(planId: String): Flow<List<PlanStepEntity>> = 
        planDao.getStepsForPlan(planId)
    
    suspend fun enterPlanMode(reason: String = ""): String {
        planModeActive = true
        val planId = UUID.randomUUID().toString().take(8)
        currentPlanId = planId
        return planId
    }
    
    suspend fun exitPlanMode() {
        planModeActive = false
        currentPlanId = null
    }
    
    suspend fun createPlan(
        title: String,
        description: String = "",
        steps: List<Map<String, Any>>,
        reason: String = ""
    ): PlanEntity {
        val planId = currentPlanId ?: UUID.randomUUID().toString().take(8)
        
        val plan = PlanEntity(
            id = planId,
            title = title,
            description = description,
            status = PlanStatus.DRAFT.value,
            reason = reason
        )
        planDao.insertPlan(plan)
        
        // Create steps
        steps.forEachIndexed { index, stepData ->
            val step = PlanStepEntity(
                id = UUID.randomUUID().toString().take(6),
                planId = planId,
                description = stepData["description"] as? String ?: "",
                stepOrder = index + 1,
                estimatedTurns = stepData["estimated_turns"] as? Int ?: 1
            )
            planDao.insertStep(step)
        }
        
        return plan
    }
    
    suspend fun submitPlan(planId: String) {
        val plan = planDao.getPlanById(planId) ?: return
        planDao.updatePlan(plan.copy(status = PlanStatus.PENDING.value))
    }
    
    suspend fun approvePlan(planId: String) {
        val plan = planDao.getPlanById(planId) ?: return
        planDao.updatePlan(plan.copy(
            status = PlanStatus.APPROVED.value,
            approvedAt = System.currentTimeMillis()
        ))
    }
    
    suspend fun rejectPlan(planId: String, reason: String) {
        val plan = planDao.getPlanById(planId) ?: return
        planDao.updatePlan(plan.copy(
            status = PlanStatus.REJECTED.value,
            rejectionReason = reason
        ))
    }
    
    suspend fun startStep(planId: String, stepId: String) {
        // Update plan to IN_PROGRESS
        val plan = planDao.getPlanById(planId) ?: return
        if (plan.status == PlanStatus.APPROVED.value) {
            planDao.updatePlan(plan.copy(status = PlanStatus.IN_PROGRESS.value))
        }
        
        // Update step
        val steps = planDao.getStepsForPlan(planId)
        // Note: Would need to get step by ID and update
    }
    
    suspend fun completeStep(planId: String, stepId: String) {
        // Update step to completed
        // Check if all steps complete -> mark plan complete
    }
}
```

---

## Step 4.2: Implement TrackerService

**File: `tracker/TrackerService.kt`**
```kotlin
package com.example.tracker

import com.example.data.*
import kotlinx.coroutines.flow.Flow
import java.util.UUID

enum class TaskType(val value: String) {
    EPIC("epic"),
    TASK("task"),
    SUBTASK("subtask"),
    BUG("bug")
}

enum class TaskStatus(val value: String) {
    OPEN("open"),
    IN_PROGRESS("in_progress"),
    BLOCKED("blocked"),
    CLOSED("closed")
}

enum class TaskPriority(val value: String) {
    LOW("low"),
    MEDIUM("medium"),
    HIGH("high"),
    CRITICAL("critical")
}

class TrackerService(private val trackerDao: TrackerDao) {
    
    fun getAllTasks(): Flow<List<TrackerTaskEntity>> = trackerDao.getAllTasks()
    
    fun getOpenTasks(): Flow<List<TrackerTaskEntity>> = trackerDao.getOpenTasks()
    
    fun getChildTasks(parentId: String): Flow<List<TrackerTaskEntity>> = 
        trackerDao.getChildTasks(parentId)
    
    suspend fun createTask(
        title: String,
        description: String = "",
        type: TaskType = TaskType.TASK,
        priority: TaskPriority = TaskPriority.MEDIUM,
        parentId: String? = null
    ): TrackerTaskEntity {
        // Validate parent exists if specified
        if (parentId != null) {
            val parent = trackerDao.getTaskById(parentId)
                ?: throw IllegalArgumentException("Parent task not found: $parentId")
        }
        
        val task = TrackerTaskEntity(
            id = UUID.randomUUID().toString().take(6),
            title = title,
            description = description,
            taskType = type.value,
            priority = priority.value,
            parentId = parentId
        )
        
        trackerDao.insertTask(task)
        return task
    }
    
    suspend fun updateStatus(taskId: String, status: TaskStatus) {
        val task = trackerDao.getTaskById(taskId) ?: return
        trackerDao.updateTask(task.copy(status = status.value))
    }
    
    suspend fun updateProgress(taskId: String, percent: Int) {
        val task = trackerDao.getTaskById(taskId) ?: return
        trackerDao.updateTask(task.copy(progressPercent = percent.coerceIn(0, 100)))
    }
    
    suspend fun closeTask(taskId: String) {
        val task = trackerDao.getTaskById(taskId) ?: return
        
        // Check dependencies (simplified)
        // In full implementation: verify all dependencies are closed
        
        trackerDao.updateTask(task.copy(
            status = TaskStatus.CLOSED.value,
            progressPercent = 100
        ))
    }
    
    suspend fun deleteTask(taskId: String) {
        trackerDao.deleteTask(taskId)
    }
}
```

---

# Phase 5: Provider Abstraction

## Step 5.1: Create LLMProvider Interface

**File: `providers/LLMProvider.kt`**
```kotlin
package com.example.providers

import kotlinx.coroutines.flow.Flow

enum class ProviderCapability {
    CHAT,
    STREAMING,
    TOOLS,
    VISION,
    EMBEDDINGS,
    JSON_MODE
}

data class ChatMessage(
    val role: String,  // user, assistant, system, tool
    val content: String,
    val toolCalls: List<ToolCall>? = null,
    val toolCallId: String? = null
)

data class ToolCall(
    val id: String,
    val name: String,
    val arguments: Map<String, Any?>
)

data class ChatResponse(
    val content: String?,
    val toolCalls: List<ToolCall>?,
    val finishReason: String?,
    val tokensUsed: TokenUsage?
)

data class TokenUsage(
    val input: Int,
    val output: Int
)

data class StreamChunk(
    val content: String?,
    val toolCalls: List<ToolCall>?,
    val isComplete: Boolean
)

interface LLMProvider {
    val name: String
    val capabilities: Set<ProviderCapability>
    
    fun supports(capability: ProviderCapability): Boolean = 
        capability in capabilities
    
    suspend fun chat(
        messages: List<ChatMessage>,
        tools: List<Map<String, Any>>? = null,
        systemInstruction: String? = null
    ): ChatResponse
    
    fun chatStream(
        messages: List<ChatMessage>,
        tools: List<Map<String, Any>>? = null,
        systemInstruction: String? = null
    ): Flow<StreamChunk>
    
    suspend fun healthCheck(): Boolean
}
```

---

## Step 5.2: Implement Providers

**File: `providers/GeminiProvider.kt`**
```kotlin
package com.example.providers

import com.example.service.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

class GeminiProvider(
    private val apiKey: String,
    private val model: String = "gemini-1.5-flash"
) : LLMProvider {
    
    override val name = "gemini"
    override val capabilities = setOf(
        ProviderCapability.CHAT,
        ProviderCapability.TOOLS,
        ProviderCapability.VISION
    )
    
    override suspend fun chat(
        messages: List<ChatMessage>,
        tools: List<Map<String, Any>>?,
        systemInstruction: String?
    ): ChatResponse {
        // Convert to Gemini format
        val contents = messages.map { msg ->
            Content(
                parts = listOf(Part(text = msg.content)),
                role = if (msg.role == "user") "user" else "model"
            )
        }
        
        val request = GenerateContentRequest(
            contents = contents,
            systemInstruction = systemInstruction?.let {
                Content(parts = listOf(Part(text = it)))
            },
            tools = tools?.let { convertTools(it) }
        )
        
        val response = GeminiApiClient.api.generateContent(model, apiKey, request)
        
        val candidate = response.candidates?.firstOrNull()
        val text = candidate?.content?.parts?.firstOrNull()?.text
        val functionCall = candidate?.content?.parts?.firstOrNull()?.functionCall
        
        return ChatResponse(
            content = text,
            toolCalls = functionCall?.let { fc ->
                listOf(ToolCall(
                    id = java.util.UUID.randomUUID().toString(),
                    name = fc.name,
                    arguments = fc.args ?: emptyMap()
                ))
            },
            finishReason = candidate?.finishReason,
            tokensUsed = null  // Gemini doesn't return token count in basic response
        )
    }
    
    override fun chatStream(
        messages: List<ChatMessage>,
        tools: List<Map<String, Any>>?,
        systemInstruction: String?
    ): Flow<StreamChunk> = flow {
        // Gemini streaming would need SSE/streaming endpoint
        // For now, emit single response
        val response = chat(messages, tools, systemInstruction)
        emit(StreamChunk(
            content = response.content,
            toolCalls = response.toolCalls,
            isComplete = true
        ))
    }
    
    override suspend fun healthCheck(): Boolean {
        return try {
            // Simple health check
            chat(listOf(ChatMessage("user", "hi")), null, null)
            true
        } catch (e: Exception) {
            false
        }
    }
    
    private fun convertTools(tools: List<Map<String, Any>>): List<Tool> {
        // Convert OpenAI tool format to Gemini format
        return tools.mapNotNull { tool ->
            val function = tool["function"] as? Map<*, *> ?: return@mapNotNull null
            val name = function["name"] as? String ?: return@mapNotNull null
            val description = function["description"] as? String ?: ""
            val parameters = function["parameters"] as? Map<*, *> ?: emptyMap<String, Any>()
            
            Tool(listOf(
                FunctionDeclaration(
                    name = name,
                    description = description,
                    parameters = Parameters(
                        properties = emptyMap(),  // Simplified
                        required = emptyList()
                    )
                )
            ))
        }
    }
}
```

---

# Phase 6: Skills System

## Step 6.1: Implement SkillLoader

**File: `skills/Skill.kt`**
```kotlin
package com.example.skills

data class SkillMetadata(
    val name: String,
    val description: String,
    val version: String = "1.0.0",
    val author: String = "",
    val tags: List<String> = emptyList(),
    val requires: List<String> = emptyList()
)

data class Skill(
    val metadata: SkillMetadata,
    val instructions: String,
    val systemPromptAddon: String? = null
)
```

**File: `skills/SkillLoader.kt`**
```kotlin
package com.example.skills

import android.content.Context
import java.io.BufferedReader
import java.io.InputStreamReader

class SkillLoader(private val context: Context) {
    
    /**
     * Load a skill from assets.
     */
    fun loadFromAssets(skillName: String): Skill? {
        val path = "skills/$skillName/SKILL.md"
        
        return try {
            val inputStream = context.assets.open(path)
            val content = BufferedReader(InputStreamReader(inputStream)).use { 
                it.readText() 
            }
            parseSkillMd(content)
        } catch (e: Exception) {
            null
        }
    }
    
    /**
     * Discover all skills in assets.
     */
    fun discoverSkills(): List<Skill> {
        return try {
            val skillDirs = context.assets.list("skills") ?: return emptyList()
            skillDirs.mapNotNull { loadFromAssets(it) }
        } catch (e: Exception) {
            emptyList()
        }
    }
    
    private fun parseSkillMd(content: String): Skill? {
        // Parse YAML frontmatter
        val frontmatterMatch = Regex("^---\\s*\\n(.*?)\\n---\\s*\\n(.*)", RegexOption.DOT_MATCHES_ALL)
            .find(content) ?: return null
        
        val frontmatter = frontmatterMatch.groupValues[1]
        val instructions = frontmatterMatch.groupValues[2].trim()
        
        // Simple YAML parsing
        val metadata = mutableMapOf<String, Any>()
        frontmatter.lines().forEach { line ->
            val parts = line.split(":", limit = 2)
            if (parts.size == 2) {
                val key = parts[0].trim()
                val value = parts[1].trim().trim('"', '\'')
                metadata[key] = value
            }
        }
        
        return Skill(
            metadata = SkillMetadata(
                name = metadata["name"] as? String ?: "Unknown",
                description = metadata["description"] as? String ?: "",
                version = metadata["version"] as? String ?: "1.0.0"
            ),
            instructions = instructions
        )
    }
}
```

---

# Phase 7: UI Integration

## Step 7.1: Add Reasoning Slider to Settings

**Modify: `ui/components/SettingsScreen.kt`**

Add a reasoning level slider:

```kotlin
// Add to SettingsScreen.kt

@Composable
fun ReasoningLevelSlider(
    currentLevel: Int,
    onLevelChange: (Int) -> Unit
) {
    val levelNames = listOf("Minimal", "Low", "Medium", "High", "Deep")
    
    Column(modifier = Modifier.padding(16.dp)) {
        Text(
            "Reasoning Level: ${levelNames[currentLevel - 1]}",
            style = MaterialTheme.typography.titleMedium
        )
        
        Slider(
            value = currentLevel.toFloat(),
            onValueChange = { onLevelChange(it.toInt()) },
            valueRange = 1f..5f,
            steps = 3,  // Creates 5 discrete positions
            modifier = Modifier.fillMaxWidth()
        )
        
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            levelNames.forEach { name ->
                Text(
                    name,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}
```

---

# Phase 8: Wire AgentRuntime to ChatViewModel

## Step 8.1: Integrate Runtime

**Modify: `ui/ChatViewModel.kt`**

Replace direct API calls with AgentRuntime:

```kotlin
// Add to ChatViewModel.kt

private val agentRuntime = AgentRuntime.create(RuntimeConfig())
private val toolScheduler = ToolScheduler(RuntimeConfig())

// Initialize tools
init {
    ToolRegistry.initializeDefaults()
    
    // Observe runtime events
    viewModelScope.launch {
        agentRuntime.events.collect { event ->
            handleRuntimeEvent(event)
        }
    }
}

private fun handleRuntimeEvent(event: RuntimeEvent) {
    when (event) {
        is RuntimeEvent.LoopDetected -> {
            _statusText.value = "Loop detected: ${event.result.loopType}"
        }
        is RuntimeEvent.BudgetExceeded -> {
            _statusText.value = "Turn budget exceeded"
        }
        is RuntimeEvent.ReasoningLevelChanged -> {
            _statusText.value = "Reasoning: ${event.to.name}"
        }
        else -> {}
    }
}

// Replace executeLocalAILoop with:
private suspend fun executeAgenticLoop(userText: String) {
    val sId = _currentSessionId.value ?: return
    
    try {
        agentRuntime.executeTurn(sId) { turn ->
            // Adjust reasoning for query
            agentRuntime.adjustReasoningForQuery(userText)
            
            // Get enhanced system prompt
            val systemPrompt = agentRuntime.getEnhancedSystemPrompt(
                "You are logy, an AI assistant."
            )
            
            // Call LLM
            val response = callLLMWithTools(userText, systemPrompt)
            
            // Record metrics
            recordTokens(response.tokensUsed?.input ?: 0, response.tokensUsed?.output ?: 0)
            
            // Handle tool calls
            if (response.toolCalls != null) {
                for (toolCall in response.toolCalls) {
                    recordToolCall()
                    
                    // Check for loop
                    val loopResult = checkLoop(AgentEvent(
                        type = AgentEventType.TOOL_CALL,
                        toolName = toolCall.name,
                        toolArgs = toolCall.arguments
                    ))
                    
                    if (loopResult.detected) {
                        // Handle recovery
                        when (agentRuntime.loopEngine.getRecoveryAction(loopResult)) {
                            RecoveryAction.ESCALATE_REASONING -> {
                                agentRuntime.reasoningController.escalate()
                            }
                            RecoveryAction.INTERRUPT_EXECUTION -> {
                                // Show user prompt
                                return@executeTurn
                            }
                            else -> {}
                        }
                    }
                    
                    // Execute tool
                    val toolResult = toolScheduler.execute(ToolCallRequest(
                        name = toolCall.name,
                        args = toolCall.arguments,
                        sessionId = sId
                    ))
                    
                    // Continue conversation with tool result
                }
            }
        }
    } catch (e: TurnBudgetExceededException) {
        _statusText.value = "Turn budget exceeded"
    } catch (e: TurnTimeoutException) {
        _statusText.value = "Turn timed out"
    }
}
```

---

# Verification Checklist

## Phase 1: Runtime Core
- [ ] RuntimeConfig with sensible defaults
- [ ] TurnManager enforces turn limits
- [ ] TurnManager times out long operations
- [ ] LoopDetectionEngine detects consecutive tool calls
- [ ] LoopDetectionEngine detects content repetition
- [ ] ReasoningController escalates/de-escalates
- [ ] AgentRuntime emits events

## Phase 2: Tool System
- [ ] ToolRegistry registers tools
- [ ] ToolScheduler executes tools
- [ ] ToolScheduler deduplicates
- [ ] ToolScheduler retries on failure
- [ ] ShellExecTool runs real commands
- [ ] FileReadTool reads files

## Phase 3: Memory System
- [ ] MemoryEntry entity works
- [ ] MemoryService stores memories
- [ ] MemoryService searches memories
- [ ] Memory formatted for prompt

## Phase 4: Planning & Tracking
- [ ] PlanService creates plans
- [ ] PlanService tracks steps
- [ ] TrackerService creates tasks
- [ ] TrackerService updates status

## Phase 5: Provider Abstraction
- [ ] LLMProvider interface implemented
- [ ] GeminiProvider works
- [ ] OllamaProvider works

## Phase 6: Skills System
- [ ] SkillLoader parses SKILL.md
- [ ] Skills discovered from assets

## Phase 7: UI Integration
- [ ] Reasoning slider works
- [ ] Plan approval modal works
- [ ] Task tracker widget works

## Phase 8: Integration
- [ ] ChatViewModel uses AgentRuntime
- [ ] Events flow to UI
- [ ] Full agentic loop works

---

# Dependencies

Add to `app/build.gradle.kts`:

```kotlin
dependencies {
    // Existing...
    
    // Kotlin serialization
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.0")
    
    // Date/time
    implementation("org.jetbrains.kotlinx:kotlinx-datetime:0.4.0")
    
    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    
    // Room (already present, ensure FTS support)
    kapt("androidx.room:room-compiler:2.6.1")
    
    // Testing
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    testImplementation("androidx.room:room-testing:2.6.1")
}
```

---

**END OF IMPLEMENTATION GUIDE**
