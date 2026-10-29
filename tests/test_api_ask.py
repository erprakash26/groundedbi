"""POST /ask through the real app, the real get_db and a seeded test database."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.config import Settings, get_settings
from app.llm import RuleBasedProvider
from app.llm.base import FinalAnswer, ProviderResponse
from app.main import app
from app.observability.middleware import REQUEST_ID_HEADER
from scripts.seed_data import seed_database

MARCH = "Why did revenue drop in March compared with February?"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("ask") / "ask.db"
    seed_database(path, seed=42, start="2026-01", months=6)
    return path


@pytest.fixture
def make_client():
    """TestClient for a database path, optionally with a different provider.

    Overriding get_settings (not get_db) keeps the real get_db in the path: read-only,
    503 when missing, closed after each request.
    """

    def make(path, provider=None) -> TestClient:
        app.dependency_overrides[get_settings] = lambda: Settings(database_path=path, _env_file=None)
        if provider is not None:
            app.dependency_overrides[get_provider] = lambda: provider
        return TestClient(app, raise_server_exceptions=False)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client, db_path):
    return make_client(db_path)


def ask(client, question):
    return client.post("/ask", json={"question": question})


def tools(body):
    return [entry["tool"] for entry in body["trace"]]


# --- the four example questions -------------------------------------------------------------


def test_march_drivers_question_is_answered_with_evidence(client):
    response = ask(client, MARCH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert "StudioBook 16 / West" in body["answer"] and "71.6%" in body["answer"]
    assert body["grounding"]["passed"] is True and body["grounding"]["numbers_checked"] > 0
    assert set(body["grounding"]) == {"passed", "numbers_checked"}  # no unsupported list in responses
    assert tools(body) == ["get_data_overview", "get_change_drivers"]
    assert body["trace"][1]["arguments"]["dimension"] == "product_region"
    assert body["clarifying_question"] is None and body["message"] is None
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}


def test_evidence_holds_the_measuring_tools_data_not_the_overview(client):
    body = ask(client, MARCH).json()
    assert [item["tool"] for item in body["evidence"]] == ["get_change_drivers"]
    data = body["evidence"][0]["data"]
    # The answer's numbers are visible in the evidence.
    assert data["rows"][0]["label"] == "StudioBook 16 / West"
    assert data["rows"][0]["change_abs_display"] in body["answer"]
    assert data["rows"][0]["share_of_total_change"] + "%" in body["answer"]


def test_evidence_for_two_tool_calls(client):
    body = ask(client, "Top 5 in March vs April").json()
    assert body["status"] == "answered"
    assert [item["tool"] for item in body["evidence"]] == ["get_top_products", "get_top_products"]


def test_unknown_region_asks_back_without_evidence(client):
    response = ask(client, "Revenue in Europe in March")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "clarification_needed"
    assert "East, North, South and West" in body["clarifying_question"]
    assert body["answer"] is None
    assert body["evidence"] is None
    assert body["grounding"] is None


def test_profit_is_out_of_scope_without_tool_calls(client):
    response = ask(client, "What was our profit in March?")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "out_of_scope"
    assert body["answer"].startswith("Profit isn't in the data")
    assert body["trace"] == []
    assert body["evidence"] is None


def test_false_premise_is_corrected(client):
    body = ask(client, "Why did revenue drop from May to June?").json()
    assert body["status"] == "answered"
    assert body["answer"].startswith("Revenue didn't drop")


# --- grounding failure and provider errors -------------------------------------------------


class HallucinatingProvider(RuleBasedProvider):
    def next_turn(self, messages, tools):
        response = super().next_turn(messages, tools)
        if isinstance(response.turn, FinalAnswer):
            return ProviderResponse(FinalAnswer(response.turn.text + " Revenue will grow by $987,654."))
        return response


def test_grounding_failure_withholds_answer_and_unverified_numbers(make_client, db_path, log_lines):
    client = make_client(db_path, provider=HallucinatingProvider())
    response = ask(client, MARCH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "grounding_failed"
    assert body["answer"] is None
    assert body["evidence"] is None
    assert body["message"] == (
        "The answer contained figures that could not be verified against the data, so it was withheld."
    )
    assert body["grounding"] == {"passed": False, "numbers_checked": body["grounding"]["numbers_checked"]}
    assert "987,654" not in response.text  # nowhere in the response
    failed = [line for line in log_lines() if line.get("event") == "grounding_failed"]
    assert failed and failed[0]["unsupported"] == ["$987,654"]  # but in the log


class RaisingProvider:
    name = "raising"
    pricing = RuleBasedProvider.pricing

    def next_turn(self, messages, tools):
        raise RuntimeError("secret provider internals")


def test_provider_exception_is_a_500_envelope(make_client, db_path):
    client = make_client(db_path, provider=RaisingProvider())
    response = ask(client, MARCH)
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "An internal error occurred."
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert "secret" not in response.text and "RuntimeError" not in response.text


# --- validation (422) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "Why did revenue drop? " + "z" * 480},  # 502 characters
        {},
        {"question": "Top products", "limit": 5},
        {"question": 12345},
        {"question": None},
        {"question": ["Top products"]},
    ],
)
def test_invalid_body_is_422_without_echoing_the_question(client, payload):
    response = client.post("/ask", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]
    question = payload.get("question")
    if isinstance(question, str) and question.strip():
        assert question.strip() not in response.text
    if question == 12345:
        assert "12345" not in response.text


def test_501_characters_is_rejected_and_500_accepted(client):
    assert client.post("/ask", json={"question": "a" * 501}).status_code == 422
    # 500 characters of "a" has no intent, so the planner asks back, but the request is valid.
    assert client.post("/ask", json={"question": "a" * 500}).status_code == 200


def test_surrounding_whitespace_is_stripped_not_rejected(client):
    body = client.post("/ask", json={"question": f"   {MARCH}  \n"}).json()
    assert body["status"] == "answered"


def test_extra_field_with_unsafe_name_is_not_echoed(client):
    injection = "'; DROP TABLE sales;--"
    response = client.post("/ask", json={"question": "x", injection: 1})
    assert response.status_code == 422
    assert injection not in response.text
    assert "DROP" not in response.text
    assert response.json()["error"]["details"][0]["field"] == "<unrecognized field>"


def test_extra_field_with_safe_name_is_named(client):
    response = client.post("/ask", json={"question": "x", "limit": 5})
    assert response.json()["error"]["details"][0]["field"] == "limit"


def test_malformed_json_is_422(client):
    response = client.post("/ask", content=b'{"question": ', headers={"Content-Type": "application/json"})
    assert response.status_code == 422


# --- routing and database ----------------------------------------------------------------


def test_get_ask_is_405(client):
    response = client.get("/ask")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_missing_database_is_503(make_client, tmp_path):
    response = ask(make_client(tmp_path / "missing.db"), MARCH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


# --- request IDs, logs and determinism ---------------------------------------------------


def test_request_id_matches_header_and_logs(client, log_lines):
    response = client.post("/ask", json={"question": MARCH}, headers={REQUEST_ID_HEADER: "ask-test-123"})
    assert response.headers[REQUEST_ID_HEADER] == "ask-test-123"
    assert response.json()["request_id"] == "ask-test-123"
    lines = log_lines()
    executed = [line for line in lines if line.get("event") == "tool_executed"]
    completed = [line for line in lines if line.get("event") == "question_completed"]
    assert len(executed) == 2 and all(line["request_id"] == "ask-test-123" for line in executed)
    assert len(completed) == 1 and completed[0]["request_id"] == "ask-test-123"
    assert completed[0]["status"] == "answered"


def test_march_request_logs_every_trace_field_the_brief_requires(client, log_lines):
    # The brief: question, tool selected, arguments, validation result, SQL duration, tokens used,
    # estimated cost and final status, all tied together by the request ID.
    client.post("/ask", json={"question": MARCH}, headers={REQUEST_ID_HEADER: "trace-fields-1"})
    lines = [line for line in log_lines() if line["request_id"] == "trace-fields-1"]
    by_event = {}
    for line in lines:
        by_event.setdefault(line.get("event"), []).append(line)

    (received,) = by_event["question_received"]
    assert received["question"] == MARCH
    executed = by_event["tool_executed"]
    assert [line["tool"] for line in executed] == ["get_data_overview", "get_change_drivers"]
    for line in executed:
        assert isinstance(line["arguments"], dict)
        assert line["arguments_valid"] is True
        assert isinstance(line["sql_duration_ms"], float)
    assert executed[1]["arguments"]["period_b_start"] == "2026-03"
    (completed,) = by_event["question_completed"]
    assert completed["status"] == "answered"
    assert completed["input_tokens"] == 0 and completed["output_tokens"] == 0  # rule-based: no LLM tokens
    assert completed["estimated_cost_usd"] == 0.0
    (access,) = by_event["request_completed"]
    assert access["path"] == "/ask" and access["status_code"] == 200


def test_generated_request_id_matches_header(client):
    response = ask(client, MARCH)
    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


def _stable(body: dict) -> dict:
    """The response without the parts that legitimately differ between runs."""
    body = {k: v for k, v in body.items() if k not in ("request_id", "duration_ms")}
    body["trace"] = [{k: v for k, v in entry.items() if k != "duration_ms"} for entry in body["trace"]]
    return body


@pytest.mark.parametrize("question", [MARCH, "Revenue in Europe in March", "What was our profit in March?"])
def test_same_question_twice_gives_the_same_response(client, question):
    first, second = ask(client, question).json(), ask(client, question).json()
    assert _stable(first) == _stable(second)
