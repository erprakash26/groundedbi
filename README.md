# GroundedBI

An AI business analytics assistant that answers natural-language questions about sales data, with every number in an answer verified against real query results.

> Status: in active development.

## Planned features
- LLM tool calling over predefined, validated analytics tools (no raw database access)
- Parameterized SQL on SQLite
- Grounding check: numbers in answers must come from tool results
- Clarifying questions for ambiguous requests
- Structured logging with request IDs
- Evaluation suite measuring tool selection and grounding

## Run locally
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python -m pytest -v
    uvicorn app.main:app --reload
