# DeployMind — Mini Agent System

A multi-agent deployment log analyser built with **Google ADK** and **Gemini 2.0 Flash**. DeployMind accepts raw deployment/runtime logs and runs them through a five-agent pipeline to automatically identify the issue, classify its type, generate ranked fixes, and validate their correctness.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Agent Responsibilities](#agent-responsibilities)
- [Workflow](#workflow)
- [Project Structure](#project-structure)
- [Setup & Installation](#setup--installation)
- [Running the System](#running-the-system)
- [Execution Screenshots](#execution-screenshots)
- [Bonus Features](#bonus-features)
- [Test Scenarios](#test-scenarios)

---

## Architecture Overview

```
                        ┌─────────────────────────────────────┐
                        │         ORCHESTRATOR                │
                        │  (coordinates all agents, manages   │
                        │   state, memory, early-stop logic)  │
                        └────────────────┬────────────────────┘
                                         │
                     ┌───────────────────▼──────────────────────┐
                     │                                           │
          ┌──────────▼──────────┐               ┌──────────────▼──────────────┐
          │   Log Analysis      │               │  Memory Store (JSON)        │
          │   Agent             │               │  incident_history.json      │
          │   ─────────────     │               │  (cross-run persistence)    │
          │  Reads raw logs,    │               └─────────────────────────────┘
          │  extracts errors,   │
          │  returns JSON       │
          └──────────┬──────────┘
                     │  structured log summary
          ┌──────────▼──────────┐
          │   Issue             │
          │   Classification    │
          │   Agent             │
          │   ─────────────     │
          │  Categorizes issue: │
          │  Dependency / Build │
          │  Runtime / Config   │
          └──────────┬──────────┘
                     │  category + severity
          ┌──────────▼──────────┐
          │   Fix Suggestion    │
          │   Agent             │
          │   ─────────────     │
          │  Generates 2-3      │
          │  ranked, actionable │
          │  fix suggestions    │
          └──────────┬──────────┘
                     │  ranked fixes
          ┌──────────▼──────────┐
          │   Validation        │
          │   Agent             │
          │   ─────────────     │
          │  Scores fixes on:   │
          │  relevance, accuracy│
          │  safety, completeness│
          │  → APPROVED/REJECTED│
          └──────────┬──────────┘
                     │
          ┌──────────▼──────────┐
          │   Final Report +    │
          │   Memory Write      │
          │   (or HALT if       │
          │    REJECTED)        │
          └─────────────────────┘
```

All agents communicate through the Orchestrator. Each agent's output is saved into a shared state dictionary and passed as structured JSON context to the next agent. This is **explicit, serialised agent-to-agent communication** — no agent calls another directly.

---

## Agent Responsibilities

### 1. Log Analysis Agent (`log_analyzer`)

| Property | Value |
|---|---|
| Model | `gemini-2.0-flash` |
| Output key | `log_analysis` |
| Output format | JSON |

**Responsibilities:**
- Reads raw deployment/runtime log text provided by the Orchestrator
- Extracts all `ERROR`, `WARN`, and `FATAL` lines
- Identifies the single primary failure (root cause line)
- Produces a structured JSON summary including service name, error counts, and a plain-English summary

**Output schema:**
```json
{
  "service": "payment-service v2.3.1",
  "timestamp": "2024-03-15 10:22:09",
  "total_errors": 4,
  "total_warnings": 1,
  "primary_failure": "No matching distribution found for stripe==7.9.0",
  "error_lines": ["..."],
  "warning_lines": ["..."],
  "summary": "..."
}
```

---

### 2. Issue Classification Agent (`issue_classifier`)

| Property | Value |
|---|---|
| Model | `gemini-2.0-flash` |
| Input | `log_analysis` JSON |
| Output key | `classification` |

**Responsibilities:**
- Categorises the issue into one of four predefined categories
- Assigns a confidence level and severity rating
- Identifies the specific affected component (file, module, or service)

**Categories:**

| Category | Trigger Signatures |
|---|---|
| **Dependency Error** | pip/npm/maven resolution failures, `No matching distribution` |
| **Build Error** | Compilation failures, Dockerfile errors, test step failures |
| **Runtime Error** | Post-startup crashes, OOM kills, port conflicts, CrashLoopBackOff |
| **Configuration Error** | Missing env vars, `KeyError` on `os.environ`, malformed config files |

**Output schema:**
```json
{
  "category": "Dependency Error",
  "confidence": "High",
  "reasoning": "...",
  "affected_component": "requirements.txt / stripe package",
  "severity": "High"
}
```

---

### 3. Fix Suggestion Agent (`fix_suggester`)

| Property | Value |
|---|---|
| Model | `gemini-2.0-flash` |
| Input | `log_analysis` + `classification` JSON |
| Output key | `fix_suggestions` |

**Responsibilities:**
- Generates 2–3 ranked, actionable fix suggestions
- Each fix includes: exact command/action, reasoning, confidence, and estimated effort
- Provides a prevention tip to avoid the same class of error in future

**Output schema:**
```json
{
  "fixes": [
    {
      "rank": 1,
      "title": "Downgrade stripe pin",
      "action": "Change stripe==7.9.0 to stripe==7.3.0 in requirements.txt",
      "reasoning": "...",
      "confidence": "High",
      "estimated_effort": "Minutes"
    }
  ],
  "prevention": "..."
}
```

---

### 4. Validation Agent (`validator`)

| Property | Value |
|---|---|
| Model | `gemini-2.0-flash` |
| Input | All three prior JSON outputs |
| Output key | `validation` |

**Responsibilities:**
- Verifies fixes are relevant to the identified root cause
- Checks for hallucinated commands, package names, or version numbers
- Confirms the fixes match the classified error category
- Approves or rejects the full pipeline output with an explanation

**Validation checks:**
- `relevance` — Do fixes address the primary failure?
- `accuracy` — Are commands/versions real and correct?
- `category_match` — Do fixes match the classified category?
- `completeness` — Is the most critical error addressed?
- `safety` — Are any dangerous operations suggested?

**Verdicts:**
- `APPROVED` (score 8–10) — Fixes are solid
- `APPROVED_WITH_WARNINGS` (score 5–7) — Fixes may work, with caveats
- `REJECTED` (score 0–4) — Pipeline halted by Orchestrator

---

### 5. Orchestrator (`orchestrator.py`)

**Responsibilities:**
- Creates and configures all four agents
- Creates separate ADK `Runner` and `Session` instances for each agent step
- Passes structured JSON output from each agent as input context to the next
- Performs early-stop: halts the pipeline and prints an error if the Validator returns `REJECTED`
- Writes completed incident records to the persistent memory store
- Queries the memory store before Fix Suggestion to surface previously successful fixes
- Prints a colour-coded, structured report to stdout

---

## Workflow

```
User provides log file
        │
        ▼
[Orchestrator] reads log text
        │
        ▼
[Step 1] Log Analysis Agent
  → Extracts errors, warnings, primary failure
  → Saves to: results["log_analysis"]
        │
        ▼
[Step 2] Issue Classification Agent
  → Classifies into 4 categories
  → Saves to: results["classification"]
  → Orchestrator checks memory store for prior incidents
        │
        ▼
[Step 3] Fix Suggestion Agent
  → Generates ranked fixes with reasoning
  → Saves to: results["fix_suggestions"]
        │
        ▼
[Step 4] Validation Agent
  → Scores and approves/rejects fixes
  → Verdict: APPROVED / APPROVED_WITH_WARNINGS / REJECTED
        │
    REJECTED? ──────────────────────────────────────────────────────────┐
        │                                                               │
    APPROVED?                                                       [HALT]
        │                                                               │
        ▼                                                         Print error
[Final Report]                                                    Return result
  → Print recommended actions
  → Write to memory store
  → Return full results dict
```

---

## Project Structure

```
deploymind/
├── orchestrator.py          # Main orchestrator + pipeline runner (live API)
├── deploymind_demo.py       # Full demo with simulated API responses
├── agents/
│   ├── __init__.py
│   ├── log_analyzer.py      # Log Analysis Agent
│   ├── classifier.py        # Issue Classification Agent
│   ├── fix_suggester.py     # Fix Suggestion Agent
│   └── validator.py         # Validation Agent
├── logs/
│   ├── dependency_error.log # Test scenario 1
│   ├── runtime_error.log    # Test scenario 2
│   └── config_error.log     # Test scenario 3
├── memory_store/
│   └── incident_history.json  # Persistent cross-run memory (auto-created)
└── README.md
```

---

## Setup & Installation

### Prerequisites
- Python 3.10+
- A [Gemini API key](https://aistudio.google.com/app/apikey)

### Install dependencies

```bash
pip install google-adk google-genai
```

### Set your API key

```bash
export GEMINI_API_KEY="your_api_key_here"
```

---

## Running the System

### Live mode (requires Gemini API key)

```bash
cd deploymind
python3 orchestrator.py
```

This runs all three test scenarios through the actual Gemini API.

### Demo mode (no API key needed)

```bash
python3 deploymind_demo.py
```

Runs the complete pipeline with realistic pre-canned responses that mirror what Gemini produces. All orchestration logic, JSON parsing, memory store, and reporting is identical to the live system.

### Run a single custom log

```python
import asyncio
from orchestrator import run_pipeline

log_text = open("my_log.txt").read()
result = asyncio.run(run_pipeline(log_text, log_name="My Custom Log"))
```

---

## Execution Screenshots

### Scenario 1 — Dependency Error

```
══════════════════════════════════════════════════════════════════════
  DeployMind Orchestrator  —  Scenario 1 — Dependency Error
══════════════════════════════════════════════════════════════════════
  Started at 2026-06-22 06:28:07

────────────────────────────────────────────────────────────
  ▶  Step 1 / 4  —  Log Analysis Agent
────────────────────────────────────────────────────────────
  → Agent: log_analyzer  |  Model: gemini-2.0-flash
  ✓  Service:  payment-service v2.3.1
  ✓  Errors:   4  |  Warnings: 1
  ℹ  Primary failure: No matching distribution found for stripe==7.9.0
  ℹ  Summary: The deployment of payment-service v2.3.1 failed during
     dependency installation. pip could not find stripe==7.9.0 as it
     does not exist on PyPI — the latest available version is 7.3.0.

────────────────────────────────────────────────────────────
  ▶  Step 2 / 4  —  Issue Classification Agent
────────────────────────────────────────────────────────────
  ✓  Category:   Dependency Error
  ✓  Severity:   High  (Confidence: High)
  ℹ  Component:  requirements.txt / stripe package version pin
  ℹ  Memory: No prior incidents for 'Dependency Error' — first occurrence.

────────────────────────────────────────────────────────────
  ▶  Step 3 / 4  —  Fix Suggestion Agent
────────────────────────────────────────────────────────────
  ✓  [Fix #1] Downgrade stripe pin to latest available version
              (High confidence, ~Minutes)
     Action: Change stripe==7.9.0 to stripe==7.3.0 in requirements.txt
  ✓  [Fix #2] Use pip-compile to lock dependencies from a range
              (High confidence, ~Hours)
     Action: Replace hard pins with ranges in requirements.in,
             run pip-compile to generate a locked requirements.txt

  Prevention: Use pip-compile or Poetry for dependency management.

────────────────────────────────────────────────────────────
  ▶  Step 4 / 4  —  Validation Agent
────────────────────────────────────────────────────────────
  Verdict: APPROVED  (Score: 9/10)
  ✓  relevance      PASS — Both fixes directly address the stripe version pinning root cause
  ✓  accuracy       PASS — stripe 7.3.0 is the latest available; pip-compile is a real tool
  ✓  category_match PASS — Dependency Error fixes for a Dependency Error classification
  ✓  completeness   PASS — Fix #1 resolves the immediate blocker
  ✓  safety         PASS — No dangerous operations suggested

══════════════════════════════════════════════════════════════════════
  PIPELINE COMPLETE  —  Scenario 1 — Dependency Error
══════════════════════════════════════════════════════════════════════
  ✓  Category: Dependency Error  |  Severity: High
  ✓  Verdict:  APPROVED  (Score: 9/10)

  Recommended Actions:
  →  [1] Downgrade stripe pin to latest available version
         In requirements.txt, change stripe==7.9.0 to stripe==7.3.0
  →  [2] Use pip-compile to lock dependencies from a range
```

---

### Scenario 2 — Runtime Error

```
══════════════════════════════════════════════════════════════════════
  DeployMind Orchestrator  —  Scenario 2 — Runtime Error
══════════════════════════════════════════════════════════════════════

  ▶  Step 1 / 4  —  Log Analysis Agent
  ✓  Service:  auth-service v1.8.0
  ✓  Errors: 4  |  Warnings: 3
  ℹ  Primary failure: OSError: [Errno 98] Address already in use: ('0.0.0.0', 8080)
  ℹ  Summary: auth-service v1.8.0 crashed on startup due to port 8080
     already being in use from a previous instance. Container entered
     CrashLoopBackOff and a rollback to v1.7.2 was initiated.

  ▶  Step 2 / 4  —  Issue Classification Agent
  ✓  Category:   Runtime Error
  ✓  Severity:   Critical  (Confidence: High)

  ▶  Step 3 / 4  —  Fix Suggestion Agent
  ✓  [Fix #1] Terminate the stale process holding port 8080  (~Minutes)
     Action: `lsof -ti:8080 | xargs kill -9` or kubectl delete pod
  ✓  [Fix #2] Add preStop lifecycle hook to Kubernetes deployment (~Hours)
     Action: Add terminationGracePeriodSeconds: 30 to pod spec
  ✓  [Fix #3] Fix DB connection error in startup event  (~Hours)
     Action: Add tenacity retry loop around db_pool.connect()

  ▶  Step 4 / 4  —  Validation Agent
  Verdict: APPROVED  (Score: 9/10)
  ⚠  Fix #3 requires identifying the correct DB_URL — ensure credentials
     are not hardcoded in retry logic
  Recommendation: Apply Fix #1 immediately. Fix #2 in next sprint.
```

---

### Scenario 3 — Configuration Error

```
══════════════════════════════════════════════════════════════════════
  DeployMind Orchestrator  —  Scenario 3 — Configuration Error
══════════════════════════════════════════════════════════════════════

  ▶  Step 1 / 4  —  Log Analysis Agent
  ✓  Service:  data-pipeline-worker v3.0.0
  ✓  Errors: 4  |  Warnings: 1
  ℹ  Primary failure: KeyError: 'AWS_ACCESS_KEY_ID' not found in environment

  ▶  Step 2 / 4  —  Issue Classification Agent
  ✓  Category:   Configuration Error
  ✓  Severity:   Critical  (Confidence: High)

  ▶  Step 3 / 4  —  Fix Suggestion Agent
  ✓  [Fix #1] Inject missing env vars via Kubernetes Secret  (~Minutes)
     Action: kubectl create secret generic aws-creds --from-literal=...
             then mount with envFrom: - secretRef: name: aws-creds
  ✓  [Fix #2] Add startup validation with human-readable errors  (~Minutes)
     Action: Replace os.environ['KEY'] with explicit EnvironmentError raise

  ▶  Step 4 / 4  —  Validation Agent
  Verdict: APPROVED  (Score: 10/10)
  ✓  All 5 checks: PASS
  Recommendation: Apply Fix #1 immediately — pure configuration gap.
```

---

### Session Summary — Memory Store

```
══════════════════════════════════════════════════════════════════════
  SESSION SUMMARY  —  Memory Store (Bonus Feature)
══════════════════════════════════════════════════════════════════════
  Total incidents stored: 3

  #    Log Name                               Category               Verdict    Score
  ──── ────────────────────────────────────── ────────────────────── ────────── ─────
  1    Scenario 1 — Dependency Error          Dependency Error       APPROVED    9/10
  2    Scenario 2 — Runtime Error             Runtime Error          APPROVED    9/10
  3    Scenario 3 — Configuration Error       Configuration Error    APPROVED   10/10

  Full history written to: memory_store/incident_history.json
```

---

## Bonus Features

### ✅ Memory-Enabled Agent (Bonus #1)

Every completed pipeline run writes a structured record to `memory_store/incident_history.json`:

```json
{
  "timestamp": "2026-06-22T06:28:07.123456",
  "log_name": "Scenario 1 — Dependency Error",
  "category": "Dependency Error",
  "severity": "High",
  "primary_failure": "No matching distribution found for stripe==7.9.0",
  "verdict": "APPROVED",
  "score": 9,
  "approved_fix": "Downgrade stripe pin to latest available version"
}
```

Before generating fixes, the Orchestrator queries this store by category. If a prior incident matches, it surfaces the previously approved fix:

```
ℹ  Memory: 2 prior incident(s) found for 'Dependency Error'.
ℹ    Last seen: 2026-06-21T14:22:01  —  payment-service-deploy
ℹ    Previously approved fix: Downgrade stripe pin to latest available version
```

This persists across separate program runs — not just within a single session.

---

### ✅ Multi-Agent Orchestration (Bonus #3)

The Orchestrator implements full multi-agent coordination:
- Creates five distinct agents, each with its own model, instruction, and output key
- Creates a separate ADK `Runner` + `Session` per agent step (clean context boundaries)
- Passes structured JSON state explicitly between agents
- Implements early-stop logic: if `validator` returns `REJECTED`, the Orchestrator halts the pipeline and skips the final report + memory write
- Aggregates all intermediate results into a single return dict for downstream use

---

## Test Scenarios

| Scenario | Log File | Expected Category | Severity |
|---|---|---|---|
| pip version conflict | `dependency_error.log` | Dependency Error | High |
| Port conflict / CrashLoopBackOff | `runtime_error.log` | Runtime Error | Critical |
| Missing AWS env vars | `config_error.log` | Configuration Error | Critical |

All three scenarios are run automatically when you execute `orchestrator.py` or `deploymind_demo.py`.

---

## Technology Stack

| Component | Technology |
|---|---|
| Agent framework | Google ADK 2.3.0 |
| LLM | Gemini 2.0 Flash (`gemini-2.0-flash`) |
| Session management | `InMemorySessionService` (ADK) |
| Memory service | `InMemoryMemoryService` (ADK) + custom JSON persistence |
| Language | Python 3.12 |
| Async runtime | `asyncio` |