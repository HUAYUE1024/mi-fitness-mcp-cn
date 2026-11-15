"""Dashboard front-end guarantees: no emoji, CSP-safe markup, security headers, XSS-safe templating."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mi_fitness_mcp.web import DEFAULT_ASSETS_DIR, create_app

STATIC = DEFAULT_ASSETS_DIR / "static"
SOURCES = [*STATIC.rglob("*.js"), *STATIC.rglob("*.css"), *DEFAULT_ASSETS_DIR.rglob("*.html")]
EMOJI = re.compile(
    "[\U0001f000-\U0001faff☀-➿⬀-⯿←-⇿️‍]"
)
VIEWS = ["overview", "trends", "sleep", "insights", "data", "sync", "access"]


def test_there_are_front_end_sources_to_check():
    assert len(SOURCES) >= 15


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_emoji_anywhere_in_the_ui(path: Path):
    hits = EMOJI.findall(path.read_text(encoding="utf-8"))
    assert not hits, f"{path.name} 含有 emoji / 符号字符: {hits[:5]}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_inline_handlers_or_javascript_urls(path: Path):
    """CSP 为 script-src 'self'：内联事件属性会被浏览器静默拦截，等于坏掉的按钮。"""
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+\s*=\s*[\"'`]", text, re.I), "内联事件处理器"
    assert "javascript:" not in text.lower()


def test_html_shell_has_no_inline_script_and_loads_one_module():
    html = (DEFAULT_ASSETS_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert inline == [], "内联 <script> 会被 CSP 拦截"
    assert html.count('type="module"') == 1
    assert 'name="viewport"' in html and "viewport-fit=cover" in html


def test_every_view_module_exists_and_is_wired_into_the_router():
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    for view in VIEWS:
        assert (STATIC / "js" / "views" / f"{view}.js").exists()
        assert f"./views/{view}.js" in app_js


def test_old_monolith_is_gone():
    assert not (STATIC / "css" / "style.css").exists()


def test_templating_escapes_untrusted_values():
    """html`` 必须转义插值，否则 API Key 的标签、账号名之类字段就是 XSS 入口。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("需要 node 来执行前端模块")
    dom = (STATIC / "js" / "lib" / "dom.js").as_posix()
    script = (
        f"import {{ html, raw }} from 'file:///{dom}';"
        "const evil = '<img src=x onerror=alert(1)>\"&';"
        "const out = String(html`<td>${evil}</td><td>${[evil, raw('<b>ok</b>')]}</td>`);"
        "if (/<img/.test(out)) { console.error('UNESCAPED', out); process.exit(1); }"
        "if (!out.includes('<b>ok</b>')) { console.error('raw lost', out); process.exit(2); }"
    )
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.fixture
def client():
    app = create_app(backend_url="http://backend.invalid")
    app.config["TESTING"] = True
    return app.test_client()


def test_dashboard_sends_strict_security_headers(client):
    resp = client.get("/")
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"


def test_all_assets_referenced_by_the_page_are_served(client):
    page = client.get("/").get_data(as_text=True)
    urls = re.findall(r'(?:src|href)="(/static/[^"]+)"', page)
    assert len(urls) >= 7
    for url in urls:
        assert client.get(url).status_code == 200, url
    for view in VIEWS:
        assert client.get(f"/static/js/views/{view}.js").status_code == 200


def test_page_makes_no_third_party_requests(client):
    page = client.get("/").get_data(as_text=True)
    assert not re.search(r'(?:src|href)="https?://', page), "页面不应引用任何外部资源"
