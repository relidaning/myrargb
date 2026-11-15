"""Validated process configuration; importing this module performs no I/O to services."""

from dataclasses import dataclass, field, fields
from urllib.parse import urlsplit
from pathlib import Path
import os
import tomllib

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
    prediction_models_path: Path
    prediction_max_chars: int
    prediction_max_input_tokens: int
    training_output_path: Path
    training_log_path: Path
    training_split_path: Path
    training_seed: int
    training_validation_fraction: float
    training_epochs: int
    training_learning_rate: float
    training_weight_decay: float
    training_batch_size: int
    training_early_stopping_patience: int
    training_max_length: int
    training_num_beams: int
    generation_min_new_tokens: int
    kafka_bootstrap_servers: str
    kafka_probe_timeout: float
    kafka_probe_cache_seconds: float
    imdb_workers: int
    kafka_max_poll_interval_ms: int
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
    crawl_page_attempts: int
    crawl_page_retry_delay: float
    browser_challenge_wait: float
    browser_diagnostics_path: Path
    browser_mode: str
    browser_cdp_url: str
    browser_profile_path: Path
    browser_executable: str
    browser_session_display: str


def load_settings(environ=None) -> Settings:
    if environ is None:
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
                minimum = 0 if name in ('tvmaze_rate_limit_delay', 'tvmaze_days_back', 'tvmaze_days_forward', 'browser_challenge_wait', 'training_seed', 'training_weight_decay', 'crawl_page_retry_delay', 'generation_min_new_tokens') else 1e-9
                if not minimum <= raw < float('inf'):
                    raise ValueError('outside supported range')
                if name == 'training_validation_fraction' and not 0 < raw < 1:
                    raise ValueError('expected fraction between 0 and 1')
                if name == 'training_seed' and raw >= 2 ** 32:
                    raise ValueError('expected seed below 2**32')
                if name == 'training_max_length' and raw < 2:
                    raise ValueError('expected at least two tokens')
                if name == 'kafka_max_poll_interval_ms' and raw > 86400000:
                    raise ValueError('expected at most 86400000 milliseconds')
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
                elif name == 'browser_mode':
                    if raw not in ('managed', 'session'):
                        raise ValueError('expected managed or session')
                elif name == 'browser_session_display':
                    if raw not in ('visible', 'background', 'headless'):
                        raise ValueError('expected visible, background or headless')
                elif name == 'browser_cdp_url':
                    endpoint = urlsplit(raw)
                    if (endpoint.scheme != 'http' or endpoint.hostname not in ('localhost', '127.0.0.1', '::1')
                            or not endpoint.port or endpoint.username or endpoint.password
                            or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment):
                        raise ValueError('expected loopback HTTP debugging endpoint with explicit port')
                elif name == 'log_level':
                    raw = raw.upper()
                    if raw not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
                        raise ValueError('unsupported log level')
            values[name] = raw
        except (ValueError, TypeError, OverflowError):
            # Never echo a resolved value; an endpoint may contain credentials.
            raise ValueError(f'Invalid setting {name} ({key})') from None
    if values['generation_min_new_tokens'] >= min(65, values['training_max_length']):
        raise ValueError('Invalid setting generation_min_new_tokens: must fit prediction and evaluation length limits')
    return Settings(**values)


settings = load_settings()
