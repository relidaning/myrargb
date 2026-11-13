# Configuration and technology map

`config/defaults.toml` is the authoritative, commented runtime configuration.
Precedence, highest first:

1. `MYRARGB_<UPPERCASE_KEY>` environment variables.
2. Legacy environment names `DEBUG`, `LOGGER_LEVEL`, `CHROME_URL` for their
   corresponding settings (the prefixed name wins if both exist).
3. Optional TOML file selected with `MYRARGB_CONFIG` (partial overrides allowed).
4. Shipped `config/defaults.toml`.

The app does not load `.env`, and Compose does not use an `env_file`.
Edit `config/defaults.toml` for shared settings; optional process environment
overrides remain supported. Keep any future credentials in the process
environment. `config/local.toml` is also ignored. Unknown TOML keys and invalid values fail at
startup with the setting name; resolved values are not printed. Settings are
loaded once per process, so changes require a restart.

Relative configuration and storage paths resolve against the project root,
including when launched from another working directory. No mutable data is moved
by this change. Existing database, Bloom filter, model and training checkpoint
paths retain their locations. New application logs go to `logs/YYYY-MM-DD.log` (UTC);
The historical root `app.log` was previously moved to `logs/app.log`. Training logs go to `logs/training`.
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
  Root `docker-compose.yml` defines the stack and source mounts. The image copies
  only the launcher, README, `src/` and `config/` after dependency installation.
  Source bind mounts use `create_host_path: false` so missing paths fail instead
  of becoming empty directories. After changing mounts, recreate the app container
  (`docker compose up -d --build --no-deps myrargb`); restarting an old container
  retains its old mount paths.
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

Compose mounts `config/defaults.toml` through `./config`; the app reads it
directly. Browser mode, display preference, proxy and worker count live there.
Compose no longer injects a separate log-level or Selenium URL override.
Its published port defaults to 5000. If changing `port` in TOML, pass the same
`MYRARGB_PORT` value to Compose so the port mapping matches; Compose cannot parse
TOML itself. Keep `host = "0.0.0.0"` for container access. A selected config file
must exist inside the container; Compose mounts `config/` for this.
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
The root Compose files, dependency manifests and root launch command keep
their existing discovery locations. The former `.env` browser settings now live
in `config/defaults.toml`; `.env` was removed and is no longer loaded. There are no deferred versioned-file moves.


## IMDb concurrency and throughput

`imdb_workers` (default 3, environment `MYRARGB_IMDB_WORKERS`) controls the
bounded batch executor inside the IMDb consumer and the shared browser pool.
Three messages from the same partition can run concurrently. This is necessary
when the backlog is concentrated in one partition: extra Kafka group members
alone cannot share that partition. Other consumer pipelines retain one worker.
This changes completion order; IMDb jobs are independent per-row updates, with
existing title-current checks discarding results after a concurrent title edit.

The consumer fetches at most one batch of this size and commits synchronously
in delivery order after callbacks return. A crash may replay completed work,
but cannot commit past an unfinished earlier callback. Caught handler errors
remain terminal attempts under the existing handler policy and are recorded as
failures; this change does not introduce a dead-letter queue or retry policy.
Browser retries count within one callback, while Kafka redeliveries count as
new processing attempts. No offsets are reset and no partitions are changed.
`kafka_max_poll_interval_ms` (default 900000) must exceed a worst-case batch
including browser retries; increasing worker count also increases browser
memory and upstream request load. UI lookups lease from the same browser pool.
Each managed slot owns its browser; session mode uses separate driver connections
and tabs in the dedicated Chrome session. Changing concurrency requires an app
restart, not a broker restart or storage migration.

Application and completed-job logs default to `logs/YYYY-MM-DD.log` in UTC,
append across runs, and roll over at midnight. An explicit legacy `log_path`
keeps its selected filename; use `logs/%Y-%m-%d.log` for daily rollover. Job
measurements are logged independently of the console log level, without message
bodies, and include group, outcome and callback duration. Retention is manual;
missing days and bounded scans are identified in the report.

Generate a local report (no new server or public endpoint):

```sh
.venv/bin/python scripts/consumer_report.py --days 1 --output /tmp/imdb-throughput.html
```

Open that file in a browser. It shows hourly UTC job volume, failure rate,
mean duration and jobs/second, with freshness and coverage information. Days
are limited to 31 and scans to 64 MiB; these metrics measure completed attempts,
not unique movies or Kafka lag. A batch starts no new work until its slowest
callback and ordered commits finish, so throughput will be below the ideal
worker-count multiplier when lookup times vary.


## Running with the host Chrome session

The current `session` mode connects to the host's dedicated Chrome at
`127.0.0.1:9222`. Run the app on the host so that loopback reaches that session;
Docker requires an explicitly configured debugging tunnel. The host also resolves
`kafka` to the local broker, matching `kafka_bootstrap_servers` in TOML.

A transient user service can manage the app and its three-worker IMDb executor:

```sh
systemd-run --user --unit=myrargb --working-directory="$PWD" \
  --property=Restart=on-failure --property=RestartSec=5 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=MALLOC_ARENA_MAX=2 \
  "$PWD/.venv/bin/python" "$PWD/app.py"
systemctl --user restart myrargb.service
systemctl --user stop myrargb.service
```

Start from the project root. This service reads `config/defaults.toml` directly;
no `.env` is involved. It is transient and does not install a boot-time unit.
The existing Chrome session must remain available. App and completed-job logs
continue to append to the configured daily log files.
