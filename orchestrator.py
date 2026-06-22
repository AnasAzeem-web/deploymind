import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

from google.adk import Agent, Runner
from google.adk.sessions import InMemorySessionService
from google.adk.memory import InMemoryMemoryService
from google.genai import types

from agents import (
    create_log_analyzer_agent,
    create_classifier_agent,
    create_fix_suggester_agent,
    create_validator_agent,
)

RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
DIM     = "\033[2m"

APP_NAME = "deploymind"
MEMORY_FILE = Path(__file__).parent / "memory_store" / "incident_history.json"


def _banner(title):
    print(f"\n{CYAN}{'═' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{CYAN}{'═' * 70}{RESET}")

def _section(title):
    print(f"\n{BLUE}{'─' * 60}{RESET}")
    print(f"{BOLD}{BLUE}  ▶  {title}{RESET}")
    print(f"{BLUE}{'─' * 60}{RESET}")

def _ok(msg):   print(f"  {GREEN}✓  {msg}{RESET}")
def _warn(msg): print(f"  {YELLOW}⚠  {msg}{RESET}")
def _err(msg):  print(f"  {RED}✗  {msg}{RESET}")
def _info(msg): print(f"  {CYAN}ℹ  {msg}{RESET}")


def _safe_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw.strip())


def _load_memory():
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text())
        except Exception:
            return []
    return []

def _save_to_memory(entry):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = _load_memory()
    history.append(entry)
    MEMORY_FILE.write_text(json.dumps(history, indent=2))

def _search_memory(category):
    return [e for e in _load_memory() if e.get("category") == category]


async def _run_agent(runner, agent, session_id, user_id, prompt):
    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    full_response = ""
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_response += part.text
    return full_response.strip()


async def run_pipeline(log_text, log_name="unknown"):
    _banner(f"DeployMind  —  {log_name}")
    print(f"  {DIM}Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")

    log_analyzer  = create_log_analyzer_agent()
    classifier    = create_classifier_agent()
    fix_suggester = create_fix_suggester_agent()
    validator     = create_validator_agent()

    session_svc = InMemorySessionService()
    memory_svc  = InMemoryMemoryService()
    results = {"log_name": log_name}

    _section("Step 1 / 4  —  Log Analysis Agent")
    runner_1 = Runner(app_name=APP_NAME, agent=log_analyzer, session_service=session_svc, memory_service=memory_svc)
    await session_svc.create_session(app_name=APP_NAME, user_id="system", session_id="step1")
    raw_analysis = await _run_agent(runner_1, log_analyzer, "step1", "system", f"Analyze the following deployment log:\n\n{log_text}")
    try:
        analysis = _safe_json(raw_analysis)
        _ok(f"Service: {analysis.get('service', 'N/A')}")
        _ok(f"Errors: {analysis.get('total_errors', '?')}  |  Warnings: {analysis.get('total_warnings', '?')}")
        _info(f"Primary failure: {analysis.get('primary_failure', 'N/A')}")
        _info(f"Summary: {analysis.get('summary', '')}")
        results["log_analysis"] = analysis
    except json.JSONDecodeError:
        _err("Log analyzer returned malformed JSON — aborting.")
        return {"error": "log_analysis_parse_failure"}

    _section("Step 2 / 4  —  Issue Classification Agent")
    runner_2 = Runner(app_name=APP_NAME, agent=classifier, session_service=session_svc, memory_service=memory_svc)
    await session_svc.create_session(app_name=APP_NAME, user_id="system", session_id="step2")
    raw_classification = await _run_agent(runner_2, classifier, "step2", "system", f"Classify this deployment issue.\n\nLog Analysis:\n{json.dumps(analysis, indent=2)}")
    try:
        classification = _safe_json(raw_classification)
        category   = classification.get("category", "Unknown")
        severity   = classification.get("severity", "Unknown")
        confidence = classification.get("confidence", "Unknown")
        colour = RED if severity == "Critical" else YELLOW if severity == "High" else RESET
        _ok(f"Category:  {BOLD}{colour}{category}{RESET}")
        _ok(f"Severity:  {colour}{severity}{RESET}  (Confidence: {confidence})")
        _info(f"Reasoning: {classification.get('reasoning', '')}")
        results["classification"] = classification
    except json.JSONDecodeError:
        _err("Classifier returned malformed JSON — aborting.")
        return {"error": "classification_parse_failure"}

    past = _search_memory(category)
    if past:
        last = past[-1]
        _info(f"Memory: {len(past)} prior incident(s) for '{category}'. Last: {last.get('log_name', '')}")
        if last.get("approved_fix"):
            _info(f"Previously approved fix: {last['approved_fix']}")
    else:
        _info(f"Memory: No prior incidents for '{category}'.")

    _section("Step 3 / 4  —  Fix Suggestion Agent")
    runner_3 = Runner(app_name=APP_NAME, agent=fix_suggester, session_service=session_svc, memory_service=memory_svc)
    await session_svc.create_session(app_name=APP_NAME, user_id="system", session_id="step3")
    raw_fixes = await _run_agent(runner_3, fix_suggester, "step3", "system",
        f"Generate fix suggestions.\n\nLog Analysis:\n{json.dumps(analysis, indent=2)}\n\nClassification:\n{json.dumps(classification, indent=2)}")
    try:
        fix_data = _safe_json(raw_fixes)
        for fix in fix_data.get("fixes", []):
            _ok(f"[Fix #{fix.get('rank')}] {BOLD}{fix.get('title')}{RESET}  ({fix.get('confidence')} confidence, ~{fix.get('estimated_effort')})")
            _info(f"    Action: {fix.get('action', '')}")
            _info(f"    Reason: {fix.get('reasoning', '')}")
        if fix_data.get("prevention"):
            print(f"\n  {MAGENTA}Prevention: {fix_data['prevention']}{RESET}")
        results["fix_suggestions"] = fix_data
    except json.JSONDecodeError:
        _err("Fix suggester returned malformed JSON — aborting.")
        return {"error": "fix_parse_failure"}

    _section("Step 4 / 4  —  Validation Agent")
    runner_4 = Runner(app_name=APP_NAME, agent=validator, session_service=session_svc, memory_service=memory_svc)
    await session_svc.create_session(app_name=APP_NAME, user_id="system", session_id="step4")
    raw_validation = await _run_agent(runner_4, validator, "step4", "system",
        f"Validate the following pipeline outputs.\n\nlog_analysis:\n{json.dumps(analysis, indent=2)}\n\nclassification:\n{json.dumps(classification, indent=2)}\n\nfix_suggestions:\n{json.dumps(fix_data, indent=2)}")
    try:
        validation = _safe_json(raw_validation)
        verdict = validation.get("verdict", "UNKNOWN")
        score   = validation.get("score", 0)
        verdict_colour = GREEN if verdict == "APPROVED" else YELLOW if "WARNINGS" in verdict else RED
        print(f"\n  {BOLD}Verdict: {verdict_colour}{verdict}{RESET}  (Score: {score}/10)")
        for check_name, check_val in validation.get("checks", {}).items():
            col = GREEN if check_val.startswith("PASS") else RED
            status = "✓" if check_val.startswith("PASS") else "✗"
            print(f"    {col}{status}  {check_name}: {check_val}{RESET}")
        for w in validation.get("warnings", []):
            _warn(w)
        _info(f"Recommendation: {validation.get('recommendation', '')}")
        results["validation"] = validation

        if verdict == "REJECTED":
            _err("Orchestrator: PIPELINE HALTED — validation rejected all fixes.")
            results["pipeline_status"] = "HALTED"
            return results
    except json.JSONDecodeError:
        _err("Validator returned malformed JSON.")
        return {"error": "validation_parse_failure"}

    results["pipeline_status"] = "COMPLETED"
    _banner(f"PIPELINE COMPLETE  —  {log_name}")
    _ok(f"Category: {category}  |  Severity: {severity}")
    _ok(f"Verdict:  {verdict}  (Score: {score}/10)")

    approved_ranks = validation.get("approved_fix_ranks", [])
    approved_fixes = [f for f in fix_data.get("fixes", []) if f.get("rank") in approved_ranks]
    if approved_fixes:
        print(f"\n  {BOLD}{GREEN}Recommended Actions:{RESET}")
        for fix in approved_fixes:
            print(f"    {GREEN}→  [{fix['rank']}] {fix['title']}{RESET}")
            print(f"       {DIM}{fix['action']}{RESET}")

    approved_fix_title = approved_fixes[0]["title"] if approved_fixes else None
    _save_to_memory({
        "timestamp":       datetime.now().isoformat(),
        "log_name":        log_name,
        "category":        category,
        "severity":        severity,
        "primary_failure": analysis.get("primary_failure", ""),
        "verdict":         verdict,
        "score":           score,
        "approved_fix":    approved_fix_title,
    })
    _ok("Incident saved to memory store.")
    print()
    return results


async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(f"{RED}ERROR: GEMINI_API_KEY not set.{RESET}")
        return
    os.environ["GOOGLE_API_KEY"] = api_key

    log_dir = Path(__file__).parent / "logs"
    test_cases = [
        ("dependency_error.log", "Scenario 1 — Dependency Error"),
        ("runtime_error.log",    "Scenario 2 — Runtime Error"),
        ("config_error.log",     "Scenario 3 — Configuration Error"),
    ]
    for filename, label in test_cases:
        log_path = log_dir / filename
        if not log_path.exists():
            print(f"{YELLOW}Skipping {filename} — not found{RESET}")
            continue
        await run_pipeline(log_path.read_text(), log_name=label)


if __name__ == "__main__":
    asyncio.run(main())