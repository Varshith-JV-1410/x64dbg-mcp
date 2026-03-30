# x64dbg OpenAI Connector MCP Server

This folder contains a single MCP server entrypoint for ChatGPT connectors/apps.

It keeps the original project code unchanged and exposes:

- Full x64dbg tools from [src/x64dbg.py](src/x64dbg.py)
- Compatibility read-only `search(query: string)` and `fetch(id: string)` tools for connector retrieval-style flows

Compatibility tools return MCP tool results as a single `content` item of type `text` with JSON-encoded payloads, following OpenAI MCP guidance for connectors/company knowledge compatibility.

## What It Uses

- Upstream x64dbg HTTP bridge (default: `http://127.0.0.1:8888/`)
- FastMCP server over Streamable HTTP (default endpoint: `/mcp`)

## Run

From workspace root:

```powershell
python openai_connector_mcp/server.py --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp --x64dbg-url http://127.0.0.1:8888/
```

MCP endpoint URL:

- `http://127.0.0.1:8000/mcp`

This server now exposes both the full x64dbg toolset and compatibility `search`/`fetch` from one endpoint.

If tool metadata is cached, refresh connector metadata in ChatGPT settings so the latest tool list is reloaded.

For ChatGPT connector testing, expose it via HTTPS tunnel (for example ngrok):

```powershell
ngrok http 8000
```

Then use:

- `https://<your-subdomain>.ngrok.app/mcp`

### Host header note

This server disables FastMCP DNS rebinding host checks by default so HTTPS tunnels
like ngrok work out of the box.

If you want strict host/origin checks, enable them explicitly:

```powershell
python openai_connector_mcp/server.py --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp --strict-host-check --allow-host 127.0.0.1:* --allow-host localhost:* --allow-host <your-subdomain>.ngrok-free.app --allow-origin http://127.0.0.1:* --allow-origin http://localhost:* --allow-origin https://<your-subdomain>.ngrok-free.app
```

## Connect In ChatGPT (Developer Mode)

1. Open ChatGPT settings.
2. Go to Apps and Connectors.
3. Create a connector.
4. Set connector URL to your HTTPS `/mcp` endpoint.
5. Refresh metadata after server changes.

## Notes

- Compatibility `search` and `fetch` are marked read-only.
- Full x64dbg tools are provided directly from `src/x64dbg.py` and include both read and action tools.
- `search` returns ids/titles/urls.
- `fetch` returns id/title/text/url/metadata for one selected document.

## Troubleshooting: 406 on `/mcp`

If you open `/mcp` in a browser and see `406 Not Acceptable`, that is expected.

- Streamable HTTP MCP expects clients that send `Accept: text/event-stream`.
- Browsers usually send `text/html` when you paste a URL in the address bar.

Quick check:

```powershell
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/mcp
curl.exe -s -N -H "Accept: text/event-stream" -o NUL -w "%{http_code}" --max-time 2 http://127.0.0.1:8000/mcp
```

Expected status codes:

- first command: `406`
- second command: `200`
