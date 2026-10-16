# komoot-mcp

An MCP server that lets Claude search your Komoot tours by distance, start point, surface and scenery, download them as GPX, and build new gravel routes by chaining the best gravel sections of tours you already saved.

## Preview

```
You:    Find me a 50 km gravel loop within 30 minutes of Amsterdam,
        at least 60% unpaved, and tell me what is scenic along the way.

Claude: I checked 14 planned tours near Amsterdam and pulled surface data for the
        closest matches.

        1. Vechtplassen gravel loop: 48 km, 71% gravel (compacted and dirt),
           starts in Weesp (25 min drive). Passes 3 saved highlights:
           Ankeveense Plassen, Fort Uitermeer, Naardermeer boardwalk.
        2. Diemerbos and IJmeer dike: 52 km, 63% gravel, starts at Diemen.
           Exposed dike section for 9 km, watch the wind.
        3. Spaarnwoude polders: 55 km, 58% gravel, just under your target.

        Want the GPX for route 1, or should I build a new loop that combines
        the gravel sections of all three?
```

## What it does

1. Lists your planned and recorded tours by sport, location and radius.
2. Returns a tour's surface breakdown (gravel, asphalt, dirt) and way types, so a ride can be scored by how much is unpaved.
3. Downloads any tour as GPX, or uploads a GPX as a new planned tour.
4. Finds highlights near a point, and reads the ones you bookmarked.
5. Builds a new route from the gravel sections of several saved tours, joined by short OSRM cycling connectors, as a GPX track plus a waypoint list for the Komoot planner.

## Why this exists

I ride gravel around Amsterdam and wanted to plan rides from inside Claude instead of clicking through maps. Komoot has the surface data; this server makes it available as tools. It is the same habit as the work tools on my profile: when a routine has a data source, give the assistant the tools and let it do the lookups.

## Requirements

- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/)
- A Komoot account. Credentials go in environment variables or the system keychain (see Install); they are never written to disk by this project.
- Claude Code or Claude Desktop

## Install

```bash
git clone https://github.com/nunomnferreirapt/komoot-mcp.git
cd komoot-mcp
uv sync
uv run pytest
```

Register the server. Claude Code:

```bash
claude mcp add -s user komoot-mcp \
  -e KOMOOT_EMAIL=you@example.com -e KOMOOT_PASSWORD=your-password \
  -- uv --directory /absolute/path/to/komoot-mcp run komoot-mcp
```

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "komoot-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/komoot-mcp", "run", "komoot-mcp"],
      "env": {
        "KOMOOT_EMAIL": "you@example.com",
        "KOMOOT_PASSWORD": "your-password"
      }
    }
  }
}
```

If you would rather not keep the password in a config file, leave out the `env` block and ask Claude once to run the `setup_credentials` tool; it stores both values in the system keychain under the service `komoot-mcp`.

Optional variables: `KOMOOT_MCP_ROUTES_DIR` (where built routes are saved, default `~/komoot-mcp-routes`) and `KOMOOT_MCP_CACHE_DIR` (parsed tour cache, default `~/.cache/komoot-mcp`).

## Usage

Ask in plain language: "list my planned tours near Utrecht", "how much of tour 123456 is gravel", "build a 60 km gravel loop from Weesp using my saved tours". The tools are `list_tours`, `get_tour_details`, `get_tour_segments`, `download_tour_gpx`, `upload_gpx_to_komoot`, `get_highlights`, `get_saved_highlights`, `build_combined_route` and `setup_credentials`. To run the server by hand: `uv run komoot-mcp` (it speaks MCP over stdio).

## How it works

A FastMCP app over Komoot's web API. It signs in with Basic Auth, keeps the short-lived session token in memory and retries once on a 401. Tour responses carry surface and way-type spans indexed into the coordinate array; the client turns these into percentages. The route combiner splits each tour into single-surface segments, groups them into runs that move forward along the start-to-end corridor, scores runs by gravel length and nearby saved highlights, and joins the chosen runs with OSRM's public cycling router. Parsed segments are cached on disk, so a rebuild only fetches tours it has not seen.

## Repository layout

```
src/komoot_mcp/
  __main__.py        MCP tools and server entry point
  auth.py            credentials and Basic Auth session
  client.py          Komoot API calls and surface parsing
  route_combiner.py  segment splitting, run selection, OSRM connectors, GPX output
  cache.py           disk cache for parsed segments and highlights
tests/               pytest, no network
pyproject.toml       uv project, console script komoot-mcp
```

## License

MIT, see `LICENSE`.

Built by [Nuno Ferreira](https://www.linkedin.com/in/nunomnferreira).
