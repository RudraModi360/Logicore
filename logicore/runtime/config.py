"""
RuntimeConfig: Centralized configuration for the agentic runtime.

This module consolidates all thresholds previously scattered across the codebase:
- logicore/agents/agent.py: max_iterations=40, 36000 chars prompt cap, 12000 chars tool result
- logicore/memory/context_middleware.py: 100000 token threshold
- logicore/agents/agent.py: 1s retry delay, HTTP timeout 20s
- And 8+ other hardcoded values

All values are configurable via:
1. Environment variables (highest priority)
2. logicore.toml configuration file
3. Default values (lowest priority)

Usage:
    from logicore.runtime.config import RuntimeConfig
    
    # Use defaults
    config = RuntimeConfig()
    
    # Override specific values
    config = RuntimeConfig(
        max_turns=60,
        loop_detection=LoopDetectionConfig(tool_threshold=3)
    )
    
    # Load from environment/TOML
    config = RuntimeConfig.from_settings()
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum


class RecoveryEscalationLevel(Enum):
    """Escalation levels for loop recovery."""
    GUIDANCE = 1      # Inject "try a different approach" message
    TOOL_COOLDOWN = 2 # Temporarily disable the repeated tool
    TERMINATE = 3     # Stop execution with explanation


@dataclass
class LoopDetectionConfig:
    """Configuration for loop detection subsystem."""
    
    # Enable/disable loop detection
    enabled: bool = True
    
    # Consecutive identical tool call threshold (gemini-cli default: 5)
    tool_call_threshold: int = 5
    
    # Content chunk repetition threshold (gemini-cli default: 10)
    content_repetition_threshold: int = 10
    
    # Content chunk size for repetition analysis (gemini-cli default: 50)
    content_chunk_size: int = 50
    
    # Max history length for content analysis (gemini-cli default: 5000)
    max_content_history: int = 5000
    
    # LLM-based loop check: activate after N turns (gemini-cli default: 30)
    llm_check_after_turns: int = 20  # Lower for Logicore's shorter contexts
    
    # LLM-based loop check interval (turns between checks)
    llm_check_interval: int = 10
    
    # Min/max LLM check intervals (dynamic adjustment based on confidence)
    llm_check_min_interval: int = 5
    llm_check_max_interval: int = 15
    
    # LLM confidence threshold for loop detection (0.0-1.0)
    llm_confidence_threshold: float = 0.9
    
    # Stagnant state detection: no progress for N turns
    stagnant_turns_threshold: int = 5
    
    # Tool result similarity threshold for embedding-based detection (0.0-1.0)
    result_similarity_threshold: float = 0.95
    
    # Use LLM-based semantic loop detection as fallback
    use_llm_fallback: bool = True
    
    # Recovery escalation policy
    escalation_levels: List[RecoveryEscalationLevel] = field(
        default_factory=lambda: [
            RecoveryEscalationLevel.GUIDANCE,
            RecoveryEscalationLevel.TOOL_COOLDOWN,
            RecoveryEscalationLevel.TERMINATE,
        ]
    )
    
    # Max recovery attempts before escalation
    max_recovery_attempts_per_level: int = 2


@dataclass
class ContextConfig:
    """Configuration for context window management."""
    
    # Maximum context window tokens (model-specific override available)
    max_context_tokens: int = 128000
    
    # Compression threshold as ratio of model context window (0.0-1.0)
    # e.g., 0.8 = compress when context reaches 80% of model's limit
    compression_threshold_ratio: float = 0.8
    
    # Fallback absolute token threshold (if model context unknown)
    compression_threshold_tokens: int = 100000
    
    # Fraction of history to preserve during compression (0.0-1.0)
    preserve_recent_ratio: float = 0.3
    
    # Number of recent messages to always preserve
    preserve_recent_count: int = 10
    
    # Tool output masking: protect recent N tokens from pruning
    protection_threshold_tokens: int = 50000
    
    # Tool output masking: trigger pruning when prunable tokens exceed N
    min_prunable_tokens: int = 30000
    
    # Protect the most recent turn from masking
    protect_latest_turn: bool = True
    
    # Max token budget for function responses in preserved history
    function_response_token_budget: int = 50000
    
    # Tool output distillation: summarize outputs larger than N tokens
    distillation_threshold_tokens: int = 8000
    
    # Max size for distillation (skip if larger)
    max_distillation_size: int = 1000000
    
    # System prompt max characters (truncate if exceeded)
    system_prompt_max_chars: int = 36000


@dataclass
class ToolConfig:
    """Configuration for tool execution."""
    
    # Max tool output characters to include in context
    max_output_chars: int = 12000
    
    # Max tool output tokens (for token-based truncation)
    max_output_tokens: int = 3000
    
    # Tool result cache TTL in seconds (0 = no caching)
    cache_ttl_seconds: int = 300
    
    # Tool execution timeout in seconds
    execution_timeout_seconds: int = 300
    
    # Default cooldown period after loop detection (seconds)
    default_cooldown_seconds: int = 30
    
    # Enable execution deduplication
    enable_deduplication: bool = True
    
    # Deduplication window (consider duplicate if same call within N seconds)
    dedup_window_seconds: int = 5


@dataclass 
class RetryConfig:
    """Configuration for retry behavior."""
    
    # Max retry attempts for LLM calls
    max_attempts: int = 3
    
    # Base delay for exponential backoff (milliseconds)
    base_delay_ms: int = 1000
    
    # Use exponential backoff (vs linear)
    use_exponential_backoff: bool = True
    
    # Max delay cap (milliseconds)
    max_delay_ms: int = 30000
    
    # Jitter factor (0.0-1.0) to add randomness
    jitter_factor: float = 0.1
    
    # Retry on these error patterns (case-insensitive)
    retryable_patterns: List[str] = field(
        default_factory=lambda: [
            "empty",
            "tool calls",
            "model output must contain",
            "output text or tool calls",
            "unexpected",
            "internal server error",
            "status code: -1",
            "status code: 500",
            "status code: 502",
            "status code: 503",
            "timeout",
            "connection",
        ]
    )


@dataclass
class TelemetryConfig:
    """Configuration for telemetry and observability."""
    
    # Enable telemetry collection
    enabled: bool = True
    
    # Log all prompts (may contain sensitive data)
    log_prompts: bool = False
    
    # Enable distributed tracing
    traces_enabled: bool = False
    
    # Structured log format (json, text)
    log_format: str = "json"
    
    # Metrics export interval (seconds)
    export_interval_seconds: int = 60


@dataclass
class ReasoningConfig:
    """
    Configuration for agent reasoning behavior.
    
    Controls reasoning depth, thinking budgets, and auto-escalation.
    Inspired by gemini-cli's thinkingConfig.
    """
    from enum import Enum
    
    # Reasoning level: 1=MINIMAL, 2=LOW, 3=MEDIUM, 4=HIGH, 5=DEEP
    level: int = 3  # MEDIUM by default
    
    # Maximum tokens for reasoning/thinking (0 = unlimited, 8192 = max for most models)
    thinking_budget: int = 2048
    
    # Whether to capture and display thinking process
    include_thoughts: bool = True
    
    # Show step-by-step reasoning to user
    show_reasoning_steps: bool = False
    
    # Automatically increase reasoning level for complex tasks
    auto_escalate: bool = True
    
    # Approval mode: "plan", "default", "auto", "yolo"
    approval_mode: str = "default"


@dataclass
class TrackerConfig:
    """Configuration for task tracking subsystem."""
    
    # Enable task tracking
    enabled: bool = True
    
    # Auto-create task for complex requests
    auto_create_tasks: bool = True
    
    # Persist tasks to disk
    persist: bool = True
    
    # Storage directory (relative to project root)
    storage_dir: str = ".logicore/tracker"


@dataclass
class PlannerConfig:
    """Configuration for plan mode subsystem."""
    
    # Enable plan mode
    enabled: bool = True
    
    # Require approval for plans
    require_approval: bool = True
    
    # Auto-enter plan mode for complex tasks
    auto_plan_threshold: int = 5  # Number of steps that trigger auto-plan
    
    # Storage directory for plans
    storage_dir: str = ".logicore/plans"


@dataclass
class RuntimeConfig:
    """
    Master configuration for the agentic runtime.
    
    Consolidates all thresholds previously hardcoded across the codebase.
    All values can be overridden via environment variables or logicore.toml.
    """
    
    # Maximum turns per chat session
    max_turns: int = 40
    
    # Maximum message history length (prevent unbounded growth)
    max_history_messages: int = 100
    
    # HTTP request timeout (seconds)
    http_timeout_seconds: float = 20.0
    
    # Enable debug logging
    debug: bool = False
    
    # Sub-configurations
    loop_detection: LoopDetectionConfig = field(default_factory=LoopDetectionConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    tool: ToolConfig = field(default_factory=ToolConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    reasoning: ReasoningConfig = field(default_factory=ReasoningConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    
    # Model-specific context windows (model_name -> token_limit)
    model_context_windows: Dict[str, int] = field(default_factory=lambda: {
        # OpenAI
        "gpt-4": 8192,
        "gpt-4-32k": 32768,
        "gpt-4-turbo": 128000,
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-3.5-turbo": 16385,
        # Anthropic
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
        "claude-3.5-sonnet": 200000,
        # Google
        "gemini-pro": 32000,
        "gemini-1.5-pro": 1000000,
        "gemini-1.5-flash": 1000000,
        # Ollama defaults (conservative)
        "llama3": 8192,
        "llama3.1": 128000,
        "mistral": 8192,
        "mixtral": 32768,
        "qwen": 32768,
        # Default fallback
        "default": 4096,
    })
    
    def get_model_context_window(self, model_name: str) -> int:
        """Get context window size for a model, with fallback to default."""
        # Try exact match
        if model_name in self.model_context_windows:
            return self.model_context_windows[model_name]
        
        # Try prefix match (e.g., "gpt-4-turbo-preview" -> "gpt-4-turbo")
        for known_model, window in self.model_context_windows.items():
            if model_name.startswith(known_model):
                return window
        
        # Try contains match (e.g., "my-custom-llama3-model" -> "llama3")
        model_lower = model_name.lower()
        for known_model, window in self.model_context_windows.items():
            if known_model.lower() in model_lower:
                return window
        
        return self.model_context_windows.get("default", 4096)
    
    def get_compression_threshold_for_model(self, model_name: str) -> int:
        """Calculate compression threshold based on model's context window."""
        context_window = self.get_model_context_window(model_name)
        threshold = int(context_window * self.context.compression_threshold_ratio)
        return max(threshold, 1000)  # Minimum 1000 tokens
    
    @classmethod
    def from_settings(cls) -> "RuntimeConfig":
        """
        Load configuration from logicore settings and environment.
        
        Uses the new centralized settings from logicore.config.settings,
        with environment variable overrides still supported.
        
        Priority: Environment > Settings (TOML/defaults) > Hardcoded defaults
        """
        from logicore.config.settings import settings
        
        def get_env_int(key: str, default: int) -> int:
            val = os.getenv(key)
            if val:
                try:
                    return int(val)
                except ValueError:
                    pass
            return default
        
        def get_env_float(key: str, default: float) -> float:
            val = os.getenv(key)
            if val:
                try:
                    return float(val)
                except ValueError:
                    pass
            return default
        
        def get_env_bool(key: str, default: bool) -> bool:
            val = os.getenv(key)
            if val:
                return val.lower() in ("true", "1", "yes", "on")
            return default
        
        # Map settings fields to RuntimeConfig
        return cls(
            max_turns=get_env_int("LOGICORE_MAX_TURNS", getattr(settings, "RUNTIME_MAX_TURNS", settings.MAX_ITERATIONS)),
            max_history_messages=get_env_int("LOGICORE_MAX_HISTORY", getattr(settings, "CONTEXT_MAX_HISTORY_MESSAGES", 100)),
            http_timeout_seconds=get_env_float("LOGICORE_HTTP_TIMEOUT", getattr(settings, "RUNTIME_DEFAULT_TIMEOUT_MS", 30000) / 1000),
            debug=get_env_bool("LOGICORE_DEBUG", settings.DEBUG),
            loop_detection=LoopDetectionConfig(
                enabled=get_env_bool("LOGICORE_LOOP_DETECTION_ENABLED", getattr(settings, "LOOP_DETECTION_ENABLED", True)),
                tool_call_threshold=get_env_int("LOGICORE_LOOP_TOOL_THRESHOLD", getattr(settings, "LOOP_TOOL_THRESHOLD", 5)),
                content_repetition_threshold=get_env_int("LOGICORE_LOOP_CONTENT_THRESHOLD", getattr(settings, "LOOP_CONTENT_THRESHOLD", 10)),
                llm_check_after_turns=get_env_int("LOGICORE_LOOP_LLM_CHECK_AFTER", 20),
                use_llm_fallback=get_env_bool("LOGICORE_LOOP_LLM_FALLBACK", getattr(settings, "LOOP_LLM_FALLBACK", True)),
            ),
            context=ContextConfig(
                max_context_tokens=get_env_int("LOGICORE_CONTEXT_MAX_TOKENS", getattr(settings, "CONTEXT_MAX_TOKENS", 128000)),
                compression_threshold_ratio=get_env_float("LOGICORE_COMPRESSION_RATIO", getattr(settings, "CONTEXT_COMPRESS_THRESHOLD", 0.85)),
                compression_threshold_tokens=get_env_int("LOGICORE_COMPRESSION_TOKENS", 100000),
                protection_threshold_tokens=get_env_int("LOGICORE_PROTECTION_TOKENS", 50000),
                min_prunable_tokens=get_env_int("LOGICORE_MIN_PRUNABLE_TOKENS", getattr(settings, "CONTEXT_TOOL_OUTPUT_MASK_THRESHOLD", 30000)),
            ),
            tool=ToolConfig(
                max_output_chars=get_env_int("LOGICORE_TOOL_MAX_OUTPUT", 12000),
                cache_ttl_seconds=get_env_int("LOGICORE_TOOL_CACHE_TTL", 300),
                execution_timeout_seconds=get_env_int("LOGICORE_TOOL_TIMEOUT", getattr(settings, "TOOL_EXECUTION_TIMEOUT", 60)),
                default_cooldown_seconds=get_env_int("LOGICORE_TOOL_COOLDOWN", getattr(settings, "TOOL_DEFAULT_COOLDOWN", 60)),
                enable_deduplication=get_env_bool("LOGICORE_TOOL_DEDUP", getattr(settings, "TOOL_ENABLE_DEDUPLICATION", True)),
            ),
            retry=RetryConfig(
                max_attempts=get_env_int("LOGICORE_RETRY_MAX_ATTEMPTS", getattr(settings, "RETRY_MAX_ATTEMPTS", 3)),
                base_delay_ms=get_env_int("LOGICORE_RETRY_BASE_DELAY", getattr(settings, "RETRY_BASE_DELAY_MS", 500)),
                use_exponential_backoff=get_env_bool("LOGICORE_RETRY_EXPONENTIAL", getattr(settings, "RETRY_EXPONENTIAL_BACKOFF", True)),
            ),
            telemetry=TelemetryConfig(
                enabled=get_env_bool("LOGICORE_TELEMETRY_ENABLED", getattr(settings, "TELEMETRY_ENABLED", True)),
                log_prompts=get_env_bool("LOGICORE_TELEMETRY_LOG_PROMPTS", getattr(settings, "TELEMETRY_LOG_PROMPTS", False)),
            ),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize configuration to dictionary."""
        return {
            "max_turns": self.max_turns,
            "max_history_messages": self.max_history_messages,
            "http_timeout_seconds": self.http_timeout_seconds,
            "debug": self.debug,
            "loop_detection": {
                "enabled": self.loop_detection.enabled,
                "tool_call_threshold": self.loop_detection.tool_call_threshold,
                "content_repetition_threshold": self.loop_detection.content_repetition_threshold,
                "llm_check_after_turns": self.loop_detection.llm_check_after_turns,
                "llm_check_interval": self.loop_detection.llm_check_interval,
                "llm_confidence_threshold": self.loop_detection.llm_confidence_threshold,
                "stagnant_turns_threshold": self.loop_detection.stagnant_turns_threshold,
            },
            "context": {
                "compression_threshold_ratio": self.context.compression_threshold_ratio,
                "compression_threshold_tokens": self.context.compression_threshold_tokens,
                "preserve_recent_ratio": self.context.preserve_recent_ratio,
                "preserve_recent_count": self.context.preserve_recent_count,
                "protection_threshold_tokens": self.context.protection_threshold_tokens,
                "min_prunable_tokens": self.context.min_prunable_tokens,
                "system_prompt_max_chars": self.context.system_prompt_max_chars,
            },
            "tool": {
                "max_output_chars": self.tool.max_output_chars,
                "max_output_tokens": self.tool.max_output_tokens,
                "cache_ttl_seconds": self.tool.cache_ttl_seconds,
                "execution_timeout_seconds": self.tool.execution_timeout_seconds,
            },
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "base_delay_ms": self.retry.base_delay_ms,
                "use_exponential_backoff": self.retry.use_exponential_backoff,
                "max_delay_ms": self.retry.max_delay_ms,
            },
            "telemetry": {
                "enabled": self.telemetry.enabled,
                "log_prompts": self.telemetry.log_prompts,
                "traces_enabled": self.telemetry.traces_enabled,
            },
        }
