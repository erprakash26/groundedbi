"""The website: three HTML pages plus the static files they load from /static.

The pages are plain files. All data comes from the JSON API (/ask, /analytics/*), so these
routes have no logic beyond picking the file and adding security headers.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"  # app/web: only site files

# Only same-origin scripts, styles and images; no plugins, no <base> tag, no framing.
# The pages have no inline script or style, which tests/test_web.py checks.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)
PAGE_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
PAGES = {"/": "index.html", "/explore": "explore.html", "/how-it-works": "how-it-works.html"}

# StaticFiles picks the Content-Type with mimetypes, which reads the Windows registry:
# .woff2 is often missing and .js can be text/plain, which browsers refuse under nosniff.
for _type, _ext in (
    ("text/javascript", ".js"),
    ("text/css", ".css"),
    ("image/svg+xml", ".svg"),
    ("font/woff2", ".woff2"),
):
    mimetypes.add_type(_type, _ext)

# Pages are for browsers, not API clients: keep them out of /docs and the OpenAPI schema.
router = APIRouter(include_in_schema=False)


def _page_route(filename: str):
    def page() -> FileResponse:
        return FileResponse(WEB_DIR / filename, media_type="text/html", headers=PAGE_HEADERS)

    return page


for _path, _filename in PAGES.items():
    router.add_api_route(_path, _page_route(_filename), methods=["GET"])
