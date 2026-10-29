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
Python 3.11 or 3.12. On Windows PowerShell:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements-dev.txt
    python -m scripts.seed_data
    python -m pytest -v
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/. `requirements-dev.txt` installs the runtime packages
(`requirements.txt`) plus pytest, httpx2 and pip-audit. `scripts.seed_data` creates the
synthetic database at `data/groundedbi.db`; without it every data endpoint answers 503.

Checks that CI (`.github/workflows/ci.yml`) runs on Python 3.11 and 3.12:

    pip-audit
    python -m pytest -v
    python -m evals.check_expectations
    python -m evals.run_evals --split dev --fail-under 1.0

## Deploying to Vercel
`vercel.json` builds the database during the Vercel build
(`python -m scripts.seed_data --db data/groundedbi.db`) and leaves `tests/`, `evals/` and
`docs/` out of the function bundle. Vercel installs `requirements.txt` only, on the Python
version in `.python-version` (3.12). If the database is missing, the app still starts, logs
`database_unavailable` and answers 503 on the data endpoints; it never creates an empty one.
