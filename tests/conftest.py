import base64
import re
import tempfile
import pytest
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

EXTENSION_PATH = str(Path(__file__).parent.parent / 'application')
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
PNG_1PX = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=')


@pytest.fixture(scope='session')
def pw():
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope='session')
def ctx(pw):
    user_data_dir = tempfile.mkdtemp(prefix='nuf-pw-')
    context = pw.chromium.launch_persistent_context(
        user_data_dir,
        headless=False,
        viewport={'width': 1280, 'height': 600},
        args=[
            f'--disable-extensions-except={EXTENSION_PATH}',
            f'--load-extension={EXTENSION_PATH}',
        ],
    )

    def handle_route(route):
        url = route.request.url
        # Cover images get a real (1 px) image: an HTML body would fail to load
        # and the sidebar would swap it for its placeholder
        if re.search(r'\.(jpe?g|png|gif|webp)(\?|$)', url, re.I):
            route.fulfill(status=200, content_type='image/png', body=PNG_1PX)
            return
        if '/series/' in url and '/series-finder' not in url and '/series-ranking' not in url:
            fixture = 'series.html'
        elif 'pg=2' in url:
            fixture = 'listing-page2.html'
        else:
            fixture = 'listing.html'
        body = (FIXTURES_DIR / fixture).read_text(encoding='utf-8')
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)

    context.route('https://www.novelupdates.com/**', handle_route)
    # Fixture covers on the image host: never fetch the live site from tests
    context.route('https://cdn.novelupdates.com/**',
                  lambda route: route.fulfill(status=200, content_type='image/png', body=PNG_1PX))

    yield context
    context.close()


@pytest.fixture(scope='session')
def sw(ctx):
    workers = ctx.service_workers  # property in Playwright Python, not a method
    if not workers:
        worker = ctx.wait_for_event('serviceworker')
    else:
        worker = workers[0]
    return worker


@pytest.fixture(scope='session')
def extension_id(sw):
    return urlparse(sw.url).hostname


@pytest.fixture
def page(ctx, sw):
    p = ctx.new_page()
    p._sw = sw  # attach service worker so helpers can use chrome.storage
    yield p
    p.close()

