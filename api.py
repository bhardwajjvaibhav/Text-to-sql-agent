"""
api.py -- FastAPI wrapper around the agent.

    uvicorn api:app --reload
    open http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent import run_agent
from database import DATABASES, get_cached_schema, schema_cache_age_seconds
from query_cache import cache_flush, cache_stats
from token_tracker import cost_report

app = FastAPI(title="Text-to-SQL Agent", version="1.0.0")


class QueryRequest(BaseModel):
    question: str
    db_name: str = "business"


class QueryResponse(BaseModel):
    question: str
    sql_query: str | None = None
    summary: str | None = None
    result: list = []
    row_count: int = 0
    warnings: list = []
    cache_hit: bool = False
    cost_usd: float = 0.0
    token_usage: dict = {}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if req.db_name not in DATABASES:
        raise HTTPException(status_code=400, detail=f"Unknown database '{req.db_name}'")
    try:
        state = run_agent(req.question, req.db_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    rows = state.get("result") or []
    return QueryResponse(
        question=req.question,
        sql_query=state.get("sql_query"),
        summary=state.get("summary"),
        result=rows,
        row_count=len(rows),
        warnings=state.get("warnings", []),
        cache_hit=state.get("cache_hit", False),
        cost_usd=state.get("cost_usd", 0.0),
        token_usage=state.get("token_usage", {}),
    )


@app.get("/health")
def health():
    schema_ok = True
    try:
        get_cached_schema("business")
    except Exception:
        schema_ok = False
    return {
        "status": "ok" if schema_ok else "degraded",
        "databases": list(DATABASES),
        "schema_cache_age_seconds": schema_cache_age_seconds("business"),
    }


@app.get("/schema")
def schema(db_name: str = "business"):
    if db_name not in DATABASES:
        raise HTTPException(status_code=400, detail=f"Unknown database '{db_name}'")
    return {"db_name": db_name, "schema": get_cached_schema(db_name)}


@app.get("/cache/stats")
def cache_statistics():
    return cache_stats()


@app.post("/cache/flush")
def flush_cache():
    cache_flush()
    return {"status": "cache cleared"}


@app.get("/cost/report")
def costs():
    return cost_report()
