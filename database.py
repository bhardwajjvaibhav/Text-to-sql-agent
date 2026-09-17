"""
database.py -- engine registry, schema introspection, and query execution.

Two jobs: give everything else an engine to run queries against, and render
the live schema as plain text for the LLM prompt.
"""

from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect, text

# Every database the agent can talk to. Add entries as you seed more DBs.
DATABASES = {
    "business": "sqlite:///business.db",
}

_engines = {}


def get_engine(db_name: str = "business"):
    if db_name not in DATABASES:
        raise ValueError(f"Unknown database '{db_name}'. Options: {list(DATABASES)}")
    if db_name not in _engines:
        _engines[db_name] = create_engine(DATABASES[db_name], echo=False)
    return _engines[db_name]


def get_schema_string(db_name: str = "business", tables: list | None = None) -> str:
    """Render the schema as plain text for the LLM prompt.

    If `tables` is given, only those tables are included -- this is what
    schema_index.py uses to send a trimmed-down schema.
    """
    engine = get_engine(db_name)
    inspector = inspect(engine)
    all_tables = inspector.get_table_names()
    target_tables = tables if tables is not None else all_tables

    lines = []
    for table_name in target_tables:
        if table_name not in all_tables:
            continue
        columns = inspector.get_columns(table_name)
        col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
        lines.append(f"TABLE {table_name}: {col_desc}")

        for fk in inspector.get_foreign_keys(table_name):
            ref_table = fk["referred_table"]
            local_col = fk["constrained_columns"][0]
            ref_col = fk["referred_columns"][0]
            lines.append(f"  FOREIGN KEY {local_col} -> {ref_table}.{ref_col}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TTL cache so we don't re-inspect on every request
# ---------------------------------------------------------------------------
_schema_cache: dict = {}
SCHEMA_CACHE_TTL = timedelta(minutes=30)


def get_cached_schema(db_name: str = "business") -> str:
    now = datetime.now()
    if db_name in _schema_cache:
        schema_str, cached_at = _schema_cache[db_name]
        if now - cached_at < SCHEMA_CACHE_TTL:
            return schema_str
    schema_str = get_schema_string(db_name)
    _schema_cache[db_name] = (schema_str, now)
    return schema_str


def schema_cache_age_seconds(db_name: str = "business"):
    if db_name not in _schema_cache:
        return None
    return round((datetime.now() - _schema_cache[db_name][1]).total_seconds(), 1)


def run_query(sql: str, db_name: str = "business") -> list:
    """Execute a query and return rows as plain dicts (JSON-serializable)."""
    engine = get_engine(db_name)
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]


if __name__ == "__main__":
    print(get_cached_schema())
