"""Code-review fixes: Start reading opens its tab inside the click, and the
bucket toast's comment input lets go of focus when the toast hides."""

import re
from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, get_storage, SERIES_PAGES

FIXTURES_DIR = Path(__file__).parent / 'fixtures'
SERIES_HTML = (FIXTURES_DIR / 'series.html').read_text(encoding='utf-8')
NO_CHAPTERS_HTML = '<!DOCTYPE html><html><head><title>Empty</title></head><body><p>No chapters</p></body></html>'
START_BTN = '.nuf-btn[data-nuf-btn="start"]'
COMMENT = '.nuf-toast-comment'


def _hold_series_routes(page):
    """Route series-page requests into a list without answering them."""
    held = []
    page.route(SERIES_PAGES, lambda route: held.append(route))
    return held


# ── Start reading: blank tab opened synchronously ────────────────────────────

def test_start_reading_uncached_opens_tab_before_lookup_resolves(page):
    """Without a cached chapter URL, the tab opens inside the click, before the series page answers."""
    held = _hold_series_routes(page)
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector(START_BTN)
    with page.expect_popup(timeout=3000) as popup_info:
        page.locator(START_BTN).first.click()
    popup = popup_info.value
    try:
        assert popup.url == 'about:blank'
        page.wait_for_timeout(300)
        assert held, 'expected the series lookup to be pending'
        for route in held:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=SERIES_HTML)
        popup.wait_for_url(re.compile(r'extnu/100001'), timeout=10000)
        expect(page.locator(START_BTN).first).to_have_text('Start reading →')
    finally:
        popup.close()


def test_start_reading_uncached_lookup_failure_closes_blank_tab(page):
    """When the lookup finds no first chapter, the blank tab is closed and the button shows '?'."""
    page.route(SERIES_PAGES, lambda route: route.fulfill(
        status=200, content_type='text/html; charset=utf-8', body=NO_CHAPTERS_HTML))
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector(START_BTN)
    with page.expect_popup(timeout=3000) as popup_info:
        page.locator(START_BTN).first.click()
    popup = popup_info.value
    try:
        popup.wait_for_event('close', timeout=10000)
        expect(page.locator(START_BTN).first).to_have_text('?')
    finally:
        if not popup.is_closed():
            popup.close()


def test_start_reading_cached_opens_chapter_directly(page):
    """With a cached chapter URL, the tab opens straight at the chapter with no series request."""
    held = _hold_series_routes(page)
    load_listing(page, {
        'prefetchTagsEnabled': False,
        'novelMeta': {'release-that-witch': {
            'g': [], 'l': 'chinese', 'd': [],
            'c1url': 'https://www.novelupdates.com/extnu/77777/',
        }},
    })
    page.wait_for_selector(START_BTN)
    with page.expect_popup(timeout=3000) as popup_info:
        page.locator(START_BTN).first.click()
    popup = popup_info.value
    try:
        popup.wait_for_url(re.compile(r'extnu/77777'), timeout=5000)
        assert not held, 'cached path must not request the series page'
    finally:
        popup.close()


# ── Toast: comment input focus after the toast hides ─────────────────────────

def _mark_first_read_and_type(page, text):
    """Mark the first card Read and type a comment into the toast."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))
    page.locator(COMMENT).click()
    page.locator(COMMENT).fill(text)


def test_toast_enter_save_releases_comment_focus(page):
    """Pressing Enter in the comment saves, hides the toast and leaves the input unfocused."""
    _mark_first_read_and_type(page, 'great start')
    page.locator(COMMENT).press('Enter')
    expect(page.locator('.nuf-toast')).not_to_have_class(re.compile('nuf-toast-visible'))
    focused = page.evaluate(
        "() => document.activeElement?.classList.contains('nuf-toast-comment') ?? false")
    assert focused is False


def test_toast_close_button_releases_comment_focus(page):
    """Dismissing the toast while the comment input has focus leaves the input unfocused."""
    _mark_first_read_and_type(page, 'meh')
    # Dismiss programmatically so the close-button mousedown cannot blur first.
    page.evaluate("() => document.querySelector('.nuf-toast-close').dispatchEvent(new MouseEvent('click'))")
    expect(page.locator('.nuf-toast')).not_to_have_class(re.compile('nuf-toast-visible'))
    focused = page.evaluate(
        "() => document.activeElement?.classList.contains('nuf-toast-comment') ?? false")
    assert focused is False


def test_toast_blur_on_hidden_toast_does_not_persist_or_restart(page):
    """A blur arriving after the toast has hidden neither saves the comment nor restarts the countdown."""
    _mark_first_read_and_type(page, 'first thoughts')
    page.locator(COMMENT).press('Enter')
    expect(page.locator('.nuf-toast')).not_to_have_class(re.compile('nuf-toast-visible'))
    page.wait_for_timeout(300)
    before = page.evaluate("() => document.querySelector('.nuf-toast-progress').style.cssText")
    page.evaluate("""() => {
        const input = document.querySelector('.nuf-toast-comment');
        input.value = 'stray text';
        input.dispatchEvent(new FocusEvent('blur'));
    }""")
    page.wait_for_timeout(500)
    after = page.evaluate("() => document.querySelector('.nuf-toast-progress').style.cssText")
    assert after == before, 'blur on a hidden toast restarted the countdown'
    reviews = get_storage(page, ['novelReviews']).get('novelReviews', {})
    assert reviews.get('release-that-witch', {}).get('comment') == 'first thoughts'
