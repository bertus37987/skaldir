# skaldir-tool-market-research

The Docker research tool of the `docker-tool-dashboard` feature (VAL-TOOL-001):
a market-research **snapshot** capability served by a small container — static
curated reference data, **no trades**, no live prices, no external fetches.

## What is here

| File | Purpose |
| --- | --- |
| `Containerfile` | the pinned build (base image by digest, unprivileged runtime user) |
| `service.py` | stdlib-only Python: `snapshot` CLI (keyed protocol) + `serve` dashboard |
| `dataset.json` | the static research dataset (5 symbols, version 2026.10) |
| `adapter-schema.json` | the declared fixture template incl. the service spec |

## Faces of the tool

1. **Mined call path (one-shot CLI)** — `docker run … snapshot --symbol AMD
   --detail brief` prints the container tool's keyed output protocol
   (`name=value` lines). The ToolBuilder verifies it against the declared
   golden fixture in `adapter-schema.json` before the tool may register.
2. **Warm dashboard (service spec)** — the adapter declares
   `service.argv = python3 /srv/service.py serve --port 8080`, `network:
   bridge`, `ports: {8080: 3400}`. `ensure_container_started` starts the ONE
   warm container with that network and port binding, so the interactive
   dashboard answers on `http://localhost:3400/`.

## Interactivity proof (two different actions → two different responses)

- `curl http://localhost:3400/api/snapshot?symbol=AMD` → the AMD snapshot JSON
- `curl http://localhost:3400/api/dataset` → the dataset inventory (different
  route, different response); the dashboard page itself offers both actions as
  buttons.

## Build & run

```sh
docker build -t skaldir-tool-market-research:v1 \
  -f docker/market-research-dashboard/Containerfile \
  docker/market-research-dashboard/

# one-shot (the call path the container adapter speaks)
docker run --rm --network none skaldir-tool-market-research:v1 \
  snapshot --symbol AMD --detail brief

# dashboard (started by the ToolBuilder via the declared service spec)
docker run -d --network bridge -p 3400:8080 skaldir-tool-market-research:v1 \
  serve --port 8080
```
