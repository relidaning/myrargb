# MyRargb

[Rargb](https://rargb.to/) is a resource sharing site. I often download movies and TVs there.
I felt it inconvenient for browsing it without a ratings, poster etc. So I build this app to scrape the information from imdb.
And it has many duplicate and obeletated items which are really annoyed me.

## Flow

1. **Crawl** — browses the rargb.to `/movies/` catalog via Playwright
2. **Extract** — uses a fine-tuned [T5-small](https://huggingface.co/google-t5/t5-small) model to extract clean item titles from noisy filenames (e.g., `The.Matrix.1999.1080p.BluRay.x264-YTS` → `The Matrix`)
3. **Deduplicate** — Bloom filter catches duplicate movies across different resolutions/sources
4. **Enrich** — fetches and match the item with: poster, rating and genre from [IMDb](https://www.imdb.com/)
5. **Browsing** — clean web UI with poster, rating...

## Usage

```bash
# Install dependencies
uv sync

# Run the app (Kafka + browser not required for UI-only use)
uv run python app.py

# Full stack with Kafka for async pipeline
docker compose up --build
```

Open `http://localhost:5000`. Click **Crawl RARGB!** to start an incremental crawl.

## Configuration

Copy `.env.example` to `.env` for local overrides. Edit the commented defaults in
[`config/defaults.toml`](config/defaults.toml), or use an optional local TOML file
and environment overrides. Paths resolve from the project root.

See [configuration, layout and technology map](docs/configuration.md) for
precedence, storage mounts, browsing without background jobs, and the code or
migration work needed to replace each component.

## Project layout

`app.py` launches the application in `src/`, including its Flask templates
and static assets. Runtime settings live in `config/`, maintenance commands in
`scripts/`, Docker assets in `docker/`, and persistent storage in ignored `data/`.
Application and training logs live in ignored `logs/`, mounted at `/app/logs` in
Docker. Root Compose files remain discoverable with `docker compose`.

- [Architecture and engineering reference](docs/architecture.md)
- [Recorded work items](docs/plans.md)

Trigger a crawl with `./scripts/send_msg_to_kafka.sh`. After this layout change,
rebuild/recreate the app container to apply its new source mounts; see the
[deployment details](docs/configuration.md#layout-and-deployment).
