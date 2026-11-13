"""UTC daily logs and completed-job measurements without message payloads."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from threading import Lock

from utils.settings import settings


class DailyFileHandler(logging.FileHandler):
    def __init__(self, path):
        self.pattern = str(path)
        super().__init__(self._filename(), encoding='utf-8', delay=False)

    def _filename(self):
        path = Path(datetime.now(timezone.utc).strftime(self.pattern))
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path.resolve())

    def emit(self, record):
        filename = self._filename()
        if filename != self.baseFilename:
            self.close()
            self.baseFilename = filename
        super().emit(record)


def utc_formatter():
    formatter = logging.Formatter('%(asctime)sZ [%(levelname)s] %(name)s: %(message)s')
    formatter.converter = time.gmtime
    return formatter


_metrics_lock = Lock()
metrics = logging.getLogger('myrargb.jobs')
metrics.setLevel(logging.INFO)
metrics.propagate = False


def record_job(group, outcome, duration):
    if not metrics.handlers:
        with _metrics_lock:
            if not metrics.handlers:
                handler = DailyFileHandler(settings.log_path)
                handler.setFormatter(utc_formatter())
                metrics.addHandler(handler)
    metrics.info('job_completed %s', json.dumps({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'group': group, 'outcome': outcome, 'duration_seconds': duration,
    }, separators=(',', ':')))
