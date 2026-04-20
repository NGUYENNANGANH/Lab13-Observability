"""
Incident Injection Script — Member D (Load Test & Incident Injection)

Toggles failure scenarios in the running app to test observability
pipeline's ability to detect, surface, and diagnose issues.

Scenarios:
  - rag_slow:    Adds 2.5s delay to RAG retrieval → latency spike
  - tool_fail:   Vector store throws RuntimeError → error rate spike
  - cost_spike:  Output tokens 4x multiplied → cost budget breach

Usage:
    python scripts/inject_incident.py --scenario rag_slow
    python scripts/inject_incident.py --scenario tool_fail
    python scripts/inject_incident.py --scenario cost_spike
    python scripts/inject_incident.py --scenario rag_slow --disable
    python scripts/inject_incident.py --status
    python scripts/inject_incident.py --scenario all
    python scripts/inject_incident.py --scenario all --disable
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8000"

# ── ANSI Colors ──────────────────────────────────────────────
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

SCENARIOS = {
    "rag_slow": {
        "description": "Retrieval latency spike (+2.5s delay on RAG)",
        "expected_symptom": "P95 latency > 3000ms",
        "affected_metric": "latency_p95",
        "affected_alert": "high_latency_p95",
        "debug_hint": "Look for slow RAG span in Langfuse trace waterfall",
    },
    "tool_fail": {
        "description": "Vector store throws RuntimeError",
        "expected_symptom": "Error rate > 2%, HTTP 500 responses",
        "affected_metric": "error_rate_pct",
        "affected_alert": "high_error_rate",
        "debug_hint": "Check error_type in logs, find RuntimeError in trace",
    },
    "cost_spike": {
        "description": "Output tokens multiplied 4x",
        "expected_symptom": "Cost per request ~4x baseline, token budget exceeded",
        "affected_metric": "total_cost_usd",
        "affected_alert": "cost_budget_spike",
        "debug_hint": "Compare tokens_out before/after in metrics endpoint",
    },
}


def fetch_status(base_url: str) -> dict:
    """Fetch current incident status and health."""
    r = httpx.get(f"{base_url}/health", timeout=10)
    r.raise_for_status()
    return r.json()


def toggle_incident(base_url: str, scenario: str, enable: bool) -> dict:
    """Enable or disable an incident scenario."""
    action = "enable" if enable else "disable"
    r = httpx.post(f"{base_url}/incidents/{scenario}/{action}", timeout=10)
    r.raise_for_status()
    return r.json()


def print_status(health: dict) -> None:
    """Print current incident status."""
    incidents = health.get("incidents", {})
    tracing = health.get("tracing_enabled", False)

    print(f"\n{BOLD}{CYAN}📡 INCIDENT STATUS{RESET}")
    print(f"{'─'*55}")

    for scenario, active in incidents.items():
        info = SCENARIOS.get(scenario, {})
        icon = f"{RED}🔴 ACTIVE{RESET}" if active else f"{GREEN}🟢 INACTIVE{RESET}"
        print(f"  {scenario:<14} {icon}")
        if active and info:
            print(f"  {DIM}  ↳ Symptoms: {info['expected_symptom']}{RESET}")

    print(f"\n  Tracing: {'✓ Enabled' if tracing else '✗ Disabled'}")
    print(f"{'─'*55}\n")


def send_test_request(base_url: str) -> dict:
    """Send a single test request and return the response metrics."""
    payload = {
        "user_id": "u_incident_test",
        "session_id": "s_incident_test",
        "feature": "qa",
        "message": "What is the refund policy?"
    }
    start = time.perf_counter()
    try:
        r = httpx.post(f"{base_url}/chat", json=payload, timeout=30)
        latency = (time.perf_counter() - start) * 1000
        if r.status_code == 200:
            body = r.json()
            return {
                "status": r.status_code,
                "latency_ms": round(latency, 1),
                "tokens_out": body.get("tokens_out", 0),
                "cost_usd": body.get("cost_usd", 0.0),
                "error": None,
            }
        else:
            return {
                "status": r.status_code,
                "latency_ms": round(latency, 1),
                "tokens_out": 0,
                "cost_usd": 0.0,
                "error": f"HTTP {r.status_code}",
            }
    except Exception as e:
        return {
            "status": 0,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "tokens_out": 0,
            "cost_usd": 0.0,
            "error": str(e),
        }


def verify_incident(base_url: str, scenario: str) -> None:
    """Send a test request to verify the incident is working."""
    info = SCENARIOS.get(scenario, {})
    print(f"\n  {BOLD}🔍 Verifying '{scenario}' effect...{RESET}")
    
    result = send_test_request(base_url)

    if scenario == "rag_slow":
        if result["latency_ms"] > 2500:
            print(f"  {GREEN}✓ Verified: latency={result['latency_ms']:.0f}ms (expected > 2500ms){RESET}")
        else:
            print(f"  {YELLOW}⚠ Latency={result['latency_ms']:.0f}ms — may not reflect delay yet{RESET}")

    elif scenario == "tool_fail":
        if result["error"]:
            print(f"  {GREEN}✓ Verified: request failed with {result['error']}{RESET}")
        else:
            print(f"  {YELLOW}⚠ Request succeeded — toggle may not be active{RESET}")

    elif scenario == "cost_spike":
        if result["tokens_out"] > 300:
            print(f"  {GREEN}✓ Verified: tokens_out={result['tokens_out']} (expected 4x baseline ~320-720){RESET}")
        else:
            print(f"  {YELLOW}⚠ tokens_out={result['tokens_out']} — may not reflect spike yet{RESET}")

    print(f"  {DIM}Debug hint: {info.get('debug_hint', 'N/A')}{RESET}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Incident Injection — toggle failure scenarios for observability testing"
    )
    parser.add_argument(
        "--scenario", type=str,
        choices=list(SCENARIOS.keys()) + ["all"],
        help="Scenario to enable/disable"
    )
    parser.add_argument(
        "--disable", action="store_true",
        help="Disable the scenario instead of enabling it"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current incident status"
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Send a test request after toggling to verify the effect"
    )
    parser.add_argument(
        "--url", default=BASE_URL,
        help=f"Base URL of the running app (default: {BASE_URL})"
    )
    args = parser.parse_args()

    if not args.scenario and not args.status:
        parser.error("either --scenario or --status is required")

    # Check connectivity
    try:
        health = fetch_status(args.url)
    except Exception as e:
        print(f"\n{RED}✗ Cannot reach {args.url}: {e}{RESET}")
        print(f"  Make sure the app is running: uvicorn app.main:app --reload")
        sys.exit(1)

    # Status mode
    if args.status:
        print_status(health)
        return

    # Toggle mode
    enable = not args.disable
    action_word = "Enabling" if enable else "Disabling"
    action_icon = "🔴" if enable else "🟢"
    scenarios_to_toggle = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]

    print(f"\n{BOLD}{CYAN}⚡ INCIDENT INJECTION{RESET}")
    print(f"{'─'*55}")

    for scenario in scenarios_to_toggle:
        info = SCENARIOS[scenario]
        print(f"\n  {action_icon} {action_word} '{scenario}'")
        print(f"  {DIM}  Description: {info['description']}{RESET}")

        try:
            result = toggle_incident(args.url, scenario, enable)
            print(f"  {GREEN}✓ Done{RESET} — Current state: {json.dumps(result.get('incidents', {}))}")
        except httpx.HTTPStatusError as e:
            print(f"  {RED}✗ Failed: {e.response.status_code} {e.response.text}{RESET}")
            continue

        # Verify if requested
        if enable and args.verify:
            verify_incident(args.url, scenario)

    # Show final status
    final_health = fetch_status(args.url)
    print_status(final_health)

    if enable:
        print(f"  {YELLOW}💡 Tip: Run load test to generate traffic under this incident:{RESET}")
        print(f"     python scripts/load_test.py -c 3 -r 2\n")
        print(f"  {YELLOW}💡 Then check alerts:{RESET}")
        print(f"     python scripts/check_alerts.py\n")
        print(f"  {YELLOW}💡 Don't forget to disable after testing:{RESET}")
        disabled_scenarios = " ".join([f"--scenario {s} --disable" for s in scenarios_to_toggle])
        print(f"     python scripts/inject_incident.py --scenario {'all' if args.scenario == 'all' else args.scenario} --disable\n")


if __name__ == "__main__":
    main()
