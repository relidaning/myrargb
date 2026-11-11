# Configuration and technology map

`config/defaults.toml` is the authoritative, commented runtime configuration.
Precedence, highest first:

1. `MYRARGB_<UPPERCASE_KEY>` environment variables.
2. Legacy environment names `DEBUG`, `LOGGER_LEVEL`, `CHROME_URL` for their
   corresponding settings (the prefixed name wins if both exist).
3. Optional TOML file selected with `MYRARGB_CONFIG` (partial overrides allowed).
4. Shipped `config/defaults.toml`.

The project-root `.env` fills missing process environment values before settings
are loaded. Keep credentials in environment variables or the ignored `.env`.
`config/local.toml` is also ignored. Unknown TOML keys and invalid values fail at
startup with the setting name; resolved values are not printed. Settings are
loaded once per process, so changes require a restart.

Relative configuration and storage paths resolve against the project root,
including when launched from another working directory. No mutable data is moved
by this change. Existing database, Bloom filter, model and training checkpoint
paths retain their locations. New application logs go to `logs/app.log`;
the former root `app.log` now lives there. Training logs go to `logs/training`.
The DB and Bloom filter create their parent directories when needed.

For browsing without Kafka consumers or scheduled network work:

```sh
MYRARGB_BACKGROUND_JOBS=false uv run python app.py
```

Model dependencies and weights load only on the first prediction/training call.
Kafka producer connections are also lazy. Broker reachability is cached for
`kafka_probe_cache_seconds`, then checked again so an outage can recover without
restarting the app. This cache does not guarantee individual message delivery.
Flask's debug reloader is disabled to avoid launching duplicate consumer threads.

## Layout and deployment

- Root `app.py`: compatibility launcher; `src/app.py`: Flask routes and
  process startup. The launcher anchors `src/` on the import path, so the existing
  `uv run python app.py` command still works without a package build step.
- `src/`: application code directly, without a project-named wrapper. Crawlers, Kafka handlers and the workflow
  enum now live here alongside the existing `db/`, `db_model/`, `model/`,
  `browserdriver/` and `utils/` components. Internal imports resolve directly from `src/`.
- `src/templates/` and `src/static/`: Flask's colocated template
  and static directories, discovered relative to the application module.
- `scripts/send_msg_to_kafka.sh`: manual crawl trigger; reads the same host/port
  settings as the server. It works from another working directory and uses the
  project `.venv/bin/python` unless `MYRARGB_PYTHON` selects another interpreter.
- `config/`: versioned runtime defaults; `tests/`: isolated regression checks.
- `docs/architecture.md`: detailed architecture/engineering reference;
  `docs/plans.md`: consolidated work lists. Recorded historical statuses are
  retained, not reverified.
- `docker/Dockerfile` and `docker/entrypoint.sh`: image build/startup.
  Root `docker-compose.yml` defines the stack and source mounts.
- `data/`: ignored runtime storage. Compose mounts host `./data` at container
  `/app/data`; default storage paths still resolve there in the container.
- `logs/`: ignored application/training logs, mounted from host `./logs` at
  container `/app/logs`. Existing tool-managed logs and caches retain their own
  locations; Kafka transaction logs are storage data, not diagnostic log files.

Compose mounts host `./src` at `/app/src`, `./config` at `/app/config`, and the
root launcher at `/app/app.py`, all read-only. Image
runtime files and its venv remain separate. The new mount layout requires an
image rebuild and container recreation before deployment; a restart alone keeps
the old container's mounts. This reorganization does not perform that deployment.

Compose reads `.env` into the app container. Its published port and application
port both use `MYRARGB_PORT` from `.env` (default 5000). Set this environment value
when changing the Docker port; changing only TOML cannot change Compose's port
mapping. Keep `host = "0.0.0.0"` for container access. A selected config file must
exist inside the container; Compose mounts `config/` for this.
Existing proxy and GPU deployment choices remain in `docker-compose.yml`.
Dependency versions stay in `pyproject.toml` / `uv.lock`; browser image versions
stay in `docker/Dockerfile`. Dependency or image changes require a rebuild.

| Role | Implementation / authoritative source | Replacement contract |
| --- | --- | --- |
| HTTP UI | Flask/Jinja; `src/app.py`, `src/templates/`, native dependency manifests | Host/port/debug are value changes; another server/framework needs entrypoint or route/template code changes. |
| Persistent records | SQLite; `src/db/db.py`, `database_path` | Path changes select another database; copying live storage or changing DB engine requires an explicit migration and repository adapter work. |
| Dedup hint | `bloom-filter`; `src/utils/bloom_utils.py`, `bloom_path` | The binary file format is library-specific. A replacement needs rebuilding the filter from stored titles; a path change alone does not migrate it. |
| Message transport | Confluent Kafka client; `src/utils/kafka_utils.py`, `kafka_bootstrap_servers`, Compose | Broker address is configurable; a different queue needs producer/consumer adapters and an offset/backlog migration plan. Topic/group names remain protocol constants in code. |
| Title extraction | Transformers T5; `src/model/model.py`, `model_name`, `model_path` | Model ID/path must remain tokenizer and seq2seq compatible. Another architecture needs inference/training code; retraining produces new weights. Local weights take precedence when the directory exists. |
| Browser scraping | Playwright default, optional Selenium; `src/browserdriver/driver.py`, Docker image | `chrome_url` applies only to Selenium. Changing the default driver requires factory code and matching browser dependencies; site changes require parser updates in `src/crawler.py`. |
| TV data | TVMaze JSON API; `src/tv_crawler.py`, `tvmaze_*` settings | Base URL must expose the same endpoints and schema; a different provider needs an adapter and a plan for existing TVMaze IDs/cache data. |

Browser routing, navigation/challenge waits and diagnostics are configurable; see
[crawler troubleshooting](crawler-troubleshooting.md). Browser identity uses
Playwright defaults. Page sizes, generation parameters, site parsers, topic/group identifiers, and schema invariants remain in code. They have
no supported configuration replacement contract in this change. Kafka storage
and GPU/proxy deployment are not migrated or redeployed by changing these files.

Run checks without contacting providers or touching live storage:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## File move record

| Former location | Current location |
| --- | --- |
| Root implementation `app.py` | `src/app.py`; root `app.py` is a thin launcher |
| `crawler.py`, `tv_crawler.py`, `handler.py`, `workflow.py` | Same filenames under `src/` |
| `db/`, `db_model/`, `model/`, `browserdriver/`, `utils/` | Same component directories under `src/` |
| `templates/`, `static/` | `src/templates/`, `src/static/` |
| `entrypoint.sh` | `docker/entrypoint.sh` |
| `send_msg_to_kafka.sh` | `scripts/send_msg_to_kafka.sh` |
| Detailed root `CLAUDE.md` | `docs/architecture.md` |
| `TODOS.md`, `todo.md` | Consolidated into `docs/plans.md`, retaining both lists |

Application and training log destinations are now `logs/`; the old root `app.log`
was moved to `logs/app.log`. No database files, Bloom filters, model weights or
running-service volumes moved.
The root Compose files, dependency manifests, `.env` and root launch command keep
their existing discovery locations. There are no deferred versioned-file moves.
