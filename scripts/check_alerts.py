"""
Alert Rule Evaluator — Role C (SLO & Alerts)
Author: Dương Phương Thảo (Member C)

Fetches live metrics from the running app and evaluates each
alert rule defined in config/alert_rules.yaml.

Supports both human-readable terminal output (with ANSI colors)
and machine-readable JSON output for CI/CD integration.

Usage:
    python scripts/check_alerts.py [--url http://localhost:8000] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ALERT_RULES_PATH = Path("config/alert_rules.yaml")
BASE_URL = "http://127.0.0.1:8000"


def load_alert_rules() -> list[dict]:
    """Load alert rules from YAML config."""
    with open(ALERT_RULES_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("alerts", [])


def load_escalation_policies() -> dict:
    """Load escalation policies from YAML config."""
    with open(ALERT_RULES_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("escalation_policies", {})


def fetch_metrics(base_url: str) -> dict:
    r = httpx.get(f"{base_url}/metrics", timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_health(base_url: str) -> dict:
    r = httpx.get(f"{base_url}/health", timeout=10)
    r.raise_for_status()
    return r.json()


def compute_error_rate(metrics: dict) -> float:
    traffic = metrics.get("traffic", 0)
    if traffic == 0:
        return 0.0
    total_errors = sum(metrics.get("error_breakdown", {}).values())
    return round((total_errors / traffic) * 100, 2)


def compute_availability(metrics: dict) -> float:
    traffic = metrics.get("traffic", 0)
    if traffic == 0:
        return 100.0
    total_errors = sum(metrics.get("error_breakdown", {}).values())
    return round(((traffic - total_errors) / traffic) * 100, 2)


def evaluate_alert(rule: dict, metrics: dict) -> dict:
    """Evaluate a single alert rule against current metrics."""
    name = rule["name"]
    metric_key = rule.get("metric", name)
    threshold = rule.get("threshold", 0)
    severity = rule.get("severity", "P3")

    # Map metric to current value + direction
    if metric_key == "error_rate_pct":
        current_value, direction = compute_error_rate(metrics), "gt"
    elif metric_key == "availability_pct":
        current_value, direction = compute_availability(metrics), "lt"
    elif metric_key == "throughput_rps":
        current_value, direction = float(metrics.get("traffic", 0)), "lt"
    elif metric_key == "error_budget_burn_rate":
        current_value, direction = 0.0, "gt"
    elif metric_key == "hourly_cost_usd":
        current_value, direction = metrics.get("total_cost_usd", 0.0), "gt"
        threshold = 0.10  # 2x baseline proxy
    elif metric_key == "quality_avg":
        current_value, direction = metrics.get("quality_avg", 0.0), "lt"
    else:
        current_value = metrics.get(metric_key, 0.0)
        direction = "gt"

    # Evaluate
    if isinstance(threshold, str):
        firing = False
    elif direction == "gt":
        firing = current_value > threshold
    elif direction == "lt":
        firing = current_value < threshold
    else:
        firing = False

    return {
        "name": name,
        "description": rule.get("description", ""),
        "severity": severity,
        "metric": metric_key,
        "threshold": threshold,
        "current_value": current_value,
        "firing": firing,
        "status": "🔴 FIRING" if firing else "🟢 OK",
        "runbook": rule.get("runbook", "N/A"),
        "type": rule.get("type", "unknown"),
        "escalation_chain": rule.get("escalation_chain", []),
        "tags": rule.get("tags", []),
    }


# ANSI colors for severity
SEV_COLOR = {"P1": "\033[91m", "P2": "\033[93m", "P3": "\033[94m"}
RESET = "\033[0m"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate alert rules against live metrics")
    parser.add_argument("--url", default=BASE_URL, help="Base URL of the running app")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    args = parser.parse_args()

    rules = load_alert_rules()
    policies = load_escalation_policies()
    print(f"Loaded {len(rules)} alert rules from {ALERT_RULES_PATH}\n")

    # Fetch live data
    try:
        metrics = fetch_metrics(args.url)
        health = fetch_health(args.url)
    except Exception as e:
        print(f"❌ Failed to connect to {args.url}: {e}")
        print("   Make sure the app is running: uvicorn app.main:app --reload")
        sys.exit(1)

    # JSON mode
    if args.json_output:
        all_results = [evaluate_alert(r, metrics) for r in rules]
        for r in all_results:
            r["status"] = "FIRING" if r["firing"] else "OK"
        firing_count = sum(1 for r in all_results if r["firing"])
        print(json.dumps({
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "incidents": health.get("incidents", {}),
            "total_rules": len(all_results),
            "firing": firing_count,
            "ok": len(all_results) - firing_count,
            "alerts": all_results,
        }, indent=2))
        return

    # Show incident toggles
    incidents = health.get("incidents", {})
    active = [k for k, v in incidents.items() if v]
    print("── Incident Toggles ─────────────────────────────")
    if active:
        for inc in active:
            print(f"  ⚠️  {inc}: ACTIVE")
    else:
        print("  ✅ No active incidents")
    print()

    # Evaluate each rule
    print("── Alert Evaluation Results ─────────────────────")
    print(f"{'Alert':<28} {'Severity':<8} {'Current':<12} {'Threshold':<12} {'Status'}")
    print("─" * 80)

    firing_alerts = []
    for rule in rules:
        result = evaluate_alert(rule, metrics)
        if result["firing"]:
            firing_alerts.append(result)

        color = SEV_COLOR.get(result["severity"], "") if result["firing"] else ""
        reset = RESET if result["firing"] else ""
        print(
            f"{color}{result['name']:<28} {result['severity']:<8} "
            f"{result['current_value']!s:<12} {result['threshold']!s:<12} {result['status']}{reset}"
        )

    print("─" * 80)
    print(f"\nSummary: {len(firing_alerts)} firing / {len(rules)} total alerts")

    # Firing alert details with escalation
    if firing_alerts:
        print("\n🚨 ALERTS FIRING — Details:")
        for alert in firing_alerts:
            sev = alert["severity"]
            print(f"\n  {'='*60}")
            print(f"  {SEV_COLOR.get(sev, '')}▸ {alert['name']} [{sev}]{RESET}")
            print(f"    Description: {alert['description']}")
            print(f"    Metric: {alert['metric']} = {alert['current_value']} (threshold: {alert['threshold']})")
            print(f"    Runbook: {alert['runbook']}")

            chain = alert.get("escalation_chain", [])
            if chain:
                print(f"    Escalation chain:")
                for step in chain:
                    print(f"      L{step['level']}: {step['target']} via {step['method']} (after {step['after']})")

            policy_key = {"P1": "p1_critical", "P2": "p2_warning", "P3": "p3_info"}.get(sev)
            if policy_key and policy_key in policies:
                p = policies[policy_key]
                print(f"    Policy: response SLA={p.get('initial_response_sla')}, "
                      f"resolution SLA={p.get('resolution_sla')}")

    print(f"\nEvaluated at: {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
