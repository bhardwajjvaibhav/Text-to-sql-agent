"""
token_tracker.py -- per-query token usage and cost, with prompt-cache savings.

Backed by a small SQLite log so /cost/report aggregates across the life of
the server, not just the current process.
"""

import sqlite3
from datetime import datetime

# USD per token (input / output). Cache reads bill at a fraction of input.
# Update these if you switch models.
PRICE_PER_INPUT_TOKEN = 1.00 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 5.00 / 1_000_000
PRICE_PER_CACHE_READ_TOKEN = 0.10 / 1_000_000

LOG_DB = "token_log.db"


def _get_conn():
    conn = sqlite3.connect(LOG_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            llm_calls INTEGER,
            cost_usd REAL,
            cache_hit INTEGER,
            created_at TEXT
        )
    """)
    return conn


def compute_cost(input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    billable_input = max(input_tokens - cache_read_tokens, 0)
    cost = (
        billable_input * PRICE_PER_INPUT_TOKEN
        + cache_read_tokens * PRICE_PER_CACHE_READ_TOKEN
        + output_tokens * PRICE_PER_OUTPUT_TOKEN
    )
    return round(cost, 8)


def log_query(question: str, token_usage: dict, cache_hit: bool = False) -> float:
    input_tokens = token_usage.get("input_tokens", 0)
    output_tokens = token_usage.get("output_tokens", 0)
    cache_read_tokens = token_usage.get("cache_read_tokens", 0)
    calls = token_usage.get("calls", 0)

    # a cache hit costs nothing, but is still logged so it counts in hit rate
    cost = 0.0 if cache_hit else compute_cost(input_tokens, output_tokens, cache_read_tokens)

    conn = _get_conn()
    conn.execute(
        """INSERT INTO token_log
           (question, input_tokens, output_tokens, cache_read_tokens, llm_calls,
            cost_usd, cache_hit, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (question, input_tokens, output_tokens, cache_read_tokens, calls,
         cost, int(cache_hit), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return cost


def cost_report() -> dict:
    conn = _get_conn()
    total_queries, total_cost, cache_hits = conn.execute("""
        SELECT COUNT(*),
               COALESCE(SUM(cost_usd), 0),
               COALESCE(SUM(cache_hit), 0)
        FROM token_log
    """).fetchone()
    conn.close()

    if total_queries == 0:
        return {
            "total_queries": 0,
            "total_cost_usd": 0.0,
            "avg_cost_per_query_usd": 0.0,
            "cache_hit_rate_pct": 0.0,
            "total_cache_savings_usd": 0.0,
        }

    non_cached = total_queries - cache_hits
    avg_non_cached_cost = (total_cost / non_cached) if non_cached else 0.0
    # savings is estimated: a hit costs nothing, so we use the average cost
    # of non-cached queries as the stand-in for "what this would have cost"
    estimated_savings = avg_non_cached_cost * cache_hits

    return {
        "total_queries": total_queries,
        "total_cost_usd": round(total_cost, 6),
        "avg_cost_per_query_usd": round(total_cost / total_queries, 6),
        "cache_hit_rate_pct": round(100 * cache_hits / total_queries, 1),
        "total_cache_savings_usd": round(estimated_savings, 6),
    }


if __name__ == "__main__":
    log_query("test question", {"input_tokens": 1200, "output_tokens": 300, "calls": 2})
    print(cost_report())
