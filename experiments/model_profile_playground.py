"""
experiments/model_profile_playground.py
Hands-on playground for the Plan 01 ModelProfile feature.

Play with model_config and see exactly what the profiling strategy resolves
and what it would write into the runtime's ContextConfig - before shipping.

Usage:
    python experiments/model_profile_playground.py --model gemma4:cloud
    python experiments/model_profile_playground.py --model llama3:8b
    python experiments/model_profile_playground.py --model my-llm --tier small --context-length 16000
    python experiments/model_profile_playground.py --model gemma4:cloud --masking-ratio 0.5 --compression-ratio 0.7
    python experiments/model_profile_playground.py --demo
    python experiments/model_profile_playground.py --agent --model custom --context-length 16000 --tier small
    python experiments/model_profile_playground.py
        -> interactive mode: type a model, then tweak tier/window/ratios live

What you are looking at:
    source = config      you passed --tier/--context-length/--ratios (model_config)
             registry    matched KNOWN_REGISTRY entry
             heuristic   matched size/vendor tags in the model name
             fallback    nothing matched -> friendly notice + standard 64k
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from logicore.runtime.config import ContextConfig  # noqa: E402
from logicore.runtime.model_profile import KNOWN_REGISTRY, ModelProfile  # noqa: E402

DEMO_MODELS = [
    ("gemma4:cloud", None),
    ("llama3:8b", None),
    ("ollama/qwen3.5:0.8b", None),
    ("gpt-4o-mini", None),
    ("my-custom-7b-instruct", None),
    ("my-local-claude-sonnet-fake", None),
    ("totally-unknown-thing", None),
    ("my-llm", {"model_tier": "small", "context_length": 16000}),
]


def build_model_config(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    model_config: Dict[str, Any] = {}
    if getattr(args, "tier", None):
        model_config["model_tier"] = args.tier
    if getattr(args, "context_length", None):
        model_config["context_length"] = args.context_length
    if getattr(args, "masking_ratio", None) is not None:
        model_config["masking_ratio"] = args.masking_ratio
    if getattr(args, "compression_ratio", None) is not None:
        model_config["compression_ratio"] = args.compression_ratio
    return model_config or None


def show_profile(model_name: str, model_config: Optional[Dict[str, Any]]) -> ModelProfile:
    profile = ModelProfile.resolve(model_name, model_config)

    before = ContextConfig()
    after = profile.apply_to_context(ContextConfig())

    print()
    print("=" * 68)
    print(f"  model_config : {model_config or '(none - auto resolve)'}")
    print("=" * 68)
    print(f"  source       : {profile.source}")
    print(f"  tier         : {profile.tier}")
    print(f"  window       : {profile.context_length:,} tokens")
    print(f"  masking      : {profile.masking_ratio}   -> protect recent "
          f"{profile.protection_threshold_tokens():,} tokens")
    print(f"  compression  : {profile.compression_ratio}   -> compress at "
          f"{profile.compression_threshold_tokens():,} tokens")
    print("-" * 68)
    print("  ContextConfig after apply (was -> now):")
    print(f"    max_context_tokens          {before.max_context_tokens:>8,} -> {after.max_context_tokens:<8,}")
    print(f"    compression_threshold_ratio {before.compression_threshold_ratio:>8} -> {after.compression_threshold_ratio:<8}")
    print(f"    compression_threshold_tokens{before.compression_threshold_tokens:>8,} -> {after.compression_threshold_tokens:<8,}")
    print(f"    protection_threshold_tokens {before.protection_threshold_tokens:>8,} -> {after.protection_threshold_tokens:<8,}")
    print("=" * 68)
    return profile


def show_agent(model_name: str, model_config: Optional[Dict[str, Any]]) -> None:
    from logicore import Agent

    agent = Agent(model=model_name, model_config=model_config)
    print()
    print("  Real Agent instance:")
    print(f"    agent.model_profile.tier/context_length : {agent.model_profile.tier} / "
          f"{agent.model_profile.context_length:,}")
    print(f"    agent.model_profile.source              : {agent.model_profile.source}")
    print(f"    runtime context.max_context_tokens      : "
          f"{agent.runtime_config.context.max_context_tokens:,}")
    print(f"    runtime context.compression_ratio       : "
          f"{agent.runtime_config.context.compression_threshold_ratio}")
    print(f"    runtime context.protection_tokens       : "
          f"{agent.runtime_config.context.protection_threshold_tokens:,}")
    print(f"    TokenBudget window (get_model_context_window) : "
          f"{agent.runtime_config.get_model_context_window(agent.model_name):,}")


def show_demo() -> None:
    print("\nRegistry snapshot:")
    for key, meta in KNOWN_REGISTRY.items():
        print(f"  {key:<28} tier={meta['tier']:<9} window={meta['context_length']:,}")
    print("\nResolution demo (one row per source):")
    for model, model_config in DEMO_MODELS:
        profile = ModelProfile.resolve(model, model_config)
        cfg = f" model_config={model_config}" if model_config else ""
        print(f"  {model:<30} -> source={profile.source:<10} tier={profile.tier:<9} "
              f"window={profile.context_length:<9,} masking={profile.masking_ratio} "
              f"compression={profile.compression_ratio}{cfg}")


def interactive() -> None:
    print("ModelProfile playground - interactive mode")
    print("Enter a model name, then optional overrides. 'q' to quit.\n")
    while True:
        try:
            model = input("model> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if model.lower() in {"q", "quit", "exit"}:
            return
        if not model:
            continue

        model_config: Dict[str, Any] = {}
        try:
            tier = input("  tier (small/standard/large, Enter=auto): ").strip()
            if tier:
                model_config["model_tier"] = tier
            length = input("  context_length (Enter=auto): ").strip()
            if length:
                model_config["context_length"] = int(length)
            mask = input("  masking_ratio (0-1, Enter=auto): ").strip()
            if mask:
                model_config["masking_ratio"] = float(mask)
            comp = input("  compression_ratio (0-1, Enter=auto): ").strip()
            if comp:
                model_config["compression_ratio"] = float(comp)
        except ValueError as exc:
            print(f"  bad input: {exc}")
            continue
        try:
            show_profile(model, model_config or None)
        except Exception as exc:
            print(f"  resolve failed: {exc}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Play with the Plan 01 ModelProfile feature")
    parser.add_argument("--model", default=None, help="model name to resolve")
    parser.add_argument("--tier", default=None,
                        choices=["small", "standard", "medium", "large"],
                        help="model_config['model_tier']")
    parser.add_argument("--context-length", type=int, default=None,
                        help="model_config['context_length']")
    parser.add_argument("--masking-ratio", type=float, default=None,
                        help="model_config['masking_ratio'] override")
    parser.add_argument("--compression-ratio", type=float, default=None,
                        help="model_config['compression_ratio'] override")
    parser.add_argument("--demo", action="store_true",
                        help="show registry + how every resolution source behaves")
    parser.add_argument("--agent", action="store_true",
                        help="instantiate a real Agent and show its runtime config")
    args = parser.parse_args(argv)

    if args.demo:
        show_demo()
        return 0

    if not args.model:
        interactive()
        return 0

    model_config = build_model_config(args)
    show_profile(args.model, model_config)
    if args.agent:
        show_agent(args.model, model_config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
