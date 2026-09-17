# Text-to-SQL Agent

A LangGraph agent that turns plain-English questions into SQL, runs them
safely against a SQLite database, and answers in plain English.

## Flow

```
cache_check --(hit)--> summarize --> END
    |(miss)
    v
schema_filter -> disambiguate -> generate_sql -> sql_guard --(blocked)--> summarize --> END
                        ^                             |(allowed)
                        |                             v
                        +------(retry <=2)------ execute_sql -> validate_result
                                                     -> summarize -> cache_store -> END
```

| Node | What it does |
|---|---|
| `cache_check` | Semantic fingerprint lookup; a hit skips straight to `summarize` |
| `schema_filter` | Scores tables against the question, keeps the top ~12 |
| `disambiguate` | Claude states the assumption it's making, if any |
| `generate_sql` | Claude writes one SELECT query |
| `sql_guard` | Deterministic SELECT-only check; no LLM involved |
| `execute_sql` | Runs it; DB errors loop back to `generate_sql`, max 2 retries |
| `validate_result` | Claude sanity-checks whether the rows answer the question |
| `summarize` | Turns rows into a plain-English answer |
| `cache_store` | Saves the result so paraphrases hit the cache next time |

## Files

| File | Purpose |
|---|---|
| `seed_db.py` | Creates `business.db` with fake but coherent data |
| `database.py` | Engine registry, schema introspection, query runner |
| `schema_index.py` | Table relevance scoring + cryptic column aliasing |
| `sql_guard.py` | SELECT-only safety enforcement |
| `query_cache.py` | Semantic fingerprint cache |
| `token_tracker.py` | Token usage, cost, and cache savings |
| `agent.py` | The LangGraph graph |
| `api.py` | FastAPI HTTP layer |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then put your real ANTHROPIC_API_KEY in it

python seed_db.py           # creates business.db
python agent.py             # one-off test run
uvicorn api:app --reload    # http://127.0.0.1:8000/docs
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/query` | Ask a question |
| GET | `/health` | Status + schema cache age |
| GET | `/schema` | Current schema as text |
| GET | `/cache/stats` | Cached queries and hit count |
| POST | `/cache/flush` | Clear the cache |
| GET | `/cost/report` | Total cost, avg cost, hit rate, savings |

Example:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which product category had the highest revenue?"}'
```

## Notes

- Pricing constants in `token_tracker.py` are per-million-token rates; update
  them if you switch models.
- `MODEL_NAME` and `MAX_SQL_RETRIES` are at the top of `agent.py`.
- Cache savings in `/cost/report` are estimated from the average cost of
  non-cached queries, since a cache hit has no real cost to compare against.
