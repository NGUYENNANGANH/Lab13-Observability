# Day 13 Observability Lab Report

> **Instruction**: Fill in all sections below. This report is designed to be parsed by an automated grading assistant. Ensure all tags (e.g., `[GROUP_NAME]`) are preserved.

## 1. Team Metadata
- [GROUP_NAME]: 
- [REPO_URL]: 
- [MEMBERS]:
  - Member A: [Name] | Role: Logging & PII
  - Member B: [Name] | Role: Tracing & Enrichment
  - Member C: Dương Phương Thảo | Role: SLO & Alerts
  - Member D: [Name] | Role: Load Test & Dashboard
  - Member E: [Name] | Role: Demo & Report

---

## 2. Group Performance (Auto-Verified)
- [VALIDATE_LOGS_FINAL_SCORE]: /100
- [TOTAL_TRACES_COUNT]: 
- [PII_LEAKS_FOUND]: 

---

## 3. Technical Evidence (Group)

### 3.1 Logging & Tracing
- [EVIDENCE_CORRELATION_ID_SCREENSHOT]: [Path to image]
- [EVIDENCE_PII_REDACTION_SCREENSHOT]: [Path to image]
- [EVIDENCE_TRACE_WATERFALL_SCREENSHOT]: [Path to image]
- [TRACE_WATERFALL_EXPLANATION]: (Briefly explain one interesting span in your trace)

### 3.2 Dashboard & SLOs

- [DASHBOARD_6_PANELS_SCREENSHOT]: [Path to image]

- [SLO_TABLE]:

| SLI | Target | Window | Current Value | Status |
|---|---:|---|---:|---|
| Latency P95 | < 3000 ms | 28d | _(run `scripts/check_slo.py`)_ | |
| Error Rate | < 2% | 28d | _(run `scripts/check_slo.py`)_ | |
| Availability | ≥ 99% | 28d | _(run `scripts/check_slo.py`)_ | |
| Daily Cost | < $2.50 USD | 1d | _(run `scripts/check_slo.py`)_ | |
| Quality Score | ≥ 0.75 | 1h rolling | _(run `scripts/check_slo.py`)_ | |
| Throughput | ≥ 1.0 rps | 28d | _(run `scripts/check_slo.py`)_ | |

> **Note (Member C):** The original template only had 3 SLIs. I expanded this to 6 SLIs covering all four USE/RED categories (performance, reliability, cost, quality). Each SLI is defined in `config/slo.yaml` with explicit objectives, measurement formulas, categories, owners, and descriptive notes. Real-time compliance can be verified via `GET /slo` or by running `python scripts/check_slo.py`.

#### SLO Design Rationale (Member C — Dương Phương Thảo)

1. **Latency P95 (≤ 3000ms, target 99.5%)** — Chosen because P95 captures the "worst realistic experience" for the majority of users while ignoring extreme outliers. The 3000ms threshold accounts for RAG retrieval + LLM generation overhead.

2. **Error Rate (≤ 2%, target 99.0%)** — Computed as `count(status >= 500) / count(all_requests) * 100`. The 2% threshold provides a small error budget (~7.2 hours/month of acceptable downtime at target).

3. **Availability (≥ 99%, target 99.9%)** — Inverse of error rate, expressed as the percentage of non-5xx responses. Provides a direct user-facing reliability metric. Below 99% triggers immediate P1 incident response.

4. **Daily Cost (≤ $2.50/day, target 100%)** — Based on token pricing ($3/M input, $15/M output). This is a hard cap (target 100%) because cost overruns have direct financial impact. The `cost_spike` incident toggle simulates token-leak scenarios.

5. **Quality Score (≥ 0.75, target 95%)** — A heuristic composite score (0.0–1.0) based on document relevance, answer length, and keyword overlap. Included because latency/availability alone don't capture whether the agent is giving *useful* answers.

6. **Throughput (≥ 1.0 rps, target 95%)** — Measures baseline capacity. Drops below threshold often correlate with high latency (backpressure) or resource exhaustion. Computed as `count(requests) / elapsed_seconds`.

#### Error Budget Model (Member C)

I implemented a three-tier burn-rate model in `config/slo.yaml`:

| Tier | Burn Rate | Window | Severity | Budget Exhaustion | Action |
|---|---:|---|---|---|---|
| Fast Burn | 14.4× | 1h | P1 | ~2 days | Page oncall immediately |
| Slow Burn | 6.0× | 6h | P2 | ~5 days | Create incident ticket |
| Gradual Burn | 3.0× | 24h | P3 | ~10 days | Review in next standup |

The formula: `budget_total = 1 - (target / 100)`. For example, latency_p95 has target 99.5%, giving an error budget of 0.5% (~3.6 hours/month). The burn rate measures how fast this budget is being consumed relative to a uniform distribution.

This model is evaluated in real-time by `app/slo.py::_compute_error_budget()` and exposed via `GET /slo/budget`.

### 3.3 Alerts & Runbook

- [ALERT_RULES_SCREENSHOT]: [Path to image]
- [SAMPLE_RUNBOOK_LINK]: [docs/alerts.md](../docs/alerts.md)

#### Alert System Overview (Member C — Dương Phương Thảo)

I designed and implemented **8 production-grade alert rules** in `config/alert_rules.yaml`, categorized into three types:

| # | Alert Name | Metric | Threshold | Severity | Type | Runbook |
|---|---|---|---|---|---|---|
| 1 | `high_latency_p95` | latency_p95 | > 3000ms for 5m | P2 | symptom-based | [§1](../docs/alerts.md#1-high-latency-p95) |
| 2 | `critical_latency_p99` | latency_p99 | > 5000ms for 2m | P1 | symptom-based | [§2](../docs/alerts.md#2-critical-latency-p99) |
| 3 | `high_error_rate` | error_rate_pct | > 2% for 5m | P1 | symptom-based | [§3](../docs/alerts.md#3-high-error-rate) |
| 4 | `cost_budget_spike` | hourly_cost_usd | > 2× baseline for 15m | P2 | budget-based | [§4](../docs/alerts.md#4-cost-budget-spike) |
| 5 | `quality_score_drop` | quality_avg | < 0.75 for 10m | P2 | symptom-based | [§5](../docs/alerts.md#5-quality-score-drop) |
| 6 | `error_budget_fast_burn` | burn_rate | > 14.4× for 1h | P1 | budget-based | [§6](../docs/alerts.md#6-error-budget-fast-burn) |
| 7 | `availability_drop` | availability_pct | < 99% for 3m | P1 | symptom-based | [§7](../docs/alerts.md#7-availability-drop) |
| 8 | `throughput_drop` | throughput_rps | < 1.0 rps for 5m | P2 | cause-based | [§8](../docs/alerts.md#8-throughput-drop) |

**Key design decisions:**

- **Symptom-based vs. cause-based separation**: Alerts 1–3, 5, 7 fire on user-visible symptoms; Alert 8 fires on infrastructure causes; Alerts 4, 6 fire on budget exhaustion. This avoids alert noise — operators see *what's broken* before investigating *why*.

- **Three-level escalation chains**: Each P1 alert escalates through `oncall-engineer → engineering-lead → vp-engineering` with time-based triggers (0m → 5m → 10m). P2 alerts are slack-first with pagerduty escalation.

- **Alert dependencies/suppression**: `critical_latency_p99` suppresses `high_latency_p95` (avoids double-paging for the same issue). `availability_drop` suppresses `high_error_rate` and `throughput_drop` (availability is the root symptom).

- **Escalation policies** with SLA contracts:
  - P1: 5min response, 30min resolution, auto-page, post-mortem required
  - P2: 15min response, 2h resolution, no auto-page
  - P3: 4h response, 24h resolution

- **Global settings**: 30s evaluation interval, 5min cooldown (prevents alert flapping), 15min dedup window, auto-resolve after 10min of recovery.

#### Runbook Details (Member C)

I authored a comprehensive 457-line runbook in [`docs/alerts.md`](../docs/alerts.md) covering:

- **General triage workflow**: `ALERT → /metrics → logs → traces → /health → mitigation`
- **Per-alert runbook** (8 sections): Each with a structured table (severity, condition, SLO target, owner, escalation), symptoms, step-by-step investigation commands (copy-pasteable `curl` + `jq` one-liners), root cause analysis tables, and mitigation commands.
- **Escalation matrix**: Response/resolution SLAs by severity level
- **Appendices**: Alert quick reference table, incident toggle cross-reference, API endpoint reference

---

## 4. Incident Response (Group)
- [SCENARIO_NAME]: (e.g., rag_slow)
- [SYMPTOMS_OBSERVED]: 
- [ROOT_CAUSE_PROVED_BY]: (List specific Trace ID or Log Line)
- [FIX_ACTION]: 
- [PREVENTIVE_MEASURE]: 

---

## 5. Individual Contributions & Evidence

### [MEMBER_A_NAME]
- [TASKS_COMPLETED]: 
- [EVIDENCE_LINK]: (Link to specific commit or PR)

### [MEMBER_B_NAME]
- [TASKS_COMPLETED]: 
- [EVIDENCE_LINK]: 

### Dương Phương Thảo (Member C — SLO & Alerts)

- [TASKS_COMPLETED]:

  1. **SLO Configuration (`config/slo.yaml`)** — Expanded the initial 4-SLI stub to a production-grade 6-SLI configuration with full metadata (description, objective, target, unit, measurement formula, owner, category, and explanatory notes). Added availability and throughput SLIs. Defined a three-tier error budget burn-rate model (fast/slow/gradual) with severity mapping and action protocols. Added dashboard integration settings.

  2. **Alert Rules (`config/alert_rules.yaml`)** — Designed and authored 8 alert rules from scratch (original stub had 0 functional rules). Each alert includes: severity classification (P1/P2/P3), threshold with sustained duration, metric binding, alert type (symptom/cause/budget-based), owner assignment, runbook link, automated actions, and multi-level escalation chains. Added 3 escalation policies with SLA contracts, 4 notification channels, alert dependency/suppression rules, and global settings (evaluation interval, cooldown, dedup, auto-resolve).

  3. **SLO Evaluator (`app/slo.py`)** — Implemented a 191-line Python module that loads SLO config from YAML and evaluates real-time metrics against each SLI. Key functions:
     - `_compute_error_rate()`: Calculates 5xx error percentage from metrics snapshot
     - `_compute_availability()`: Inverse of error rate (non-5xx / total)
     - `_compute_throughput()`: Requests per second since app startup
     - `_compute_error_budget()`: Full error budget calculation with consumed/remaining percentages and burn rate, handling both `<=` and `>=` comparison operators
     - `evaluate_slo_compliance()`: Main evaluator with optional SLI/category filtering, three-state status logic (healthy/at_risk/breaching), and overall compliance summary

  4. **API Endpoints in `app/main.py`** — Added three new endpoints to expose SLO/alert data:
     - `GET /slo` — Full SLO compliance report with optional `?sli=` and `?category=` query filters
     - `GET /slo/budget` — Error budget summary (avg remaining, min remaining, max burn rate, burn status)
     - `GET /alerts` — Real-time alert evaluation against all 8 rules, returns firing/OK status per rule

  5. **Alert Checker Script (`scripts/check_alerts.py`)** — 218-line CLI tool that fetches live metrics from the running app and evaluates all 8 alert rules. Features: colored terminal output by severity, incident toggle display, escalation chain details for firing alerts, policy SLA display, JSON export mode (`--json`), configurable base URL.

  6. **SLO Checker Script (`scripts/check_slo.py`)** — 192-line CLI tool that fetches live metrics and checks SLO compliance. Features: per-SLI pass/breach evaluation, error budget summary with traffic-light icons (🟢/🟡/🔴), auto-generated SLO table for `blueprint-template.md`, SLI and category filtering, JSON export mode, configurable base URL.

  7. **Alert Runbook (`docs/alerts.md`)** — 457-line comprehensive runbook with: general triage workflow, per-alert investigation procedures (8 sections), copy-pasteable debugging commands, root cause analysis tables, mitigation steps, escalation matrix, and three appendices (alert reference, incident toggles, API endpoints).

- [EVIDENCE_LINK]:
  - Primary commit: `9c4f430` — "feat: implement SLO compliance and alert evaluation endpoints with supporting logic and scripts" (1,561 insertions across 7 files)
  - Files authored/owned:
    - [`config/slo.yaml`](../config/slo.yaml) — 135 lines (100% authored)
    - [`config/alert_rules.yaml`](../config/alert_rules.yaml) — 294 lines (100% authored)
    - [`app/slo.py`](../app/slo.py) — 191 lines (100% authored)
    - [`app/main.py`](../app/main.py) — Added `/slo`, `/slo/budget`, `/alerts` endpoints
    - [`scripts/check_alerts.py`](../scripts/check_alerts.py) — 218 lines (100% authored)
    - [`scripts/check_slo.py`](../scripts/check_slo.py) — 192 lines (100% authored)
    - [`docs/alerts.md`](../docs/alerts.md) — 457 lines (100% authored)

- [TECHNICAL_DEPTH]:

  **How P95 Latency Is Computed:**
  The `metrics.py::percentile()` function uses a rank-based algorithm: `idx = max(0, min(len-1, round((p/100) * len + 0.5) - 1))`. For P95 with 100 samples, this selects the 95th sorted value. The SLO evaluator in `slo.py` then compares this value against the 3000ms objective.

  **How Error Budget Burn Rate Is Calculated:**
  In `slo.py::_compute_error_budget()`:
  - `budget_total = 100.0 - target` (e.g., 0.5% for latency_p95 with target 99.5%)
  - For `<=` operators (latency, error rate, cost): if `current > objective`, overshoot ratio = `(current - objective) / objective`, consumed = `min(overshoot / (budget_total/100), 1.0)`
  - For `>=` operators (availability, quality, throughput): if `current < objective`, undershoot ratio = `(objective - current) / objective`, consumed similarly
  - `burn_rate = consumed_ratio * 100 / budget_total` — a multiplier indicating how fast the budget depletes relative to the window

  **Alert Evaluation Logic:**
  In `scripts/check_alerts.py::evaluate_alert()`, each alert maps to a metric key with a comparison direction (`gt` or `lt`). The evaluator fetches live metrics from `/metrics`, computes derived values (error rate, availability), and checks `current_value > threshold` (for `gt`) or `current_value < threshold` (for `lt`). Results include firing status, escalation chain, and runbook link.

  **Three-State SLO Status Logic:**
  In `slo.py`, compliance status uses three states rather than binary:
  - `healthy` (✅): SLI meets objective
  - `at_risk` (⚠️): SLI breaching but >30% error budget remaining
  - `breaching` (❌): SLI breaching with ≤30% error budget remaining
  This provides early warning before full budget exhaustion.

### [MEMBER_D_NAME]
- [TASKS_COMPLETED]: 
- [EVIDENCE_LINK]: 

### [MEMBER_E_NAME]
- [TASKS_COMPLETED]: 
- [EVIDENCE_LINK]: 

---

## 6. Bonus Items (Optional)
- [BONUS_COST_OPTIMIZATION]: (Description + Evidence)
- [BONUS_AUDIT_LOGS]: (Description + Evidence)
- [BONUS_CUSTOM_METRIC]: (Description + Evidence)
