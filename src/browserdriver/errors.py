"""Safe browser/crawler failures that can be surfaced through the HTTP API."""

import re


class BrowserFetchError(RuntimeError):
    code = 'upstream_error'

    def __init__(self, message, *, diagnostic_id=None, ray_id=None):
        super().__init__(message)
        self.diagnostic_id = diagnostic_id
        # Upstream response headers must not inject arbitrary text into logs/UI.
        self.ray_id = ray_id if ray_id and re.fullmatch(r'[A-Za-z0-9-]{1,80}', ray_id) else None


class BrowserChallengeError(BrowserFetchError):
    code = 'browser_challenge'


class UnexpectedPageError(BrowserFetchError):
    code = 'unexpected_page'


def challenge_reason(html, headers=None):
    """Distinguish interstitials from pages merely loading Cloudflare assets."""
    if (headers or {}).get('cf-mitigated', '').lower() == 'challenge':
        return 'Cloudflare verification'
    lower = html.lower()
    if any(marker in lower for marker in (
        'id="challenge-form"', "id='challenge-form'", '_cf_chl_opt',
        'cf-chl-widget', 'cf-chl-error', '<title>just a moment',
    )):
        return 'Cloudflare verification'
    if '/cdn-cgi/challenge-platform/' in lower and any(marker in lower for marker in (
        'just a moment', 'verify you are human', 'checking your browser',
        'performing security verification',
    )):
        return 'Cloudflare verification'
    if 'challenge-container' in lower or ('awswaf' in lower and 'captcha' in lower):
        return 'Browser verification'
    return None
