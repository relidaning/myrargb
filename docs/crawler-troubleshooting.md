# Checking catalog access

Run one fetch from the project root before starting a full crawl:

```sh
.venv/bin/python scripts/check_crawler.py
```

The command uses the exact catalog URL from `RargbCrawler.page_url()` and the
application's browser settings. It does not start Kafka consumers, load the ML
model, or write catalog records. It makes a real request when you run it.
It also works from another working directory if invoked with absolute paths.
`--url` selects a different page for diagnosis.

Success prints `Catalog accessible` and a row count. Failure exits with code 1,
prints the reason, and, when available, identifies JSON/screenshot files in
`logs/browser/`. You can also click **Crawl RARGB!** in the app: a blocked request
now returns HTTP 502 and displays the server's error instead of claiming success.
An unreachable Kafka broker returns HTTP 503; the single-fetch command avoids
that dependency entirely.

## Browser configuration

Edit the browser settings in [`config/defaults.toml`](../config/defaults.toml).
The same settings accept `MYRARGB_...` environment overrides:

| Setting | Purpose |
| --- | --- |
| `browser_proxy` | Empty selects a direct connection. Otherwise pass a proxy URL explicitly to Playwright. The browser does not derive this setting from `HTTP_PROXY` or `HTTPS_PROXY`. |
| `browser_headless` | Set false to inspect the browser locally, on a machine with a working graphical display. A headed browser alone does not guarantee challenge access. |
| `browser_navigation_timeout` | Maximum navigation wait in seconds. |
| `browser_challenge_wait` | Bounded passive wait for an interstitial to clear. It does not click verification controls, solve CAPTCHAs, or retry requests repeatedly. |
| `browser_diagnostics_path` | Directory for failure metadata and screenshots, relative to the project root unless absolute. |

For example, to use your existing local proxy for a single diagnostic fetch:

```sh
MYRARGB_BROWSER_PROXY=http://127.0.0.1:10808 .venv/bin/python scripts/check_crawler.py
```

Use an address reachable from the process: inside Docker, `127.0.0.1` is the
container itself. Set the browser proxy explicitly to the same reachable proxy
as your intended connection. Put authenticated proxy URLs in the private process
environment or ignored `.env`, rather than tracked TOML. URL-encode special
characters in credentials. Browser startup and navigation errors omit raw
Playwright exception strings so those credentials do not reach the API/logs.

The current Playwright driver uses its native browser identity; the former
hardcoded Chrome version/client hints and JavaScript fingerprint patches are
removed. Legacy Selenium drivers are not selected by the default factory, and
the new browser settings apply to Playwright only.

## Interpreting failures

Cloudflare identifies challenge responses with the `cf-mitigated: challenge`
header. The driver also recognizes common interstitial HTML markers as a
fallback. A page that merely loads a Cloudflare JavaScript resource is not
classified as a challenge. AWS-style browser verification is detected too.
See [Cloudflare's response detection documentation](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).

On a challenge, failed HTTP response, or navigation error, diagnostics record:

- A unique ID, requested/final URL, HTTP status and page title when available.
- Cloudflare Ray ID when present and whether the browser used an explicit proxy.
- A screenshot when capture succeeds.

URLs omit credentials, query parameters and fragments. Raw response HTML,
cookies, arbitrary headers and proxy credentials are not dumped into logs.
Screenshots still show the rendered page. If screenshots or file writes fail,
the original browser error is preserved and the diagnostic ID can be absent.
Files are retained under the configured log directory until you remove them;
they are ignored by Git and excluded from the Docker build context.

A missing catalog table is now an error indicating an unexpected page, rather
than an empty result. A present table with no movie rows remains a valid empty
catalog result. An IMDb browser failure is propagated without marking a movie
permanently `unmatched`. Records completed before a later failure remain stored;
this change does not redesign incremental checkpoint recovery.

## If verification still fails

Open the exact URL in an ordinary, updated Chrome or Firefox browser. Enable
JavaScript/cookies and temporarily disable extensions or a VPN/proxy to diagnose
browser/network issues. Collect the Ray ID and error screenshot if the problem
persists. These are [Cloudflare's troubleshooting steps](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/).

Cloudflare lists Playwright and Selenium as unsupported environments for solving
production challenges. This change provides accurate failures and diagnostics;
it does not guarantee automated access. If an ordinary browser succeeds but the
crawler remains blocked, use an operator-approved API/feed or arrange access
with the website owner. See [supported browser environments](https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/).
