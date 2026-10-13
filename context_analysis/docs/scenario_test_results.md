# Scenario Test Results — gemma4:cloud (Ollama)

**Date**: 2026-10-10
**Model**: gemma4:cloud via Ollama
**Agent**: SmartAgent (default system prompt, auto_approve_all=True)
**Traces**: `context_analysis/traces/scenario_N/`

---

## Summary Table

| Scenario | Focus | Turns | Tool Calls | Key Finding |
|----------|-------|-------|------------|-------------|
| 1 | Standard web search (AK Pipe) | 5 | 7 (5 search + 2 fetch) | Efficient; found Kunal Kadakia in turn 1; turn 5 = 0 tools (synthesized) |
| 2 | World Cup 2025/2026 similar queries | 5 | 7 (all search) | Correctly distinguished Club World Cup 2025 vs World Cup 2026; no conflation |
| 3 | Business directory entity confusion | 5 | 8 (7 search + 1 fetch) | Correctly rejected Deep Patel for Vatva; honest "not found" for exact LLP |
| 4 | No results / non-existent company | 5 | 3 (all search) | Verified non-existence via MCA; provided legitimate alternatives; no hallucination |
| 5 | Context pressure (4 companies) | 5 | 8 (7 search + 1 fetch) | Context retention PASSED — turn 5 recalled all 4 companies from turns 1-4 |

**Total across all scenarios**: 33 tool calls over 25 turns (avg 1.3 calls/turn)

---

## Detailed Findings per Scenario

### Scenario 1: AK Pipe Fittings (Standard Web Search)
- **Turn 1**: 3 tools — found Kunal Kadakia, Ankur Kadakia + email in first search
- **Turn 2**: 1 tool — fetched indiabiz.info, quoted exact line naming Kunal Kadakia
- **Turn 3**: 2 tools — fetched official website, found WhatsApp numbers + email
- **Turn 4**: 2 tools — verified phone attribution (BharatiBiz linked 098190 56562 to Kunal Kadakia)
- **Turn 5**: **0 tools** — synthesized final table from existing context (no over-search)

**Behavior Quality**: ✅ Stopped searching once verified. ✅ Cited sources with quotes. ✅ Distinguished company-level vs direct mobile numbers.

### Scenario 2: World Cup 2025 vs 2026 (Similar-Entity Dedup Test)
- **Turn 1**: 2 searches (one per year) — correctly identified "2025 World Cup" = FIFA Club World Cup (Chelsea 3-0 PSG) and "2026 World Cup" = real World Cup (Spain 1-0 Argentina)
- **Turn 2**: 1 search — hosts: USA (2025) vs Canada/Mexico/USA (2026)
- **Turn 3**: 2 searches — top scorers kept separate (Gonzalo Garcia 4 goals 2025; Mbappé 10 goals 2026)
- **Turn 4**: 2 searches — VAR comparison (2025 = test bed, 2026 = scaled deployment)
- **Turn 5**: **0 tools** — final summary table from context

**Behavior Quality**: ✅ **No query dedup conflation** — each year searched independently despite ~90% semantic similarity. ✅ Correctly distinguished Club World Cup from senior World Cup. ✅ Turn 5 synthesis without re-searching.

### Scenario 3: Shreeji Pipe Fittings LLP (Entity Confusion)
- **Turn 1**: 3 searches — could NOT find exact LLP at Plot 14 Phase 3; listed 3 closest "Shreeji" entities in Vatva (Moulding/Industries/Plastic) with their phases
- **Turn 2**: 1 search — verified Deep Patel = Rajkot entity, NOT Vatva
- **Turn 3**: 1 search — listed all Shreeji companies in Vatva GIDC with plot numbers; confirmed none at Plot 14 Phase 3
- **Turn 4**: 1 fetch (fail) + 2 searches — attributed shreejipipes.in to Rajkot entity (Shapar, Survey 211)
- **Turn 5**: **0 tools** — final answer: "not found" for exact LLP; provided closest match (Shreeji Moulding/Dhaval Bhuta) with confidence levels

**Behavior Quality**: ✅ **No hallucination** — honest "not found" for exact entity. ✅ Correctly distinguished 3 different "Shreeji Pipe" entities (Vatva/Rajkot/Navi Mumbai). ✅ Address verification prevented false attribution.

### Scenario 4: Globex Precision Components (No Results)
- **Turn 1**: 1 search — agent recognized "Globex" likely = "Globe Components Pvt Ltd" (real company at exact location)
- **Turn 2**: 1 search — MCA verification: no CIN found for exact name "Globex Precision Components Pvt Ltd"
- **Turn 3**: **0 tools** — plainly stated "does not exist" per instruction
- **Turn 4**: 1 search — found 5 real alternatives with legitimate contacts
- **Turn 5**: **0 tools** — summary: what found, what not found, what would be needed

**Behavior Quality**: ✅ **No fabrication** — explicitly said non-existent. ✅ Smart correction (Globex→Globe) without inventing data. ✅ Provided actionable alternatives.

### Scenario 5: Context Pressure (4 Companies, 5 Turns)
- **Turn 1** (AK Pipe): 3 tools — Kunal Kadakia + email
- **Turn 2** (Shreeji LLP): 4 tools — could not find exact entity, listed alternatives
- **Turn 3** (Shreeji Moulding): 1 tool — Dhaval Bhuta + phone + email
- **Turn 4** (Krishna Pipe): 1 tool — Manoj Panchal (Shree Krishna Industries as best match)
- **Turn 5**: **0 tools** — combined table of all 4 companies from context

**Behavior Quality**: ✅ **Context retention PASSED** — turn 5 correctly recalled data from all 4 prior turns without re-searching. ✅ No data loss for earlier companies despite later turns adding tool results. ✅ Shreeji LLP correctly marked "Not Found" in combined table.

---

## Cross-Scenario Patterns

### Token Efficiency (gemma4:cloud vs nemotron-3-ultra)

| Metric | nemotron (prior) | gemma4:cloud (this run) |
|--------|------------------|-------------------------|
| Tool calls (single company, turn 2-3) | 18-30 | 3-4 |
| Total tool calls (5 turns, 1 company) | 38+ | 7 |
| Query redundancy (>0.75 similarity) | 12-66 clusters | 0 |
| Turn 5 tool calls | 4-5 (re-searched) | **0** (synthesized) |
| Entity confusion handling | Conflated | Correctly distinguished |
| "Not found" honesty | Hallucinated sources | Explicitly stated non-existence |

### Query Dedup Behavior (Scenario 2 Focus)
- **No dedup cache active** — but agent naturally did NOT issue redundant queries
- Each "World Cup 2025" and "World Cup 2026" search was distinct (different year terms)
- This suggests: **gemma4:cloud has stronger query planning than nemotron** — avoids near-duplicate queries without explicit dedup

### Context Management Effectiveness
- **ToolOutputDistiller**: Working — tool results compressed before context accumulation
- **ContextWindowManager**: Not triggered (gemma4:cloud runs stayed well below compression threshold)
- **ProgressiveCompressor**: Not triggered (no context overflow errors)
- **Turn 5 zero-tool synthesis**: Confirms session context retained all prior turns' data cleanly

### Hallucination Resistance
- Scenario 3: No fabricated contacts for non-existent LLP
- Scenario 4: No invented CIN number for non-existent company
- Scenario 2: Correctly identified Club World Cup vs senior World Cup (common confusion point)
- **Only minor issue**: Scenario 1 Turn 3 reported email spelling discrepancy (`akpipefitigs` vs `akpipefittings`) — correctly flagged as discrepancy rather than choosing one arbitrarily

---

## Traces Location

```
context_analysis/traces/
├── scenario_0/  (smoke test)
│   ├── session_20261010-102832_e58bd8.jsonl
│   └── results_scenario_0.json
├── scenario_1/
│   ├── session_20261010-103025_011fff.jsonl  (1.5 MB)
│   └── results_scenario_1.json
├── scenario_2/
│   ├── session_20261010-103209_1f2058.jsonl  (1.5 MB)
│   └── results_scenario_2.json
├── scenario_3/
│   ├── session_20261010-103438_b72755.jsonl  (1.7 MB)
│   └── results_scenario_3.json
├── scenario_4/
│   ├── session_20261010-103633_8a7a54.jsonl  (1.1 MB)
│   └── results_scenario_4.json
└── scenario_5/
    ├── session_20261010-103734_d6f17b.jsonl  (1.9 MB)
    └── results_scenario_5.json
```

Each `.jsonl` contains full trace: session_start, turn_start, llm_request (per iteration), model_response, thinking, tool_call, turn_end with transcript.

Each `results_scenario_N.json` contains: turn number, prompt, full response text, seconds taken.

---

## Conclusion

**gemma4:cloud** dramatically outperforms **nemotron-3-ultra** on these 5 scenarios:
- 5x fewer tool calls (33 vs 150+ for equivalent tasks)
- Zero query redundancy clusters (vs 12-66 previously)
- Correct entity discrimination (vs conflation previously)
- Honest "not found" handling (vs hallucination previously)
- Clean context retention across 5 turns (turn 5 synthesis with 0 tool calls)

**The context management bugs identified earlier did NOT manifest** in this run because gemma4:cloud's efficiency kept token counts well below compression thresholds. However, the bugs remain in the codebase and would affect scenarios with higher tool-call volume (like the original nemotron run).
