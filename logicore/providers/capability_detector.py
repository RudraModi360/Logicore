"""
Model Capability Detector

Detects LLM model capabilities (tool calling, vision, etc.) across different providers.
This module provides a unified interface to probe models and determine their feature support.
"""

import asyncio
import logging
import json
import os
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from logicore.providers.cache import get_capability_cache

logger = logging.getLogger(__name__)

@dataclass
class ModelCapabilities:
    """Represents the capabilities of an LLM model."""
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_json_mode: bool = False
    max_context_length: Optional[int] = None
    provider: str = "unknown"
    model_name: str = "unknown"
    detection_method: str = "default"  # 'probe', 'api', 'cache', 'keyword', 'default'
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ModelCapabilities':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def validate_input(self, messages: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Validates if the input messages are compatible with the model's capabilities.
        Returns (is_valid, error_message).
        """
        has_images = False
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("image_url", "image", "media_url", "media"):
                        has_images = True
                        break
            if has_images: break
            
        if has_images and not self.supports_vision:
            return False, f"Model '{self.model_name}' is a text-based model. It does not support image/media input. Please remove media from your request."
            
        return True, None


class CapabilityDetector:
    """
    Detects model capabilities across different providers.
    Uses a multi-strategy approach:
    1. Check cache (fastest)
    2. Keyword-based matching for common model families
    3. Query provider API (if available)
    4. Probe model with test request (slowest, most accurate)
    """
    
    def __init__(self, provider_name: str):
        self.provider_name = provider_name.lower()
        self.cache = get_capability_cache()
    
    async def detect(self, model_name: str, provider_instance=None) -> ModelCapabilities:
        """
        Detect capabilities for a given model.
        
        Args:
            model_name: The model identifier
            provider_instance: Optional provider instance for probing
            
        Returns:
            ModelCapabilities object with detected capabilities
        """
        # Strategy 0: Check hardcoded known capabilities first (fastest)
        known = get_known_capability(model_name, self.provider_name)
        if known:
            result = ModelCapabilities(
                supports_tools=known.get("supports_tools", False),
                supports_vision=known.get("supports_vision", False),
                supports_streaming=True,
                provider=self.provider_name,
                model_name=model_name,
                detection_method="known"
            )
            # Cache it for future use
            self.cache.set(self.provider_name, model_name, result.to_dict())
            return result
        
        # Strategy 1: Check cache
        cached_data = self.cache.get(self.provider_name, model_name)
        if cached_data:
            caps = ModelCapabilities.from_dict(cached_data)
            caps.detection_method = "cache"
            return caps
        
        # Strategy 2: Keyword-based matching (Initial guess)
        name_lower = model_name.lower()
        
        # Vision + Tools Keywords
        vision_keywords = ["gpt-4o", "o1-", "claude-3", "sonnet", "haiku", "opus", "pixtral", "llava", "gemini", "vision", "vl", "minicpm"]
        supports_vision = any(kw in name_lower for kw in vision_keywords)
        
        tool_keywords = ["gpt-4", "gpt-3.5", "claude-", "llama-3", "mistral", "mixtral", "qwen", "command-r", "phi-3", "deepseek"]
        supports_tools = any(kw in name_lower for kw in tool_keywords)
        
        # Override for known text-only families even if they match broad keywords
        no_tool_families = ['gemma', 'tinyllama', 'falcon', 'yi', 'orca', 'stablelm', 'starcoder']
        if any(f in name_lower for f in no_tool_families):
            supports_tools = False

        # If keyword match is strong, we might use it as a starting point but still try API/Probe
        method = "keyword"
        
        # Strategy 3: Provider-specific API query (More reliable)
        api_caps = None
        if provider_instance:
            if self.provider_name == "ollama":
                try:
                    api_caps = await self._detect_ollama_capabilities(model_name, provider_instance)
                except Exception as e:
                    logger.warning(f"[CapabilityDetector] Ollama API detection failed: {e}")
            
            elif self.provider_name == "gemini":
                try:
                    api_caps = await self._detect_gemini_capabilities(model_name, provider_instance)
                except Exception as e:
                    logger.warning(f"[CapabilityDetector] Gemini API detection failed: {e}")
        
        if api_caps:
            supports_tools = api_caps.supports_tools
            supports_vision = api_caps.supports_vision
            method = "api"
        else:
            # Strategy 4: Probe the model (most reliable but slow)
            # Only probe if we don't have a high-confidence API result and it's not a known conservative case
            if provider_instance:
                try:
                    probe_caps = await self._probe_model(model_name, provider_instance)
                    supports_tools = probe_caps.supports_tools
                    # Only update vision if not already high-confidence from api or keywords
                    if not supports_vision:
                        supports_vision = probe_caps.supports_vision
                    method = "probe"
                except Exception as e:
                    logger.warning(f"[CapabilityDetector] Probe detection failed: {e}")

        # Final capabilities object
        result = ModelCapabilities(
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            supports_streaming=True,
            provider=self.provider_name,
            model_name=model_name,
            detection_method=method
        )
        
        # Cache the result in memory
        self.cache.set(self.provider_name, model_name, result.to_dict())
        
        # Save newly discovered capabilities to JSON file for future use
        if method in ["probe", "api"]:
            update_model_capability(
                self.provider_name,
                model_name,
                supports_tools=supports_tools,
                supports_vision=supports_vision,
                supports_text=True,
                supports_audio=False
            )
        
        return result
    
    async def _detect_ollama_capabilities(self, model_name: str, provider) -> ModelCapabilities:
        """Detect capabilities using Ollama's model info API."""
        try:
            info = provider.client.show(model_name)
            details = info.get('details', {})
            
            # Check for vision capability
            families = details.get('families', []) or []
            if details.get('family'):
                families.append(details.get('family'))
            
            vision_families = ['clip', 'vision', 'momo', 'llava', 'multimodal', 'mllama']
            supports_vision = any(f.lower() in vision_families for f in families)
            
            # Check for tool support via model family
            tool_families = ['llama', 'mistral', 'mixtral', 'qwen', 'command-r', 'phi3', 'deepseek']
            no_tool_families = ['gemma', 'tinyllama', 'falcon', 'yi', 'orca', 'stablelm', 'starcoder']
            
            supports_tools = any(f.lower() in tool_families for f in families)
            if any(f.lower() in no_tool_families for f in families):
                supports_tools = False
            
            return ModelCapabilities(
                supports_tools=supports_tools,
                supports_vision=supports_vision,
                supports_streaming=True,
                provider=self.provider_name,
                model_name=model_name,
                detection_method="api"
            )
            
        except Exception as e:
            raise RuntimeError(f"Failed to query Ollama model info: {e}")

    async def _detect_gemini_capabilities(self, model_name: str, provider) -> ModelCapabilities:
        """Detect capabilities using Gemini's model metadata."""
        try:
            api_model_name = model_name
            if not api_model_name.startswith("models/"):
                api_model_name = f"models/{api_model_name}"
            
            model_info = provider.client.models.get(model=api_model_name)
            actions = model_info.supported_actions or []
            
            # Modern Gemini models usually support tools and vision
            supports_tools = True 
            supports_vision = 'generateContent' in actions
            
            return ModelCapabilities(
                supports_tools=supports_tools,
                supports_vision=supports_vision,
                supports_streaming=True,
                provider=self.provider_name,
                model_name=model_name,
                detection_method="api"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to query Gemini model info: {e}")
    
    async def _probe_model(self, model_name: str, provider) -> ModelCapabilities:
        """Probe the model with a test request to determine tool support."""
        test_tool = [{
            "type": "function",
            "function": {
                "name": "test_capability",
                "description": "A test function to check tool support",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "test": {"type": "string", "description": "Test parameter"}
                    },
                    "required": ["test"]
                }
            }
        }]
        
        test_messages = [
            {"role": "user", "content": "Use the test_capability tool with test='hello'"}
        ]
        
        supports_tools = False
        supports_vision = False
        
        try:
            response = await provider.chat(test_messages, tools=test_tool)
            # If it responds with tool_calls, it supports tools
            if isinstance(response, dict):
                if response.get('tool_calls'): supports_tools = True
                else: supports_tools = True # Conservative: assume support if no error
            else:
                if hasattr(response, 'tool_calls') and response.tool_calls: supports_tools = True
                else: supports_tools = True
                    
        except Exception as e:
            error_str = str(e).lower()
            if 'does not support tools' in error_str or ('tool' in error_str and 'not supported' in error_str):
                supports_tools = False
            else:
                # Other errors might still mean tools are supported but the test failed
                supports_tools = False # Play it safe on error during probe
        
        # Vision check is harder to probe without cost, use provider inference
        if hasattr(provider, '_supports_vision'):
            supports_vision = provider._supports_vision()
        elif any(kw in model_name.lower() for kw in ["vision", "llava", "gemini", "gpt-4o", "claude-3", "pixtral"]):
            supports_vision = True
        
        return ModelCapabilities(
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            supports_streaming=True,
            provider=self.provider_name,
            model_name=model_name,
            detection_method="probe"
        )


async def detect_model_capabilities(
    provider_name: str, 
    model_name: str, 
    provider_instance=None
) -> ModelCapabilities:
    """Convenience function to detect model capabilities."""
    detector = CapabilityDetector(provider_name)
    return await detector.detect(model_name, provider_instance)

def _get_capabilities_file_path() -> str:
    """Get the path to the model capabilities JSON file."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(current_dir, "model_capabilities.json")

def _load_capabilities_from_file() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Load model capabilities from JSON file."""
    try:
        path = _get_capabilities_file_path()
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[CapabilityDetector] Failed to load capabilities file: {e}")
    
    return {}

def _save_capabilities_to_file(capabilities: Dict[str, Dict[str, Dict[str, Any]]]) -> bool:
    """Save/update model capabilities to JSON file."""
    try:
        path = _get_capabilities_file_path()
        with open(path, 'w') as f:
            json.dump(capabilities, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"[CapabilityDetector] Failed to save capabilities file: {e}")
        return False

def update_model_capability(
    provider: str, 
    model_name: str, 
    supports_tools: bool = False,
    supports_vision: bool = False,
    supports_text: bool = True,
    supports_audio: bool = False
) -> bool:
    """
    Update or add a model's capabilities to the JSON file.
    
    Args:
        provider: Provider name (groq, gemini, ollama, etc)
        model_name: Model identifier
        supports_tools: Whether model supports tool calling
        supports_vision: Whether model supports vision/images
        supports_text: Whether model supports text (default True)
        supports_audio: Whether model supports audio
        
    Returns:
        True if successfully saved, False otherwise
    """
    capabilities = _load_capabilities_from_file()
    
    # Ensure provider exists
    if provider not in capabilities:
        capabilities[provider] = {}
    
    # Update model capability
    capabilities[provider][model_name] = {
        "supports_tools": supports_tools,
        "supports_vision": supports_vision,
        "supports_text": supports_text,
        "supports_audio": supports_audio,
        "last_tested": datetime.now().strftime("%Y-%m-%d")
    }
    
    # Save to file
    success = _save_capabilities_to_file(capabilities)
    if success:
        logger.info(f"[CapabilityDetector] Updated {provider}/{model_name} capabilities")
    return success

def get_known_capability(model_name: str, provider: str = "unknown") -> Optional[Dict[str, bool]]:
    """
    Quick synchronous lookup for cached capabilities from JSON file.
    Returns None if model is not in the file.
    """
    # Load from JSON file
    capabilities = _load_capabilities_from_file()
    
    # Check if provider and model exist in JSON
    if provider in capabilities:
        if model_name in capabilities[provider]:
            model_caps = capabilities[provider][model_name]
            return {
                "supports_tools": model_caps.get("supports_tools", False),
                "supports_vision": model_caps.get("supports_vision", False),
                "supports_text": model_caps.get("supports_text", True),
                "supports_audio": model_caps.get("supports_audio", False)
            }
    
    # Fallback to runtime cache if not in JSON
    cache = get_capability_cache()
    cached = cache.get(provider, model_name)
    if cached:
        return {
            "supports_tools": cached.get("supports_tools", False),
            "supports_vision": cached.get("supports_vision", False),
            "supports_text": cached.get("supports_text", True),
            "supports_audio": cached.get("supports_audio", False)
        }
    return None
