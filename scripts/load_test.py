"""
Load Test Script — Member D (Load Test & Incident Injection)

Generates concurrent traffic to the FastAPI agent and collects
detailed performance statistics for dashboard and SLO analysis.

Usage:
    python scripts/load_test.py                      # sequential, 1 round
    python scripts/load_test.py --concurrency 5      # 5 threads
    python scripts/load_test.py --rounds 3 -c 5      # 3 rounds × 5 threads
    python scripts/load_test.py --export results.json # export results
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import httpx

BASE_URL = "http://127.0.0.1:8000"
QUERIES = Path("data/sample_queries.jsonl")

# ── ANSI Colors ──────────────────────────────────────────────
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class RequestResult:
    """Stores the result of a single request."""
    status_code: int
    latency_ms: float
    correlation_id: str
    feature: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    quality_score: float = 0.0
    error: Optional[str] = None


@dataclass
class LoadTestReport:
    """Aggregated report for a load test run."""
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    quality_scores: list[float] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    concurrency: int = 1
    rounds: int = 1

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time

    @property
    def throughput_rps(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return round(self.total_requests / self.duration_s, 2)

    @property
    def error_rate_pct(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return round((self.failed / self.total_requests) * 100, 2)

    @property
    def latency_p50(self) -> float:
        return _percentile(self.latencies_ms, 50)

    @property
    def latency_p95(self) -> float:
        return _percentile(self.latencies_ms, 95)

    @property
    def latency_p99(self) -> float:
        return _percentile(self.latencies_ms, 99)

    @property
    def avg_quality(self) -> float:
        if not self.quality_scores:
            return 0.0
        return round(statistics.mean(self.quality_scores), 4)

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "successful": self.successful,
            "failed": self.failed,
            "error_rate_pct": self.error_rate_pct,
            "duration_s": round(self.duration_s, 2),
            "throughput_rps": self.throughput_rps,
            "concurrency": self.concurrency,
            "rounds": self.rounds,
            "latency_ms": {
                "min": round(min(self.latencies_ms), 1) if self.latencies_ms else 0,
                "p50": self.latency_p50,
                "p95": self.latency_p95,
                "p99": self.latency_p99,
                "max": round(max(self.latencies_ms), 1) if self.latencies_ms else 0,
                "avg": round(statistics.mean(self.latencies_ms), 1) if self.latencies_ms else 0,
            },
            "tokens": {
                "total_in": self.total_tokens_in,
                "total_out": self.total_tokens_out,
            },
            "cost": {
                "total_usd": round(self.total_cost_usd, 6),
                "avg_per_request_usd": round(self.total_cost_usd / max(1, self.total_requests), 6),
            },
            "quality_avg": self.avg_quality,
            "errors": self.errors,
        }


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    items = sorted(values)
    idx = max(0, min(len(items) - 1, round((p / 100) * len(items) + 0.5) - 1))
    return round(items[idx], 1)


def send_request(client: httpx.Client, payload: dict, base_url: str) -> RequestResult:
    """Send a single request and return the result."""
    try:
        start = time.perf_counter()
        r = client.post(f"{base_url}/chat", json=payload)
        latency = (time.perf_counter() - start) * 1000

        if r.status_code == 200:
            body = r.json()
            return RequestResult(
                status_code=r.status_code,
                latency_ms=latency,
                correlation_id=body.get("correlation_id", "N/A"),
                feature=payload.get("feature", "unknown"),
                tokens_in=body.get("tokens_in", 0),
                tokens_out=body.get("tokens_out", 0),
                cost_usd=body.get("cost_usd", 0.0),
                quality_score=body.get("quality_score", 0.0),
            )
        else:
            return RequestResult(
                status_code=r.status_code,
                latency_ms=latency,
                correlation_id="N/A",
                feature=payload.get("feature", "unknown"),
                error=f"HTTP {r.status_code}",
            )
    except Exception as e:
        return RequestResult(
            status_code=0,
            latency_ms=0,
            correlation_id="N/A",
            feature=payload.get("feature", "unknown"),
            error=str(e),
        )


def print_request_result(result: RequestResult, index: int) -> None:
    """Print a single request result with color coding."""
    if result.error:
        status_color = RED
        status_icon = "✗"
    elif result.latency_ms > 3000:
        status_color = YELLOW
        status_icon = "⚠"
    else:
        status_color = GREEN
        status_icon = "✓"

    print(
        f"  {status_color}{status_icon}{RESET} "
        f"[{index:02d}] {DIM}{result.correlation_id}{RESET} | "
        f"{result.feature:<8} | "
        f"{result.latency_ms:>7.1f}ms | "
        f"tokens: {result.tokens_in}→{result.tokens_out} | "
        f"${result.cost_usd:.6f} | "
        f"q={result.quality_score:.2f}"
    )


def print_report(report: LoadTestReport) -> None:
    """Print a formatted summary report."""
    print(f"\n{'='*70}")
    print(f"{BOLD}{CYAN}  📊 LOAD TEST REPORT{RESET}")
    print(f"{'='*70}")

    # ── Overview
    print(f"\n  {BOLD}Overview{RESET}")
    print(f"  ├─ Total Requests : {report.total_requests}")
    print(f"  ├─ Successful     : {GREEN}{report.successful}{RESET}")
    print(f"  ├─ Failed         : {RED}{report.failed}{RESET}")
    print(f"  ├─ Error Rate     : {report.error_rate_pct}%")
    print(f"  ├─ Duration       : {report.duration_s:.2f}s")
    print(f"  ├─ Concurrency    : {report.concurrency}")
    print(f"  ├─ Rounds         : {report.rounds}")
    print(f"  └─ Throughput     : {report.throughput_rps} req/s")

    # ── Latency
    if report.latencies_ms:
        print(f"\n  {BOLD}Latency (ms){RESET}")
        print(f"  ├─ Min : {min(report.latencies_ms):>8.1f}")
        print(f"  ├─ P50 : {report.latency_p50:>8.1f}")
        print(f"  ├─ P95 : {report.latency_p95:>8.1f}")
        p95_status = GREEN if report.latency_p95 <= 3000 else RED
        print(f"  │       {p95_status}{'✓ Within SLO (< 3000ms)' if report.latency_p95 <= 3000 else '✗ Exceeds SLO (> 3000ms)'}{RESET}")
        print(f"  ├─ P99 : {report.latency_p99:>8.1f}")
        print(f"  ├─ Max : {max(report.latencies_ms):>8.1f}")
        print(f"  └─ Avg : {statistics.mean(report.latencies_ms):>8.1f}")

    # ── Cost & Tokens
    print(f"\n  {BOLD}Cost & Tokens{RESET}")
    print(f"  ├─ Total Cost    : ${report.total_cost_usd:.6f}")
    avg_cost = report.total_cost_usd / max(1, report.total_requests)
    print(f"  ├─ Avg/Request   : ${avg_cost:.6f}")
    print(f"  ├─ Tokens In     : {report.total_tokens_in:,}")
    print(f"  └─ Tokens Out    : {report.total_tokens_out:,}")

    # ── Quality
    print(f"\n  {BOLD}Quality{RESET}")
    q_status = GREEN if report.avg_quality >= 0.75 else RED
    print(f"  └─ Avg Score     : {q_status}{report.avg_quality:.4f}{RESET}"
          f" {'✓ Meets SLO (≥ 0.75)' if report.avg_quality >= 0.75 else '✗ Below SLO (< 0.75)'}")

    # ── Errors
    if report.errors:
        print(f"\n  {BOLD}{RED}Errors{RESET}")
        for err_type, count in report.errors.items():
            print(f"  ├─ {err_type}: {count}")

    print(f"\n{'='*70}\n")


def run_load_test(
    base_url: str,
    concurrency: int,
    rounds: int,
    queries: list[dict],
) -> LoadTestReport:
    """Execute the load test and return a report."""
    report = LoadTestReport(concurrency=concurrency, rounds=rounds)
    report.start_time = time.perf_counter()

    request_index = 0

    with httpx.Client(timeout=30.0) as client:
        for round_num in range(1, rounds + 1):
            if rounds > 1:
                print(f"\n{BOLD}── Round {round_num}/{rounds} ──{RESET}")

            if concurrency > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                    future_to_payload = {
                        executor.submit(send_request, client, q, base_url): q
                        for q in queries
                    }
                    for future in concurrent.futures.as_completed(future_to_payload):
                        request_index += 1
                        result = future.result()
                        report.total_requests += 1
                        if result.error:
                            report.failed += 1
                            report.errors[result.error] = report.errors.get(result.error, 0) + 1
                        else:
                            report.successful += 1
                            report.latencies_ms.append(result.latency_ms)
                            report.total_cost_usd += result.cost_usd
                            report.total_tokens_in += result.tokens_in
                            report.total_tokens_out += result.tokens_out
                            report.quality_scores.append(result.quality_score)
                        print_request_result(result, request_index)
            else:
                for q in queries:
                    request_index += 1
                    result = send_request(client, q, base_url)
                    report.total_requests += 1
                    if result.error:
                        report.failed += 1
                        report.errors[result.error] = report.errors.get(result.error, 0) + 1
                    else:
                        report.successful += 1
                        report.latencies_ms.append(result.latency_ms)
                        report.total_cost_usd += result.cost_usd
                        report.total_tokens_in += result.tokens_in
                        report.total_tokens_out += result.tokens_out
                        report.quality_scores.append(result.quality_score)
                    print_request_result(result, request_index)

    report.end_time = time.perf_counter()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Test Script — generates traffic for observability analysis"
    )
    parser.add_argument(
        "-c", "--concurrency", type=int, default=1,
        help="Number of concurrent threads (default: 1)"
    )
    parser.add_argument(
        "-r", "--rounds", type=int, default=1,
        help="Number of rounds to repeat all queries (default: 1)"
    )
    parser.add_argument(
        "--url", default=BASE_URL,
        help=f"Base URL of the running app (default: {BASE_URL})"
    )
    parser.add_argument(
        "--export", type=str, default=None,
        help="Export results to JSON file (e.g., data/load_test_results.json)"
    )
    args = parser.parse_args()

    # Load queries
    if not QUERIES.exists():
        print(f"{RED}Error: {QUERIES} not found.{RESET}")
        sys.exit(1)

    lines = [line for line in QUERIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    queries = [json.loads(line) for line in lines]

    print(f"\n{BOLD}{CYAN}🚀 LOAD TEST — Day 13 Observability Lab{RESET}")
    print(f"   Target      : {args.url}")
    print(f"   Queries     : {len(queries)}")
    print(f"   Concurrency : {args.concurrency}")
    print(f"   Rounds      : {args.rounds}")
    print(f"   Total Reqs  : {len(queries) * args.rounds}")
    print(f"{'─'*50}")

    # Check health
    try:
        health = httpx.get(f"{args.url}/health", timeout=5).json()
        incidents = health.get("incidents", {})
        active = [k for k, v in incidents.items() if v]
        if active:
            print(f"   {YELLOW}⚠ Active incidents: {', '.join(active)}{RESET}")
        else:
            print(f"   {GREEN}✓ No active incidents{RESET}")
        print(f"   Tracing     : {'✓ Enabled' if health.get('tracing_enabled') else '✗ Disabled'}")
    except Exception as e:
        print(f"\n{RED}✗ Cannot reach {args.url}: {e}{RESET}")
        print(f"  Make sure the app is running: uvicorn app.main:app --reload")
        sys.exit(1)

    print(f"\n{BOLD}── Sending Requests ──{RESET}")

    # Run load test
    report = run_load_test(
        base_url=args.url,
        concurrency=args.concurrency,
        rounds=args.rounds,
        queries=queries,
    )

    # Print report
    print_report(report)

    # Export if requested
    if args.export:
        export_path = Path(args.export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_data = {
            "test_config": {
                "url": args.url,
                "concurrency": args.concurrency,
                "rounds": args.rounds,
                "total_queries": len(queries),
            },
            "results": report.to_dict(),
        }
        export_path.write_text(json.dumps(export_data, indent=2), encoding="utf-8")
        print(f"  {GREEN}✓ Results exported to {export_path}{RESET}\n")


if __name__ == "__main__":
    main()
