# Feature Plan 02D: Generalized Semantic Tool Classifier & Structural Resource Tracker

> **Target Scope:** Experimental Sandbox Prototype (`Logicore_Agent_Testing/test_02_episodic_context_pipeline.py`)  
> **Status:** READY FOR IMPLEMENTATION & TESTING  
> **Purpose:** Replace brittle 50-word English dictionaries (`_EDIT_WORDS`, `_READ_WORDS`, `LOCATOR_ARG_KEYS`) with a robust, domain-agnostic 3-level classification system (Declarative Manifest -> Structural Parameter Shape Analysis -> Safe Opaque Fallback).

---

## 1. Architectural Problem & Mental Model

### The Problem with Lexicon-Based Tool Tracking
The previous design relied on hardcoded English vocabulary lists:
```python
_EDIT_WORDS = ("edit", "update", "modify", "patch", "amend", ...)
_READ_WORDS = ("read", "get", "fetch", "show", "view", ...)
LOCATOR_ARG_KEYS = ("path", "file_path", "url", "mailbox", ...)
```
When domain-specific or novel tools appear (e.g. `email_scan` vs. `email_post`, `k8s_apply`, `sql_exec`, `dispatch_event`):
1. **Vocabulary Drift:** Tools whose names don't match the hardcoded verb lists degrade to `OPAQUE` or are misclassified (e.g. `scan` treated as probe, `post` failing to find its resource instance).
2. **Missing Resource Keys:** If an argument is named `to`, `topic`, `endpoint`, or `table`, but isn't in `LOCATOR_ARG_KEYS`, the resource instance is registered as empty (`""`), breaking temporal thread linking.
3. **High Maintenance Overhead:** Every new tool requires manually adding more English words to dictionaries.

### The Solution: 3-Level Structural Classification Architecture

```
[Tool Call Invocation: tool_name, args, schema]
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│ LEVEL 1: DECLARATIVE MANIFEST OVERRIDE (Explicit)       │
│  - Check if tool has explicit `side_effect` or           │
│    `resource_family` registered via manifest.            │
│  - If YES -> 100% Confident (READ / MUTATION / PROBE).   │
└──────────────────────────────────────────────────────────┘
                       │ (If not registered)
                       ▼
┌──────────────────────────────────────────────────────────┐
│ LEVEL 2: STRUCTURAL PARAMETER SHAPE ANALYSIS (Generic)   │
│  - Analyzes the schema / invocation argument properties: │
│    * Selector vs. Payload Argument Separation:           │
│      - Selector Args (IDs, filters, paths, keys <120c)   │
│      - Payload Args (multiline, markdown, body, >120c)   │
│    * If Tool has Payload Args -> MUTATION / WRITE        │
│    * If Tool ONLY has Selector Args -> READ / PROBE      │
│  - Zero domain regexes. Zero English word lists.        │
└──────────────────────────────────────────────────────────┘
                       │ (If ambiguous or executable like bash)
                       ▼
┌──────────────────────────────────────────────────────────┐
│ LEVEL 3: CONSERVATIVE OPAQUE SAFETY FALLBACK             │
│  - Classify as OPAQUE.                                   │
│  - Rule: OPAQUE tools NEVER purge other context, and     │
│    are NEVER purged themselves. (First, do no harm).     │
└──────────────────────────────────────────────────────────┘
```

---

## 2. Component Design & Implementation Steps

### Step 1: Structural Argument Discriminator (Selector vs. Payload)
Instead of matching argument names against a 50-word list, classify argument values by **Structural Type**:

1. **Payload Arguments (Mutating signal):**
   - Value is multiline (`"\n"` in string).
   - Value length $> 120$ characters (raw text, email body, JSON block, patch code).
   - Value is a complex dictionary or list containing structured records.
2. **Selector / Locator Arguments (Resource Identity signal):**
   - Value is a primitive scalar (string, integer, float).
   - Single-line, length $\le 120$ characters.
   - Represents an address, identifier, filter, or target (e.g. `path="foo.py"`, `folder="inbox"`, `to="user@test.com"`, `id=102`).
3. **Slice / Range Arguments (Chunking signal):**
   - Standard pagination/offset keys: `start`, `end`, `offset`, `limit`, `page`, `cursor`, `range`.

```python
def analyze_arguments_shape(args: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Separates tool arguments into (selectors, payloads, slices)
    based purely on data shape, without hardcoded noun dictionaries.
    """
    selectors = {}
    payloads = {}
    slices = {}

    SLICE_NAMES = {"start", "end", "offset", "limit", "page", "cursor", "range", "skip", "count"}

    for k, v in args.items():
        if v is None:
            continue
        k_lower = k.lower().replace("_", "")

        # 1. Slices (chunking parameters)
        if any(s in k_lower for s in SLICE_NAMES) and isinstance(v, (int, str)) and str(v).isdigit():
            slices[k] = v
            continue

        # 2. Payload detection (multiline, long strings, complex objects)
        if isinstance(v, (dict, list)):
            payloads[k] = v
        elif isinstance(v, str) and ("\n" in v or len(v) > 120):
            payloads[k] = v
        else:
            # 3. Selector / Identity argument
            selectors[k] = str(v).strip()

    return selectors, payloads, slices
```

---

### Step 2: Universal Resource Identity Fingerprinting
Instead of scanning for `LOCATOR_ARG_KEYS = ("path", "url", ...)`:
- The resource target is composed of **all selector arguments sorted**:
  - `email_scan({"folder": "inbox"})` $\rightarrow$ Target: `folder=inbox`
  - `email_post({"to": "ceo@acme.com", "body": "..."})` $\rightarrow$ Target: `to=ceo@acme.com`
  - `read_file({"path": "app.py", "start_line": 1})` $\rightarrow$ Target: `path=app.py`, Slice: `slice:1:None`
- If no selector exists, target falls back to the tool's container namespace (e.g. `tool_name`).

```python
def extract_resource_fingerprint(tool_name: str, selectors: Dict[str, Any]) -> str:
    """Generates a stable resource identifier from selector arguments."""
    if not selectors:
        # Implicit container (e.g., global state tool)
        return f"container:{tool_name.split('_')[0]}"
    
    parts = [f"{k}={v}" for k, v in sorted(selectors.items())]
    return "|".join(parts)
```

---

### Step 3: Pure Structural Effect Classifier
Replace `_EDIT_WORDS`, `_WRITE_WORDS`, `_READ_WORDS` with the structural rule:

```python
class StructuralEffectClassifier:
    """
    Classifies tool side-effects across ANY domain:
    1. Explicit Manifest (Level 1)
    2. Structural Argument Shape (Level 2)
    3. Safe Opaque Fallback (Level 3)
    """

    def __init__(self, explicit_manifest: Optional[Dict[str, Dict[str, str]]] = None):
        # Optional user-registered metadata
        self.manifest = explicit_manifest or {}

    def classify(self, tool_name: str, args: Dict[str, Any]) -> Tuple[str, str]:
        """Returns (effect: 'read' | 'mutation' | 'opaque', reason: str)."""
        # Level 1: Explicit Manifest Override
        if tool_name in self.manifest:
            meta = self.manifest[tool_name]
            return meta.get("effect", "opaque"), "explicit_manifest"

        # Level 2: Structural Shape Analysis
        selectors, payloads, slices = analyze_arguments_shape(args)

        # Tools that take execution code or arbitrary commands are always OPAQUE
        if any(k.lower() in ("cmd", "command", "bash", "code", "script") for k in args):
            return "opaque", "arbitrary_execution"

        # If it carries payload/content/body to store or modify -> MUTATION
        if len(payloads) > 0:
            return "mutation", f"carries_payload_data({list(payloads.keys())})"

        # If it only carries selectors/filters -> READ
        if len(selectors) > 0 or len(slices) > 0:
            return "read", f"pure_selector_query({list(selectors.keys())})"

        # Level 3: Safe Opaque Fallback
        return "opaque", "no_identifiable_shape"
```

---

### Step 4: Refactor `MutationAwarePreCleaner` in `test_02_episodic_context_pipeline.py`

Replace lines 180–440 of `test_02_episodic_context_pipeline.py`:
1. Remove `_EDIT_WORDS`, `_WRITE_WORDS`, `_READ_WORDS`, `_PROBE_WORDS`, `LOCATOR_ARG_KEYS`, and `TOKEN_STOPWORDS`.
2. Wire `analyze_arguments_shape` and `StructuralEffectClassifier`.
3. Update `LifecycleStateMachine`:
   - `Read` on target $T$ before `Mutation` on target $T$ $\rightarrow$ `stale_baseline` (purged).
   - `Read` on target $T$ after `Mutation` on target $T$ $\rightarrow$ `verification_pass` (collapsed).
   - Sequential `Read`s with contiguous `slices` $\rightarrow$ `chunk_stream` (preserved).
   - Any tool with `opaque` $\rightarrow$ `opaque_observed` (strictly preserved, zero context loss).

---

## 3. Concrete Verification Scenarios

Once implemented in `test_02_episodic_context_pipeline.py`, verify against these 4 tests:

| Test Case | Tool Invocations | Expected Classification | Expected Pipeline Behavior |
| :--- | :--- | :--- | :--- |
| **Email Agent Test** | 1. `email_scan(folder="inbox")`<br>2. `email_post(to="ceo@test.com", body="Meeting at 2pm...")`<br>3. `email_scan(folder="inbox")` | `email_scan` = READ<br>`email_post` = MUTATION<br>`email_scan` = READ | Call 1 marked `stale_baseline`; Call 3 marked `verification_pass`. Raw pre-edit scan is purged. |
| **Chunked Read Test** | 1. `read_file(path="big.py", start=1, end=100)`<br>2. `read_file(path="big.py", start=101, end=200)` | Both = READ on same target with contiguous slices. | Classified as `chunk_stream`. Both chunks preserved intact without false deduplication. |
| **SQL Database Test** | 1. `sql_query(query="SELECT * FROM users")`<br>2. `sql_exec(query="UPDATE users SET status='active'")`<br>3. `sql_query(query="SELECT * FROM users")` | `sql_query` = READ<br>`sql_exec` = MUTATION | Call 1 purged as stale; Call 3 preserved as latest truth. |
| **Unknown Tool Test** | `custom_k8s_action(cluster="prod", spec={...})` | Ambiguous / complex object = OPAQUE | Kept 100% intact. Zero context destruction. |

---

## 4. Success Criteria
- [ ] Complete removal of static English verb dictionaries (`_EDIT_WORDS`, `_READ_WORDS`) and locator keys (`LOCATOR_ARG_KEYS`).
- [ ] `email_scan` and `email_post` correctly identified as Read $\rightarrow$ Mutation $\rightarrow$ Verification without custom regexes.
- [ ] Safe fallback for opaque tools with zero false purges.
- [ ] Verification passes compile cleanly with `python -m py_compile test_02_episodic_context_pipeline.py`.
