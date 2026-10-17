# SearXNG Free-Search Docker Tool

This tool provides free, self-hosted web search via SearXNG for Skaldir.

## Services
- **searxng**: Self-hosted SearXNG instance on port 8080.
- **search**: Client runner providing programmatic query execution via `search.py`.

## Quick Start
```bash
# Start SearXNG
docker compose up -d searxng

# Run a test query
python3 search.py "top AI news today"
```
