from pathlib import Path
import re


STATIC = Path(__file__).parents[1] / "randostats" / "static"
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
