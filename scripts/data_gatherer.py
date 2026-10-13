"""Simple interactive chatbot — using SmartAgent with live streaming."""
import asyncio
import json
import os

from logicore import SmartAgent
from logicore.stream.events import StreamEventType
from logicore.utils.colors import (
    colored, error, tool_call, tool_result,
    DIM, RESET, GRAY, BLUE,
)
try:
    from session_trace import SessionTracer
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from session_trace import SessionTracer

SYSTEM_PROMPT = """
## ROLE ##

- you are proffesional Company data / company C suite / Top managment level person's Information finder guy , Company Hired you for finding the contact details for making the leads for company's internal use cases , so you have to go over browser and find the details of company's regardals from several open platform like facebook , linked in , their own website if present , instagram and all .

## CONTEXT ##

- you will be assigned with very few details about company which will include the data like company name , working hours , location (latitude , longitude) and in text too .

- so based on that you need to find the guy who can be relavant / whom can we connect for pitching our products as their working industries is suited for our raw matierial based products use cases .

- so if information came to me accurately our company's sales person can connect with them for pitching .

## DATA ##

- it will be something like

Company Name	, Maps_Name	, Maps_Category, Maps_Address	Maps_Plus_Code,Maps_Phone	, Maps_Website	, Maps_Website_URL	Maps_Rating	,Maps_Reviews	,Maps_Hours	,Maps_Lat	,Maps_Lng

So you have to go over browser and then you have to collect what so ever you found about the guys whom we can contact for ...

## AVAILABLE TOOLS ##

- You have no browser. Research through `web_search` and `url_fetch` only.
- Search order: official website -> LinkedIn company/people pages -> Facebook/Instagram -> industry directories (IndiaMART, JustDial, TradeIndia, ExportersIndia) -> news / tenders.
- First call `web_search` with "<company name> <city> <category> owner OR director OR contact", then `url_fetch` the promising results to pull real names, phone and email.
- Keep going until you have at least one sourced contact or you have exhausted 3 different query phrasings. Never stop after a single failed search.

## OUTPUT CONTRACT ##

- Work autonomously. Never ask clarifying questions - the row in the user message is all you get.
- Finish with exactly one markdown table: | Name | Designation | Company | Phone | Email | Source URL | Confidence |
- NEVER invent a name, phone or email. Every person row needs a Source URL you actually fetched; otherwise write "not found".
- If no person-level contact exists, say so plainly and give the best company-level contact (official phone / email from the website) as the only row.
- End with a "Sources:" line listing every URL used.
"""

def make_stream_handler():
    in_thinking = [False]

    def on_event(ev):
        if ev.type == StreamEventType.TOKEN:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False
            print(ev.data.get("delta", ""), end="", flush=True)
        elif ev.type == StreamEventType.REASONING:
            if not in_thinking[0]:
                print(GRAY, end="", flush=True)
                in_thinking[0] = True
            print(ev.data.get("delta", ""), end="", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_START:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False
            name = ev.data.get("name", "?")
            args = ev.data.get("args", {})
            print(f"\n{tool_call(name)}({json.dumps(args)[:100]})", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_END:
            preview = str(ev.data.get("preview", ""))[:120]
            print(f" {tool_result(ev.data.get('success'), preview)}", flush=True)
        elif ev.type == StreamEventType.TOOL_OUTPUT:
            stream = ev.data.get("stream", "stdout")
            line = ev.data.get("line", "")
            if stream == "stderr":
                print(f"{DIM}[stderr]{RESET} {line}", flush=True)
            else:
                print(line, flush=True)
        elif ev.type == StreamEventType.ERROR:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False
            print(f"\n{error('[error]')} {ev.data.get('message')}", flush=True)
        elif ev.type == StreamEventType.DONE:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False

    return on_event


async def main():
    agent = SmartAgent(
        provider="ollama",
        model="nemotron-3-ultra:cloud",
        debug=True,
        telemetry=True,
        max_iterations=60,
    )
    agent.set_auto_approve_all(True)
    session_id = "leadgen-research"
    on_event = make_stream_handler()
    tracer = SessionTracer(agent)
    if tracer.enabled and tracer.path:
        print(f"Session trace: {tracer.path}")
    print("Agent ready. Type 'quit' to exit.\n")
    try:
        while (msg := input("You: ").strip()) and msg != "quit":
            tracer.begin_turn(msg, session_id=session_id)
            run = await agent.stream_run(msg, session_id=session_id)
            try:
                async for ev in run.stream_events():
                    on_event(ev)
                    tracer.trace_event(ev)
            except asyncio.CancelledError:
                pass
            final = await run
            tracer.end_turn(final, session_id=session_id)
            print()
    except (EOFError, KeyboardInterrupt):
        print()
    tracer.close()


asyncio.run(main())
