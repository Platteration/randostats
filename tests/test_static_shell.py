from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "randostats" / "static"
INDEX = STATIC / "index.html"


def test_index_references_existing_local_assets():
    html = INDEX.read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/static/([^"?#]+)', html)
    assert refs, "index.html should reference local static assets"
    missing = [ref for ref in refs if not (STATIC / ref).is_file()]
    assert missing == []


def test_overview_shell_has_accessible_tab_and_live_region():
    html = INDEX.read_text(encoding="utf-8")
    assert 'data-tab="overview"' in html
    assert 'id="tab-overview"' in html
    assert 'id="overview-content" aria-live="polite"' in html


class _InlineHandlers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        for name, _ in attrs:
            if name.startswith("on"):
                self.found.append(f"line {self.getpos()[0]}: <{tag} {name}=...>")


@pytest.mark.parametrize("page", ["index.html", "m.html"])
def test_no_inline_event_handlers(page):
    """The CSP is script-src 'self' (test_security_headers_are_sent), so the browser
    refuses an onclick= attribute and the control silently does nothing. The Import
    tab's "Try sample data" button shipped that way once."""
    parser = _InlineHandlers()
    parser.feed((STATIC / page).read_text(encoding="utf-8"))
    assert parser.found == [], "inline event handlers are dead under the CSP:\n  " + "\n  ".join(parser.found)


def test_new_frontend_modules_are_loaded_in_dependency_order():
    html = INDEX.read_text(encoding="utf-8")
    ui = html.index('/static/ui-state.js')
    app = html.index('/static/app.js')
    overview = html.index('/static/overview.js')
    assert ui < app < overview


def test_overview_uses_shared_frontend_core():
    core = (STATIC / "ui-state.js").read_text(encoding="utf-8")
    overview = (STATIC / "overview.js").read_text(encoding="utf-8")
    assert "window.RandoCore" in core
    assert "window.RandoCore" in overview
    assert "const api = async" not in overview
    assert "const make = (" not in overview


def test_repo_polish_docs_and_release_workflow_exist():
    assert (ROOT / "CONTRIBUTING.md").is_file()
    release = ROOT / ".github" / "workflows" / "release.yml"
    assert release.is_file()
    text = release.read_text(encoding="utf-8")
    assert 'tags:' in text and '"v*"' in text
    assert "python -m build" in text
    assert "upload-artifact@v4" in text
