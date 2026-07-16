"""CSV and Markdown exports for a completed audit (B6, invariant 7).

Both exports RENDER persisted evidence only — the per-execution ``AuditTask``
rows and the audit ``summary`` aggregate. They never re-score and never call a
provider. The Markdown export leads with an explicit methodology block so the
evidence can be dropped into a client deck without over-claiming.

Adapted from the reference ``ai_visibility/exports.py`` (run/execution ->
audit/task; summary shape unchanged).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.models.audit import Audit, AuditTask

_CSV_COLUMNS = [
    "audit_id",
    "prompt_index",
    "prompt_text",
    "repetition",
    "randomized_position",
    "logical_engine",
    "transport_model",
    "status",
    "search_used",
    "search_query_count",
    "search_queries",
    "prompt_class",
    "prompt_contains_brand",
    "prompt_contains_competitor",
    "brand_mentioned",
    "brand_injected_in_search",
    "owned_domain_cited",
    "owned_citation_count",
    "unintended_domain_cited",
    "citation_count",
    "citation_domains",
    "competitors_mentioned",
    "competitor_domains_cited",
    "fanout_features",
    "latency_ms",
    "error_code",
]


def _join(values: Any) -> str:
    if not values:
        return ""
    return json.dumps(values, ensure_ascii=False)


def audit_to_csv(audit: Audit, tasks: list[AuditTask]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for task in sorted(tasks, key=lambda x: (x.prompt_index, x.repetition)):
        score = task.score or {}
        queries = [ev.get("query") for ev in (task.search_events or [])]
        citation_domains = [
            c.get("domain") for c in (task.citations or []) if c.get("domain")
        ]
        writer.writerow(
            {
                "audit_id": str(audit.id),
                "prompt_index": task.prompt_index,
                "prompt_text": task.prompt_text,
                "repetition": task.repetition,
                "randomized_position": task.randomized_position,
                "logical_engine": task.logical_engine,
                "transport_model": task.transport_model,
                "status": task.status,
                "search_used": task.search_used,
                "search_query_count": score.get("search_query_count", 0),
                "search_queries": _join(queries),
                "prompt_class": score.get("prompt_class", ""),
                "prompt_contains_brand": score.get("prompt_contains_brand", False),
                "prompt_contains_competitor": score.get(
                    "prompt_contains_competitor", False
                ),
                "brand_mentioned": score.get("brand_mentioned", False),
                "brand_injected_in_search": score.get(
                    "brand_injected_in_search", False
                ),
                "owned_domain_cited": score.get("owned_domain_cited", False),
                "owned_citation_count": score.get("owned_citation_count", 0),
                "unintended_domain_cited": score.get("unintended_domain_cited", False),
                "citation_count": score.get("citation_count", 0),
                "citation_domains": _join(citation_domains),
                "competitors_mentioned": _join(score.get("competitors_mentioned")),
                "competitor_domains_cited": _join(
                    score.get("competitor_domains_cited")
                ),
                "fanout_features": _join(score.get("fanout_features")),
                "latency_ms": task.latency_ms,
                "error_code": task.error_code,
            }
        )
    return buffer.getvalue()


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _usd(value: Any) -> str:
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def audit_to_markdown(audit: Audit, tasks: list[AuditTask]) -> str:
    config = audit.configuration or {}
    summary = audit.summary or {}
    brand = config.get("brand_name", "Brand")
    lines = [f"# AI Search Visibility Benchmark — {brand}", ""]
    lines.extend(_methodology_lines(audit, config, summary))
    lines.extend(_headline_lines(summary))
    lines.extend(_competitor_lines(config, summary, brand))
    lines.extend(_per_prompt_lines(summary))
    lines.extend(_domain_lines(summary))
    lines.extend(_failure_lines(tasks))
    lines.extend(_limitation_lines())
    return "\n".join(lines)


def _methodology_lines(audit, config, summary):
    engines = config.get("engines") or []
    lines = ["## Methodology", ""]
    lines.append(
        "- **Engines measured:** "
        + (", ".join(f"`{e}`" for e in engines) if engines else "—")
    )
    lines.append(
        "- **Statelessness:** every prompt is a fresh, independent request. No "
        "account history or chat context influences any answer."
    )
    mode = str(config.get("benchmark_mode") or audit.benchmark_mode or "")
    lines.append(f"- **Benchmark mode:** `{mode}` — {_mode_text(mode)}.")
    if mode and mode != "consumer_like":
        lines.append(
            "- **Localization:** benchmark context supplied country "
            f"`{config.get('country_code', '')}` and language "
            f"`{config.get('language_code', '')}` to the model; it was not "
            "inferred from device or account location."
        )
    lines.extend(_panel_lines(config, summary))
    lines.append(
        f"- **Design:** {audit.requested_count} executions "
        f"({audit.repetitions} repetition(s) per prompt x engine), execution "
        f"order randomized (seed `{audit.random_seed}`)."
    )
    lines.append(
        "- **Citations:** only explicit source citations returned by the API "
        "are counted. Publisher domains prefer resolved/direct URLs and use the "
        "citation title only as fallback; this is not a complete ledger of every "
        "page the model read."
    )
    lines.append(
        f"- **Scoring:** deterministic alias/domain matching "
        f"(`{audit.analyzer_version or 'unversioned'}`). No LLM is used for "
        "headline metrics; sentiment and average position are roadmap and are "
        "not computed."
    )
    lines.extend(
        [
            f"- **Result:** {summary.get('total_completed', 0)} completed, "
            f"{audit.failed_count} failed.",
            "",
        ]
    )
    return lines


def _mode_text(mode):
    return {
        "consumer_like": "exact visible prompt; no system instruction",
        "controlled_localized": "visible prompt plus disclosed market/language context",
        "forced_grounded": (
            "disclosed market/language context plus forced current-web citations"
        ),
    }.get(mode, mode or "—")


def _panel_lines(config, summary):
    classes = summary.get("prompt_class_counts", {})
    class_names = [name for name, count in classes.items() if count]
    if not class_names:
        panel_label = "Prompt classification unavailable until executions complete."
    elif set(class_names) == {"non_branded"}:
        panel_label = "All prompts are unaided/non-branded."
    else:
        panel_label = (
            "Mixed panel: "
            + ", ".join(
                f"{name}={count}" for name, count in sorted(classes.items()) if count
            )
            + "."
        )
    return [
        f"- **Prompt panel:** {panel_label} Brand and competitor data is applied "
        "only during scoring.",
        f"- **Panel fingerprint:** `{config.get('panel_id', 'unavailable')}`; prompt "
        "text hashes are frozen in the audit configuration.",
    ]


def _headline_lines(summary):
    cost = summary.get("cost") or {}
    lines = [
        "## Headline Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Brand mention rate | {_pct(summary.get('brand_mention_rate'))} |",
        f"| Owned-domain citation rate | {_pct(summary.get('owned_citation_rate'))} |",
        "| Mention → owned-citation conversion | "
        f"{_pct(summary.get('mention_to_owned_citation_conversion'))} |",
        f"| Search-use rate | {_pct(summary.get('search_use_rate'))} |",
        "| Avg. search queries / answer | "
        f"{summary.get('avg_queries_per_execution', 0)} |",
        "| Brand injected into search fanout | "
        f"{_pct(summary.get('brand_fanout_injection_rate'))} |",
        "| Unintended-domain citation rate | "
        f"{_pct(summary.get('unintended_domain_citation_rate'))} |",
        "| Paid-list token cost estimate | "
        f"{_usd(cost.get('paid_list_token_estimate_usd'))} |",
        "| Grounding cost if outside free allowance | "
        f"{_usd(cost.get('grounding_cost_if_billable_usd'))} |",
    ]
    if float(cost.get("provider_reported_cost_usd") or 0) > 0:
        lines.append(
            "| Provider-reported cost | "
            f"{_usd(cost.get('provider_reported_cost_usd'))} |"
        )
    lines.extend([f"| Grounded requests | {cost.get('grounded_requests', 0)} |", ""])
    return lines


def _competitor_lines(config, summary, brand):
    competitors = [item.get("name") for item in (config.get("competitors") or [])]
    if not competitors:
        return []
    mention = summary.get("competitor_mention_rate", {})
    citation = summary.get("competitor_citation_rate", {})
    lines = [
        "## Competitor Comparison",
        "",
        "| Competitor | Mention rate | Citation rate |",
        "|---|---|---|",
        f"| **{brand}** | {_pct(summary.get('brand_mention_rate'))} | "
        f"{_pct(summary.get('owned_citation_rate'))} |",
    ]
    lines.extend(
        f"| {name} | {_pct(mention.get(name))} | {_pct(citation.get(name))} |"
        for name in competitors
    )
    lines.append("")
    return lines


def _per_prompt_lines(summary):
    lines = [
        "## Per-Prompt Results (with immediate binary consistency)",
        "",
        "| # | Prompt | Theme | Brand mentioned | Owned cited | "
        "Immediate consistency |",
        "|---|---|---|---|---|---|",
    ]
    for row in summary.get("per_prompt", []):
        reps = row.get("repetitions", 0)
        lines.append(
            f"| {row.get('prompt_index')} | {row.get('prompt_text')} | "
            f"{row.get('theme')} | {row.get('brand_mentioned_count')}/{reps} | "
            f"{row.get('owned_cited_count')}/{reps} | "
            f"{_pct(row.get('mention_stability'))} |"
        )
    lines.append("")
    return lines


def _domain_lines(summary):
    share = summary.get("citation_annotation_share_by_domain", {})
    if not share:
        return []
    lines = [
        "## Top Domains by Inline Citation-Annotation Share",
        "",
        "| Domain | Share of citations |",
        "|---|---|",
    ]
    lines.extend(f"| {domain} | {_pct(value)} |" for domain, value in share.items())
    lines.append("")
    return lines


def _failure_lines(tasks):
    failures = [task for task in tasks if task.status == "failed"]
    if not failures:
        return []
    lines = [
        "## Failed Executions",
        "",
        "| Prompt | Repetition | Engine | Error |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {task.prompt_text} | {task.repetition} | {task.logical_engine} | "
        f"{task.error_code} |"
        for task in failures
    )
    lines.append("")
    return lines


def _limitation_lines():
    return [
        "## Limitations",
        "",
        "- A single grounded API surface is not a proxy for all AI answer engines; "
        "provider APIs and consumer applications retrieve and route differently.",
        "- Grounded answers vary by date, index freshness and location; results are "
        "a point-in-time snapshot. Immediate repetitions measure short-term binary "
        "consistency, not statistical confidence or day-to-day volatility.",
        "- Only explicit citations are scored; a mention without a citation is "
        "recorded as a mention, not a source.",
        "- Sentiment and average position are roadmap metrics and are not computed "
        "at this MVP.",
        "",
    ]
