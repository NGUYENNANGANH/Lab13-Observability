# Alert Runbooks — Day 13 Observability Lab

> **Owner**: Role C (SLO & Alerts)
> **Last updated**: 2026-04-20
> **Service**: `day13-observability-lab`

This document provides step-by-step investigation and mitigation procedures for each alert defined in [`config/alert_rules.yaml`](../config/alert_rules.yaml).

---

## General Triage Workflow

```
ALERT fires  →  Check /metrics endpoint  →  Inspect recent logs (data/logs.jsonl)
                                          →  Review traces in Langfuse
                                          →  Check /health for incident toggles
                                          →  Apply mitigation from runbook below
```

**Quick commands:**
```bash
curl -s http://localhost:8000/health | python3 -m json.tool
curl -s http://localhost:8000/metrics | python3 -m json.tool
curl -s http://localhost:8000/slo | python3 -m json.tool
curl -s http://localhost:8000/slo/budget | python3 -m json.tool
curl -s http://localhost:8000/alerts | python3 -m json.tool
python3 scripts/check_alerts.py
python3 scripts/check_slo.py
```

---

## Escalation Matrix

| Severity | Response SLA | Resolution SLA | Auto-page | Post-mortem |
|----------|-------------|----------------|-----------|-------------|
| **P1** | 5 min | 30 min | ✅ Yes | ✅ Required |
| **P2** | 15 min | 2 hours | ❌ No | ❌ Optional |
| **P3** | 4 hours | 24 hours | ❌ No | ❌ No |

---

## 1. High Latency P95

| Field | Value |
|-------|-------|
| **Alert name** | `high_latency_p95` |
| **Severity** | P2 — Warning |
| **Condition** | `latency_p95_ms > 3000` sustained for 5 minutes |
| **SLO target** | P95 < 3000ms, 99.5% of rolling 28d window |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (slack, 0m) → L2: engineering-lead (pagerduty, 15m) |

### Symptoms
- Users experience noticeably slow responses
- P95 latency > 3000ms on `/metrics` endpoint

### Investigation Steps

1. **Check current metrics:**
   ```bash
   curl -s http://localhost:8000/metrics | python3 -c "
   import sys, json
   m = json.load(sys.stdin)
   print(f'P50: {m[\"latency_p50\"]}ms | P95: {m[\"latency_p95\"]}ms | P99: {m[\"latency_p99\"]}ms')
   "
   ```

2. **Check incident toggles:**
   ```bash
   curl -s http://localhost:8000/health | python3 -m json.tool
   ```
   → If `rag_slow: true`, this is an injected incident.

3. **Examine slow traces in Langfuse:**
   - Filter traces by latency > 3000ms
   - Compare spans: `rag-retrieve` vs `llm-generate`

4. **Check recent logs:**
   ```bash
   tail -20 data/logs.jsonl | python3 -c "
   import sys, json
   for line in sys.stdin:
       d = json.loads(line.strip())
       if d.get('latency_ms', 0) > 3000:
           print(f'{d.get(\"correlation_id\")} | {d.get(\"latency_ms\")}ms')
   "
   ```

### Root Cause Analysis

| Likely Cause | Evidence | Fix |
|---|---|---|
| RAG retrieval slow | `rag-retrieve` span > 2000ms | Truncate query, fallback retrieval |
| LLM generation slow | `llm-generate` span > 2000ms | Reduce prompt size |
| `rag_slow` incident active | `/health` shows `rag_slow: true` | Disable: `POST /incidents/rag_slow/disable` |

### Mitigation
```bash
curl -s -X POST http://localhost:8000/incidents/rag_slow/disable
python3 scripts/load_test.py && curl -s http://localhost:8000/metrics | python3 -m json.tool
```

---

## 2. Critical Latency P99

| Field | Value |
|-------|-------|
| **Alert name** | `critical_latency_p99` |
| **Severity** | P1 — Critical |
| **Condition** | `latency_p99_ms > 5000` sustained for 2 minutes |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (pagerduty, 0m) → L2: engineering-lead (5m) → L3: vp-engineering (10m) |

### Symptoms
- Extreme tail latency affecting worst-case users
- P99 > 5000ms indicates systemic degradation

### Investigation Steps

1. Follow **all steps** from [Alert #1](#1-high-latency-p95)
2. **Urgently check** if multiple incident toggles are active:
   ```bash
   curl -s http://localhost:8000/health
   ```
3. **Check if error rate is also elevated** — correlated P1 latency + errors means cascading failure

### Mitigation
```bash
# Emergency: Disable all incident toggles
for incident in rag_slow tool_fail cost_spike; do
  curl -s -X POST http://localhost:8000/incidents/$incident/disable
done
```

---

## 3. High Error Rate

| Field | Value |
|-------|-------|
| **Alert name** | `high_error_rate` |
| **Severity** | P1 — Critical |
| **Condition** | `error_rate_pct > 2%` sustained for 5 minutes |
| **SLO target** | Error rate < 2%, 99.0% of rolling 28d window |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (pagerduty, 0m) → L2: engineering-lead (5m) → L3: vp-engineering (15m) |

### Symptoms
- Users receive 500 errors from `/chat` endpoint
- Error count increasing in `/metrics` → `error_breakdown`

### Investigation Steps

1. **Identify error types:**
   ```bash
   curl -s http://localhost:8000/metrics | python3 -c "
   import sys, json
   m = json.load(sys.stdin)
   total = m['traffic']
   errors = m.get('error_breakdown', {})
   total_errors = sum(errors.values())
   pct = (total_errors / total * 100) if total else 0
   print(f'Error rate: {pct:.1f}% ({total_errors}/{total})')
   for k, v in errors.items():
       print(f'  {k}: {v}')
   "
   ```

2. **Check if `tool_fail` incident is active:**
   ```bash
   curl -s http://localhost:8000/health | python3 -m json.tool
   ```

3. **Inspect failed traces** in Langfuse (filter by `level=ERROR`)

4. **Review error logs:**
   ```bash
   grep '"level":"error"' data/logs.jsonl | tail -10 | python3 -m json.tool
   ```

### Root Cause Analysis

| Likely Cause | Evidence | Fix |
|---|---|---|
| `tool_fail` incident active | `/health` shows `tool_fail: true` | Disable toggle |
| Schema validation error | `ValidationError` in logs | Fix request schema |
| LLM timeout | `TimeoutError` in logs | Retry with fallback |

### Mitigation
```bash
curl -s -X POST http://localhost:8000/incidents/tool_fail/disable
python3 scripts/load_test.py
```

---

## 4. Cost Budget Spike

| Field | Value |
|-------|-------|
| **Alert name** | `cost_budget_spike` |
| **Severity** | P2 — Warning |
| **Condition** | `hourly_cost_usd > 2x baseline` sustained for 15 minutes |
| **SLO target** | Daily cost < $2.50 |
| **Owner** | finops-owner |
| **Escalation** | L1: finops-owner (slack, 0m) → L2: finops-lead (pagerduty, 30m) |

### Symptoms
- Total cost on `/metrics` rising faster than expected
- Token counts significantly higher than normal

### Investigation Steps

1. **Check current cost:**
   ```bash
   curl -s http://localhost:8000/metrics | python3 -c "
   import sys, json; m = json.load(sys.stdin)
   print(f'Total cost: \${m[\"total_cost_usd\"]}')
   print(f'Avg cost/req: \${m[\"avg_cost_usd\"]}')
   print(f'Tokens in: {m[\"tokens_in_total\"]} | out: {m[\"tokens_out_total\"]}')
   "
   ```

2. **Check `cost_spike` incident:**
   ```bash
   curl -s http://localhost:8000/health | python3 -m json.tool
   ```

3. **Analyze cost by feature** in Langfuse — group traces by tag `feature`

### Root Cause Analysis

| Likely Cause | Evidence | Fix |
|---|---|---|
| `cost_spike` incident active | `/health` shows `cost_spike: true` | Disable toggle |
| Long prompts | `tokens_in` > 2000 per request | Shorten system prompt |
| Verbose responses | `tokens_out` > 1000 per request | Add `max_tokens` limit |

### Mitigation
```bash
curl -s -X POST http://localhost:8000/incidents/cost_spike/disable
python3 scripts/load_test.py
```

---

## 5. Quality Score Drop

| Field | Value |
|-------|-------|
| **Alert name** | `quality_score_drop` |
| **Severity** | P2 — Warning |
| **Condition** | `quality_avg < 0.75` sustained for 10 minutes |
| **SLO target** | Quality avg ≥ 0.75, 95% of evaluation windows |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (slack, 0m) → L2: ml-lead (slack, 30m) |

### Symptoms
- Average quality score on `/metrics` is below 0.75

### Investigation Steps

1. **Check current quality:**
   ```bash
   curl -s http://localhost:8000/metrics | python3 -c "
   import sys, json; m=json.load(sys.stdin)
   print(f'Quality avg: {m[\"quality_avg\"]}')
   "
   ```

2. **Correlate with RAG retrieval** — low quality often means retrieval failed
3. **Review low-quality traces** in Langfuse — filter by `quality_score < 0.75`

### Mitigation
- Ensure RAG retrieval is functioning (disable `rag_slow` if active)
- Verify the document corpus contains relevant content

---

## 6. Error Budget Fast Burn

| Field | Value |
|-------|-------|
| **Alert name** | `error_budget_fast_burn` |
| **Severity** | P1 — Critical |
| **Condition** | `burn_rate > 14.4x` sustained for 1 hour |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (pagerduty, 0m) → L2: engineering-lead (5m) → L3: vp-engineering (10m) |

### Symptoms
- Multiple SLIs are breaching simultaneously
- Error budget will be exhausted within ~2 days at current rate

### Investigation Steps

1. **Check SLO compliance + error budget:**
   ```bash
   curl -s http://localhost:8000/slo | python3 -m json.tool
   curl -s http://localhost:8000/slo/budget | python3 -m json.tool
   ```

2. **Run full alert evaluation:**
   ```bash
   python3 scripts/check_alerts.py
   ```

### Mitigation
```bash
# Emergency recovery
for incident in rag_slow tool_fail cost_spike; do
  curl -s -X POST http://localhost:8000/incidents/$incident/disable
done
python3 scripts/load_test.py
python3 scripts/check_alerts.py
```

---

## 7. Availability Drop

| Field | Value |
|-------|-------|
| **Alert name** | `availability_drop` |
| **Severity** | P1 — Critical |
| **Condition** | `availability_pct < 99%` sustained for 3 minutes |
| **SLO target** | Availability ≥ 99%, 99.9% of rolling 28d window |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (pagerduty, 0m) → L2: engineering-lead (5m) → L3: vp-engineering (10m) |

### Symptoms
- More than 1% of requests returning 5xx errors
- Correlated with high error rate and/or throughput drop

### Investigation Steps

1. **Check current availability:**
   ```bash
   curl -s "http://localhost:8000/slo?sli=availability_pct" | python3 -m json.tool
   ```

2. **Compare availability with error rate:**
   ```bash
   curl -s http://localhost:8000/metrics | python3 -c "
   import sys, json; m = json.load(sys.stdin)
   traffic = m['traffic']
   errors = sum(m.get('error_breakdown', {}).values())
   avail = ((traffic - errors) / traffic * 100) if traffic else 100
   print(f'Availability: {avail:.2f}% ({traffic - errors}/{traffic} successful)')
   "
   ```

3. **Check all incident toggles:**
   ```bash
   curl -s http://localhost:8000/health | python3 -m json.tool
   ```

### Root Cause Analysis

| Likely Cause | Evidence | Fix |
|---|---|---|
| `tool_fail` incident active | `/health` shows `tool_fail: true` | Disable toggle |
| Multiple incidents stacked | Multiple toggles active | Disable all toggles |

### Mitigation
```bash
for incident in rag_slow tool_fail cost_spike; do
  curl -s -X POST http://localhost:8000/incidents/$incident/disable
done
python3 scripts/load_test.py
```

### Post-Incident
- P1 availability drops **require a post-mortem**

---

## 8. Throughput Drop

| Field | Value |
|-------|-------|
| **Alert name** | `throughput_drop` |
| **Severity** | P2 — Warning |
| **Condition** | `throughput_rps < 1.0` sustained for 5 minutes |
| **SLO target** | Throughput ≥ 1.0 rps, 95% of evaluation windows |
| **Owner** | team-oncall |
| **Escalation** | L1: oncall-engineer (slack, 0m) → L2: platform-lead (pagerduty, 15m) |

### Symptoms
- Service processing requests slower than baseline
- Often preceded by latency increase

### Investigation Steps

1. **Check current throughput:**
   ```bash
   curl -s "http://localhost:8000/slo?sli=throughput_rps" | python3 -m json.tool
   ```

2. **Check overall metrics:**
   ```bash
   curl -s http://localhost:8000/metrics | python3 -c "
   import sys, json; m = json.load(sys.stdin)
   print(f'Traffic: {m[\"traffic\"]} requests')
   print(f'P95 latency: {m[\"latency_p95\"]}ms')
   "
   ```

3. **Check if latency is also elevated** — high latency reduces throughput

### Root Cause Analysis

| Likely Cause | Evidence | Fix |
|---|---|---|
| High latency causing backpressure | P95/P99 elevated | Fix underlying latency issue |
| `rag_slow` incident active | `/health` shows `rag_slow: true` | Disable toggle |

### Mitigation
```bash
curl -s -X POST http://localhost:8000/incidents/rag_slow/disable
python3 scripts/load_test.py
```

---

## Appendix: Alert Quick Reference

| Alert | Metric | Threshold | Severity | Type |
|---|---|---|---|---|
| `high_latency_p95` | latency_p95 | > 3000ms | P2 | symptom |
| `critical_latency_p99` | latency_p99 | > 5000ms | P1 | symptom |
| `high_error_rate` | error_rate_pct | > 2% | P1 | symptom |
| `cost_budget_spike` | hourly_cost_usd | > 2x baseline | P2 | budget |
| `quality_score_drop` | quality_avg | < 0.75 | P2 | symptom |
| `error_budget_fast_burn` | burn_rate | > 14.4x | P1 | budget |
| `availability_drop` | availability_pct | < 99% | P1 | symptom |
| `throughput_drop` | throughput_rps | < 1.0 rps | P2 | cause |

## Appendix: Incident Toggle Reference

| Toggle | Effect | Related Alerts |
|---|---|---|
| `rag_slow` | Increases RAG retrieval latency | `high_latency_p95`, `critical_latency_p99`, `throughput_drop` |
| `tool_fail` | Causes tool/function errors | `high_error_rate`, `availability_drop` |
| `cost_spike` | Increases token usage and cost | `cost_budget_spike` |

## Appendix: API Endpoints for SLO & Alerts

| Endpoint | Method | Description |
|---|---|---|
| `/slo` | GET | Full SLO compliance (supports `?sli=` and `?category=` filters) |
| `/slo/budget` | GET | Error budget summary |
| `/alerts` | GET | Evaluate all alert rules against current metrics |
| `/metrics` | GET | Raw metrics snapshot |
| `/health` | GET | Service health + incident toggles |
