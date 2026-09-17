"""
schema_index.py -- picks which tables are relevant to a question, and
translates cryptic column names into something the LLM can read.

No embeddings, no vector DB -- token overlap plus difflib fuzzy matching.
"""

import re
from difflib import SequenceMatcher

from sqlalchemy import inspect

from database import get_engine, get_schema_string

# Cryptic enterprise-style column names -> human-readable labels.
# The raw name is kept in the annotation because that's what has to appear
# in the generated SQL; the label is what stops the LLM guessing.
COLUMN_ALIASES = {
    "cust_nm": "customer name",
    "cust_id": "customer id",
    "ttl_amt": "total amount",
    "ord_dt": "order date",
    "ord_hdr": "order header",
    "prod_cd": "product code",
    "prod_nm": "product name",
    "qty_ord": "quantity ordered",
    "reg_cd": "region code",
    "stat_fl": "status flag",
    "crt_dt": "created date",
    "upd_dt": "updated date",
    "ln_ttl": "line total",
    "unt_prc": "unit price",
    "cat_cd": "category code",
    "tier_cd": "tier code",
}

STOPWORDS = {"what", "is", "the", "are", "a", "an", "of", "for", "in", "on",
             "by", "to", "and", "or", "how", "many", "which", "show", "me"}


def annotate_column(col_name: str) -> str:
    """'ttl_amt' -> 'total amount (ttl_amt)'; readable names pass through."""
    alias = COLUMN_ALIASES.get(col_name.lower())
    return f"{alias} ({col_name})" if alias else col_name


def _tokenize(text_in: str) -> set:
    words = re.findall(r"[a-z0-9]+", text_in.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


class SchemaIndex:
    def __init__(self, db_name: str, table_columns: dict):
        """table_columns: {'customers': ['id', 'cust_nm', 'reg_cd'], ...}"""
        self.db_name = db_name
        self.table_columns = table_columns

    def _score_table(self, table: str, question_tokens: set) -> float:
        vocab_parts = [table] + self.table_columns[table]
        vocab_parts += [
            COLUMN_ALIASES[c] for c in self.table_columns[table] if c in COLUMN_ALIASES
        ]
        vocab_tokens = _tokenize(" ".join(vocab_parts))
        if not vocab_tokens:
            return 0.0

        overlap = len(question_tokens & vocab_tokens)
        score = overlap * 2.0  # exact matches weigh heaviest

        # fuzzy pass catches "customer" vs "cust"
        for qt in question_tokens:
            for vt in vocab_tokens:
                ratio = SequenceMatcher(None, qt, vt).ratio()
                if ratio > 0.75:
                    score += ratio
        return score

    def filter_tables(self, question: str, top_n: int = 12) -> list:
        question_tokens = _tokenize(question)
        scored = [(t, self._score_table(t, question_tokens)) for t in self.table_columns]
        scored.sort(key=lambda pair: pair[1], reverse=True)

        relevant = [t for t, s in scored if s > 0][:top_n]
        # never hand an empty schema to the LLM
        return relevant or ([scored[0][0]] if scored else [])


def build_schema_index(db_name: str = "business") -> SchemaIndex:
    inspector = inspect(get_engine(db_name))
    table_columns = {
        t: [c["name"] for c in inspector.get_columns(t)]
        for t in inspector.get_table_names()
    }
    return SchemaIndex(db_name, table_columns)


def get_filtered_schema(question: str, db_name: str = "business", top_n: int = 12) -> str:
    """What agent.py's schema_filter node calls."""
    index = build_schema_index(db_name)
    tables = index.filter_tables(question, top_n=top_n)
    return get_schema_string(db_name, tables=tables)


if __name__ == "__main__":
    print(get_filtered_schema("which customers spent the most last year?"))
