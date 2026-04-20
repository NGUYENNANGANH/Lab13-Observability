"""
SLO Compliance Evaluator — Role C (SLO & Alerts)
Author: Dương Phương Thảo (Member C)

Loads SLO definitions from config/slo.yaml and evaluates
current metrics against the defined objectives in real-time.
Used by the /slo and /slo/budget API endpoints in main.py.

Architecture:
    config/slo.yaml  →  _load_slo_config()  →  evaluate_slo_compliance()
                                                    ├── per-SLI evaluation
                                                    ├── error budget computation
                                                    └── three-state status logic

Design decisions:
    - YAML-driven: SLO definitions are declarative, not hardcoded
    - Lazy loading with caching: config parsed once on first call
    - Comparison operators: supports both <= (latency, errors, cost)
      and >= (availability, quality, throughput)
    - Three-state status: healthy → at_risk → breaching based on
      error budget remaining percentage (not just pass/fail)
    - Burn rate model: measures depletion speed relative to budget window
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

SLO_CONFIG_PATH = Path("config/slo.yaml")

_slo_cache: dict | None = None
_start_time: float = time.time()


def _load_slo_config() -> dict:
    """Load and cache SLO config."""
    global _slo_cache
    if _slo_cache is None:
        with open(SLO_CONFIG_PATH, encoding="utf-8") as f:
            _slo_cache = yaml.safe_load(f)
    return _slo_cache


def _compute_error_rate(metrics: dict) -> float:
    """Compute error rate percentage from metrics snapshot."""
    traffic = metrics.get("traffic", 0)
    if traffic == 0:
        return 0.0
    total_errors = sum(metrics.get("error_breakdown", {}).values())
    return round((total_errors / traffic) * 100, 2)


def _compute_availability(metrics: dict) -> float:
    """Compute availability percentage (inverse of error rate)."""
    traffic = metrics.get("traffic", 0)
    if traffic == 0:
        return 100.0
    total_errors = sum(metrics.get("error_breakdown", {}).values())
    return round(((traffic - total_errors) / traffic) * 100, 2)


def _compute_throughput(metrics: dict) -> float:
    """Compute current throughput in requests per second."""
    traffic = metrics.get("traffic", 0)
    elapsed = time.time() - _start_time
    if elapsed <= 0:
        return 0.0
    return round(traffic / elapsed, 2)


def _compute_error_budget(target: float, compliant: bool,
                          current_value: float, objective: float,
                          operator: str) -> dict:
    """Compute error budget details for a given SLI."""
    budget_total = round(100.0 - target, 4)
    if budget_total <= 0:
        return {
            "budget_total_pct": 0.0,
            "budget_consumed_pct": 0.0 if compliant else 100.0,
            "budget_remaining_pct": 100.0 if compliant else 0.0,
            "burn_rate": 0.0,
        }

    if objective == 0:
        consumed_ratio = 0.0
    elif operator == "<=":
        if current_value <= objective:
            consumed_ratio = 0.0
        else:
            overshoot = (current_value - objective) / objective
            consumed_ratio = min(overshoot / (budget_total / 100.0), 1.0)
    else:
        if current_value >= objective:
            consumed_ratio = 0.0
        else:
            undershoot = (objective - current_value) / objective
            consumed_ratio = min(undershoot / (budget_total / 100.0), 1.0)

    consumed_pct = round(consumed_ratio * 100, 2)
    remaining_pct = round(100.0 - consumed_pct, 2)
    burn_rate = round(consumed_ratio * 100 / budget_total, 2) if budget_total > 0 else 0.0

    return {
        "budget_total_pct": budget_total,
        "budget_consumed_pct": consumed_pct,
        "budget_remaining_pct": remaining_pct,
        "burn_rate": burn_rate,
    }


def evaluate_slo_compliance(metrics: dict,
                            sli_filter: str | None = None,
                            category_filter: str | None = None) -> dict:
    """
    Evaluate all SLOs against the current metrics snapshot.

    Args:
        metrics: Current metrics snapshot from metrics.snapshot()
        sli_filter: Optional SLI name to filter
        category_filter: Optional category to filter

    Returns a dict with per-SLI results + overall compliance + error budget.
    """
    config = _load_slo_config()
    slis = config.get("slis", {})
    error_budget_config = config.get("error_budget", {})
    results = {}

    sli_evaluators = {
        "latency_p95_ms":    lambda m: (m.get("latency_p95", 0.0), "<="),
        "error_rate_pct":    lambda m: (_compute_error_rate(m), "<="),
        "availability_pct":  lambda m: (_compute_availability(m), ">="),
        "daily_cost_usd":    lambda m: (m.get("total_cost_usd", 0.0), "<="),
        "quality_score_avg": lambda m: (m.get("quality_avg", 0.0), ">="),
        "throughput_rps":    lambda m: (_compute_throughput(m), ">="),
    }

    for sli_name, sli_config in slis.items():
        if sli_filter and sli_name != sli_filter:
            continue
        if category_filter and sli_config.get("category", "") != category_filter:
            continue

        objective = sli_config.get("objective", 0)
        target = sli_config.get("target", 100.0)
        unit = sli_config.get("unit", "")
        description = sli_config.get("description", sli_name)

        evaluator = sli_evaluators.get(sli_name)
        if evaluator:
            current_value, operator = evaluator(metrics)
            compliant = (current_value <= objective) if operator == "<=" else (current_value >= objective)
        else:
            current_value, operator, compliant = 0.0, "<=", True

        budget = _compute_error_budget(target, compliant, current_value, objective, operator)

        if compliant:
            status, status_icon = "healthy", "✅"
        elif budget["budget_remaining_pct"] > 30:
            status, status_icon = "at_risk", "⚠️"
        else:
            status, status_icon = "breaching", "❌"

        results[sli_name] = {
            "description": description,
            "current_value": current_value,
            "objective": objective,
            "unit": unit,
            "target_pct": target,
            "compliant": compliant,
            "status": status,
            "status_icon": status_icon,
            "error_budget": budget,
        }

    passing = sum(1 for r in results.values() if r["compliant"])
    total = len(results)

    all_remaining = [r["error_budget"]["budget_remaining_pct"] for r in results.values()]
    max_burn_rate = max((r["error_budget"]["burn_rate"] for r in results.values()), default=0.0)

    burn_status = "normal"
    for t in error_budget_config.get("burn_rate_thresholds", []):
        if max_burn_rate >= t.get("rate", 0):
            burn_status = t.get("name", "unknown")
            break

    return {
        "service": config.get("service", "unknown"),
        "version": config.get("version", "1.0"),
        "window": config.get("window", "28d"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "overall_compliant": passing == total,
        "passing": f"{passing}/{total}",
        "slis": results,
        "error_budget_summary": {
            "avg_remaining_pct": round(sum(all_remaining) / len(all_remaining), 2) if all_remaining else 100.0,
            "min_remaining_pct": min(all_remaining) if all_remaining else 100.0,
            "max_burn_rate": max_burn_rate,
            "burn_status": burn_status,
        },
    }
