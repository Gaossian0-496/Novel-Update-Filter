"""Fifth review round: Undo puts back exactly what was there before, the series
page and start-up handle security checks, language detection, the Request
queue page's timing, toast placement and typing in Chinese, Japanese or
Korean."""

from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, load_series, get_storage, SERIES_PAGES
from test_review_prefetch_scroll import _isolated

CONTENT_DIR = Path(__file__).parent.parent / 'application' / 'content'
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
SLUG = 'release-that-witch'
CARD = f'.nuf-entry-wrap:has([data-nuf-slug="{SLUG}"])'
CHALLENGE_SCRIPT = '<script src="/cdn-cgi/challenge-platform/h/b/scripts/jsd/main.js"></script>'
CHALLENGE_HTML = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>'
                  '<h1>www.novelupdates.com</h1><div id="challenge-running"></div></body></html>')


def _undo(page):
    page.locator('.nuf-toast-undo').click()
    page.wait_for_timeout(400)


# ── Undo puts back what was there before ────────────────────────────────────

def test_series_page_undo_puts_back_the_previous_list(page):
    """On a series page, Read then Undo returns a Not Finished novel to Not Finished, not to no list."""
    load_series(page, {'notFinished': [SLUG]})
    page.locator('.nuf-controls .nuf-btn[data-nuf-btn="read"]').click()
    _undo(page)
    stored = get_storage(page, ['read', 'notFinished'])
    assert SLUG in stored['notFinished'] and SLUG not in stored['read'], stored


def test_series_page_undo_puts_back_to_read(page):
    """On a series page, Read then Undo on a To Read novel puts it back in To Read, saved, not only in memory."""
    load_series(page, {'toRead': [SLUG]})
    page.locator('.nuf-controls .nuf-btn[data-nuf-btn="read"]').click()
    _undo(page)
    stored = get_storage(page, ['read', 'toRead'])
    assert SLUG in stored['toRead'] and SLUG not in stored['read'], stored


def test_listing_undo_puts_a_to_read_card_back_in_to_read(page):
    """Marking a To Read card as Read, then Undo, puts it back in To Read with its glow."""
    load_listing(page, {'prefetchTagsEnabled': False, 'toRead': [SLUG]})
    # The pulsing To Read glow keeps Playwright's click waiting; this test is about what Undo restores
    page.locator(f'{CARD} .nuf-btn[data-nuf-btn="read"]').dispatch_event('click')
    _undo(page)
    assert SLUG in get_storage(page, ['toRead'])['toRead']
    assert 'nuf-to-read' in page.locator(f'.search_main_box_nu[data-nuf-slug="{SLUG}"]').get_attribute('class')


def test_undo_also_takes_back_the_rating_given_in_that_toast(page):
    """A rating given in the toast is undone with the mark, so it cannot skew scores later."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.locator(f'{CARD} .nuf-btn[data-nuf-btn="read"]').click()
    page.locator('.nuf-toast-rating-btn').first.click()
    _undo(page)
    assert SLUG not in (get_storage(page, ['novelReviews']).get('novelReviews') or {})


# ── Security checks ──────────────────────────────────────────────────────────

def test_listing_with_cloudflare_script_but_cards_still_gets_the_extension(page):
    """A normal listing that also carries Cloudflare's script still gets bucket buttons and the rest."""
    listing = (FIXTURES_DIR / 'listing.html').read_text(encoding='utf-8').replace('</body>', CHALLENGE_SCRIPT + '</body>')
    page.route(lambda url: 'series-finder' in url,
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=listing))
    load_listing(page)
    page.wait_for_selector('.nuf-controls', timeout=5000)


def test_series_url_answered_by_a_security_check_saves_no_title(page):
    """A security check served at a series URL is left alone: no buttons, and its heading is not saved as the title."""
    page.route(SERIES_PAGES, lambda route: route.fulfill(status=200, content_type='text/html', body=CHALLENGE_HTML))
    load_series(page)
    page.wait_for_timeout(1000)
    assert page.locator('.nuf-controls').count() == 0
    meta = (get_storage(page, ['novelMeta']).get('novelMeta') or {}).get(SLUG, {})
    assert meta.get('t') != 'www.novelupdates.com', meta


# ── Language, queue page timing, toast placement ────────────────────────────

def test_language_comes_from_the_origin_label_not_the_synopsis(page):
    """A Korean novel whose synopsis mentions JP is still Korean."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    lang = _isolated(page, '''(() => {
        const card = document.createElement('div');
        card.innerHTML = '<div class="search_body_nu">Reincarnated as a JP idol</div>'
                       + '<div class="search_ratings"><span class="orgcn">KR</span> (4.5)</div>';
        return getLangFromEntry(card);
    })()''')
    assert lang == 'korean', lang


def test_queue_page_uses_the_lookup_gap_when_only_lookups_wait(page, extension_id):
    """With only tag lookups waiting, the next-request countdown uses their 2 s gap, not the 1 s page gap."""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    page.goto(f'chrome-extension://{extension_id}/queue.html')
    expect(page.locator('#status')).to_have_attribute('data-state', 'idle')
    headline = page.evaluate('''() => {
        const t = Date.now();
        snapshot = { now: t, baseGapMs: 1000, backgroundGapMs: 2000, paceDelay: 0, successes: 0, recoveryAfter: 5,
            backgroundStopped: false, backgroundHoldUntil: 0, notBefore: 0, lastGrantAt: t - 1500, inFlight: null,
            history: [], waitingCounts: { prefetch: 1 }, waitingTotal: 1, progress: { done: 0, total: 0 },
            waiting: [{ key: 'k', kind: 'prefetch', url: 'https://www.novelupdates.com/series/x/', since: t, attempts: 0,
                        retryAt: null, paused: false }] };
        clockOffset = 0;
        render();
        return document.getElementById('status-headline').textContent;
    }''')
    assert '0.5 s' in headline, headline


def test_toast_for_a_card_near_the_bottom_stays_on_screen(page):
    """A card marked near the bottom of the window gets its toast (and Undo) fully on screen."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    card = page.locator(CARD)
    box = card.bounding_box()
    viewport = page.viewport_size['height']
    page.evaluate(f'window.scrollBy(0, {box["y"] - (viewport - 60)})')
    page.locator(f'{CARD} .nuf-btn[data-nuf-btn="read"]').click()
    toast = page.locator('.nuf-toast.nuf-toast-visible')
    expect(toast).to_be_visible()
    tbox = toast.bounding_box()
    assert tbox['y'] + tbox['height'] <= viewport, (tbox, viewport)


# ── Imports and typing ──────────────────────────────────────────────────────

def test_enter_while_typing_in_chinese_japanese_or_korean_does_not_close_the_toast():
    """Enter that confirms a word in an input method is not taken as Save."""
    toast = (CONTENT_DIR / 'toast.js').read_text(encoding='utf-8')
    assert "e.key === 'Enter' && !e.isComposing" in toast
