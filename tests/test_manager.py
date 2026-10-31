"""Tests for the List Manager page (manager.html / manager.js)."""
import re
import json
import pytest
from playwright.sync_api import expect
from helpers import get_storage, set_storage, SERIES_PAGES


def _capture_export_payload(page, export_btn_selector='#export-btn'):
    """Stub clipboard + downloadJson on the page, click Export, return parsed JSON."""
    page.evaluate('''() => {
        window._capturedExport = null;
        navigator.clipboard.writeText = (text) => {
            window._capturedExport = text;
            return Promise.resolve();
        };
        if (typeof window.downloadJson === 'function') {
            window.downloadJson = () => {};
        }
    }''')
    page.locator(export_btn_selector).click()
    # Give the click handler a moment to call writeText
    page.wait_for_function('() => window._capturedExport !== null', timeout=3000)
    return json.loads(page.evaluate('() => window._capturedExport'))


def load_manager(page, extension_id, storage_data=None):
    """Clear storage, optionally seed, then navigate to the manager page."""
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
    page.goto(f'chrome-extension://{extension_id}/manager.html')
    page.wait_for_load_state('domcontentloaded')


# ── Rendering ───────────────────────────────────────────────────────────────

def test_manager_renders_items_in_correct_columns(page, extension_id):
    load_manager(page, extension_id, {
        'read': ['slug-a'],
        'notFinished': ['slug-b'],
        'notInterested': ['slug-c'],
    })
    expect(page.locator('#list-read .item[data-slug="slug-a"]')).to_have_count(1)
    expect(page.locator('#list-nf .item[data-slug="slug-b"]')).to_have_count(1)
    expect(page.locator('#list-ni .item[data-slug="slug-c"]')).to_have_count(1)


def test_manager_column_count_badge_matches_item_count(page, extension_id):
    load_manager(page, extension_id, {
        'read': ['a', 'b', 'c'],
        'notFinished': ['d', 'e'],
        'notInterested': ['f'],
    })
    expect(page.locator('#cnt-read')).to_have_text('3')
    expect(page.locator('#cnt-nf')).to_have_text('2')
    expect(page.locator('#cnt-ni')).to_have_text('1')


def test_manager_header_stats_match_counts(page, extension_id):
    load_manager(page, extension_id, {
        'read': ['a', 'b', 'c'],
        'notFinished': ['d', 'e'],
        'notInterested': ['f'],
    })
    expect(page.locator('#stat-read')).to_have_text('3')
    expect(page.locator('#stat-nf')).to_have_text('2')
    expect(page.locator('#stat-ni')).to_have_text('1')


def test_manager_empty_column_shows_placeholder(page, extension_id):
    load_manager(page, extension_id, {
        'read': [],
        'notFinished': [],
        'notInterested': [],
    })
    expect(page.locator('#list-read .empty')).to_have_count(1)
    assert 'Nothing here' in page.locator('#list-read .empty').text_content()


# ── Per-item actions ────────────────────────────────────────────────────────

def test_manager_move_item_to_other_bucket(page, extension_id):
    load_manager(page, extension_id, {'read': ['slug-a']})

    # Click "→ Not Finished" on slug-a's row
    page.locator('#list-read .item[data-slug="slug-a"] .move-nf').click()

    expect(page.locator('#list-read .item[data-slug="slug-a"]')).to_have_count(0)
    expect(page.locator('#list-nf .item[data-slug="slug-a"]')).to_have_count(1)

    stored = get_storage(page, ['read', 'notFinished'])
    assert 'slug-a' not in (stored.get('read') or [])
    assert 'slug-a' in (stored.get('notFinished') or [])


def test_manager_delete_item(page, extension_id):
    load_manager(page, extension_id, {'read': ['slug-a', 'slug-b']})

    page.locator('#list-read .item[data-slug="slug-a"] .del').click()

    expect(page.locator('#list-read .item[data-slug="slug-a"]')).to_have_count(0)
    expect(page.locator('#list-read .item[data-slug="slug-b"]')).to_have_count(1)

    stored = get_storage(page, 'read')
    assert stored.get('read') == ['slug-b']


# ── Bulk operations ─────────────────────────────────────────────────────────

def test_manager_select_all_checks_all_checkboxes(page, extension_id):
    load_manager(page, extension_id, {'read': ['a', 'b', 'c']})

    page.locator('#sel-all-read').click()

    checkboxes = page.locator('#list-read input[type="checkbox"]')
    expect(checkboxes).to_have_count(3)
    for i in range(3):
        assert checkboxes.nth(i).is_checked()
    expect(page.locator('#sel-cnt-read')).to_have_text('3')


def test_manager_bulk_move(page, extension_id):
    load_manager(page, extension_id, {'read': ['slug-a', 'slug-b']})

    page.locator('#sel-all-read').click()
    page.locator('#bulk-move-read-nf').click()

    expect(page.locator('#list-read .item')).to_have_count(0)
    expect(page.locator('#list-nf .item[data-slug="slug-a"]')).to_have_count(1)
    expect(page.locator('#list-nf .item[data-slug="slug-b"]')).to_have_count(1)

    stored = get_storage(page, ['read', 'notFinished'])
    assert stored.get('read') == []
    assert 'slug-a' in stored.get('notFinished', [])
    assert 'slug-b' in stored.get('notFinished', [])


def test_manager_bulk_delete(page, extension_id):
    load_manager(page, extension_id, {'read': ['slug-a', 'slug-b']})

    page.locator('#sel-all-read').click()
    page.locator('#bulk-del-read').click()

    expect(page.locator('#list-read .item')).to_have_count(0)
    stored = get_storage(page, 'read')
    assert stored.get('read') == []


# ── Column search ───────────────────────────────────────────────────────────

def test_manager_column_search_filters_visible_items(page, extension_id):
    # Pre-seed titleCache so fetchMissingTitles doesn't overwrite with fixture data
    load_manager(page, extension_id, {
        'read': ['release-that-witch', 'against-the-gods'],
        'titleCache': {
            'release-that-witch': 'Release that Witch',
            'against-the-gods': 'Against the Gods',
        },
    })

    # Wait for init() to finish and render both items before searching
    expect(page.locator('#list-read .item')).to_have_count(2)
    # Directly invoke the filter state — event dispatch is unreliable in extension pages
    page.evaluate("""() => { searches.read = 'witch'; renderList('read'); }""")

    expect(page.locator('#list-read .item[data-slug="release-that-witch"]')).to_have_count(1)
    expect(page.locator('#list-read .item[data-slug="against-the-gods"]')).to_have_count(0)


# ── Import / Export ─────────────────────────────────────────────────────────

def test_manager_import_json_populates_lists(page, extension_id):
    load_manager(page, extension_id)

    page.locator('#import-toggle').click()
    page.fill('#import-text', json.dumps({
        'version': 1,
        'read': ['slug-x'],
        'notFinished': [],
        'notInterested': [],
    }))
    page.locator('#do-import').click()

    expect(page.locator('#list-read .item[data-slug="slug-x"]')).to_have_count(1)
    stored = get_storage(page, 'read')
    assert 'slug-x' in stored.get('read', [])


def test_manager_import_invalid_json_shows_alert(page, extension_id):
    load_manager(page, extension_id)

    page.locator('#import-toggle').click()
    page.fill('#import-text', 'not valid json')

    dialogs = []
    page.once('dialog', lambda d: dialogs.append(d.type) or d.dismiss())
    page.locator('#do-import').click()
    page.wait_for_timeout(500)

    assert 'alert' in dialogs
    # Import area remains open — import did not succeed
    assert 'open' in (page.locator('#import-area').get_attribute('class') or '')


# ── Live storage update ─────────────────────────────────────────────────────

def test_manager_live_storage_update_rerenders_lists(page, extension_id):
    load_manager(page, extension_id, {'read': ['slug-a']})

    expect(page.locator('#list-read .item[data-slug="slug-a"]')).to_have_count(1)
    expect(page.locator('#list-read .item[data-slug="slug-b"]')).to_have_count(0)

    # Write to storage externally — triggers storage.onChanged in manager
    set_storage(page, {'read': ['slug-a', 'slug-b']})

    expect(page.locator('#list-read .item[data-slug="slug-b"]')).to_have_count(1, timeout=3000)


# ── Feature 003: To Read column ─────────────────────────────────────────────

def test_feature_003_to_read_manager(page, extension_id):
    """To Read fourth column renders and move-to-Read works."""
    load_manager(page, extension_id, {
        'toRead': ['slug-a'],
        'titleCache': {'slug-a': 'Title A'},
    })

    # To Read column must exist and show slug-a
    expect(page.locator('#list-tr .item[data-slug="slug-a"]')).to_have_count(1)
    expect(page.locator('#cnt-tr')).to_have_text('1')

    # Click "→ Read" button on slug-a in To Read column
    page.locator('#list-tr .item[data-slug="slug-a"] .move-read').click()

    # slug-a must leave To Read and appear in Read
    expect(page.locator('#list-tr .item[data-slug="slug-a"]')).to_have_count(0)
    expect(page.locator('#list-read .item[data-slug="slug-a"]')).to_have_count(1)


# ── Defect 036 ──────────────────────────────────────────────────────────────

def test_defect036_item_title_visible(page, extension_id):
    """Item title must have non-zero width — not squeezed out by action buttons."""
    load_manager(page, extension_id, {
        'read': ['slug-a'],
        'titleCache': {'slug-a': 'Release that Witch'},
    })
    title_el = page.locator('#list-read .item-title').first
    expect(title_el).to_be_visible()
    width = title_el.evaluate('el => el.offsetWidth')
    assert width > 0, f'Expected item-title to have non-zero width, got {width}'


def test_defect036_bulk_move_to_read_from_read(page, extension_id):
    """Bulk bar in Read column must have a To Read button that moves selected items."""
    load_manager(page, extension_id, {
        'read': ['slug-a', 'slug-b'],
        'titleCache': {'slug-a': 'Title A', 'slug-b': 'Title B'},
    })
    # Bulk move button must exist
    btn = page.locator('#bulk-move-read-tr')
    expect(btn).to_have_count(1)

    # Select both items and click the button
    page.locator('#sel-all-read').click()
    btn.click()

    # Both slugs should now be in To Read, not in Read
    expect(page.locator('#list-read .item')).to_have_count(0)
    expect(page.locator('#list-tr .item[data-slug="slug-a"]')).to_have_count(1)
    expect(page.locator('#list-tr .item[data-slug="slug-b"]')).to_have_count(1)

    stored = get_storage(page, ['read', 'toRead'])
    assert stored.get('read', []) == []
    assert set(stored.get('toRead', [])) == {'slug-a', 'slug-b'}


def test_defect036_nf_header_colour_is_amber(page, extension_id):
    """NOT FINISHED column header must be amber, not the same grey as NOT INTERESTED."""
    load_manager(page, extension_id, {})
    nf_colour = page.locator('#col-nf .col-title').evaluate(
        'el => getComputedStyle(el).color'
    )
    ni_colour = page.locator('#col-ni .col-title').evaluate(
        'el => getComputedStyle(el).color'
    )
    assert nf_colour != ni_colour, (
        f'Expected NOT FINISHED header colour ({nf_colour}) to differ from '
        f'NOT INTERESTED ({ni_colour})'
    )


# ── defect-047: manager title fetch + subtitle + bulk-bar layout ────────────

def test_defect047_manager_uses_novel_meta_title_without_fetch(page, extension_id):
    """Manager must read titles from state.novelMeta[slug].t when no titleCache exists,
    avoiding a network fetch entirely. Currently manager.js only loads titleCache."""
    # Track any series-page fetches the manager may fire. If the novelMeta fallback
    # is wired correctly, none should fire for this slug.
    requests_seen = []
    page.route(
        '**/series/some-slug/**',
        lambda route: (
            requests_seen.append(route.request.url),
            route.fulfill(status=200, content_type='text/html; charset=utf-8',
                          body='<html><body><h1 class="seriestitlenu">Network Title</h1></body></html>'),
        )[1],
    )

    load_manager(page, extension_id, {
        'read': ['some-slug'],
        'novelMeta': {
            'some-slug': {
                't': 'Title From Meta',
                'img': 'https://cdn.novelupdates.com/imgmid/series_1.jpg',  # cover known too, so nothing to fetch
                'g': ['fantasy'],
                'l': 'chinese',
                'd': [],
                'ts': 1716000000000,
            },
        },
    })

    item = page.locator('#list-read .item[data-slug="some-slug"]')
    expect(item.locator('.item-title')).to_have_text('Title From Meta')
    # Give the fire-and-forget fetchMissingTitles a moment to NOT run
    page.wait_for_timeout(800)
    assert len(requests_seen) == 0, (
        f'Manager fetched the series page even though novelMeta had a title: {requests_seen}'
    )


def test_defect047_subtitle_is_date_when_title_present(page, extension_id):
    """When a title is resolvable, the subtitle line should show the add-date
    (from novelMeta[slug].ts), not the redundant slug."""
    # Fixed timestamp: 2026-04-01T00:00:00Z → ms
    fixed_ts = 1774915200000
    load_manager(page, extension_id, {
        'read': ['some-slug'],
        'titleCache': {'some-slug': 'Some Title'},
        'novelMeta': {'some-slug': {'ts': fixed_ts}},
    })

    item = page.locator('#list-read .item[data-slug="some-slug"]')
    expect(item.locator('.item-title')).to_have_text('Some Title')

    # Subtitle should be a formatted date, NOT the slug
    sub = item.locator('.item-sub, .item-slug').first
    expect(sub).not_to_have_text('some-slug')
    # The formatted date must contain "2026" so we know it's the timestamp, not the slug
    assert '2026' in sub.text_content(), (
        f'Expected subtitle to be a date containing 2026, got: {sub.text_content()!r}'
    )


def test_defect047_bulk_bar_buttons_render_on_single_line(page, extension_id):
    """Bulk-bar buttons must not wrap their labels at standard manager viewport widths."""
    page.set_viewport_size({'width': 1400, 'height': 900})
    load_manager(page, extension_id, {
        'read': ['a'], 'notFinished': ['b'], 'notInterested': ['c'], 'toRead': ['d'],
    })
    buttons = page.locator('.bulk-bar > button')
    count = buttons.count()
    assert count >= 4, f'Expected at least 4 bulk-bar buttons per column, got {count}'
    for i in range(count):
        h = buttons.nth(i).bounding_box()['height']
        label = buttons.nth(i).text_content()
        assert h < 30, f'Bulk-bar button {label!r} wraps to {h}px (>30 = multi-line)'


# ── defect-053: full-state export / import ──────────────────────────────────

FULL_STATE_SEED = {
    # Buckets
    'read':          ['slug-a', 'slug-b'],
    'notFinished':   ['slug-c'],
    'notInterested': ['slug-d'],
    'toRead':        ['slug-e'],
    # Per-novel metadata (expensive prefetch work)
    'novelMeta': {
        'slug-a': {
            'g': ['fantasy'], 'l': 'chinese', 'd': ['magic', 'kingdom'],
            't': 'Title A', 'ts': 1716000000000,
            'tags': ['kingdom building', 'transmigration'],
            'c1url': 'https://example.com/chapter-1',
        },
        'slug-b': {
            'g': ['action'], 'l': 'korean', 'd': ['cultivation'],
            't': 'Title B', 'ts': 1716000010000,
            'tags': ['martial arts'],
        },
    },
    # Pure user input (irretrievable on loss)
    'novelReviews': {
        'slug-a': {'rating': 5, 'comment': 'Amazing world-building'},
        'slug-b': {'rating': 3},
    },
    # Manager's title cache — must cover every bucketed slug, otherwise
    # fetchMissingTitles will fire on manager init and inject extra entries
    # (the conftest series stub returns "Release that Witch" for any slug).
    'titleCache': {
        'slug-a': 'Pretty Title A', 'slug-b': 'Pretty Title B',
        'slug-c': 'Pretty Title C', 'slug-d': 'Pretty Title D',
        'slug-e': 'Pretty Title E',
    },
    # Filter selections
    'excludeLanguages': ['japanese'],
    'excludeTags':      ['harem'],
    # Preferences / toggles
    'recommendationsEnabled': True,
    'showReasoningChips':     True,
    'prefetchTagsEnabled':    True,
    'expandSynopsis':         True,
    'hideReviews':            False,
    'minRating':              4.0,
    'minRecommendationScore': 6,
    'recencyHalfLifeDays':    180,
    # Migration gate
    'ratingScaleVersion':     2,
}


def test_defect053_export_includes_novel_meta_and_reviews(page, extension_id):
    """Export payload must include every meaningful key from chrome.storage.local,
    not just the four bucket arrays. Otherwise users moving to a new browser
    lose all NU tag prefetch work, ratings, comments, and filter preferences."""
    load_manager(page, extension_id, FULL_STATE_SEED)
    expect(page.locator('#list-read .item')).to_have_count(2)
    payload = _capture_export_payload(page)
    # Each non-trivial key must be present
    for key in ['read', 'notFinished', 'notInterested', 'toRead',
                'novelMeta', 'novelReviews', 'titleCache',
                'excludeLanguages', 'excludeTags',
                'recommendationsEnabled', 'showReasoningChips', 'prefetchTagsEnabled',
                'expandSynopsis', 'hideReviews',
                'minRating', 'minRecommendationScore', 'recencyHalfLifeDays',
                'ratingScaleVersion']:
        assert key in payload, f'Export payload missing key: {key!r}'
    # And the values round-trip correctly for the expensive/irretrievable ones
    assert payload['novelMeta'] == FULL_STATE_SEED['novelMeta']
    assert payload['novelReviews'] == FULL_STATE_SEED['novelReviews']
    assert payload['titleCache'] == FULL_STATE_SEED['titleCache']
    assert payload['excludeTags'] == FULL_STATE_SEED['excludeTags']


def test_defect053_export_uses_version_2(page, extension_id):
    """The richer-shape export must declare version: 2 so future importers can
    distinguish it from the legacy bucket-only version: 1 shape."""
    load_manager(page, extension_id, FULL_STATE_SEED)
    payload = _capture_export_payload(page)
    assert payload.get('version') == 2, (
        f"export version is {payload.get('version')!r}, expected 2 for the full-shape export"
    )


def test_defect053_import_round_trip_restores_everything(page, extension_id):
    """Round-trip: seed full state → export → clear storage → import → assert
    every key restored to its original value. This is the user's stated goal:
    'I should be able to restore exactly my saved state'."""
    load_manager(page, extension_id, FULL_STATE_SEED)
    payload_text = json.dumps(_capture_export_payload(page))

    # Clear everything, then import the captured payload via the UI
    page._sw.evaluate('async () => new Promise(r => chrome.storage.local.clear(r))')
    page.reload()
    page.wait_for_load_state('domcontentloaded')

    page.locator('#import-toggle').click()
    page.locator('#import-text').fill(payload_text)
    page.locator('#do-import').click()

    # Give import write a moment to land in storage
    page.wait_for_timeout(500)

    stored = get_storage(page, list(FULL_STATE_SEED.keys()))
    for key, expected in FULL_STATE_SEED.items():
        actual = stored.get(key)
        assert actual == expected, (
            f'Round-trip mismatch on {key!r}: expected {expected!r}, got {actual!r}'
        )


def test_defect054_import_from_file_round_trip(page, extension_id):
    """The manager must support a file-picker import path: select a .json file,
    its contents are read and imported the same as a paste-and-click. Round-trip
    a non-trivial state to confirm equivalence."""
    seed = {
        'read': ['from-file-a'], 'notFinished': ['from-file-b'],
        'notInterested': [], 'toRead': [],
        'novelMeta': {'from-file-a': {'g': ['fantasy'], 'l': 'cn', 'd': [], 't': 'A', 'tags': ['x']}},
        'novelReviews': {'from-file-a': {'rating': 5, 'comment': 'great'}},
        'titleCache': {'from-file-a': 'Title A', 'from-file-b': 'Title B'},
        'excludeLanguages': ['japanese'],
        'excludeTags': ['harem'],
        'recommendationsEnabled': True, 'showReasoningChips': True,
        'prefetchTagsEnabled': True, 'expandSynopsis': True, 'hideReviews': False,
        'minRating': 4.0, 'minRecommendationScore': 6, 'recencyHalfLifeDays': 180,
        'ratingScaleVersion': 2,
    }
    payload_text = json.dumps({'version': 2, **seed})

    load_manager(page, extension_id, {})
    page.locator('#import-toggle').click()
    # Push the JSON via the file input — Playwright's set_input_files lets us
    # supply an in-memory file rather than a path on disk.
    page.locator('#import-file').set_input_files({
        'name': 'novel-updates-filter-test.json',
        'mimeType': 'application/json',
        'buffer': payload_text.encode('utf-8'),
    })
    page.wait_for_timeout(500)

    stored = get_storage(page, list(seed.keys()))
    for key, expected in seed.items():
        assert stored.get(key) == expected, (
            f'File-import mismatch on {key!r}: expected {expected!r}, got {stored.get(key)!r}'
        )


def test_defect053_import_accepts_legacy_v1_payload(page, extension_id):
    """Backward-compat: a v1 payload (buckets only) must still import cleanly
    without falsely failing validation when novelMeta/etc. are absent."""
    load_manager(page, extension_id, {})
    legacy_payload = json.dumps({
        'version': 1,
        'read':          ['legacy-a'],
        'notFinished':   ['legacy-b'],
        'notInterested': [],
        'toRead':        [],
    })
    page.locator('#import-toggle').click()
    page.locator('#import-text').fill(legacy_payload)
    page.locator('#do-import').click()
    page.wait_for_timeout(300)

    stored = get_storage(page, ['read', 'notFinished'])
    assert stored.get('read') == ['legacy-a']
    assert stored.get('notFinished') == ['legacy-b']


# ── Fixed-defect audit (2026-10-25): defect-047 plan items B and C ───────────

def test_defect047_title_fetch_retries_after_block(page, extension_id):
    """A blocked title fetch (429 with Retry-After) is retried rather than abandoned."""
    calls = []

    def handler(route):
        calls.append(1)
        if len(calls) == 1:
            route.fulfill(status=429, headers={'Retry-After': '1'}, body='Too Many Requests')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8',
                          body='<html><body><h1 class="seriestitlenu">Retried Title</h1></body></html>')

    page.route('**/series/retry-slug/**', handler)
    load_manager(page, extension_id, {'read': ['retry-slug']})
    item = page.locator('#list-read .item[data-slug="retry-slug"]')
    expect(item.locator('.item-title')).to_have_text('Retried Title', timeout=15000)
    assert len(calls) == 2


def test_defect047_failed_title_fetch_is_shown_not_left_loading(page, extension_id):
    """When a title cannot be fetched, the row says so instead of showing 'Fetching title…' forever."""
    page.route('**/series/gone-slug/**', lambda route: route.fulfill(status=404, body='Not Found'))
    load_manager(page, extension_id, {'read': ['gone-slug']})
    item = page.locator('#list-read .item[data-slug="gone-slug"]')
    expect(item.locator('.item-failed')).to_be_visible(timeout=10000)
    expect(item.locator('.item-title')).to_have_text('gone-slug')
    assert item.locator('.item-loading').count() == 0


def test_defect047_title_fetches_are_spaced_out(page, extension_id):
    """Title fetches stay about 2 s apart (jittered) so the manager cannot burst requests."""
    import time
    times = []

    def handler(route):
        times.append(time.time())
        slug = route.request.url.rstrip('/').split('/')[-1]
        route.fulfill(status=200, content_type='text/html; charset=utf-8',
                      body=f'<html><body><h1 class="seriestitlenu">T {slug}</h1></body></html>')

    page.route('**/series/space-*/**', handler)
    load_manager(page, extension_id, {'read': ['space-a', 'space-b', 'space-c']})
    expect(page.locator('#list-read .item-title')).to_have_count(3, timeout=15000)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) == 3 and all(g >= 1.5 for g in gaps), gaps


# ── Manager redesign (2026-10-25): undo, selection, sort, links, layout ──────

def test_manager_remove_offers_undo(page, extension_id):
    """Removing a novel shows an Undo toast that restores it to the same list."""
    load_manager(page, extension_id, {'read': ['slug-a', 'slug-b'], 'titleCache': {'slug-a': 'A', 'slug-b': 'B'}})
    page.locator('#list-read .item[data-slug="slug-a"] .del').click()
    expect(page.locator('#undo-toast')).to_be_visible()
    expect(page.locator('#undo-msg')).to_contain_text('Removed "A" from Read')
    page.locator('#undo-btn').click()
    expect(page.locator('#list-read .item[data-slug="slug-a"]')).to_have_count(1)
    expect(page.locator('#undo-toast')).to_be_hidden()
    assert sorted(get_storage(page, 'read')['read']) == ['slug-a', 'slug-b']


def test_manager_clear_list_can_be_undone(page, extension_id):
    """Clear list asks first, then still offers Undo."""
    load_manager(page, extension_id, {'notFinished': ['x', 'y'], 'titleCache': {'x': 'X', 'y': 'Y'}})
    page.once('dialog', lambda d: d.accept())
    page.locator('#clear-nf').click()
    expect(page.locator('#list-nf .item')).to_have_count(0)
    expect(page.locator('#undo-msg')).to_contain_text('Cleared Not Finished (2 novels)')
    page.locator('#undo-btn').click()
    expect(page.locator('#list-nf .item')).to_have_count(2)


def test_manager_bulk_buttons_disabled_until_selection(page, extension_id):
    """Bulk move and remove only become clickable once something is selected."""
    load_manager(page, extension_id, {'read': ['slug-a'], 'titleCache': {'slug-a': 'A'}})
    buttons = page.locator('#col-read .bulk-bar button')
    for i in range(buttons.count()):
        assert buttons.nth(i).is_disabled()
    page.locator('#list-read .item[data-slug="slug-a"] input[type="checkbox"]').check()
    for i in range(buttons.count()):
        assert buttons.nth(i).is_enabled()


def test_manager_select_all_toggles_to_clear_selection(page, extension_id):
    load_manager(page, extension_id, {'read': ['a', 'b'], 'titleCache': {'a': 'A', 'b': 'B'}})
    page.locator('#sel-all-read').click()
    expect(page.locator('#sel-all-read')).to_have_text('Clear selection')
    expect(page.locator('#sel-cnt-read')).to_have_text('2')
    page.locator('#sel-all-read').click()
    expect(page.locator('#sel-all-read')).to_have_text('Select all')
    expect(page.locator('#sel-cnt-read')).to_have_text('0')


def test_manager_selection_survives_storage_update(page, extension_id):
    """A title or bucket change elsewhere re-renders the lists without dropping the user's selection."""
    load_manager(page, extension_id, {'read': ['a', 'b'], 'titleCache': {'a': 'A', 'b': 'B'}})
    page.locator('#list-read .item[data-slug="a"] input[type="checkbox"]').check()
    set_storage(page, {'notInterested': ['z'], 'titleCache': {'a': 'A', 'b': 'B', 'z': 'Z'}})
    expect(page.locator('#list-ni .item')).to_have_count(1)
    assert page.locator('#list-read .item[data-slug="a"] input[type="checkbox"]').is_checked()
    expect(page.locator('#sel-cnt-read')).to_have_text('1')


def test_manager_sorts_recently_added_first_by_default(page, extension_id):
    load_manager(page, extension_id, {
        'read': ['old', 'new'],
        'titleCache': {'old': 'Old', 'new': 'New'},
        'novelMeta': {'old': {'ts': 1700000000000}, 'new': {'ts': 1760000000000}},
    })
    expect(page.locator('#list-read .item-title')).to_have_text(['New', 'Old'])


def test_manager_sort_by_title_is_remembered(page, extension_id):
    load_manager(page, extension_id, {
        'read': ['b', 'a'], 'titleCache': {'a': 'Alpha', 'b': 'Beta'},
        'novelMeta': {'a': {'ts': 1700000000000}, 'b': {'ts': 1760000000000}},
    })
    page.locator('#sort-select').select_option('title')
    expect(page.locator('#list-read .item-title')).to_have_text(['Alpha', 'Beta'])
    page.reload()
    assert page.locator('#sort-select').input_value() == 'title'
    expect(page.locator('#list-read .item-title')).to_have_text(['Alpha', 'Beta'])
    page.locator('#sort-select').select_option('newest')


def test_manager_title_links_to_series_page_and_shows_rating_and_cover(page, extension_id):
    load_manager(page, extension_id, {
        'read': ['release-that-witch'],
        'titleCache': {'release-that-witch': 'Release that Witch'},
        'novelMeta': {'release-that-witch': {'img': 'https://cdn.novelupdates.com/imgmid/series_1.jpg'}},
        'novelReviews': {'release-that-witch': {'rating': 4}},
    })
    item = page.locator('#list-read .item[data-slug="release-that-witch"]')
    link = item.locator('a.item-title')
    assert link.get_attribute('href') == 'https://www.novelupdates.com/series/release-that-witch/'
    assert link.get_attribute('target') == '_blank'
    expect(item.locator('.rating')).to_have_text('★★★★☆')
    assert item.locator('img.item-cover').get_attribute('src').endswith('series_1.jpg')


def test_manager_every_button_has_a_tooltip(page, extension_id):
    load_manager(page, extension_id, {'read': ['a'], 'titleCache': {'a': 'A'}})
    buttons = page.locator('button:visible')
    untitled = [buttons.nth(i).text_content() for i in range(buttons.count()) if not buttons.nth(i).get_attribute('title')]
    assert untitled == [], f'buttons without tooltips: {untitled}'


@pytest.mark.parametrize('width,columns', [(1440, 4), (1000, 2), (600, 1)])
def test_manager_columns_reflow_without_horizontal_scroll(page, extension_id, width, columns):
    page.set_viewport_size({'width': width, 'height': 900})
    load_manager(page, extension_id, {'read': ['a'], 'titleCache': {'a': 'A very long novel title that must wrap rather than overflow'}})
    tops = page.evaluate("[...document.querySelectorAll('.col')].map(c => Math.round(c.getBoundingClientRect().top))")
    assert tops.count(tops[0]) == columns, tops
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


# ── defect-058: manager cover photos ─────────────────────────────────────────

def test_defect058_manager_fetches_missing_cover(page, extension_id):
    """A novel with a title but no stored cover gets its cover from its series page."""
    load_manager(page, extension_id, {
        'read': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'t': 'Release that Witch'}},
    })
    cover = page.locator('#list-read .item[data-slug="release-that-witch"] img.item-cover')
    expect(cover).to_have_attribute('src', re.compile(r'/img/series/release-that-witch\.jpg$'), timeout=10000)
    cache = get_storage(page, 'coverCache').get('coverCache', {})
    assert cache.get('release-that-witch', '').endswith('/img/series/release-that-witch.jpg'), cache


def test_defect058_manager_does_not_refetch_known_covers(page, extension_id):
    """Novels whose cover is already known (or known to be missing) are not fetched."""
    requests = []
    page.route(SERIES_PAGES, lambda route: (requests.append(route.request.url), route.fulfill(status=404, body=''))[1])
    load_manager(page, extension_id, {
        'read': ['has-cover', 'no-cover'],
        'novelMeta': {'has-cover': {'t': 'Has Cover', 'img': 'https://cdn.novelupdates.com/imgmid/series_1.jpg'},
                      'no-cover': {'t': 'No Cover'}},
        'coverCache': {'no-cover': ''},
    })
    page.wait_for_timeout(2500)
    assert requests == [], requests


def test_defect058_manager_cover_requests_send_no_referrer(page, extension_id):
    """Covers load without a chrome-extension:// referrer, which image hosts may reject."""
    load_manager(page, extension_id, {
        'read': ['a'], 'novelMeta': {'a': {'t': 'A', 'img': 'https://cdn.novelupdates.com/imgmid/series_1.jpg'}},
    })
    cover = page.locator('#list-read .item[data-slug="a"] img.item-cover')
    assert cover.get_attribute('referrerpolicy') == 'no-referrer'
