"""
Model Capability Testing & Management Script

This script helps test new models and register their capabilities.
Models are tested once and their capabilities are stored in model_capabilities.json

Usage:
    # Test a new model and save capabilities
    python test_model_capability.py groq "new-model-name"
    
    # Manually register a model's capabilities
    python test_model_capability.py gemini "new-model" --tools --vision --text
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from logicore.providers.capability_detector import (
    detect_model_capabilities,
    update_model_capability,
    get_known_capability,
    _load_capabilities_from_file,
    _save_capabilities_to_file
)
from logicore.agents import Agent
from datetime import datetime

async def test_model_capability(provider: str, model: str):
    """
    Test a new model and automatically save its capabilities.
    """
    print(f"\n{'='*60}")
    print(f"Testing Model Capabilities: {provider.upper()} / {model}")
    print(f"{'='*60}\n")
    
    try:
        # Create agent to initialize provider
        agent = Agent(
            llm=provider,
            model=model,
            debug=True
        )
        
        # Detect capabilities
        print("Detecting capabilities...")
        capabilities = await detect_model_capabilities(
            provider,
            model,
            provider_instance=agent.llm
        )
        
        # Display results
        print(f"\n{'='*60}")
        print("RESULTS:")
        print(f"{'='*60}")
        print(f"Provider:          {capabilities.provider}")
        print(f"Model:             {capabilities.model_name}")
        print(f"Tool Support:      {capabilities.supports_tools}")
        print(f"Vision Support:    {capabilities.supports_vision}")
        print(f"Text Support:      {getattr(capabilities, 'supports_text', True)}")
        print(f"Audio Support:     {getattr(capabilities, 'supports_audio', False)}")
        print(f"Detection Method:  {capabilities.detection_method}")
        print(f"{'='*60}\n")
        
        print(f"✓ Capabilities automatically saved to model_capabilities.json")
        return True
        
    except Exception as e:
        print(f"\n❌ Error testing model: {e}")
        import traceback
        traceback.print_exc()
        return False

def list_known_models():
    """List all models in the configuration file."""
    capabilities = _load_capabilities_from_file()
    
    print(f"\n{'='*60}")
    print("KNOWN MODELS AND THEIR CAPABILITIES")
    print(f"{'='*60}\n")
    
    for provider, models in capabilities.items():
        print(f"\n{provider.upper()}:")
        print("-" * 40)
        for model_name, caps in models.items():
            tools = "✓" if caps.get("supports_tools") else "✗"
            vision = "✓" if caps.get("supports_vision") else "✗"
            text = "✓" if caps.get("supports_text", True) else "✗"
            audio = "✓" if caps.get("supports_audio") else "✗"
            tested = caps.get("last_tested", "unknown")
            
            print(f"  {model_name:40} | T:{tools} V:{vision} A:{audio} X:{text} | {tested}")

def manually_register_model(provider: str, model: str, tools: bool, vision: bool, text: bool, audio: bool):
    """Manually register a model's capabilities without testing."""
    print(f"\nRegistering {provider}/{model}...")
    
    success = update_model_capability(
        provider=provider,
        model_name=model,
        supports_tools=tools,
        supports_vision=vision,
        supports_text=text,
        supports_audio=audio
    )
    
    if success:
        print(f"✓ Successfully registered {model}")
        caps = get_known_capability(model, provider)
        print(f"  Capabilities: {caps}")
    else:
        print(f"❌ Failed to register {model}")
    
    return success

async def main():
    parser = argparse.ArgumentParser(
        description="Test and register LLM model capabilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test a new model (auto-detects and saves)
  python test_model_capability.py groq "meta-llama/new-model"
  
  # List all known models
  python test_model_capability.py --list
  
  # Manually register without testing
  python test_model_capability.py ollama "my-model" \\
    --tools --vision --text
        """
    )
    
    parser.add_argument("provider", nargs="?", help="Provider name (groq, gemini, ollama, etc)")
    parser.add_argument("model", nargs="?", help="Model name/ID")
    parser.add_argument("--list", action="store_true", help="List all known models")
    parser.add_argument("--tools", action="store_true", help="Model supports tools")
    parser.add_argument("--vision", action="store_true", help="Model supports vision")
    parser.add_argument("--text", action="store_true", help="Model supports text (default: true)")
    parser.add_argument("--audio", action="store_true", help="Model supports audio")
    
    args = parser.parse_args()
    
    # List known models
    if args.list:
        list_known_models()
        return
    
    # Validate required arguments
    if not args.provider or not args.model:
        parser.print_help()
        return
    
    # Manual registration mode (if capability flags provided)
    if args.tools or args.vision or args.audio or args.text:
        manually_register_model(
            provider=args.provider,
            model=args.model,
            tools=args.tools,
            vision=args.vision,
            text=args.text or True,  # Default text to True
            audio=args.audio
        )
    else:
        # Auto-test mode
        success = await test_model_capability(args.provider, args.model)
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
