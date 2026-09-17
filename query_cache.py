"""
query_cache.py -- semantic fingerprint cache.

Not an exact-string cache: two differently-worded questions that mean the
same thing collide, because tokens are sorted before hashing.
"""

import hashlib
import json
import re
import sqlite3
from datetime import datetime

STOPWORDS = {
    "what", "the", "a", "an", "of", "for", "in", "on", "at", "by", "to",
    "and", "or", "how", "many", "much", "which", "who", "show", "me", "us",
    "please", "give", "list", "tell", "get", "find", "there", "that", "it",
    # auxiliary/linking verbs -- "had" vs "has" must not split a fingerprint
    "is", "are", "was", "were", "be", "been", "am",
    "do", "does", "did", "has", "have", "had",
    "can", "could", "will", "would", "should",
}

CACHE_DB = "query_cache.db"


def _stem(word: str) -> str:
    """Crude suffix stripping so 'categories'/'category' and
    'customers'/'customer' collapse to the same token."""
    for suffix, replacement in (("ies", "y"), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + replacement
    return word


def fingerprint(question: str) -> str:
    """Lowercase, drop punctuation and stopwords, stem, sort tokens, hash.

    Sorting is the trick -- word order and phrasing stop mattering.
    """
    words = re.findall(r"[a-z0-9]+", question.lower())
    meaningful = sorted(_stem(w) for w in words if w not in STOPWORDS)
    return hashlib.sha256(" ".join(meaningful).encode()).hexdigest()


def _get_conn():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            fingerprint TEXT PRIMARY KEY,
            question TEXT,
            sql_query TEXT,
            summary TEXT,
            result_json TEXT,
            db_name TEXT,
            hit_count INTEGER DEFAULT 0,
            created_at TEXT,
            last_hit_at TEXT
        )
    """)
    return conn


def cache_lookup(question: str, db_name: str = "business"):
    fp = fingerprint(question)
    conn = _get_conn()
    row = conn.execute(
        "SELECT sql_query, summary, result_json, hit_count FROM cache "
        "WHERE fingerprint = ? AND db_name = ?",
        (fp, db_name),
    ).fetchone()

    if row is None:
        conn.close()
        return None

    sql_query, summary, result_json, hit_count = row
    conn.execute(
        "UPDATE cache SET hit_count = ?, last_hit_at = ? "
        "WHERE fingerprint = ? AND db_name = ?",
        (hit_count + 1, datetime.now().isoformat(), fp, db_name),
    )
    conn.commit()
    conn.close()

    return {
        "sql_query": sql_query,
        "summary": summary,
        "result": json.loads(result_json),
        "cache_hit": True,
    }


def cache_store(question: str, sql_query: str, summary: str,
                result: list, db_name: str = "business") -> None:
    """Only called after a successful run -- never cache a blocked or failed query."""
    fp = fingerprint(question)
    now = datetime.now().isoformat()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO cache
           (fingerprint, question, sql_query, summary, result_json, db_name,
            hit_count, created_at, last_hit_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
           ON CONFLICT(fingerprint) DO UPDATE SET
               sql_query=excluded.sql_query,
               summary=excluded.summary,
               result_json=excluded.result_json,
               last_hit_at=excluded.last_hit_at""",
        (fp, question, sql_query, summary, json.dumps(result, default=str), db_name, now, now),
    )
    conn.commit()
    conn.close()


def cache_stats() -> dict:
    conn = _get_conn()
    total_entries, total_hits = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(hit_count), 0) FROM cache"
    ).fetchone()
    conn.close()
    return {
        "total_cached_queries": total_entries,
        "total_cache_hits": total_hits,
    }


def cache_flush() -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM cache")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    a = fingerprint("Which product category had the highest revenue last quarter?")
    b = fingerprint("what product category has highest revenue in the last quarter")
    print(a)
    print(b)
    print("match:", a == b)
