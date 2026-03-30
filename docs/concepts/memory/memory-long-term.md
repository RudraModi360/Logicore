---
title: Long-Term Memory Handling
description: How persistent memory is extracted, stored, and retrieved over time.
---
Long-term memory in Logicore is powered by SimpleMem + LanceDB vector storage.

---

## What Gets Stored

SimpleMem does not store every line blindly. It applies filtering and scoring:
- drops small talk and vague acknowledgements
- avoids transient reminder chatter
- skips low-signal content
- stores atomic, high-signal facts with score metadata

This helps reduce memory contamination.

---

## Storage Pipeline

1. User and assistant turns are queued.
2. process_pending extracts atomic facts.
3. Facts are embedded.
4. Entries are stored in a LanceDB table.

Each entry can include:
- lossless_restatement
- keywords
- timestamp
- persons/entities/topic

---

## Retrieval Pipeline

1. Query text is embedded.
2. Vector similarity search runs in LanceDB.
3. Results are filtered by retrieval score threshold.
4. Top relevant memory strings are returned.

Retrieval is embedding-based and designed to be fast.

---

## Persistence Scope

Persistence behavior depends on table naming:
- default: per-user per-session table isolation
- optional: per-user shared table when session isolation is disabled

If you need memory shared across sessions, use a shared table strategy (shown in the integration page).
