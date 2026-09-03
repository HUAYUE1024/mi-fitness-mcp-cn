"""Flask Web UI & Reverse Proxy for Mi Fitness MCP.

Provides a modern dashboard interface and reverse proxy to the backend FastAPI service.
- Serves the frontend at GET /
- Reverse proxies /proxy/<path> to the Python FastAPI backend (default http://127.0.0.1:8321)
- Automatically attaches X-API-Key and X-Admin-Key from environment variables
- Accurately reports upstream response time via X-Upstream-Time-Ms
- Supports streaming, JSON, and binary responses (e.g., QR code PNGs)
"""

import os
import time
from pathlib import Path

import httpx
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from mi_fitness_mcp.security import default_allowed_hosts, host_allowed

# Assets directory: src/mi_fitness_mcp/web_assets or fallback to testpage
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_ASSETS_DIR = PACKAGE_DIR / "web_assets"


def create_app(
    backend_url: str | None = None,
    api_key: str | None = None,
    admin_key: str | None = None,
    assets_dir: Path | str | None = None,
) -> Flask:
    """Create and configure the Flask web application."""
    assets_path = Path(assets_dir) if assets_dir else DEFAULT_ASSETS_DIR

    app = Flask(
        __name__,
        static_folder=str(assets_path / "static") if (assets_path / "static").exists() else str(assets_path),
        static_url_path="/static",
        template_folder=str(assets_path / "templates") if (assets_path / "templates").exists() else str(assets_path),
    )

    # Configuration with env fallbacks
    app.config["BACKEND_URL"] = (
        backend_url
        or os.environ.get("MI_FITNESS_API_URL")
        or "http://127.0.0.1:8321"
    ).rstrip("/")
    app.config["API_KEY"] = api_key or os.environ.get("MI_FITNESS_API_KEY")
    app.config["ADMIN_KEY"] = admin_key or os.environ.get("MI_FITNESS_ADMIN_KEY")

    # Persistent HTTP client with 300s timeout (trust_env=False avoids proxy interference on localhost)
    http_client = httpx.Client(trust_env=False, timeout=300.0, follow_redirects=True)

    @app.before_request
    def _check_host():
        # Host 头白名单（防 DNS 重绑定）：默认仅本机域名，环境变量可覆盖
        if not host_allowed(request.host, default_allowed_hosts()):
            return jsonify({"detail": "Host not allowed"}), 403
        return None

    @app.route("/")
    def index():
        """Render the main dashboard interface."""
        template_file = assets_path / "templates" / "index.html"
        if template_file.exists():
            return render_template(
                "index.html",
                backend_url=app.config["BACKEND_URL"],
                has_api_key=bool(app.config["API_KEY"]),
                has_admin_key=bool(app.config["ADMIN_KEY"]),
            )
        # Fallback to direct static index.html
        direct_index = assets_path / "index.html"
        if direct_index.exists():
            return send_from_directory(str(assets_path), "index.html")
        return jsonify({"status": "ok", "message": "Mi Fitness Web UI is running"}), 200

    @app.route("/api/proxy_status")
    def proxy_status():
        """Get status of the Flask proxy and configured upstream backend."""
        backend = app.config["BACKEND_URL"]
        healthy = False
        latency_ms = None
        detail = ""
        try:
            t0 = time.perf_counter()
            resp = http_client.get(f"{backend}/", timeout=3.0)
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            healthy = resp.status_code < 500
        except Exception as e:
            detail = str(e)

        return jsonify({
            "proxy": "running",
            "proxy_type": "Flask (Python)",
            "backend_url": backend,
            "backend_reachable": healthy,
            "backend_latency_ms": latency_ms,
            "has_server_api_key": bool(app.config["API_KEY"]),
            "has_server_admin_key": bool(app.config["ADMIN_KEY"]),
            "error_detail": detail if not healthy else None,
        })

    @app.route("/proxy", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    @app.route("/proxy/", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    @app.route("/proxy/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    def proxy(subpath: str = ""):
        """Reverse proxy all requests to the backend FastAPI service."""
        backend = app.config["BACKEND_URL"]
        query_string = request.query_string.decode("utf-8")
        clean_subpath = subpath.lstrip("/")
        target_url = f"{backend}/{clean_subpath}" if clean_subpath else f"{backend}/"
        if query_string:
            target_url = f"{target_url}?{query_string}"

        # Prepare headers: forward browser headers except host and content-length
        forward_headers: dict[str, str] = {}
        for key, value in request.headers.items():
            k_lower = key.lower()
            if k_lower not in ("host", "content-length", "transfer-encoding"):
                forward_headers[key] = value

        # Attach API Key (Server env priority, otherwise pass through client key)
        server_api_key = app.config["API_KEY"]
        if server_api_key:
            forward_headers["X-API-Key"] = server_api_key

        # Attach Admin Key if configured
        server_admin_key = app.config["ADMIN_KEY"]
        if server_admin_key:
            forward_headers["X-Admin-Key"] = server_admin_key

        body = request.get_data() if request.method in ("POST", "PUT", "PATCH", "DELETE") else None

        started = time.perf_counter()
        try:
            upstream_resp = http_client.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                content=body,
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

            # Build response back to browser
            excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
            response_headers: list[tuple[str, str]] = [
                (k, v)
                for k, v in upstream_resp.headers.items()
                if k.lower() not in excluded_headers
            ]
            response_headers.append(("X-Upstream-Time-Ms", str(int(elapsed_ms))))

            return Response(
                response=upstream_resp.content,
                status=upstream_resp.status_code,
                headers=response_headers,
                mimetype=upstream_resp.headers.get("content-type"),
            )
        except httpx.RequestError as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            error_body = {
                "error": "Flask 代理无法访问后端",
                "detail": str(exc),
                "target": target_url,
                "hint": f"请确认 Python FastAPI 服务已在 {backend} 正常启动",
            }
            resp = jsonify(error_body)
            resp.status_code = 502
            resp.headers["X-Upstream-Time-Ms"] = str(int(elapsed_ms))
            return resp

    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 8322,
    backend_url: str | None = None,
    api_key: str | None = None,
    admin_key: str | None = None,
    debug: bool = False,
):
    """Start the Flask web development server."""
    app = create_app(backend_url=backend_url, api_key=api_key, admin_key=admin_key)
    print("[*] Mi Fitness Web UI started!")
    print(f"    Web Dashboard: http://{host}:{port}/")
    print(f"    Proxy Backend: {app.config['BACKEND_URL']}")
    if app.config["API_KEY"]:
        print("    Auth Mode: Server X-API-Key configured")
    if app.config["ADMIN_KEY"]:
        print("    Admin Auth: Server X-Admin-Key configured")
    print("    Press Ctrl+C to quit")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    addr = os.environ.get("BIND", "127.0.0.1:8322")
    if ":" in addr:
        h, p = addr.split(":", 1)
        port_num = int(p)
    else:
        h = addr
        port_num = 8322
    run_server(host=h, port=port_num)
