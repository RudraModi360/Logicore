import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from logicore import Agent

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = WORKSPACE_ROOT / "docs"
MAX_CHUNK_CHARS = 1200


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    chunks: List[str] = []
    current = ""

    for paragraph in paragraphs:
        if not current:
            current = paragraph
            continue
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}"
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def _build_rag_database() -> List[Dict[str, Any]]:
    sources: List[Path] = []
    if DOCS_ROOT.exists():
        sources.extend(sorted(DOCS_ROOT.rglob("*.md")))
        sources.extend(sorted(DOCS_ROOT.rglob("*.txt")))

    # Add a couple of workspace anchors that often contain useful support context.
    for relative_path in ("README.md", "system_prompt.txt"):
        candidate = WORKSPACE_ROOT / relative_path
        if candidate.exists():
            sources.append(candidate)

    database: List[Dict[str, Any]] = []
    seen_paths = set()
    for source in sources:
        if source in seen_paths or not source.is_file():
            continue
        seen_paths.add(source)
        try:
            text = source.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not text:
            continue

        for index, chunk in enumerate(_chunk_text(text), start=1):
            database.append(
                {
                    "source": str(source.relative_to(WORKSPACE_ROOT)).replace(
                        "\\", "/"
                    ),
                    "chunk_id": index,
                    "content": chunk,
                }
            )

    return database


RAG_DATABASE = _build_rag_database()
TICKET_STORE: Dict[str, Dict[str, Any]] = {}
TICKET_COUNTER = 0

FAQ_KB = [
    {
        "topic": "installation",
        "question": "How do I install the project?",
        "answer": "Install the package with pip, then follow the quickstart in the docs for the supported runtime setup.",
    },
    {
        "topic": "streaming",
        "question": "How do I stream responses?",
        "answer": "Use the agent chat method with streaming enabled and pass a token callback to print incremental output.",
    },
    {
        "topic": "rag",
        "question": "How does the docs search work?",
        "answer": "The demo builds a small in-memory index from the workspace docs and returns the highest-scoring chunks for a query.",
    },
    {
        "topic": "tickets",
        "question": "How do ticket tools work?",
        "answer": "The ticket tools are dummy CRUD helpers backed by an in-memory dictionary so you can test create, read, update, list, and delete flows.",
    },
]


def _format_records(records: List[Dict[str, Any]]) -> str:
    return json.dumps(records, indent=2, ensure_ascii=True)


def _next_ticket_id() -> str:
    global TICKET_COUNTER
    TICKET_COUNTER += 1
    return f"TKT-{TICKET_COUNTER:04d}"


def query_rag_database(query: str, top_k: int = 3) -> str:
    """Search the docs-backed RAG index and return the best matching chunks."""
    query_terms = _tokenize(query)
    if not query_terms:
        return json.dumps({"query": query, "matches": []}, indent=2, ensure_ascii=True)

    scored_matches = []
    for item in RAG_DATABASE:
        content = item["content"]
        content_terms = Counter(_tokenize(content))
        overlap_score = sum(
            min(content_terms[term], query_terms.count(term))
            for term in set(query_terms)
        )
        if query.lower() in content.lower():
            overlap_score += 10
        if overlap_score <= 0:
            continue

        snippet = content.strip().replace("\n", " ")
        if len(snippet) > 500:
            snippet = f"{snippet[:497]}..."

        scored_matches.append(
            {
                "source": item["source"],
                "chunk_id": item["chunk_id"],
                "score": overlap_score,
                "excerpt": snippet,
            }
        )

    scored_matches.sort(key=lambda entry: entry["score"], reverse=True)
    return json.dumps(
        {"query": query, "matches": scored_matches[: max(1, top_k)]},
        indent=2,
        ensure_ascii=True,
    )


def create_ticket(
    title: str, description: str = "", priority: str = "medium", status: str = "open"
) -> str:
    """Create a dummy support ticket."""
    ticket_id = _next_ticket_id()
    ticket = {
        "ticket_id": ticket_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
    }
    TICKET_STORE[ticket_id] = ticket
    return _format_records([ticket])


def get_ticket(ticket_id: str) -> str:
    """Get a ticket by ticket ID."""
    ticket = TICKET_STORE.get(ticket_id)
    if not ticket:
        return json.dumps(
            {"error": f"Ticket '{ticket_id}' not found."}, indent=2, ensure_ascii=True
        )
    return _format_records([ticket])


def update_ticket(
    ticket_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    """Update a dummy support ticket."""
    ticket = TICKET_STORE.get(ticket_id)
    if not ticket:
        return json.dumps(
            {"error": f"Ticket '{ticket_id}' not found."}, indent=2, ensure_ascii=True
        )

    if title is not None:
        ticket["title"] = title
    if description is not None:
        ticket["description"] = description
    if priority is not None:
        ticket["priority"] = priority
    if status is not None:
        ticket["status"] = status

    return _format_records([ticket])


def delete_ticket(ticket_id: str) -> str:
    """Delete a dummy support ticket."""
    ticket = TICKET_STORE.pop(ticket_id, None)
    if not ticket:
        return json.dumps(
            {"error": f"Ticket '{ticket_id}' not found."}, indent=2, ensure_ascii=True
        )
    return json.dumps(
        {"deleted": True, "ticket_id": ticket_id}, indent=2, ensure_ascii=True
    )


def list_tickets(status: Optional[str] = None) -> str:
    """List tickets, optionally filtered by status."""
    tickets = list(TICKET_STORE.values())
    if status:
        tickets = [ticket for ticket in tickets if ticket.get("status") == status]
    return _format_records(tickets)


def list_faq_topics() -> str:
    """List the available FAQ topics."""
    return _format_records(
        [{"topic": item["topic"], "question": item["question"]} for item in FAQ_KB]
    )


def search_faqs(query: str, top_k: int = 3) -> str:
    """Search the FAQ bank and return the most relevant entries."""
    query_terms = _tokenize(query)
    if not query_terms:
        return json.dumps({"query": query, "matches": []}, indent=2, ensure_ascii=True)

    matches = []
    for item in FAQ_KB:
        haystack = f"{item['question']} {item['answer']} {item['topic']}"
        haystack_terms = Counter(_tokenize(haystack))
        score = sum(
            min(haystack_terms[term], query_terms.count(term))
            for term in set(query_terms)
        )
        if query.lower() in haystack.lower():
            score += 10
        if score > 0:
            matches.append(
                {
                    "topic": item["topic"],
                    "question": item["question"],
                    "answer": item["answer"],
                    "score": score,
                }
            )

    matches.sort(key=lambda entry: entry["score"], reverse=True)
    return json.dumps(
        {"query": query, "matches": matches[: max(1, top_k)]},
        indent=2,
        ensure_ascii=True,
    )


def answer_faq(question: str) -> str:
    """Return the best FAQ answer for a question."""
    matches = json.loads(search_faqs(question, top_k=1))
    best = matches.get("matches", [])[:1]
    if not best:
        return json.dumps(
            {"question": question, "answer": "No FAQ match found."},
            indent=2,
            ensure_ascii=True,
        )
    return json.dumps(
        {
            "question": question,
            "topic": best[0]["topic"],
            "answer": best[0]["answer"],
        },
        indent=2,
        ensure_ascii=True,
    )


SYSTEM_PROMPT = """You are Scratchy Support RAG Agent.

Your job is to answer support and product questions using the workspace docs as the primary source of truth.

Operating rules:
1. For any question about the project, architecture, setup, behavior, or implementation, call `query_rag_database` first.
2. Use the retrieved chunks to ground the final answer. Do not invent details that are not present in the docs.
3. Use the ticket tools when the user wants to create, inspect, update, list, or delete a support ticket.
4. Use the FAQ tools for repetitive how-to or policy-style questions.
5. If the docs do not contain the answer, say that clearly and suggest the next action.
6. Keep the response concise, practical, and support-oriented.
7. Prefer structured replies when reporting ticket or FAQ results.

You are allowed to ask a short clarifying question if the user request is underspecified.
"""


TOOLS = [
    query_rag_database,
    create_ticket,
    get_ticket,
    update_ticket,
    delete_ticket,
    list_tickets,
    list_faq_topics,
    search_faqs,
    answer_faq,
]


async def main():
    agent = Agent(
        provider="ollama",
        model="gemma4:cloud",
        api_key='a145267cdbad47e0868d72f5cc911032.dAxriNsuF0PeB7zvfnFV28ot',
        debug=True,
        telemetry=False,
        max_iterations=60,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
    # agent.set_auto_approve_all(True)
    print("Agent ready. Type 'quit' to exit.\n")
    while (msg := input("You: ").strip()) and msg != "quit":
        await agent.chat(
            msg, stream=True, streaming_funct=lambda t: print(t, end="", flush=True)
        )
        print()


asyncio.run(main())
