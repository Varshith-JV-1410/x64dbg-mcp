import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations

DEFAULT_X64DBG_URL = "http://127.0.0.1:8888/"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_SEARCH_RESULTS = 20
ANALYZE_INSTR_COUNT = 25
LOCAL_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

x64dbg_server_url = os.getenv("X64DBG_URL", DEFAULT_X64DBG_URL)
x64dbg_tools_module: Any = None

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    openWorldHint=False,
    destructiveHint=False,
    idempotentHint=True,
)


def _env_port() -> int:
    value = os.getenv("MCP_PORT", str(DEFAULT_PORT))
    try:
        return int(value)
    except ValueError:
        return DEFAULT_PORT


def _load_x64dbg_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    import x64dbg as x64dbg_module  # type: ignore

    return x64dbg_module


def _normalized_x64dbg_url() -> str:
    if x64dbg_server_url.endswith("/"):
        return x64dbg_server_url
    return f"{x64dbg_server_url}/"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def safe_get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    if params is None:
        params = {}

    url = f"{_normalized_x64dbg_url()}{endpoint.lstrip('/')}"

    try:
        response = requests.get(url, params=params, timeout=15)
    except Exception as exc:
        return {"error": "request_failed", "detail": str(exc)}

    response.encoding = "utf-8"
    if not response.ok:
        return {
            "error": "http_error",
            "status": response.status_code,
            "detail": response.text.strip(),
        }

    try:
        return response.json()
    except ValueError:
        return response.text.strip()


def _module_url(module: dict[str, Any]) -> str:
    module_path = str(module.get("path") or "").strip()
    if module_path:
        try:
            path_obj = Path(module_path)
            if path_obj.is_absolute():
                return path_obj.as_uri()
        except Exception:
            pass

    name = str(module.get("name") or "unknown")
    base = str(module.get("base") or "")
    return f"https://x64dbg.local/module/{quote(name)}?base={quote(base)}"


def _session_documents() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []

    if x64dbg_tools_module is not None:
        debug_status = x64dbg_tools_module.GetDebugStatus()
        context = x64dbg_tools_module.GetContext()
        analysis = x64dbg_tools_module.Analyze(count=ANALYZE_INSTR_COUNT)
        modules = x64dbg_tools_module.GetModuleList()
    else:
        debug_status = {
            "debugging": safe_get("Is_Debugging"),
            "running": safe_get("IsDebugActive"),
        }
        context = safe_get("GetContext")
        analysis = safe_get("Analyze", {"count": str(ANALYZE_INSTR_COUNT)})
        modules = safe_get("GetModuleList")

    module_count = len(modules) if isinstance(modules, list) else 0
    overview_payload = {
        "debug_status": debug_status,
        "module_count": module_count,
    }
    docs.append(
        {
            "id": "session:overview",
            "title": "x64dbg session overview",
            "url": "https://x64dbg.local/session/overview",
            "text": _json_text(overview_payload),
            "metadata": {"source": "x64dbg", "kind": "session_overview"},
        }
    )

    docs.append(
        {
            "id": "session:context",
            "title": "x64dbg CPU context",
            "url": "https://x64dbg.local/session/context",
            "text": _json_text(context),
            "metadata": {"source": "x64dbg", "kind": "cpu_context"},
        }
    )

    docs.append(
        {
            "id": "session:analysis",
            "title": "x64dbg disassembly analysis",
            "url": "https://x64dbg.local/session/analysis",
            "text": _json_text(analysis),
            "metadata": {"source": "x64dbg", "kind": "disassembly"},
        }
    )

    if isinstance(modules, list):
        for module in modules:
            if not isinstance(module, dict):
                continue

            name = str(module.get("name") or "unknown")
            base = str(module.get("base") or "")
            module_id = f"module:{name}:{base}"
            docs.append(
                {
                    "id": module_id,
                    "title": f"module {name}",
                    "url": _module_url(module),
                    "text": _json_text(module),
                    "metadata": {
                        "source": "x64dbg",
                        "kind": "module",
                        "name": name,
                        "base": base,
                    },
                }
            )

    return docs


def _register_search_fetch_compat_tools(mcp: Any) -> None:
    try:
        existing = {tool.name for tool in mcp._tool_manager.list_tools()}
    except Exception:
        existing = set()

    if "search" not in existing:

        @mcp.tool(
            name="search",
            title="Search x64dbg",
            description=(
                "Search x64dbg session documents and return ids for follow-up fetch calls."
            ),
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def search(query: str) -> CallToolResult:
            documents = _session_documents()
            needle = (query or "").strip().lower()

            if not needle:
                matched = documents[:MAX_SEARCH_RESULTS]
            else:
                scored: list[tuple[int, dict[str, Any]]] = []
                for doc in documents:
                    haystack = f"{doc.get('title', '')}\n{doc.get('text', '')}".lower()
                    if needle in haystack:
                        scored.append((haystack.count(needle), doc))

                scored.sort(key=lambda item: (-item[0], item[1].get("title", "")))
                matched = [doc for _, doc in scored[:MAX_SEARCH_RESULTS]]

            payload = {
                "results": [
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "url": doc["url"],
                    }
                    for doc in matched
                ]
            }

            return CallToolResult(
                content=[TextContent(type="text", text=_json_text(payload))],
            )

    if "fetch" not in existing:

        @mcp.tool(
            name="fetch",
            title="Fetch x64dbg document",
            description="Fetch one x64dbg session document by id.",
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def fetch(id: str) -> CallToolResult:
            documents = _session_documents()
            document = next((doc for doc in documents if doc.get("id") == id), None)

            if document is None:
                not_found = {
                    "id": id,
                    "title": "Document not found",
                    "text": f"No x64dbg document exists for id: {id}",
                    "url": "https://x64dbg.local/not-found",
                    "metadata": {"source": "x64dbg", "error": "not_found"},
                }
                return CallToolResult(
                    content=[TextContent(type="text", text=_json_text(not_found))],
                    isError=True,
                )

            payload = {
                "id": document["id"],
                "title": document["title"],
                "text": document.get("text", ""),
                "url": document["url"],
                "metadata": document.get("metadata", {}),
            }

            return CallToolResult(
                content=[TextContent(type="text", text=_json_text(payload))],
            )


def _configure_transport_security(
    mcp: Any,
    strict_host_check: bool,
    allow_hosts: list[str],
    allow_origins: list[str],
) -> None:
    if not strict_host_check:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        return

    hosts = allow_hosts[:] if allow_hosts else LOCAL_ALLOWED_HOSTS.copy()
    origins = allow_origins[:] if allow_origins else LOCAL_ALLOWED_ORIGINS.copy()

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified ChatGPT-compatible MCP server for x64dbg"
    )
    parser.add_argument(
        "--x64dbg-url",
        default=os.getenv("X64DBG_URL", DEFAULT_X64DBG_URL),
        help="x64dbg HTTP bridge URL (default: http://127.0.0.1:8888/)",
    )
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio", "sse"],
        default=os.getenv("MCP_TRANSPORT", "streamable-http"),
        help="MCP transport to run",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", DEFAULT_HOST),
        help="Host to bind",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_port(),
        help="Port to bind",
    )
    parser.add_argument(
        "--path",
        default=os.getenv("MCP_PATH", "/mcp"),
        help="Streamable HTTP path (default: /mcp)",
    )
    parser.add_argument(
        "--strict-host-check",
        action="store_true",
        help="Enable DNS rebinding protection (disabled by default for tunnel/dev use).",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Allowed Host header value (repeatable) when --strict-host-check is enabled.",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Allowed Origin header value (repeatable) when --strict-host-check is enabled.",
    )
    return parser.parse_args()


def main() -> None:
    global x64dbg_server_url
    global x64dbg_tools_module

    args = _parse_args()
    x64dbg_server_url = args.x64dbg_url

    x64dbg_tools_module = _load_x64dbg_module()
    x64dbg_tools_module.set_x64dbg_server_url(args.x64dbg_url)

    mcp = x64dbg_tools_module.mcp
    _register_search_fetch_compat_tools(mcp)

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = (
        args.path if args.path.startswith("/") else f"/{args.path}"
    )

    mcp.settings.stateless_http = True
    mcp.settings.json_response = True

    _configure_transport_security(
        mcp=mcp,
        strict_host_check=args.strict_host_check,
        allow_hosts=args.allow_host,
        allow_origins=args.allow_origin,
    )

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
