"""
SLO Compliance Checker — Role C (SLO & Alerts)

Fetches live metrics and compares them against SLO targets
defined in config/slo.yaml.

Usage:
    python scripts/check_slo.py [--url http://localhost:8000] [--json] [--sli latency_p95_ms]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

SLO_PATH = Path("config/slo.yaml")
BASE_URL = "http://127.0.0.1:8000"


def load_slo_config() -> dict:
    with open(SLO_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_metrics(base_url: str) -> dict:
    r = httpx.get(f"{base_url}/metrics", timeout=10)
    r.raise_for_status()
    return r.json()


def compute_error_rate(metrics: dict) -> float:
    traffic = metrics.get("traffic", 0)
    if traffic == 0:
        return 0.0
    return round(sum(metrics.get("error_breakdown", {}).values()) / traffic * 100, 2)


def check_sli(sli_name: str, sli_config: dict, metrics: dict) -> dict:
    """Check a single SLI against its objective."""
    objective = sli_config.get("objective", 0)
    target = sli_config.get("target", 100.0)
    unit = sli_config.get("unit", "")
    description = sli_config.get("description", sli_name)

    if sli_name == "latency_p95_ms":
        current = metrics.get("latency_p95", 0.0)
        compliant = current <= objective
    elif sli_name == "error_rate_pct":
        current = compute_error_rate(metrics)
        compliant = current <= objective
    elif sli_name == "availability_pct":
        traffic = metrics.get("traffic", 0)
        errors = sum(metrics.get("error_breakdown", {}).values())
        current = round(((traffic - errors) / traffic) * 100, 2) if traffic > 0 else 100.0
        compliant = current >= objective
    elif sli_name == "daily_cost_usd":
        current = metrics.get("total_cost_usd", 0.0)
        compliant = current <= objective
    elif sli_name == "quality_score_avg":
        current = metrics.get("quality_avg", 0.0)
        compliant = current >= objective
    elif sli_name == "throughput_rps":
        current = float(metrics.get("traffic", 0))
        compliant = current >= objective
    else:
        current, compliant = 0.0, True

    # Error budget
    budget_total = 100.0 - target
    if budget_total <= 0:
        budget_remaining = 100.0 if compliant else 0.0
    else:
        budget_remaining = 100.0 if compliant else 0.0

    return {
        "sli": sli_name,
        "description": description,
        "objective": objective,
        "target_pct": target,
        "current_value": current,
        "unit": unit,
        "compliant": compliant,
        "status": "✅ PASS" if compliant else "❌ BREACH",
        "error_budget_total_pct": budget_total,
        "error_budget_remaining_pct": budget_remaining,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check SLO compliance against live metrics")
    parser.add_argument("--url", default=BASE_URL, help="Base URL of the running app")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    parser.add_argument("--sli", default=None, help="Filter by SLI name")
    parser.add_argument("--category", default=None, help="Filter by category")
    args = parser.parse_args()

    config = load_slo_config()
    slis = config.get("slis", {})
    service = config.get("service", "unknown")
    window = config.get("window", "28d")

    # Fetch metrics
    try:
        metrics = fetch_metrics(args.url)
    except Exception as e:
        print(f"❌ Failed to connect to {args.url}: {e}")
        print("   Make sure the app is running: uvicorn app.main:app --reload")
        sys.exit(1)

    # Filter SLIs
    filtered = {}
    for name, cfg in slis.items():
        if args.sli and name != args.sli:
            continue
        if args.category and cfg.get("category", "") != args.category:
            continue
        filtered[name] = cfg

    # Evaluate
    results = [check_sli(name, cfg, metrics) for name, cfg in filtered.items()]

    # JSON mode
    if args.json_output:
        passing = sum(1 for r in results if r["compliant"])
        print(json.dumps({
            "service": service,
            "window": window,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "overall_compliant": passing == len(results),
            "passing": f"{passing}/{len(results)}",
            "slis": results,
        }, indent=2))
        return

    # Pretty print
    print(f"SLO Compliance Report — {service}")
    print(f"Window: {window}")
    print()

    print(f"{'SLI':<22} {'Objective':<14} {'Current':<14} {'Target %':<10} {'Budget':<10} {'Status'}")
    print("─" * 85)

    for r in results:
        obj_display = f"{r['objective']} {r['unit']}"
        cur_display = f"{r['current_value']} {r['unit']}"
        budget_display = f"{r['error_budget_remaining_pct']:.0f}%"
        print(
            f"{r['sli']:<22} {obj_display:<14} {cur_display:<14} "
            f"{r['target_pct']:<10} {budget_display:<10} {r['status']}"
        )

    print("─" * 85)

    passing = sum(1 for r in results if r["compliant"])
    total = len(results)
    print(f"\nCompliance: {passing}/{total} SLIs passing")

    if passing == total:
        print("🎉 All SLOs are within budget!")
    else:
        print("\n⚠️  SLO Breaches detected:")
        for b in [r for r in results if not r["compliant"]]:
            print(f"  → {b['sli']}: current={b['current_value']}{b['unit']}, "
                  f"objective={b['objective']}{b['unit']}")

    # Error Budget Summary
    print("\n── Error Budget Summary ─────────────────────────")
    for r in results:
        icon = "🟢" if r["error_budget_remaining_pct"] > 50 else ("🟡" if r["error_budget_remaining_pct"] > 20 else "🔴")
        print(f"  {icon} {r['sli']}: {r['error_budget_remaining_pct']:.0f}% remaining "
              f"(total budget: {r['error_budget_total_pct']:.2f}%)")

    # SLO Table for blueprint report
    print("\n── SLO Table (for blueprint-template.md) ────────")
    print("| SLI | Target | Window | Current Value | Status |")
    print("|---|---:|---|---:|---|")
    for r in results:
        st = "PASS" if r["compliant"] else "BREACH"
        print(f"| {r['description']} | {r['objective']} {r['unit']} | {window} | "
              f"{r['current_value']} {r['unit']} | {st} |")

    print(f"\nEvaluated at: {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
