"""cc-router — lightweight proxy with intelligent image routing + Web management panel."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse, FileResponse
from starlette.routing import Route

from config import BackendConfig, ConfigManager, LoggingConfig, PROVIDER_PRESETS, ServerConfig
from core import detect_images, forward, connect_stream, iter_stream_bytes, TIMEOUT

# ── Globals ─────────────────────────────────────────────────────────
config_mgr = ConfigManager()
start_time = time.monotonic()
stats: dict[str, int] = {"text": 0, "multimodal": 0, "errors": 0}
activity_log: list[dict[str, Any]] = []  # Last 50 routing decisions
MAX_LOG = 50

logger = logging.getLogger("cc-router")

CONFIG_STRING_KEYS = {"api_key", "base_url", "host", "level", "model", "name", "provider"}
CONFIG_SCALAR_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$")
REQUIRED_BACKEND_FIELDS = ("name", "base_url", "api_key", "model", "provider")


def _split_inline_comment(value: str) -> tuple[str, str]:
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip(), value[index:]
    return value.rstrip(), ""


def _quote_yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalize_config_quotes(content: str) -> str:
    """Normalize known string fields to double-quoted YAML scalars."""
    lines = content.splitlines()
    normalized: list[str] = []

    for line in lines:
        match = CONFIG_SCALAR_RE.match(line)
        if not match:
            normalized.append(line)
            continue

        indent, key, raw_value = match.groups()
        if key not in CONFIG_STRING_KEYS:
            normalized.append(line)
            continue

        value, comment = _split_inline_comment(raw_value)
        stripped = value.strip()
        if not stripped or stripped[0] in {"'", '"', "[", "{", "|", ">"}:
            normalized.append(line)
            continue

        suffix = f" {comment}" if comment else ""
        normalized.append(f"{indent}{key}: {_quote_yaml_string(stripped)}{suffix}")

    trailing_newline = "\n" if content.endswith(("\n", "\r")) else ""
    return "\n".join(normalized) + trailing_newline


def validate_config_mapping(parsed: dict[str, Any]) -> str | None:
    try:
        server = parsed.get("server", {})
        if not isinstance(server, dict):
            return "Section must be a mapping: server"
        ServerConfig(**server)

        logging_config = parsed.get("logging", {})
        if not isinstance(logging_config, dict):
            return "Section must be a mapping: logging"
        LoggingConfig(**logging_config)

        for role in ("text", "multimodal"):
            section = parsed.get(role)
            if not isinstance(section, dict):
                return f"Section must be a mapping: {role}"

            missing = [field for field in REQUIRED_BACKEND_FIELDS if field not in section]
            if missing:
                return f"Missing required field: {role}.{missing[0]}"

            provider = section.get("provider")
            if provider not in PROVIDER_PRESETS:
                return f"Unknown provider in {role}.provider: {provider}"

            BackendConfig(**section)
    except TypeError as exc:
        return f"Invalid config schema: {exc}"

    return None


def _record(route: str, has_image: bool, source_model: str, target_model: str, backend_name: str, status: int) -> None:
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    activity_log.append({
        "time": now, "route": route, "has_image": has_image,
        "source_model": source_model, "target_model": target_model,
        "backend": backend_name, "status": status,
    })
    if len(activity_log) > MAX_LOG:
        activity_log.pop(0)


# ── Route handlers ──────────────────────────────────────────────────


async def messages(req: Request) -> StreamingResponse | JSONResponse:
    """POST /v1/messages — main proxy endpoint."""
    try:
        cfg = config_mgr.config
    except Exception as exc:
        stats["errors"] += 1
        logger.error("Config load failed: %s", exc)
        return JSONResponse({"error": "Service unavailable: config invalid"}, status_code=503)

    client: httpx.AsyncClient = req.app.state.client

    try:
        body: dict[str, Any] = await req.json()
    except (json.JSONDecodeError, ValueError):
        stats["errors"] += 1
        return JSONResponse({"error": "Invalid request body"}, status_code=400)

    if not isinstance(body, dict):
        stats["errors"] += 1
        return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)

    has_image = detect_images(body)
    backend = cfg.multimodal if has_image else cfg.text
    route_label = "multimodal" if has_image else "text"
    stats[route_label] += 1

    source_model = str(body.get("model", "?"))
    client_version = req.headers.get("anthropic-version", "2023-06-01")

    logger.info("→ %s | has_image=%s | %s → %s (%s)", route_label, has_image, source_model, backend.model, backend.name)

    is_stream = body.get("stream", False)

    try:
        if is_stream:
            resp, _proxy_body = await connect_stream(client, backend, body, client_version)
            proxy_headers = {"x-cc-router-backend": backend.provider, "x-cc-router-route": route_label}
            if resp.status_code != 200:
                error_body = await resp.aread()
                await resp.aclose()
                _record(route_label, has_image, source_model, backend.model, backend.name, resp.status_code)
                return Response(content=error_body, status_code=resp.status_code, media_type="application/json", headers=proxy_headers)
            _record(route_label, has_image, source_model, backend.model, backend.name, 200)
            return StreamingResponse(iter_stream_bytes(resp), media_type="text/event-stream", headers=proxy_headers)

        status_code, resp_headers, resp_body = await forward(client, backend, body, client_version)

        proxy_headers = {
            k: v for k, v in resp_headers.items()
            if k.lower() not in {"transfer-encoding", "content-encoding", "content-length"}
        }
        proxy_headers["x-cc-router-backend"] = backend.provider
        proxy_headers["x-cc-router-route"] = route_label

        _record(route_label, has_image, source_model, backend.model, backend.name, status_code)
        return Response(content=resp_body, status_code=status_code, headers=proxy_headers, media_type="application/json")
    except Exception as exc:
        stats["errors"] += 1
        _record(route_label, has_image, source_model, backend.model, backend.name, 502)
        logger.error("Proxy error: %s", exc)
        return JSONResponse({"error": "Backend request failed"}, status_code=502)


async def health(req: Request) -> JSONResponse:
    """GET /health — machine-readable health check."""
    return JSONResponse({"status": "ok", "uptime": int(time.monotonic() - start_time)})


# ── API endpoints ───────────────────────────────────────────────────


async def api_config_get(req: Request) -> JSONResponse:
    """GET /api/config — return config.yaml content."""
    path = config_mgr._path
    try:
        content = path.read_text(encoding="utf-8")
        return JSONResponse({"content": content, "mtime": path.stat().st_mtime})
    except OSError as exc:
        logger.error("Failed to read config file: %s", exc)
        return JSONResponse({"error": "Failed to read config file"}, status_code=500)


async def api_config_post(req: Request) -> JSONResponse:
    """POST /api/config — save config.yaml (validates YAML first)."""
    try:
        payload = await req.json()
        content = payload.get("content", "")
        if content is None:
            content = ""
        if not content.strip():
            return JSONResponse({"ok": False, "error": "Content is empty"}, status_code=400)
        # Validate YAML syntax + schema
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            return JSONResponse({"ok": False, "error": "Config must be a YAML mapping"}, status_code=400)
        if "text" not in parsed:
            return JSONResponse({"ok": False, "error": "Missing required section: text"}, status_code=400)
        if "multimodal" not in parsed:
            return JSONResponse({"ok": False, "error": "Missing required section: multimodal"}, status_code=400)
        validation_error = validate_config_mapping(parsed)
        if validation_error:
            return JSONResponse({"ok": False, "error": validation_error}, status_code=400)
        content = normalize_config_quotes(content)
        config_mgr._path.write_text(content, encoding="utf-8")
        # Force reload on next request
        config_mgr._mtime = 0.0
        logger.info("Config saved via Web UI")
        return JSONResponse({"ok": True})
    except yaml.YAMLError as exc:
        logger.error("YAML parse error: %s", exc)
        return JSONResponse({"ok": False, "error": "YAML syntax error"}, status_code=400)
    except Exception as exc:
        logger.error("Failed to save config: %s", exc)
        return JSONResponse({"ok": False, "error": "Failed to save config"}, status_code=400)


async def api_config_provider_post(req: Request) -> JSONResponse:
    """POST /api/config/provider — safely update a backend section via structured JSON."""
    try:
        payload = await req.json()
        role = payload.get("role")
        provider = payload.get("provider")
        api_key = (payload.get("api_key") or "").strip()
        model = (payload.get("model") or "").strip()
        base_url = (payload.get("base_url") or "").strip()

        if role not in ("text", "multimodal"):
            return JSONResponse({"ok": False, "error": "role must be 'text' or 'multimodal'"}, status_code=400)
        if not provider:
            return JSONResponse({"ok": False, "error": "provider is required"}, status_code=400)
        if not api_key:
            return JSONResponse({"ok": False, "error": "api_key is required"}, status_code=400)
        if not model:
            return JSONResponse({"ok": False, "error": "model is required"}, status_code=400)

        preset = PROVIDER_PRESETS.get(provider)
        if not preset:
            return JSONResponse({"ok": False, "error": f"Unknown provider: {provider}"}, status_code=400)
        if not base_url:
            base_url = preset["base_url"]

        # Parse config for validation only
        raw = config_mgr._path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict):
            parsed = {}

        # Build the new section as YAML text (preserves comments in other sections)
        section_yaml = (
            f"{role}:\n"
            f"  name: {_quote_yaml_string(str(preset['name']))}\n"
            f"  base_url: {_quote_yaml_string(base_url)}\n"
            f"  api_key: {_quote_yaml_string(api_key)}\n"
            f"  model: {_quote_yaml_string(model)}\n"
            f"  provider: {_quote_yaml_string(provider)}\n"
        )
        # Replace only the target section in the raw text
        section_pattern = re.compile(rf"^{role}:\s*\n(?:[ \t]+.*\n)*", re.MULTILINE)
        if section_pattern.search(raw):
            new_raw = section_pattern.sub(section_yaml, raw)
        else:
            new_raw = raw.rstrip() + "\n\n" + section_yaml + "\n"

        new_raw = normalize_config_quotes(new_raw)
        config_mgr._path.write_text(new_raw, encoding="utf-8")
        config_mgr._mtime = 0.0
        logger.info("Config updated via provider modal: %s → %s", role, provider)
        return JSONResponse({"ok": True})
    except yaml.YAMLError as exc:
        logger.error("YAML error: %s", exc)
        return JSONResponse({"ok": False, "error": "YAML syntax error"}, status_code=400)
    except Exception as exc:
        logger.error("Failed to update provider config: %s", exc)
        return JSONResponse({"ok": False, "error": "Failed to update provider config"}, status_code=400)


async def api_stats(req: Request) -> JSONResponse:
    """GET /api/stats — routing statistics + recent activity."""
    cfg = config_mgr.config
    return JSONResponse({
        "text": stats["text"],
        "multimodal": stats["multimodal"],
        "errors": stats["errors"],
        "uptime": int(time.monotonic() - start_time),
        "total": stats["text"] + stats["multimodal"] + stats["errors"],
        "text_backend": {"name": cfg.text.name, "model": cfg.text.model, "provider": cfg.text.provider, "url": cfg.text.messages_url},
        "multimodal_backend": {"name": cfg.multimodal.name, "model": cfg.multimodal.model, "provider": cfg.multimodal.provider, "url": cfg.multimodal.messages_url},
        "server": {"host": cfg.server.host, "port": cfg.server.port},
        "recent": list(reversed(activity_log)),
    })


async def api_presets(req: Request) -> JSONResponse:
    """GET /api/presets — available provider presets."""
    return JSONResponse(PROVIDER_PRESETS)


# ── Frontend static files ────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".js", ".css", ".html"}
MIME_MAP = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}
FRONTEND_NO_CACHE_HEADERS = {"Cache-Control": "no-store"}


async def serve_static(req: Request) -> Response:
    """Serve frontend static assets (.js, .css only)."""
    path = req.path_params.get("path", "")
    full = Path(__file__).parent / path
    if full.suffix.lower() not in ALLOWED_EXTENSIONS:
        return Response("Not Found", status_code=404)
    try:
        resolved = full.resolve()
        project_root = str(Path(__file__).parent.resolve())
        if not (str(resolved) + os.sep).startswith(project_root + os.sep):
            return Response("Forbidden", status_code=403)
        return FileResponse(
            str(resolved),
            media_type=MIME_MAP.get(full.suffix.lower()),
            headers=FRONTEND_NO_CACHE_HEADERS,
        )
    except (OSError, ValueError):
        return Response("Not Found", status_code=404)


STATUS_PAGE_PATH = Path(__file__).with_name("index.html")


def load_status_page() -> str:
    """Load the Web UI shell from disk."""
    try:
        return STATUS_PAGE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to load status page template: %s", exc)
        return "<!DOCTYPE html><html><body><h1>Status page unavailable</h1></body></html>"


# ── Management panel ──────────────────────────────────────────────

async def root_redirect(req: Request) -> RedirectResponse:
    return RedirectResponse(url="/status")


async def status_page(req: Request) -> HTMLResponse:
    return HTMLResponse(load_status_page(), headers=FRONTEND_NO_CACHE_HEADERS)


# ── App factory ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: Starlette):
    app.state.client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        yield
    finally:
        await app.state.client.aclose()


def create_app() -> Starlette:
    return Starlette(debug=False, routes=[
        Route("/", root_redirect, methods=["GET"]),
        Route("/v1/messages", messages, methods=["POST"]),
        Route("/status", status_page, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/api/config", api_config_get, methods=["GET"]),
        Route("/api/config", api_config_post, methods=["POST"]),
        Route("/api/config/provider", api_config_provider_post, methods=["POST"]),
        Route("/api/stats", api_stats, methods=["GET"]),
        Route("/api/presets", api_presets, methods=["GET"]),
        Route("/{path:path}", serve_static, methods=["GET"]),
    ], lifespan=lifespan)


# ── Entry point ─────────────────────────────────────────────────────


def _print_activate() -> None:
    """Print environment variables that users can copy-paste into their shell."""
    cfg = ConfigManager().config  # best-effort; defaults if config is invalid
    host, port = cfg.server.host, cfg.server.port
    lines = [
        f"export ANTHROPIC_BASE_URL='http://{host}:{port}'",
        "export ANTHROPIC_AUTH_TOKEN='cc-router'",
        "export ANTHROPIC_API_KEY=''",
    ]
    print("\n".join(lines))


def main() -> None:
    if "--activate" in sys.argv:
        _print_activate()
        return

    cfg = config_mgr.config
    log_level = cfg.logging.level.upper()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if cfg.logging.log_file:
        Path(cfg.logging.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(cfg.logging.log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    if not cfg.text.api_key or cfg.text.api_key.startswith("sk-YOUR"):
        logger.warning("Text backend API key not configured!")
    if not cfg.multimodal.api_key or cfg.multimodal.api_key.startswith("sk-YOUR"):
        logger.warning("Multimodal backend API key not configured!")

    logger.info("cc-router starting on %s:%d", cfg.server.host, cfg.server.port)
    logger.info("  Text backend : %s (%s)", cfg.text.model, cfg.text.name)
    logger.info("  Multimodal   : %s (%s)", cfg.multimodal.model, cfg.multimodal.name)
    logger.info("  Web UI       : http://%s:%d/status", cfg.server.host, cfg.server.port)

    app = create_app()

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
