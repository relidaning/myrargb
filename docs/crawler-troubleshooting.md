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
| `browser_mode` | `managed` launches an isolated browser; `session` reuses the dedicated visible Chrome window described below. |
| `browser_cdp_url` | Local debugging endpoint used in session mode. |
| `browser_profile_path` | Dedicated Chrome profile used by the launcher. |
| `browser_executable` | Installed Chrome-compatible executable used by the launcher. |
| `browser_session_display` | Dedicated session launcher: `visible`, `background` (Linux off-screen display), or standard `headless`. |
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
environment rather than tracked TOML. The app does not load `.env`. URL-encode special
characters in credentials. Browser startup and navigation errors omit raw
Playwright exception strings so those credentials do not reach the API/logs.

Playwright runs on one dedicated worker thread, so requests from Flask and
Kafka share it safely; a dead browser is relaunched once.
In managed mode, Playwright uses its native browser identity; the former
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
permanently `unmatched`. Records completed before a later failure remain stored. The merged incremental
catch-up fix keeps the previous range boundary until a rerun reaches it, walking
past already-stored URLs after an interruption. This does not repair historical
gaps or redesign interrupted first-ever crawls. The original `collected` schema
uses SQLite row IDs through its repository, without a data migration.

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

## Reuse a manually verified Chrome session

When the site works in ordinary Chrome but blocks an isolated browser, use
`browser_mode = "session"`. This attaches to a dedicated visible Chrome window
and reuses its existing session. It does not click or solve verification controls.

1. Launch the dedicated window on your desktop:

   ```sh
   .venv/bin/python scripts/open_crawler_browser.py
   ```

2. Complete verification in that window if prompted, then leave it open.
3. Check the catalog using that same session:

   ```sh
   MYRARGB_BROWSER_MODE=session .venv/bin/python scripts/check_crawler.py
   ```

4. Set `browser_mode = "session"` in `config/defaults.toml` and restart the app
   normally to use it from the Crawl button. The existing running process reads
   settings only at startup. To return to isolated browsing, use `managed`.

`browser_cdp_url` selects a loopback debugging endpoint with an explicit port;
`browser_profile_path` stores this dedicated profile outside source control;
`browser_executable` selects an installed Chrome-compatible executable. The
launcher defaults to `google-chrome` on PATH. It never opens your personal Chrome
profile, and rejects proxy credentials in command arguments. Proxy settings are
applied when this Chrome window starts; the attached crawler inherits them and
cannot change its route. On Linux with an X display, the launcher selects X11 to
avoid Wayland compositor/color-management crashes observed on this host.

The browser remains open when the crawler disconnects, including across separate
check commands. Only crawler-owned tabs are closed. If verification expires,
complete it again in the same dedicated window. If the browser closes, reopen
it with the launcher; its dedicated profile is retained. Runtime files are under
ignored `data/browser-session/`; launcher diagnostics are in
`logs/browser-session.log`.

Local debugger discovery explicitly ignores HTTP proxy environment variables and
connects to the returned loopback WebSocket, so a global proxy cannot intercept
that local connection. The debugging interface is restricted to loopback because
it grants control over the dedicated session. Do not expose it publicly. In
Docker, loopback refers to the container, so this desktop session mode needs a
loopback tunnel to the host; it does not automatically work with a desktop
browser outside the container. Use a local app process for the desktop workflow.

## Run without desktop windows on Linux

`browser_session_display = "background"` starts full Chrome on its off-screen
Ozone display backend (`--ozone-platform=headless`). It retains the dedicated
profile and does not create desktop windows or tabs in your personal browser.
This is different from Chrome's `--headless` mode, which was still challenged in
our live test despite retaining the same profile. Off-screen display mode was
verified to fetch the catalog with 25 rows after manual verification.

Keep `browser_mode = "session"` and configure the same `browser_proxy` route that
was used during verification. Set `browser_session_display = "background"` in
`config/defaults.toml`. The launcher reads that preference;
it does not change an already-running browser in place.

Start the background session:

```sh
.venv/bin/python scripts/open_crawler_browser.py --background
.venv/bin/python scripts/check_crawler.py
```

The browser remains running after these commands. To renew verification, switch
the dedicated profile to a visible window, complete verification there, then
switch it back:

```sh
.venv/bin/python scripts/open_crawler_browser.py --stop
.venv/bin/python scripts/open_crawler_browser.py --visible
# Complete verification manually before continuing.
.venv/bin/python scripts/open_crawler_browser.py --stop
.venv/bin/python scripts/open_crawler_browser.py --background
```

`--stop` closes only the Chrome endpoint configured for this dedicated session;
it retains the profile. Start one browser at a time for that profile. `--visible`
overrides the saved display preference so you can interact with a challenge.
`--headless` is available for testing standard Chrome headless mode but is not the
verified working choice for this site. Background display mode requires Linux;
other platforms can use visible mode or test standard headless mode.

Crawler tabs are opened as inactive background targets and closed after each
fetch. No CAPTCHA solving or fingerprint spoofing is introduced. Verification
can still expire; background mode does not guarantee permanent access.

Concurrent catalog and IMDb requests in session mode select tabs by their Chrome
CDP target IDs. Each request closes only its own tab; shared browser page events
are not used to infer tab ownership.

Catalog navigation failures retry the same page up to `crawl_page_attempts`
(default 3), waiting `crawl_page_retry_delay` seconds (default 2) between attempts.
Challenge pages and unexpected catalog layouts stop immediately. A failure
retains inserted items and does not advance the collected range across a gap.
