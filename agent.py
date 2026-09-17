"""
agent.py -- LangGraph text-to-SQL agent.

Node sequence:
  cache_check --(hit)--> summarize --> END
      |(miss)
      v
  schema_filter -> disambiguate -> generate_sql -> sql_guard --(blocked)--> summarize --> END
                                          ^                |(allowed)
                                          |                v
                                          +--(retry <=2)-- execute_sql -> validate_result
                                                              -> summarize -> cache_store -> END
"""

from typing import Optional, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, StateGraph

from query_cache import cache_lookup, cache_store
from schema_index import get_filtered_schema
from sql_guard import check_sql
from database import run_query
from token_tracker import log_query

load_dotenv()

MODEL_NAME = "claude-haiku-4-5"
MAX_SQL_RETRIES = 2

llm = ChatAnthropic(model=MODEL_NAME, temperature=0, max_tokens=1024)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    question: str
    db_name: str
    schema: str
    disambiguation_note: str
    sql_query: str
    guard_allowed: bool
    retry_count: int
    last_error: Optional[str]
    result: list
    validation_note: str
    summary: str
    warnings: list
    cache_hit: bool
    token_usage: dict
    cost_usd: float


def _init_state(question: str, db_name: str = "business") -> AgentState:
    return {
        "question": question,
        "db_name": db_name,
        "retry_count": 0,
        "last_error": None,
        "warnings": [],
        "cache_hit": False,
        "token_usage": {"input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": 0, "calls": 0},
    }


def _track(state: AgentState, response) -> None:
    """Accumulate token usage from a ChatAnthropic response."""
    usage = getattr(response, "usage_metadata", None) or {}
    state["token_usage"]["input_tokens"] += usage.get("input_tokens", 0)
    state["token_usage"]["output_tokens"] += usage.get("output_tokens", 0)
    details = usage.get("input_token_details") or {}
    state["token_usage"]["cache_read_tokens"] += details.get("cache_read", 0)
    state["token_usage"]["calls"] += 1


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def cache_check(state: AgentState) -> AgentState:
    hit = cache_lookup(state["question"], state["db_name"])
    if hit:
        state["sql_query"] = hit["sql_query"]
        state["summary"] = hit["summary"]
        state["result"] = hit["result"]
        state["cache_hit"] = True
    return state


def schema_filter(state: AgentState) -> AgentState:
    state["schema"] = get_filtered_schema(state["question"], state["db_name"])
    return state


def disambiguate(state: AgentState) -> AgentState:
    prompt = f"""You are helping prepare a question for SQL generation.
Schema:
{state['schema']}

Question: "{state['question']}"

If the question is ambiguous (e.g. "revenue" could include or exclude
cancelled orders, "recent" is undefined, "top" without a metric), state the
single most reasonable assumption you are making in one short sentence.
If it isn't ambiguous, reply with exactly: "No assumption needed."
"""
    response = llm.invoke(prompt)
    _track(state, response)
    state["disambiguation_note"] = response.content.strip()
    return state


def generate_sql(state: AgentState) -> AgentState:
    retry_context = ""
    if state.get("last_error"):
        retry_context = f"""
Your previous attempt failed with this database error:
{state['last_error']}
Previous SQL:
{state.get('sql_query', '')}
Fix the query.
"""
    prompt = f"""You are a SQLite expert. Write ONE read-only SELECT query
to answer the question below. Use only the tables/columns listed in the
schema. Return ONLY the raw SQL, no markdown fences, no explanation.

Schema:
{state['schema']}

Assumption: {state.get('disambiguation_note', 'None')}

Question: "{state['question']}"
{retry_context}
SQL:"""
    response = llm.invoke(prompt)
    _track(state, response)

    sql = response.content.strip()
    if sql.startswith("```"):
        sql = sql.strip("`").removeprefix("sql").strip()
    state["sql_query"] = sql
    return state


def sql_guard_node(state: AgentState) -> AgentState:
    result = check_sql(state["sql_query"])
    state["guard_allowed"] = result.allowed
    if not result.allowed:
        state["warnings"].append(f"Blocked: {result.reason}")
        state["summary"] = f"I can't run that query: {result.reason}"
    return state


def execute_sql(state: AgentState) -> AgentState:
    try:
        state["result"] = run_query(state["sql_query"], state["db_name"])
        state["last_error"] = None
    except Exception as exc:
        state["last_error"] = str(exc)
        state["retry_count"] += 1
    return state


def validate_result(state: AgentState) -> AgentState:
    preview = str(state.get("result", [])[:5])
    prompt = f"""Question: "{state['question']}"
SQL used: {state['sql_query']}
First rows of result: {preview}
Total rows: {len(state.get('result', []))}

Does this result plausibly answer the question? Reply with either:
"PLAUSIBLE" or "SUSPICIOUS: <one short reason>".
"""
    response = llm.invoke(prompt)
    _track(state, response)
    note = response.content.strip()
    state["validation_note"] = note
    if note.startswith("SUSPICIOUS"):
        state["warnings"].append(note)
    return state


def summarize(state: AgentState) -> AgentState:
    if state.get("cache_hit"):
        return state  # summary came from the cache
    if not state.get("guard_allowed", True):
        return state  # summary set by sql_guard_node

    prompt = f"""Question: "{state['question']}"
SQL: {state['sql_query']}
Result rows: {state.get('result', [])[:20]}

Write a short, direct 1-3 sentence answer to the question using this data.
Do not mention SQL or databases -- answer like a human analyst."""
    response = llm.invoke(prompt)
    _track(state, response)
    state["summary"] = response.content.strip()
    return state


def cache_store_node(state: AgentState) -> AgentState:
    if (not state.get("cache_hit")
            and state.get("guard_allowed", True)
            and state.get("result") is not None):
        cache_store(
            state["question"], state["sql_query"], state["summary"],
            state["result"], state["db_name"],
        )
    return state


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------
def route_after_cache(state: AgentState) -> str:
    return "summarize" if state.get("cache_hit") else "schema_filter"


def route_after_guard(state: AgentState) -> str:
    return "execute_sql" if state["guard_allowed"] else "summarize"


def route_after_execute(state: AgentState) -> str:
    if state.get("last_error") and state["retry_count"] <= MAX_SQL_RETRIES:
        return "generate_sql"  # retry, feeding the error back into the prompt
    if state.get("last_error"):
        state["warnings"].append(
            f"Gave up after {MAX_SQL_RETRIES} retries: {state['last_error']}"
        )
        state["result"] = []
    return "validate_result"


def route_after_summarize(state: AgentState) -> str:
    if state.get("cache_hit") or not state.get("guard_allowed", True):
        return END
    return "cache_store"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("cache_check", cache_check)
    graph.add_node("schema_filter", schema_filter)
    graph.add_node("disambiguate", disambiguate)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("sql_guard", sql_guard_node)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("validate_result", validate_result)
    graph.add_node("summarize", summarize)
    graph.add_node("cache_store", cache_store_node)

    graph.set_entry_point("cache_check")

    graph.add_conditional_edges("cache_check", route_after_cache,
                                {"summarize": "summarize", "schema_filter": "schema_filter"})
    graph.add_edge("schema_filter", "disambiguate")
    graph.add_edge("disambiguate", "generate_sql")
    graph.add_edge("generate_sql", "sql_guard")
    graph.add_conditional_edges("sql_guard", route_after_guard,
                                {"execute_sql": "execute_sql", "summarize": "summarize"})
    graph.add_conditional_edges("execute_sql", route_after_execute,
                                {"generate_sql": "generate_sql",
                                 "validate_result": "validate_result"})
    graph.add_edge("validate_result", "summarize")
    graph.add_conditional_edges("summarize", route_after_summarize,
                                {"cache_store": "cache_store", END: END})
    graph.add_edge("cache_store", END)

    return graph.compile()


_compiled_graph = None


def run_agent(question: str, db_name: str = "business") -> dict:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()

    final_state = _compiled_graph.invoke(_init_state(question, db_name))
    final_state["cost_usd"] = log_query(
        question, final_state["token_usage"], final_state.get("cache_hit", False)
    )
    return final_state


if __name__ == "__main__":
    result = run_agent("Which product category had the highest revenue?")
    print("SQL:     ", result.get("sql_query"))
    print("Summary: ", result.get("summary"))
    print("Warnings:", result.get("warnings"))
    print("Tokens:  ", result.get("token_usage"))
    print("Cost:    ", result.get("cost_usd"))
