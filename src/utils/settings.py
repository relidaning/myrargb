"""Validated process configuration; importing this module performs no I/O to services."""

from dataclasses import dataclass, field, fields
from urllib.parse import urlsplit
from pathlib import Path
import os
import tomllib

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    debug: bool
    log_level: str
    log_path: Path
    database_path: Path
    bloom_path: Path
    model_name: str
    model_path: Path
    training_output_path: Path
    training_log_path: Path
    kafka_bootstrap_servers: str
    kafka_probe_timeout: float
    kafka_probe_cache_seconds: float
    background_jobs: bool
    tvmaze_base_url: str
    tvmaze_timeout: float
    tvmaze_rate_limit_delay: float
    tvmaze_poll_interval: int
    tvmaze_days_back: int
    tvmaze_days_forward: int
    chrome_url: str
    browser_proxy: str = field(repr=False)
    browser_headless: bool
    browser_navigation_timeout: float
    browser_challenge_wait: float
    browser_diagnostics_path: Path


def load_settings(environ=None) -> Settings:
    if environ is None:
        load_dotenv(PROJECT_ROOT / '.env', override=False)
        environ = os.environ
    with (PROJECT_ROOT / 'config/defaults.toml').open('rb') as source:
        values = tomllib.load(source)
    selected = environ.get('MYRARGB_CONFIG')
    if selected:
        path = Path(selected).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        with path.open('rb') as source:
            overrides = tomllib.load(source)
        unknown = overrides.keys() - values.keys()
        if unknown:
            raise ValueError(f"Unknown configuration settings: {', '.join(sorted(unknown))}")
        values.update(overrides)

    aliases = {'debug': 'DEBUG', 'log_level': 'LOGGER_LEVEL', 'chrome_url': 'CHROME_URL'}
    for field in fields(Settings):
        name, kind = field.name, field.type
        key = f'MYRARGB_{name.upper()}'
        raw = environ.get(key, environ.get(aliases.get(name, ''), values[name]))
        try:
            if kind is bool:
                if isinstance(raw, str) and raw.lower() in ('true', 'false'):
                    raw = raw.lower() == 'true'
                if type(raw) is not bool:
                    raise ValueError('expected true or false')
            elif kind in (int, float):
                if isinstance(raw, bool) or (kind is int and isinstance(raw, float)):
                    raise ValueError('expected a number of the declared type')
                raw = kind(raw)
                minimum = 0 if name in ('tvmaze_rate_limit_delay', 'tvmaze_days_back', 'tvmaze_days_forward', 'browser_challenge_wait') else 1e-9
                if not minimum <= raw < float('inf'):
                    raise ValueError('outside supported range')
                if name == 'port' and raw > 65535:
                    raise ValueError('expected port in 1..65535')
            elif name == 'browser_proxy':
                if not isinstance(raw, str):
                    raise ValueError('expected proxy URL or empty string')
                raw = raw.strip()
                if raw:
                    proxy = urlsplit(raw)
                    if (proxy.scheme not in ('http', 'https', 'socks5') or not proxy.hostname
                            or proxy.path not in ('', '/') or proxy.query or proxy.fragment):
                        raise ValueError('invalid proxy URL')
                    proxy.port  # Validate an optional port without printing credentials.
            else:
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError('expected a nonempty string')
                if kind is Path:
                    raw = Path(raw).expanduser()
                    if not raw.is_absolute():
                        raw = PROJECT_ROOT / raw
                elif name == 'log_level':
                    raw = raw.upper()
                    if raw not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
                        raise ValueError('unsupported log level')
            values[name] = raw
        except (ValueError, TypeError, OverflowError):
            # Never echo a resolved value; an endpoint may contain credentials.
            raise ValueError(f'Invalid setting {name} ({key})') from None
    return Settings(**values)


settings = load_settings()
