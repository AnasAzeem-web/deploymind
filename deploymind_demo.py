"""
deploymind_demo.py
------------------
Standalone demo that runs the full DeployMind pipeline using realistic
mock responses (no API key required). This demonstrates the exact same
orchestration logic, JSON parsing, memory store, and coloured reporting
that the live system produces with Gemini.

Run with:  python3 deploymind_demo.py
"""

import asyncio
import json
import sys
import os
from datetime import datetime
from pathlib import Path
import re

# ── Colour codes ──────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
DIM     = "\033[2m"

MEMORY_FILE = Path(__file__).parent / "memory_store" / "incident_history.json"

# ── Mock agent responses ──────────────────────────────────────────────────────

MOCK_RESPONSES = {
    # ── Scenario 1: Dependency Error ──────────────────────────────────────────
    "dependency_log_analysis": json.dumps({
        "service": "payment-service v2.3.1",
        "timestamp": "2024-03-15 10:22:09",
        "total_errors": 4,
        "total_warnings": 1,
        "primary_failure": "No matching distribution found for stripe==7.9.0",
        "error_lines": [
            "Could not find a version that satisfies the requirement stripe==7.9.0",
            "No matching distribution found for stripe==7.9.0",
            "Dependency resolution failed after 3 retries",
            "Build step [Install dependencies] FAILED"
        ],
        "warning_lines": ["pip's dependency resolver does not currently take into account all packages"],
        "summary": "The deployment of payment-service v2.3.1 failed during dependency installation. pip could not find stripe==7.9.0 as it does not exist on PyPI — the latest available version is 7.3.0. The build pipeline was aborted after 10.4 seconds."
    }),
    "dependency_classification": json.dumps({
        "category": "Dependency Error",
        "confidence": "High",
        "reasoning": "The failure occurred during pip install with an explicit 'No matching distribution found' error for a specific package version, which is the canonical signature of a dependency resolution failure.",
        "affected_component": "requirements.txt / stripe package version pin",
        "severity": "High"
    }),
    "dependency_fixes": json.dumps({
        "fixes": [
            {
                "rank": 1,
                "title": "Downgrade stripe pin to latest available version",
                "action": "In requirements.txt, change stripe==7.9.0 to stripe==7.3.0 (latest available), or use stripe>=7.0.0,<8.0.0 for flexibility.",
                "reasoning": "stripe==7.9.0 does not exist on PyPI. The closest available version is 7.3.0. Pinning to the correct version immediately resolves the build failure.",
                "confidence": "High",
                "estimated_effort": "Minutes"
            },
            {
                "rank": 2,
                "title": "Use pip-compile to lock dependencies from a range",
                "action": "Replace hard version pins with ranges in requirements.in, run pip-compile to generate a locked requirements.txt, commit the result.",
                "reasoning": "Hard pins to non-existent versions indicate the requirements.txt was manually edited rather than generated. pip-compile prevents future pin mistakes by resolving from ranges.",
                "confidence": "High",
                "estimated_effort": "Hours"
            }
        ],
        "prevention": "Use pip-compile or Poetry for dependency management so that pinned versions are always verified to exist before committing to the repository."
    }),
    "dependency_validation": json.dumps({
        "verdict": "APPROVED",
        "score": 9,
        "checks": {
            "relevance": "PASS — Both fixes directly address the stripe version pinning root cause",
            "accuracy": "PASS — stripe 7.3.0 is the latest available; pip-compile is a real tool",
            "category_match": "PASS — Dependency Error fixes for a Dependency Error classification",
            "completeness": "PASS — Fix #1 resolves the immediate blocker; Fix #2 prevents recurrence",
            "safety": "PASS — No dangerous operations suggested"
        },
        "warnings": [],
        "recommendation": "Apply Fix #1 immediately to unblock the deployment. Schedule Fix #2 as a follow-up to improve dependency hygiene across all services.",
        "approved_fix_ranks": [1, 2]
    }),

    # ── Scenario 2: Runtime Error ──────────────────────────────────────────────
    "runtime_log_analysis": json.dumps({
        "service": "auth-service v1.8.0",
        "timestamp": "2024-03-15 14:05:22",
        "total_errors": 4,
        "total_warnings": 3,
        "primary_failure": "OSError: [Errno 98] Address already in use: ('0.0.0.0', 8080)",
        "error_lines": [
            "asyncpg.exceptions.ConnectionDoesNotExistError: the connection was closed",
            "OSError: [Errno 98] Address already in use: ('0.0.0.0', 8080)",
            "Unhandled exception in ASGI lifecycle",
            "Container exited with code 1 — CrashLoopBackOff"
        ],
        "warning_lines": [
            "Health check attempt 1/5 failed: connection refused on port 8080",
            "Health check attempt 2/5 failed: connection refused on port 8080",
            "Health check attempt 3/5 failed: connection refused on port 8080"
        ],
        "summary": "auth-service v1.8.0 crashed on startup due to port 8080 already being in use, likely from a previous instance that was not cleaned up. A secondary DB connection error also surfaced during the ASGI startup lifecycle. The container entered CrashLoopBackOff and a rollback to v1.7.2 was initiated."
    }),
    "runtime_classification": json.dumps({
        "category": "Runtime Error",
        "confidence": "High",
        "reasoning": "The container started successfully but crashed during the application startup event with an OS-level port binding error and a database connection failure — both are runtime conditions that occur after the image is built and the container is launched.",
        "affected_component": "auth-service ASGI startup / port 8080 binding",
        "severity": "Critical"
    }),
    "runtime_fixes": json.dumps({
        "fixes": [
            {
                "rank": 1,
                "title": "Terminate the stale process holding port 8080",
                "action": "On the node: `lsof -ti:8080 | xargs kill -9` or `kubectl delete pod <old-auth-pod>` to remove the previous instance before redeploying.",
                "reasoning": "EADDRINUSE 98 means another process already bound to 8080. The new pod cannot start until the port is released. This is the immediate blocker.",
                "confidence": "High",
                "estimated_effort": "Minutes"
            },
            {
                "rank": 2,
                "title": "Add preStop lifecycle hook to Kubernetes deployment",
                "action": "Add a preStop hook in the pod spec: `lifecycle: { preStop: { exec: { command: ['sh','-c','sleep 5'] } } }` and set terminationGracePeriodSeconds: 30 to ensure the old pod fully terminates before the new one starts.",
                "reasoning": "The port conflict is a symptom of rolling deployment overlap — the old pod was still alive when the new one tried to bind. A proper termination grace period prevents this in future rollouts.",
                "confidence": "High",
                "estimated_effort": "Hours"
            },
            {
                "rank": 3,
                "title": "Fix DB connection error in startup event",
                "action": "In main.py startup_event, add retry logic: wrap `await db_pool.connect()` in a retry loop with exponential backoff (e.g., tenacity library) before the port bind attempt.",
                "reasoning": "The DB ConnectionDoesNotExistError is a secondary error that will cause repeated CrashLoopBackOff even after the port issue is fixed, if the DB is temporarily unavailable on startup.",
                "confidence": "Medium",
                "estimated_effort": "Hours"
            }
        ],
        "prevention": "Implement readiness probes and proper termination grace periods in all Kubernetes deployments to prevent port conflicts during rolling updates."
    }),
    "runtime_validation": json.dumps({
        "verdict": "APPROVED",
        "score": 9,
        "checks": {
            "relevance": "PASS — All three fixes directly address errors identified in the log",
            "accuracy": "PASS — lsof, kubectl delete pod, and Kubernetes lifecycle hooks are standard and correct",
            "category_match": "PASS — All fixes are appropriate for a Runtime Error / CrashLoopBackOff scenario",
            "completeness": "PASS — Immediate fix (#1), structural fix (#2), and secondary bug fix (#3) are all covered",
            "safety": "PASS — `kill -9` on a stale pod process is safe in this context"
        },
        "warnings": [
            "Fix #3 requires identifying the correct DB_URL — ensure credentials are not hardcoded in retry logic"
        ],
        "recommendation": "Apply Fix #1 immediately to unblock the deployment. Apply Fix #2 in the next sprint to prevent recurrence. Address Fix #3 before the next major release.",
        "approved_fix_ranks": [1, 2, 3]
    }),

    # ── Scenario 3: Configuration Error ───────────────────────────────────────
    "config_log_analysis": json.dumps({
        "service": "data-pipeline-worker v3.0.0",
        "timestamp": "2024-03-15 16:30:02",
        "total_errors": 4,
        "total_warnings": 1,
        "primary_failure": "KeyError: 'AWS_ACCESS_KEY_ID' not found in environment",
        "error_lines": [
            "KeyError: 'AWS_ACCESS_KEY_ID' not found in environment",
            "KeyError: 'AWS_SECRET_ACCESS_KEY' is not set",
            "KeyError: 'S3_BUCKET_NAME' is not set",
            "Configuration validation failed: 3 required environment variables missing"
        ],
        "warning_lines": ["Configuration file found but environment overrides missing"],
        "summary": "data-pipeline-worker v3.0.0 failed to start because three required AWS environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME) were not injected into the container. The config loader raised KeyError when attempting to read them from os.environ. The service aborted before any application logic ran."
    }),
    "config_classification": json.dumps({
        "category": "Configuration Error",
        "confidence": "High",
        "reasoning": "The failure is explicitly caused by missing environment variables required by the application config — a textbook configuration error. All three missing keys are AWS credentials and bucket names that should be injected at deploy time via Kubernetes Secrets or a CI/CD environment.",
        "affected_component": "/app/config/settings.py — load_aws_config()",
        "severity": "Critical"
    }),
    "config_fixes": json.dumps({
        "fixes": [
            {
                "rank": 1,
                "title": "Inject missing env vars via Kubernetes Secret",
                "action": "Create a K8s Secret: `kubectl create secret generic aws-creds --from-literal=AWS_ACCESS_KEY_ID=<key> --from-literal=AWS_SECRET_ACCESS_KEY=<secret> --from-literal=S3_BUCKET_NAME=<bucket>` then mount it in the deployment spec under `envFrom: - secretRef: name: aws-creds`.",
                "reasoning": "The three missing variables are AWS credentials — they must never be baked into images and should always be injected as Kubernetes Secrets. This is both the fix and the correct architectural pattern.",
                "confidence": "High",
                "estimated_effort": "Minutes"
            },
            {
                "rank": 2,
                "title": "Add startup validation with human-readable errors",
                "action": "In settings.py, replace bare `os.environ['KEY']` with `os.environ.get('KEY') or raise EnvironmentError(f'Required variable KEY is not set. See deployment docs.')` to give clearer failure messages.",
                "reasoning": "The current KeyError gives a cryptic traceback. Explicit validation with descriptive messages makes future config errors much faster to diagnose.",
                "confidence": "High",
                "estimated_effort": "Minutes"
            }
        ],
        "prevention": "Add a CI/CD pre-deploy check that validates all required environment variables are present in the target namespace before triggering a rollout."
    }),
    "config_validation": json.dumps({
        "verdict": "APPROVED",
        "score": 10,
        "checks": {
            "relevance": "PASS — Both fixes directly address the missing environment variables root cause",
            "accuracy": "PASS — kubectl create secret syntax and envFrom pattern are correct",
            "category_match": "PASS — Configuration Error fixes for a Configuration Error classification",
            "completeness": "PASS — Fix #1 resolves the immediate blocker; Fix #2 improves future debuggability",
            "safety": "PASS — No dangerous operations; Fix #1 correctly uses Secrets rather than ConfigMaps for credentials"
        },
        "warnings": [],
        "recommendation": "Apply Fix #1 immediately — this is a pure configuration gap, not a code bug. The service should deploy cleanly once secrets are injected.",
        "approved_fix_ranks": [1, 2]
    }),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _banner(title: str) -> None:
    width = 70
    print(f"\n{CYAN}{'═' * width}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{CYAN}{'═' * width}{RESET}")

def _section(title: str) -> None:
    print(f"\n{BLUE}{'─' * 60}{RESET}")
    print(f"{BOLD}{BLUE}  ▶  {title}{RESET}")
    print(f"{BLUE}{'─' * 60}{RESET}")

def _ok(msg)   : print(f"  {GREEN}✓  {msg}{RESET}")
def _warn(msg) : print(f"  {YELLOW}⚠  {msg}{RESET}")
def _err(msg)  : print(f"  {RED}✗  {msg}{RESET}")
def _info(msg) : print(f"  {CYAN}ℹ  {msg}{RESET}")


def _load_memory() -> list:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text())
        except Exception:
            return []
    return []

def _save_to_memory(entry: dict) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = _load_memory()
    history.append(entry)
    MEMORY_FILE.write_text(json.dumps(history, indent=2))

def _search_memory(category: str) -> list:
    return [e for e in _load_memory() if e.get("category") == category]


# ── Pipeline ──────────────────────────────────────────────────────────────────

def simulate_pipeline(scenario_key: str, log_name: str, log_text: str) -> dict:
    """Run a full pipeline simulation using pre-canned mock responses."""

    _banner(f"DeployMind Orchestrator  —  {log_name}")
    print(f"  {DIM}Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"  {DIM}[DEMO MODE — Gemini API calls simulated]{RESET}")

    # ── Step 1: Log Analysis ──────────────────────────────────────────────────
    _section("Step 1 / 4  —  Log Analysis Agent")
    print(f"  {DIM}→ Agent: log_analyzer  |  Model: gemini-2.0-flash{RESET}")
    print(f"  {DIM}→ Prompt: Analyze deployment log ({len(log_text)} chars){RESET}")

    analysis = json.loads(MOCK_RESPONSES[f"{scenario_key}_log_analysis"])
    _ok(f"Service:  {BOLD}{analysis['service']}{RESET}")
    _ok(f"Errors:   {analysis['total_errors']}  |  Warnings: {analysis['total_warnings']}")
    _info(f"Primary failure: {analysis['primary_failure']}")
    _info(f"Summary: {analysis['summary']}")

    # ── Step 2: Classification ────────────────────────────────────────────────
    _section("Step 2 / 4  —  Issue Classification Agent")
    print(f"  {DIM}→ Agent: issue_classifier  |  Model: gemini-2.0-flash{RESET}")

    classification = json.loads(MOCK_RESPONSES[f"{scenario_key}_classification"])
    category   = classification["category"]
    severity   = classification["severity"]
    confidence = classification["confidence"]

    colour = RED if severity == "Critical" else YELLOW if severity == "High" else RESET
    _ok(f"Category:   {BOLD}{colour}{category}{RESET}")
    _ok(f"Severity:   {colour}{severity}{RESET}  (Confidence: {confidence})")
    _info(f"Reasoning:  {classification['reasoning']}")
    _info(f"Component:  {classification['affected_component']}")

    # Memory lookup
    past = _search_memory(category)
    if past:
        _info(f"Memory: {len(past)} prior incident(s) found for '{category}'.")
        last = past[-1]
        _info(f"  Last seen: {last.get('timestamp', '')[:19]}  —  {last.get('log_name', '')}")
        if last.get("approved_fix"):
            _info(f"  Previously approved fix: {last['approved_fix']}")
    else:
        _info(f"Memory: No prior incidents for '{category}' — first occurrence.")

    # ── Step 3: Fix Suggestions ───────────────────────────────────────────────
    _section("Step 3 / 4  —  Fix Suggestion Agent")
    print(f"  {DIM}→ Agent: fix_suggester  |  Model: gemini-2.0-flash{RESET}")

    fix_data = json.loads(MOCK_RESPONSES[f"{scenario_key}_fixes"])
    for fix in fix_data["fixes"]:
        rank   = fix["rank"]
        title  = fix["title"]
        conf   = fix["confidence"]
        effort = fix["estimated_effort"]
        _ok(f"[Fix #{rank}] {BOLD}{title}{RESET}  ({conf} confidence, ~{effort})")
        _info(f"    Action: {fix['action']}")
        _info(f"    Reason: {fix['reasoning']}")
    print(f"\n  {MAGENTA}Prevention: {fix_data['prevention']}{RESET}")

    # ── Step 4: Validation ────────────────────────────────────────────────────
    _section("Step 4 / 4  —  Validation Agent")
    print(f"  {DIM}→ Agent: validator  |  Model: gemini-2.0-flash{RESET}")

    validation = json.loads(MOCK_RESPONSES[f"{scenario_key}_validation"])
    verdict = validation["verdict"]
    score   = validation["score"]

    verdict_colour = GREEN if verdict == "APPROVED" else YELLOW if "WARNINGS" in verdict else RED
    print(f"\n  {BOLD}Verdict: {verdict_colour}{verdict}{RESET}  (Score: {score}/10)")

    for check_name, check_val in validation["checks"].items():
        status = "✓" if check_val.startswith("PASS") else "✗"
        col = GREEN if check_val.startswith("PASS") else RED
        print(f"    {col}{status}  {check_name:18s}  {check_val}{RESET}")

    for w in validation.get("warnings", []):
        _warn(w)
    _info(f"Recommendation: {validation['recommendation']}")

    # Orchestrator halts on REJECTED
    if verdict == "REJECTED":
        _err("Orchestrator: PIPELINE HALTED — validation rejected all fixes.")
        return {"pipeline_status": "HALTED", "log_name": log_name}

    # ── Final Report ──────────────────────────────────────────────────────────
    _banner(f"PIPELINE COMPLETE  —  {log_name}")
    _ok(f"Category:   {BOLD}{category}{RESET}  |  Severity: {colour}{severity}{RESET}")
    _ok(f"Verdict:    {verdict_colour}{verdict}{RESET}  (Score: {score}/10)")

    approved_ranks = validation.get("approved_fix_ranks", [])
    approved_fixes = [f for f in fix_data["fixes"] if f["rank"] in approved_ranks]
    if approved_fixes:
        print(f"\n  {BOLD}{GREEN}Recommended Actions:{RESET}")
        for fix in approved_fixes:
            print(f"    {GREEN}→  [{fix['rank']}] {fix['title']}{RESET}")
            print(f"       {DIM}{fix['action']}{RESET}")

    # Memory save
    approved_fix_title = approved_fixes[0]["title"] if approved_fixes else None
    _save_to_memory({
        "timestamp":       datetime.now().isoformat(),
        "log_name":        log_name,
        "category":        category,
        "severity":        severity,
        "primary_failure": analysis["primary_failure"],
        "verdict":         verdict,
        "score":           score,
        "approved_fix":    approved_fix_title,
    })
    _ok(f"Incident saved to memory store.")
    print()

    return {
        "pipeline_status": "COMPLETED",
        "log_name":        log_name,
        "category":        category,
        "verdict":         verdict,
        "score":           score,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Clear memory store for a clean demo run
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()

    log_dir = Path(__file__).parent / "logs"
    test_cases = [
        ("dependency", "Scenario 1 — Dependency Error",    "dependency_error.log"),
        ("runtime",    "Scenario 2 — Runtime Error",       "runtime_error.log"),
        ("config",     "Scenario 3 — Configuration Error", "config_error.log"),
    ]

    results = []
    for scenario_key, label, filename in test_cases:
        log_text = (log_dir / filename).read_text()
        result = simulate_pipeline(scenario_key, label, log_text)
        results.append(result)

    # Session summary (demonstrates memory store across runs)
    _banner("SESSION SUMMARY  —  Memory Store (Bonus Feature)")
    history = _load_memory()
    print(f"  Total incidents stored: {BOLD}{len(history)}{RESET}\n")
    print(f"  {'#':<4} {'Log Name':<38} {'Category':<22} {'Verdict':<10} {'Score'}")
    print(f"  {'─'*4} {'─'*38} {'─'*22} {'─'*10} {'─'*5}")
    for i, entry in enumerate(history, 1):
        col = GREEN if entry['verdict'] == 'APPROVED' else YELLOW
        print(
            f"  {i:<4} {entry['log_name']:<38} {entry['category']:<22} "
            f"{col}{entry['verdict']:<10}{RESET} {entry['score']}/10"
        )

    print(f"\n  {DIM}Full history written to: {MEMORY_FILE}{RESET}")
    print()


if __name__ == "__main__":
    main()