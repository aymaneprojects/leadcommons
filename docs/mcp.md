# Use LeadCommons from Claude or ChatGPT (MCP)

LeadCommons ships an [MCP](https://modelcontextprotocol.io) server. Once connected, you can ask an AI assistant things like "find 10 software companies in Lyon with 11-50 people, find their CEOs, and export a CSV". The assistant calls LeadCommons tools; the data stays in your own LeadCommons database.

## Tools

| Tool | Kind | What it does |
| --- | --- | --- |
| `search_companies` | read | search the open company packs (q, countries, industries, size_ranges, keywords; 50 per page max) |
| `get_company` | read | full company record with sources and licences |
| `find_people` | write, crawls the web | read a company's public pages (by `company_id` or `domain`), save and return the people found |
| `search_people` | read | search people already saved locally |
| `find_email` | write, network | find and verify the likely work email of a saved person |
| `list_lists`, `list_members` | read | browse lists |
| `create_list`, `add_to_list` | write | build lists of people or companies |
| `export_list` | read | the list as CSV text |
| `opt_out` | write | add an email or domain to the suppression list |

Suppressed (opted-out) addresses and domains are never returned, stored or exported. Exported CSV cells that could run as spreadsheet formulas are neutralised. People data is never uploaded anywhere by LeadCommons; note that the AI assistant you connect does see what the tools return.

## Claude Desktop (local, stdio)

Install LeadCommons first ([install.md](install.md)), so that `leadcommons` is on your PATH. Edit `claude_desktop_config.json` (Settings, Developer, Edit config):

```json
{
  "mcpServers": {
    "leadcommons": {
      "command": "leadcommons",
      "args": ["mcp"]
    }
  }
}
```

If Claude Desktop cannot find the command, use its full path (`which leadcommons`, or `where leadcommons` on Windows). Restart Claude Desktop.

## Claude Code

```sh
claude mcp add leadcommons -- leadcommons mcp
```

From a source checkout: `claude mcp add leadcommons -- uv run --directory /path/to/leadcommons leadcommons mcp`.

## ChatGPT and other remote clients (HTTP)

Remote clients need a URL, so the server must be reachable from the internet over HTTPS, with a token.

1. Set a token in the server's `.env`: `LEADCOMMONS_MCP_TOKEN=$(openssl rand -hex 32)`.
2. Run the MCP server next to the app. With the Docker Compose deployment from [server.md](server.md), add a service that reuses the same image and environment:

   ```yaml
   mcp:
     image: ghcr.io/aymaneprojects/leadcommons:latest
     restart: unless-stopped
     command: ["leadcommons", "mcp", "--http", "--host", "0.0.0.0", "--port", "8001"]
     environment:
       LEADCOMMONS_MODE: server
       LEADCOMMONS_MCP_TOKEN: ${LEADCOMMONS_MCP_TOKEN:?set LEADCOMMONS_MCP_TOKEN in .env}
       LEADCOMMONS_PASSWORD_HASH: ${LEADCOMMONS_PASSWORD_HASH}
       LEADCOMMONS_SECRET_KEY: ${LEADCOMMONS_SECRET_KEY}
       LEADCOMMONS_DATABASE_URL: postgresql+psycopg://leadcommons:${POSTGRES_PASSWORD}@db:5432/leadcommons
     volumes:
       - app-data:/data
     depends_on:
       db:
         condition: service_healthy
   ```

3. Route `/mcp` to it in the `Caddyfile`:

   ```
   {$DOMAIN} {
   	handle /mcp* {
   		reverse_proxy mcp:8001
   	}
   	handle {
   		reverse_proxy app:8000
   	}
   }
   ```

4. `docker compose up -d`, then add the connector with URL `https://your-domain/mcp` and header `Authorization: Bearer <token>`. In ChatGPT: Settings, Connectors, developer mode, add a custom MCP connector. Claude (web/Desktop) accepts remote servers the same way where custom connectors are available. Clients that can only use OAuth (no custom header) are not supported yet.

Test: `curl -i https://your-domain/mcp` must answer `401`.

Command-line summary:

```sh
LEADCOMMONS_MCP_TOKEN=... leadcommons mcp --http --host 127.0.0.1 --port 8001
```

The HTTP transport is the streamable HTTP endpoint at `/mcp`. In server mode (or on a non-loopback `--host`) the command refuses to start without `LEADCOMMONS_MCP_TOKEN`.

## Security notes

- The token gives full access to your leads, lists and opt-out list. Use a long random value (32+ bytes), keep it in `.env` (mode 600), rotate it by changing it and restarting.
- Always put the HTTP transport behind HTTPS (Caddy does this). Never expose port 8001 directly.
- The token is separate from the web login password; the web app login does not protect `/mcp`.
- stdio mode has no network exposure: only the app that launched the process can talk to it.
- Tools that crawl (`find_people`, `find_email`) contact third-party sites and mail servers from your machine, politely and respecting robots.txt.
- Be careful what you paste into an assistant connected to LeadCommons: web pages can contain instructions aimed at the assistant. Review exports before using them.

## Limits

- `find_people` and `find_email` run synchronously and can take several seconds per company.
- Results per page are capped at 50; `list_members` returns at most 500 records with details (use `export_list` for all).
- No OAuth; static bearer token only.
