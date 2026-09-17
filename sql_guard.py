"""
sql_guard.py -- deterministic SELECT-only SQL safety enforcement.

Sits between generate_sql and execute_sql. Enforces safety with code, not
trust: no LLM call, just a hard allow/deny from string and regex checks.
"""

import re
from dataclasses import dataclass

# Comment stripping runs FIRST, so a banned keyword can't hide in a comment.
_LINE_COMMENT = re.compile(r"--.*?(\n|$)")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return sql


BANNED_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "grant", "revoke", "attach", "detach", "pragma", "vacuum",
}


def _first_statement_keyword(sql: str) -> str:
    match = re.match(r"\s*([a-zA-Z]+)", sql)
    return match.group(1).lower() if match else ""


def _has_multiple_statements(sql: str) -> bool:
    # "SELECT 1; DROP TABLE customers" -- a trailing semicolon alone is fine.
    stripped = sql.strip().rstrip(";")
    return ";" in stripped


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""


def check_sql(raw_sql: str) -> GuardResult:
    cleaned = _strip_comments(raw_sql).strip()

    if not cleaned:
        return GuardResult(False, "empty query")

    first_word = _first_statement_keyword(cleaned)
    if first_word not in {"select", "with"}:  # allow CTEs via WITH ... SELECT
        return GuardResult(False, f"query must start with SELECT, got '{first_word}'")

    if _has_multiple_statements(cleaned):
        return GuardResult(False, "multiple statements are not allowed")

    lowered = cleaned.lower()
    for keyword in BANNED_KEYWORDS:
        # \b word boundaries: don't reject a column named created_at
        if re.search(rf"\b{keyword}\b", lowered):
            return GuardResult(False, f"banned keyword detected: '{keyword}'")

    return GuardResult(True)


def sql_guard_node(state: dict) -> dict:
    """Node shape matching what agent.py's LangGraph calls."""
    result = check_sql(state["sql_query"])
    state["guard_allowed"] = result.allowed
    if not result.allowed:
        state["warnings"] = state.get("warnings", []) + [f"Blocked: {result.reason}"]
    return state


if __name__ == "__main__":
    tests = [
        "SELECT * FROM customers",
        "SELECT * FROM customers; DROP TABLE customers",
        "SELECT * FROM x -- ; DROP TABLE y",
        "UPDATE customers SET tier='Gold'",
        "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
    ]
    for sql in tests:
        print(check_sql(sql), "|", sql)
