"""Code-review fixes for tag prefetch, series-page lookups and infinite scroll:
security-check pages, cards hidden by the score threshold, infinite-scroll
cards, sidebar redraws, what a lookup stores, cover priority, covers with
prefetch off, idempotent page append, and no acronyms in the load timer."""

import json
import re
from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, get_storage, set_storage, SERIES_PAGES

APPLICATION_DIR = Path(__file__).parent.parent / 'application'
CONTENT_DIR = APPLICATION_DIR / 'content'
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
SERIES_HTML = (FIXTURES_DIR / 'series.html').read_text(encoding='utf-8')
SERIES_COVER = 'img/series/release-that-witch.jpg'
CHALLENGE_HTML = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
                  '<body><div id="challenge-running"></div>'
                  '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script></body></html>')
SIDEBAR_IMG = '.nuf-sidebar-item img.nuf-sidebar-img'

# Page-1 listing cards, seeded with tags so they make no lookups of their own
PAGE1_SLUGS = ['release-that-witch', 'against-the-gods', 'i-shall-seal-the-heavens', 'lord-of-the-mysteries']


def _read(path):
    return path.read_text(encoding='utf-8')


def _tagged(slugs):
    return {slug: {'g': ['fantasy'], 'l': 'chinese', 'd': ['cultivation'], 'tags': ['magic'], 't': slug} for slug in slugs}


def _fulfil_series(route, body=SERIES_HTML, status=200):
    route.fulfill(status=status, content_type='text/html; charset=utf-8', body=body)


def _series_requests(page):
    """Record every series-page URL requested and answer it with the series fixture."""
    calls = []

    def handler(route):
        calls.append(route.request.url)
        _fulfil_series(route)

    page.route(SERIES_PAGES, handler)
    return calls


def _isolated(page, expression):
    """Evaluate an expression in the extension's content-script world and return its value."""
    session = page.context.new_cdp_session(page)
    contexts = []
    session.on('Runtime.executionContextCreated', lambda event: contexts.append(event['context']))
    session.send('Runtime.enable')
    world = None
    for _ in range(50):
        world = next((c for c in contexts
                      if c.get('auxData', {}).get('type') == 'isolated'
                      and c.get('name') == 'Novel Updates Filter'), None)
        if world:
            break
        page.wait_for_timeout(100)
    assert world, f'content-script world not found: {[c.get("name") for c in contexts]}'
    result = session.send('Runtime.evaluate', {
        'expression': expression, 'contextId': world['id'],
        'returnByValue': True, 'awaitPromise': True,
    })
    session.detach()
    assert 'exceptionDetails' not in result, result
    return result['result'].get('value')


def _queue_snapshot(page):
    return page._sw.evaluate('() => globalThis.nufBroker.snapshot()')


# ── 1. Security-check page served with HTTP 200 ──────────────────────────────

def test_challenge_check_lives_in_utils_and_scroll_still_uses_it():
    """_isChallengePage is defined in utils.js (loaded before scroll.js and prefetch.js) and used by both."""
    utils, scroll, prefetch = (_read(CONTENT_DIR / f) for f in ('utils.js', 'scroll.js', 'prefetch.js'))
    assert 'function _isChallengePage(' in utils
    assert 'function _isChallengePage(' not in scroll
    assert '_isChallengePage(' in scroll and '_isChallengePage(' in prefetch
    order = json.loads(_read(APPLICATION_DIR / 'manifest.json'))['content_scripts'][0]['js']
    assert order.index('content/utils.js') < order.index('content/prefetch.js') < order.index('content/scroll.js')


def test_series_lookup_treats_security_check_as_blocked(page):
    """A security-check page with HTTP 200 comes back from fetchSeriesDoc as status 503, which the queue reads as blocked."""
    page.route(SERIES_PAGES, lambda route: _fulfil_series(route, CHALLENGE_HTML))
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-controls')
    result = _isolated(page, '''(async () => {
        const result = await fetchSeriesDoc('https://www.novelupdates.com/series/checked-novel/');
        return { status: result.status, hasDoc: !!result.doc, outcome: queueOutcome(result).outcome };
    })()''')
    assert result == {'status': 503, 'hasDoc': False, 'outcome': 'blocked'}


def test_series_lookup_with_cloudflare_beacon_script_is_still_a_series_page(page):
    """A real series page that carries a Cloudflare challenge-platform script is still read as a series page."""
    body = SERIES_HTML.replace('</body>', '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></body>')
    page.route(SERIES_PAGES, lambda route: _fulfil_series(route, body))
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-controls')
    result = _isolated(page, '''(async () => {
        const result = await fetchSeriesDoc('https://www.novelupdates.com/series/release-that-witch/');
        return { status: result.status, hasDoc: !!result.doc };
    })()''')
    assert result == {'status': 200, 'hasDoc': True}


def test_security_check_does_not_store_empty_sidebar_cover(page):
    """A To Read cover lookup that hits a security check does not record the novel as having no cover."""
    calls = []

    def handler(route):
        calls.append(route.request.url)
        _fulfil_series(route, CHALLENGE_HTML)

    page.route(SERIES_PAGES, handler)
    load_listing(page, {'toRead': ['checked-novel'], 'novelMeta': _tagged(PAGE1_SLUGS)})
    for _ in range(40):
        if any('checked-novel' in url for url in calls):
            break
        page.wait_for_timeout(250)
    assert any('checked-novel' in url for url in calls), calls
    page.wait_for_timeout(1500)  # past the metadata save debounce
    meta = get_storage(page, 'novelMeta').get('novelMeta', {}).get('checked-novel', {})
    assert 'img' not in meta, meta


# ── 2. Cards hidden by the score threshold still get tags ───────────────────

def _threshold_storage(**extra):
    """Storage where a Read reference scores every card, and a threshold of 10 hides them all."""
    novel_meta = {'release-that-witch': {'g': ['fantasy'], 'l': 'chinese', 'd': ['witch'], 'tags': ['kingdom building'], 't': 'Release that Witch'}}
    return {'read': ['release-that-witch'], 'novelMeta': novel_meta, 'minRecommendationScore': 10, **extra}


def test_threshold_hidden_cards_are_queued_for_tags(page):
    """Cards hidden by the score threshold never come into view, but still get their tag lookups."""
    calls = _series_requests(page)
    load_listing(page, _threshold_storage())
    page.wait_for_selector('.nuf-hidden-threshold[data-nuf-slug]', state='attached')
    hidden = page.evaluate('''() => [...document.querySelectorAll('.nuf-hidden-threshold[data-nuf-slug]')].map(e => e.dataset.nufSlug)''')
    assert hidden, 'expected cards hidden by the threshold'
    for _ in range(60):
        if any(f'/series/{hidden[0]}/' in url for url in calls):
            break
        page.wait_for_timeout(250)
    assert any(f'/series/{hidden[0]}/' in url for url in calls), (hidden, calls)


def test_threshold_hidden_cards_are_queued_when_prefetch_turned_back_on(page):
    """Turning prefetch back on queues lookups for cards hidden by the score threshold."""
    calls = _series_requests(page)
    load_listing(page, _threshold_storage(prefetchTagsEnabled=False))
    page.wait_for_selector('.nuf-hidden-threshold[data-nuf-slug]', state='attached')
    hidden = page.evaluate('''() => [...document.querySelectorAll('.nuf-hidden-threshold[data-nuf-slug]')].map(e => e.dataset.nufSlug)''')
    page.wait_for_timeout(500)
    assert not calls, calls
    set_storage(page, {'prefetchTagsEnabled': True})
    for _ in range(60):
        if any(f'/series/{hidden[0]}/' in url for url in calls):
            break
        page.wait_for_timeout(250)
    assert any(f'/series/{hidden[0]}/' in url for url in calls), (hidden, calls)


# ── 3. Infinite-scroll cards get tags ────────────────────────────────────────

def test_infinite_scroll_cards_are_queued_for_tags(page):
    """Cards appended by infinite scroll get tag lookups like page-1 cards."""
    calls = _series_requests(page)
    load_listing(page, {'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-sentinel', state='attached')
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    page.wait_for_selector('[data-nuf-slug="the-desolate-era"]', state='attached', timeout=20000)
    page.evaluate('''() => document.querySelector('[data-nuf-slug="the-desolate-era"]').scrollIntoView()''')
    for _ in range(80):
        if any('/series/the-desolate-era/' in url for url in calls):
            break
        page.wait_for_timeout(250)
    assert any('/series/the-desolate-era/' in url for url in calls), calls


# ── 4. Sidebar redraws after lookups ─────────────────────────────────────────

def _count_sidebar_renders(page):
    _isolated(page, '''(() => {
        globalThis.__sidebarRenders = 0;
        const original = renderToReadSidebar;
        renderToReadSidebar = (...args) => { globalThis.__sidebarRenders++; return original(...args); };
    })()''')


def _apply_series_page(page, slug, times=1):
    _isolated(page, f'''(() => {{
        for (let i = 0; i < {times}; i++) {{
            const doc = new DOMParser().parseFromString({json.dumps(SERIES_HTML)}, 'text/html');
            applySeriesPage({json.dumps(slug)}, doc, null);
        }}
    }})()''')


def test_sidebar_redraw_after_lookups_is_debounced(page):
    """Several To Read lookups finishing together redraw the sidebar once."""
    load_listing(page, {'toRead': ['queued-novel'], 'prefetchTagsEnabled': False,
                        'novelMeta': {'queued-novel': {'img': '', 't': 'Queued'}, **_tagged(PAGE1_SLUGS)}})
    page.wait_for_selector('.nuf-sidebar-item')
    _count_sidebar_renders(page)
    _apply_series_page(page, 'queued-novel', times=3)
    page.wait_for_timeout(800)
    assert _isolated(page, 'globalThis.__sidebarRenders') == 1


def test_sidebar_redraw_waits_for_start_reading_in_progress(page):
    """A lookup finishing while a sidebar Start reading is in progress does not replace that button."""
    load_listing(page, {'toRead': ['queued-novel'], 'prefetchTagsEnabled': False,
                        'novelMeta': {'queued-novel': {'img': '', 't': 'Queued'}, **_tagged(PAGE1_SLUGS)}})
    page.wait_for_selector('.nuf-sidebar-start')
    page.evaluate('''() => {
        const btn = document.querySelector('.nuf-sidebar-start');
        btn.dataset.fetching = '1';
        btn.dataset.testMarker = '1';
    }''')
    _apply_series_page(page, 'queued-novel')
    page.wait_for_timeout(800)
    assert page.locator('.nuf-sidebar-start[data-test-marker]').count() == 1
    page.evaluate('''() => { delete document.querySelector('.nuf-sidebar-start').dataset.fetching; }''')
    expect(page.locator('.nuf-sidebar-start[data-test-marker]')).to_have_count(0, timeout=3000)
    expect(page.locator(SIDEBAR_IMG)).to_have_count(1)


# ── 5. What a lookup stores ──────────────────────────────────────────────────

def test_lookup_of_unbucketed_novel_stores_no_sidebar_fields(page):
    """A tag lookup for a novel in no list stores its tags but not the series page's title, cover or synopsis."""
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-controls')
    _apply_series_page(page, 'unlisted-novel')
    meta = _isolated(page, 'state.novelMeta["unlisted-novel"]')
    assert meta.get('tags'), meta
    assert 't' not in meta and 'img' not in meta and 'desc' not in meta, meta


def test_lookup_of_read_novel_stores_title_and_cover_not_synopsis(page):
    """A lookup for a Read novel stores the title and cover the manager shows, but not the full synopsis."""
    load_listing(page, {'prefetchTagsEnabled': False, 'read': ['read-novel'], 'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-controls')
    _apply_series_page(page, 'read-novel')
    meta = _isolated(page, 'state.novelMeta["read-novel"]')
    assert meta.get('t') == 'Release that Witch', meta
    assert meta.get('img', '').endswith(SERIES_COVER), meta
    assert 'desc' not in meta, meta


def test_series_page_placeholder_cover_reads_as_no_cover(page):
    """A series page showing only a placeholder image yields '' (no cover), never the placeholder URL."""
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-controls')
    img = _isolated(page, '''readSeriesSidebarMeta(new DOMParser().parseFromString(
        '<h1 class="seriestitlenu">X</h1><div class="seriesimg"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></div>',
        'text/html')).img''')
    assert img == '', img


def test_series_page_cover_replaces_stored_placeholder(page):
    """A real series-page cover replaces a placeholder stored earlier from a card."""
    meta = _tagged(PAGE1_SLUGS)
    meta['read-novel'] = {'g': ['action'], 'img': 'data:image/gif;base64,R0lGODlhAQABAAAAACw='}
    load_listing(page, {'prefetchTagsEnabled': False, 'read': ['read-novel'], 'novelMeta': meta})
    page.wait_for_selector('.nuf-controls')
    _apply_series_page(page, 'read-novel')
    stored = _isolated(page, 'state.novelMeta["read-novel"]')
    assert stored.get('img', '').endswith(SERIES_COVER), stored


def test_lookup_of_to_read_novel_stores_synopsis(page):
    """A lookup for a To Read novel stores title, cover and synopsis for the sidebar."""
    load_listing(page, {'prefetchTagsEnabled': False, 'toRead': ['queued-novel'], 'novelMeta': _tagged(PAGE1_SLUGS)})
    page.wait_for_selector('.nuf-controls')
    _apply_series_page(page, 'queued-novel')
    meta = _isolated(page, 'state.novelMeta["queued-novel"]')
    assert meta.get('t') == 'Release that Witch' and meta.get('img', '').endswith(SERIES_COVER), meta
    assert 'Cheng Yan travels' in meta.get('desc', ''), meta


# ── 6. Moved to To Read while a tag lookup waits ─────────────────────────────

def test_novel_moved_to_to_read_while_waiting_gets_cover_priority(page):
    """A novel moved to To Read from elsewhere while its tag lookup waits gets a cover lookup ahead of tag lookups."""
    held = []
    page.route(SERIES_PAGES, lambda route: held.append(route))
    load_listing(page, {'read': ['slow-novel', 'moved-novel'], 'novelMeta': _tagged(PAGE1_SLUGS)})
    for _ in range(40):
        if held:
            break
        page.wait_for_timeout(100)
    assert held and '/series/slow-novel/' in held[0].request.url, [r.request.url for r in held]
    set_storage(page, {'read': ['slow-novel'], 'toRead': ['moved-novel']})
    waiting = []
    for _ in range(30):
        waiting = _queue_snapshot(page)['waiting']
        if any(j['kind'] == 'cover' for j in waiting):
            break
        page.wait_for_timeout(100)
    assert waiting and waiting[0]['kind'] == 'cover' and '/series/moved-novel/' in waiting[0]['url'], waiting


# ── 7. Covers do not depend on the prefetch setting ──────────────────────────

def test_sidebar_cover_fetched_with_prefetch_off(page):
    """With tag prefetch off, a To Read novel with no cover still gets its sidebar cover."""
    _series_requests(page)
    load_listing(page, {'toRead': ['queued-novel'], 'prefetchTagsEnabled': False, 'novelMeta': _tagged(PAGE1_SLUGS)})
    img = page.locator(SIDEBAR_IMG)
    expect(img).to_have_count(1, timeout=15000)
    assert img.get_attribute('src').endswith(SERIES_COVER)


def test_turning_prefetch_off_keeps_waiting_cover_lookups(page):
    """Switching tag prefetch off cancels tag lookups only; waiting cover lookups still run."""
    held = []
    release = {'now': False}

    def handler(route):
        if release['now']:
            _fulfil_series(route)
        else:
            held.append(route)

    page.route(SERIES_PAGES, handler)
    load_listing(page, {'toRead': ['cover-one', 'cover-two'], 'novelMeta': _tagged(PAGE1_SLUGS)})
    for _ in range(40):
        if held:
            break
        page.wait_for_timeout(100)
    assert held
    set_storage(page, {'prefetchTagsEnabled': False})
    page.wait_for_timeout(500)
    release['now'] = True
    for route in held:
        _fulfil_series(route)
    expect(page.locator(SIDEBAR_IMG)).to_have_count(2, timeout=20000)


# ── 8. Appending a page is idempotent ────────────────────────────────────────

def test_page_append_retry_after_error_does_not_duplicate_cards(page):
    """When appending a page fails partway, the retry adds no duplicate cards and counts nothing as filtered."""
    load_listing(page, {'novelMeta': _tagged(PAGE1_SLUGS + ['the-desolate-era', 'renegade-immortal'])})
    page.wait_for_selector('.nuf-sentinel', state='attached')
    _isolated(page, '''(() => {
        const original = injectControls;
        let thrown = false;
        injectControls = (entry, slug, title) => {
            if (slug === 'renegade-immortal' && !thrown) { thrown = true; throw new Error('test failure'); }
            return original(entry, slug, title);
        };
    })()''')
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    sentinel = page.locator('.nuf-sentinel-done')
    expect(sentinel).to_have_count(1, timeout=30000)
    assert page.locator('[data-nuf-slug="the-desolate-era"]').count() == 1
    assert page.locator('[data-nuf-slug="renegade-immortal"]').count() == 1
    assert page.locator('.nuf-entry-wrap [data-nuf-slug="renegade-immortal"]').count() == 1
    assert 'filtered' not in sentinel.text_content(), sentinel.text_content()


# ── 9. No acronyms in the load timer ─────────────────────────────────────────

def test_scroll_load_timer_has_no_acronym_identifiers():
    """The queue's turn-wait message and scroll.js's load timer use no 'eta' acronym."""
    scroll = _read(CONTENT_DIR / 'scroll.js')
    background = _read(CONTENT_DIR.parent / 'background.js')
    assert not re.search(r'\beta\w*\b', scroll), 'acronym identifier left in scroll.js'
    assert not re.search(r'\beta\w*\b', background), 'acronym identifier left in background.js'


# ── Metadata saves under a steady stream of changes ─────────────────────────

def test_metadata_save_is_not_postponed_forever(page):
    """A steady stream of metadata changes still saves within the maximum wait, not only once they stop."""
    load_listing(page, {'prefetchTagsEnabled': False, 'read': ['steady-novel']})
    page.wait_for_selector('.nuf-controls')
    _isolated(page, '''(async () => {
        const end = Date.now() + 6500;
        while (Date.now() < end) {
            state.novelMeta['steady-novel'] = { t: 'Steady', g: ['action'], d: ['x'], l: 'chinese', n: Date.now() };
            scheduleMetaSave();
            await new Promise(r => setTimeout(r, 400));
        }
    })()''')
    stored = get_storage(page, ['novelMeta']).get('novelMeta') or {}
    assert stored.get('steady-novel', {}).get('t') == 'Steady', 'nothing was saved while changes kept arriving'
