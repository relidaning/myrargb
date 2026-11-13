#!/usr/bin/env python3
"""Start dedicated Chrome with a retained profile, either visible or headless."""

import argparse
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from crawler import RargbCrawler
from browserdriver.session import debugger_websocket
from utils.settings import settings


def launch_arguments(mode=None):
    mode = mode or settings.browser_session_display
    headless = mode == 'headless'
    endpoint = urlsplit(settings.browser_cdp_url)
    executable = shutil.which(settings.browser_executable)
    if not executable:
        raise ValueError('Chrome executable not found; set browser_executable.')
    # The endpoint belongs to this dedicated profile, not the user's personal Chrome.
    args = [executable, f'--user-data-dir={settings.browser_profile_path}',
            f'--remote-debugging-address={"::1" if endpoint.hostname == "::1" else "127.0.0.1"}', f'--remote-debugging-port={endpoint.port}',
            '--no-first-run', '--no-default-browser-check']
    if mode == 'background':
        if not sys.platform.startswith('linux'):
            raise ValueError('Background display mode requires Linux; use visible or headless on this platform.')
        args.append('--ozone-platform=headless')
    elif headless:
        args.append('--headless=new')
    elif sys.platform.startswith('linux') and os.getenv('DISPLAY'):
        # Use the available X display; some Wayland compositors crash on Chrome color-management negotiation.
        args.append('--ozone-platform=x11')
    if settings.browser_proxy:
        proxy = urlsplit(settings.browser_proxy)
        if proxy.username is not None or proxy.password is not None:
            raise ValueError('Configure proxy authentication in Chrome; do not put credentials in launch arguments.')
        args.append(f'--proxy-server={settings.browser_proxy}')
    else:
        args.append('--no-proxy-server')
    args.append(RargbCrawler.page_url(1))
    return args


def stop_session():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            debugger_websocket(settings.browser_cdp_url, settings.browser_navigation_timeout),
            timeout=settings.browser_navigation_timeout * 1000)
        browser.new_browser_cdp_session().send('Browser.close')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--headless', dest='mode', action='store_const', const='headless',
                       help='Run without a visible window, retaining the dedicated profile')
    group.add_argument('--visible', dest='mode', action='store_const', const='visible',
                       help='Open a visible window for manual verification')
    group.add_argument('--background', dest='mode', action='store_const', const='background',
                       help='Run full Chrome on the Linux off-screen display backend without desktop windows')
    group.add_argument('--stop', action='store_true',
                       help='Close the browser at the configured dedicated debugging endpoint')
    parsed = parser.parse_args()
    mode = parsed.mode or settings.browser_session_display
    headless = mode == 'headless'
    process = None
    try:
        if parsed.stop:
            stop_session()
            print('Dedicated Chrome stopped; profile retained.')
            return 0
        args = launch_arguments(mode)
        endpoint = urlsplit(settings.browser_cdp_url)
        if mode == 'visible' and os.name != 'nt' and sys.platform != 'darwin' and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
            raise ValueError('A desktop display is required for manual verification.')
        # Do not unexpectedly replace or reuse an unknown browser on the chosen port.
        family = socket.AF_INET6 if endpoint.hostname == '::1' else socket.AF_INET
        address = '::1' if family == socket.AF_INET6 else '127.0.0.1'
        with socket.socket(family) as probe:
            try:
                probe.bind((address, endpoint.port))
            except OSError:
                raise ValueError('The debugging port is already in use. Leave the dedicated Chrome window open, or choose another browser_cdp_url.') from None
        settings.browser_profile_path.mkdir(parents=True, exist_ok=True)
        settings.browser_profile_path.chmod(0o700)
        logfile = settings.log_path.parent / 'browser-session.log'
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with logfile.open('ab') as log:
            process = subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + settings.browser_navigation_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ValueError('Chrome exited before startup; see logs/browser-session.log.')
            try:
                if debugger_websocket(settings.browser_cdp_url, timeout=1):
                    print('Dedicated Chrome is running headlessly.' if headless else
                          'Dedicated Chrome is running on an off-screen display with no desktop window.' if mode == 'background' else
                          'Dedicated Chrome is open. Complete verification there if prompted; leave the window open.')
                    print('Then check access using the same browser session:')
                    print('MYRARGB_BROWSER_MODE=session .venv/bin/python scripts/check_crawler.py')
                    return 0
            except (OSError, ValueError):
                time.sleep(0.2)
        raise ValueError('Chrome debugging endpoint did not become ready; see logs/browser-session.log.')
    except Exception as error:
        # Only our own messages are printed; OS errors can include sensitive arguments.
        message = str(error) if isinstance(error, ValueError) else 'Browser launch failed; check the port, profile and log permissions.'
        print(message, file=sys.stderr)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
