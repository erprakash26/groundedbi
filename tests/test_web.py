"""The website: pages, security headers, static files and front-end source rules."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.examples import ASK_EXAMPLES
from app.api.web import CONTENT_SECURITY_POLICY, PAGES, WEB_DIR
from app.main import app
from app.schemas.analytics import MetaResponse
from app.schemas.ask import AskRequest

client = TestClient(app)  # the pages and /static never touch the database

EXPECTED_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".txt": "text/plain",  # the font licences
}
WEB_FILES = sorted(path for path in WEB_DIR.rglob("*") if path.is_file())
HTML_FILES = [path for path in WEB_FILES if path.suffix == ".html"]
SCRIPT_FILES = [path for path in WEB_FILES if path.suffix in (".js", ".html")]
UNSAFE_JS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")


def _rel(path: Path) -> str:
    return path.relative_to(WEB_DIR).as_posix()


class _TagCollector(HTMLParser):
    """Collects every start tag with its attributes, and the text inside each <script>."""

    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.script_text: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.script_text.append(data)


def _parse(path: Path) -> _TagCollector:
    parser = _TagCollector()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


# --- pages ---


@pytest.mark.parametrize(("url", "filename"), PAGES.items())
def test_page_returns_its_file_with_security_headers(url, filename):
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == (WEB_DIR / filename).read_bytes()
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers[name] == value


def test_the_policy_in_code_is_the_one_tested():
    assert CONTENT_SECURITY_POLICY == EXPECTED_HEADERS["content-security-policy"]


def test_api_responses_do_not_get_the_page_policy():
    # /docs loads Swagger UI from a CDN; the strict page policy would break it.
    assert "content-security-policy" not in client.get("/health").headers
    assert "content-security-policy" not in client.get("/docs").headers


def test_unknown_page_is_404():
    assert client.get("/about").status_code == 404


def test_pages_only_accept_get():
    assert client.post("/explore").status_code == 405


# --- static files ---


def test_the_web_folder_has_every_kind_of_file():
    assert {path.suffix for path in WEB_FILES} == set(CONTENT_TYPES)


@pytest.mark.parametrize("path", WEB_FILES, ids=_rel)
def test_every_web_file_is_served_with_its_content_type(path):
    response = client.get(f"/static/{_rel(path)}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == CONTENT_TYPES[path.suffix]
    assert response.content == path.read_bytes()


@pytest.mark.parametrize("url", ["/static/missing.js", "/static/../main.py", "/static/..%2Fmain.py"])
def test_missing_or_outside_static_files_are_404(url):
    assert client.get(url).status_code == 404


# --- front-end source rules ---


@pytest.mark.parametrize("path", SCRIPT_FILES, ids=_rel)
def test_no_html_parsing_or_eval_in_scripts(path):
    text = path.read_text(encoding="utf-8")
    found = [token for token in UNSAFE_JS if token in text]
    assert not found, f"{_rel(path)} uses {found}: build nodes with createElement/textContent instead"


@pytest.mark.parametrize("path", HTML_FILES, ids=_rel)
def test_no_inline_script_style_or_event_handlers(path):
    parsed = _parse(path)
    assert not "".join(parsed.script_text).strip(), "inline <script> code"
    assert all(attrs.get("src") for tag, attrs in parsed.tags if tag == "script"), "<script> without src"
    assert "style" not in {tag for tag, _ in parsed.tags}, "<style> element"
    for tag, attrs in parsed.tags:
        assert "style" not in attrs, f"style= on <{tag}>"
        handlers = [name for name in attrs if name.startswith("on")]
        assert not handlers, f"{handlers} on <{tag}>"


@pytest.mark.parametrize("path", HTML_FILES, ids=_rel)
def test_scripts_and_stylesheets_are_local_static_files(path):
    urls = [attrs.get("src") for tag, attrs in _parse(path).tags if tag == "script"]
    urls += [attrs.get("href") for tag, attrs in _parse(path).tags if tag == "link"]
    assert urls
    for url in urls:
        assert url and re.fullmatch(r"/static/[A-Za-z0-9_./-]+", url) and ".." not in url, url
        assert (WEB_DIR / url.removeprefix("/static/")).is_file(), f"{url} does not exist"


def test_the_source_rules_catch_violations(tmp_path):
    # The checks above would pass trivially if the parser saw nothing; prove they can fail.
    bad = tmp_path / "bad.html"
    bad.write_text(
        '<script>alert(1)</script><div style="x" onclick="y"></div>'
        '<link href="https://cdn.example/x.css"><script src="//cdn.example/x.js"></script>',
        encoding="utf-8",
    )
    parsed = _parse(bad)
    assert "".join(parsed.script_text).strip() == "alert(1)"
    assert {"style", "onclick"} <= set(parsed.tags[1][1])
    assert parsed.tags[2][1]["href"].startswith("https://")


def test_adapt_meta_reads_only_fields_the_api_returns():
    common = (WEB_DIR / "common.js").read_text(encoding="utf-8")
    adapt = common[common.index("function adaptMeta") : common.index("async function loadMeta")]
    read = set(re.findall(r"body\.([a-z_]+)", adapt))
    assert read == set(MetaResponse.model_fields)


# --- /ask/examples ---


def test_ask_examples_match_the_openapi_examples():
    response = client.get("/ask/examples")
    assert response.status_code == 200
    body = response.json()
    assert body == [{"summary": e.summary, "question": e.question} for e in ASK_EXAMPLES]
    documented = app.openapi()["paths"]["/ask"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert [{"summary": e["summary"], "question": e["value"]["question"]} for e in documented.values()] == body


def test_ask_examples_are_valid_ask_requests():
    assert ASK_EXAMPLES
    for example in ASK_EXAMPLES:
        assert example.summary
        assert AskRequest(question=example.question).question == example.question  # valid and already stripped


# --- OpenAPI ---


def test_pages_and_static_are_not_in_the_openapi_schema():
    paths = set(app.openapi()["paths"])
    assert not paths & set(PAGES)
    assert not [path for path in paths if path.startswith("/static")]
    assert "/ask/examples" in paths
