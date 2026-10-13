"""Scenario runner: executes up to 5 turns in one session with tracing.

Usage:
    python scripts/run_scenario.py --scenario 1 --turns-file context_analysis/prompts/scenario_1_turns.json
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _safe_print(text: str) -> str:
    return text.encode("cp1252", errors="replace").decode("cp1252")


def make_stream_handler():
    from logicore.stream.events import StreamEventType

    def on_event(ev):
        if ev.type == StreamEventType.TOOL_CALL_START:
            name = ev.data.get("name", "?")
            args = ev.data.get("args", {})
            print(_safe_print(f"\n[tool] {name}({json.dumps(args)[:120]})"), flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_END:
            ok = ev.data.get("success")
            preview = str(ev.data.get("preview", ""))[:100]
            print(_safe_print(f"[tool -> {'ok' if ok else 'fail'}] {preview}"), flush=True)
        elif ev.type == StreamEventType.ERROR:
            print(_safe_print(f"\n[error] {ev.data.get('message')}"), flush=True)
        elif ev.type == StreamEventType.TOKEN:
            print(_safe_print(ev.data.get("delta", "")), end="", flush=True)

    return on_event


async def run_scenario(scenario: int, turns_file: str, max_iterations: int, model: str):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    trace_dir = os.path.join(root, "context_analysis", "traces", f"scenario_{scenario}")
    os.makedirs(trace_dir, exist_ok=True)
    os.environ["LOGICORE_TRACE_DIR"] = trace_dir

    with open(turns_file, encoding="utf-8") as f:
        turns = json.load(f)
    assert isinstance(turns, list) and 1 <= len(turns) <= 5, "turns file must have 1-5 prompts"

    from logicore import SmartAgent
    from session_trace import SessionTracer

    agent = SmartAgent(
        provider="ollama",
        model=model,
        debug=False,
        telemetry=False,
        max_iterations=max_iterations,
    )
    agent.set_auto_approve_all(True)
    tracer = SessionTracer(agent)
    print(f"Trace file: {tracer.path}", flush=True)

    session_id = f"scenario-{scenario}"
    on_event = make_stream_handler()
    results = []

    for i, prompt in enumerate(turns, 1):
        print(_safe_print(f"\n{'=' * 70}\n=== Turn {i}/{len(turns)}: {prompt[:140]}\n{'=' * 70}"), flush=True)
        tracer.begin_turn(prompt, session_id=session_id)
        t0 = time.time()
        run = await agent.stream_run(prompt, session_id=session_id)
        try:
            async for ev in run.stream_events():
                on_event(ev)
                tracer.trace_event(ev)
        except asyncio.CancelledError:
            pass
        final = await run
        tracer.end_turn(final, session_id=session_id)
        dt = time.time() - t0
        print(_safe_print(f"\n--- Turn {i} done in {dt:.1f}s | response {len(str(final))} chars ---"), flush=True)
        results.append({
            "turn": i,
            "prompt": prompt,
            "response": str(final),
            "seconds": round(dt, 1),
        })

    tracer.close()
    out_path = os.path.join(trace_dir, f"results_scenario_{scenario}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nDone. Trace: {tracer.path}\nResults: {out_path}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", type=int, required=True)
    p.add_argument("--turns-file", required=True)
    p.add_argument("--max-iterations", type=int, default=40)
    p.add_argument("--model", default="gemma4:cloud")
    args = p.parse_args()
    asyncio.run(run_scenario(args.scenario, args.turns_file, args.max_iterations, args.model))


if __name__ == "__main__":
    main()
