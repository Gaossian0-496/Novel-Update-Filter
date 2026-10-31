import re

# Series pages only. A '**/series/**' glob also matches cover images under /img/series/,
# which would then get HTML and fail to load.
SERIES_PAGES = re.compile(r'novelupdates\.com/series/')

"""Shared helper functions for extension tests."""

import threading


def load_listing(page, storage_data=None):
    """Clear storage, optionally seed, then navigate to the listing fixture."""
    if storage_data is None:
        storage_data = {}
    # Use the service worker to access chrome.storage — unavailable from regular page context
    page._sw.evaluate(
        '''async (data) => {
            await new Promise(r => chrome.storage.local.clear(r));
            await globalThis.nufBroker?.reset();  // pacing state is shared across tests
            if (Object.keys(data).length) {
                await new Promise(r => chrome.storage.local.set(data, r));
            }
        }''',
        storage_data,
    )
    page.goto('https://www.novelupdates.com/series-finder/')
    page.wait_for_load_state('domcontentloaded')


def load_series(page, storage_data=None):
    """Clear storage, optionally seed, then navigate to the series page fixture."""
    if storage_data is None:
        storage_data = {}
    page._sw.evaluate(
        '''async (data) => {
            await new Promise(r => chrome.storage.local.clear(r));
            await globalThis.nufBroker?.reset();  // pacing state is shared across tests
            if (Object.keys(data).length) {
                await new Promise(r => chrome.storage.local.set(data, r));
            }
        }''',
        storage_data,
    )
    page.goto('https://www.novelupdates.com/series/release-that-witch/')
    page.wait_for_load_state('domcontentloaded')


def get_storage(page, keys):
    return page._sw.evaluate('(k) => new Promise(r => chrome.storage.local.get(k, r))', keys)


def set_storage(page, data):
    """Write to storage — triggers storage.onChanged in the content script."""
    page._sw.evaluate('(d) => new Promise(r => chrome.storage.local.set(d, r))', data)


def parse_scores(page):
    """Extract integer scores from all .nuf-score-label elements on the page."""
    label_texts = page.locator('.nuf-score-label').all_text_contents()
    return [int(t.split(': ')[1].split('/')[0]) for t in label_texts]


def hang_route(route):
    """Route handler that hangs for 10 s then continues (keeps fetches pending)."""
    threading.Event().wait(10)
    route.continue_()


# storage.js saves novelMeta 2 s after the last change, or at most 5 s after the
# first unsaved one while lookups keep arriving: wait out the longest case
META_SAVE_WAIT_MS = 5500


def wait_for_meta_save(page):
    """Wait long enough for any pending novelMeta save to reach storage."""
    page.wait_for_timeout(META_SAVE_WAIT_MS)
