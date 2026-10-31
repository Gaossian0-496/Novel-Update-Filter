"""Code-review fixes: card cover/title/synopsis capture (recommend.js) and the To Read sidebar (sidebar.js)."""

import base64
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from helpers import load_listing, get_storage, set_storage, SERIES_PAGES

FIXTURES_DIR = Path(__file__).parent / 'fixtures'
LISTING_HTML = (FIXTURES_DIR / 'listing.html').read_text(encoding='utf-8')
CARD_1_IMG = '<img src="https://cdn.novelupdates.com/imgmid/series_1.jpg">'
CARD_1_COVER = 'https://cdn.novelupdates.com/imgmid/series_1.jpg'
SERIES_FIXTURE_COVER = 'https://www.novelupdates.com/img/series/release-that-witch.jpg'
LAZY_PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
SITE_NO_IMAGE = 'https://www.novelupdates.com/img/noimagefound.jpg'

PAGE_2_HTML = '''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Series Finder</title></head>
<body><div class="w-blog-content other">
  <div class="search_main_box_nu">
    <div class="search_img_nu">
      <img src="https://cdn.novelupdates.com/imgmid/series_5.jpg">
      <div class="search_ratings"><span class="orgcn">CN</span> (4.1)</div>
    </div>
    <div class="search_body_nu">
      <div class="search_title">
        <a href="https://www.novelupdates.com/series/the-desolate-era/">The Desolate Era</a>
      </div>
      <div class="search_genre"><a class="gennew search" href="https://www.novelupdates.com/genre/fantasy/">Fantasy</a></div>
      A young man walks the long road to immortality across a desolate world.
    </div>
  </div>
</div></body></html>'''


def _serve_listing(page, page1_html=None, page2_html=None):
    """Route this page's listing requests to the given HTML (page routes win over the context route)."""
    if page1_html is not None:
        page.route(
            lambda url: '/series-finder' in url and 'pg=2' not in url,
            lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=page1_html),
        )
    if page2_html is not None:
        page.route(
            lambda url: '/series-finder' in url and 'pg=2' in url,
            lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=page2_html),
        )


def _wait_for_stored_meta(page, slug, condition, timeout=8.0):
    """Poll stored novelMeta until condition(meta) is true; return the last meta seen."""
    deadline = time.time() + timeout
    meta = None
    while time.time() < deadline:
        meta = (get_storage(page, ['novelMeta']).get('novelMeta') or {}).get(slug)
        if meta is not None and condition(meta):
            return meta
        time.sleep(0.25)
    return meta


# ── Finding 1: the '' "no cover" marker survives a card visit ────────────────

def test_review_no_cover_marker_kept_when_card_has_no_cover(page):
    """A stored '' cover (no cover) stays '' after its coverless card is captured, so no cover lookup repeats."""
    _serve_listing(page, page1_html=LISTING_HTML.replace(CARD_1_IMG, '', 1))
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'t': 'Release that Witch', 'img': '', 'desc': 'A prince.', 'tags': ['kingdom building']}},
        'prefetchTagsEnabled': True,
    })
    page.wait_for_selector('[data-nuf-slug="release-that-witch"]')
    meta = _wait_for_stored_meta(page, 'release-that-witch', lambda m: bool(m.get('g')))
    assert meta and meta.get('g'), f'Expected genres captured from the card, got {meta!r}'
    assert meta.get('img') == '', f"Expected the no-cover marker '' to be kept, got {meta.get('img')!r}"


# ── Finding 2: placeholders are never stored, and a real cover replaces one ──

@pytest.mark.parametrize('placeholder', [LAZY_PLACEHOLDER, SITE_NO_IMAGE], ids=['lazy-data-url', 'site-no-image'])
def test_review_card_placeholder_cover_not_saved(page, placeholder):
    """A card showing a lazy-load placeholder or the site's no-image picture does not block the series-page cover lookup."""
    _serve_listing(page, page1_html=LISTING_HTML.replace(CARD_1_IMG, f'<img src="{placeholder}">', 1))
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'t': 'Release that Witch', 'tags': ['kingdom building']}},
        'prefetchTagsEnabled': True,
    })
    page.wait_for_selector('[data-nuf-slug="release-that-witch"]')
    meta = _wait_for_stored_meta(page, 'release-that-witch', lambda m: m.get('img') == SERIES_FIXTURE_COVER)
    assert meta and meta.get('img') == SERIES_FIXTURE_COVER, (
        f'Expected the series-page cover after the placeholder was ignored, got {meta and meta.get("img")!r}'
    )
    expect(page.locator('.nuf-sidebar-img').first).to_have_attribute('src', SERIES_FIXTURE_COVER, timeout=5000)


@pytest.mark.parametrize('placeholder', [LAZY_PLACEHOLDER, SITE_NO_IMAGE], ids=['lazy-data-url', 'site-no-image'])
def test_review_real_card_cover_replaces_stored_placeholder(page, placeholder):
    """A stored placeholder cover is replaced by the real cover on the novel's card."""
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {
            't': 'Release that Witch', 'g': ['fantasy'], 'd': ['prince'], 'l': 'chinese',
            'tags': ['kingdom building'], 'img': placeholder,
        }},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('[data-nuf-slug="release-that-witch"]')
    expect(page.locator('.nuf-sidebar-img').first).to_have_attribute('src', CARD_1_COVER, timeout=3000)
    meta = _wait_for_stored_meta(page, 'release-that-witch', lambda m: m.get('img') == CARD_1_COVER)
    assert meta and meta.get('img') == CARD_1_COVER, f'Expected the card cover to replace the placeholder, got {meta!r}'


# ── Finding 3: the sidebar redraws when a card fills in a To Read novel ──────

def test_review_sidebar_redraws_when_scrolled_card_adds_cover(page):
    """A To Read novel's card arriving by infinite scroll fills its sidebar cover, title and synopsis straight away."""
    _serve_listing(page, page2_html=PAGE_2_HTML)
    # Cover lookups run even with prefetch off; keep this test about the card alone
    page.route(SERIES_PAGES, lambda route: route.fulfill(status=404, body=''))
    load_listing(page, {
        'toRead': ['the-desolate-era'],
        'novelMeta': {'the-desolate-era': {'g': ['fantasy'], 'd': ['immortality'], 'l': 'chinese', 'tags': ['cultivation']}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-sidebar-item')
    expect(page.locator('.nuf-sidebar-title').first).to_have_text('the-desolate-era')
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    page.wait_for_selector('[data-nuf-slug="the-desolate-era"]', state='attached', timeout=10000)
    # Well inside the 2 s metadata save debounce, so only a direct redraw can pass
    expect(page.locator('.nuf-sidebar-title').first).to_have_text('The Desolate Era', timeout=1000)
    expect(page.locator('.nuf-sidebar-img').first).to_have_attribute(
        'src', 'https://cdn.novelupdates.com/imgmid/series_5.jpg', timeout=1000)
    expect(page.locator('.nuf-sidebar-desc').first).to_contain_text('desolate world', timeout=1000)


# ── Finding 4: the sidebar title comes from the card's title link ───────────

def test_review_sidebar_title_from_title_link_not_cover_link(page):
    """When a card's cover is wrapped in a series link, the sidebar title still comes from the title link."""
    wrapped = (f'<a href="https://www.novelupdates.com/series/release-that-witch/">Cover</a>'
               f'<a href="https://www.novelupdates.com/series/release-that-witch/">{CARD_1_IMG}</a>')
    _serve_listing(page, page1_html=LISTING_HTML.replace(CARD_1_IMG, wrapped, 1))
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'g': ['fantasy'], 'd': ['prince'], 'l': 'chinese', 'tags': ['kingdom building']}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('[data-nuf-slug="release-that-witch"]', state='attached')
    expect(page.locator('.nuf-sidebar-title').first).to_have_text('Release that Witch', timeout=1000)
    meta = _wait_for_stored_meta(page, 'release-that-witch', lambda m: m.get('img') == CARD_1_COVER)
    assert meta and meta.get('t') == 'Release that Witch', f'Expected the title link text stored, got {meta!r}'


# ── Finding 5: novels known to have no cover are left alone ─────────────────

def test_review_sidebar_keeps_no_cover_marker_across_meta_changes(page):
    """A To Read novel marked as having no cover keeps '' and its placeholder through cross-tab novelMeta changes."""
    novel_meta = {'not-on-this-page': {'t': 'Elsewhere Novel', 'img': '', 'tags': ['magic']}}
    load_listing(page, {
        'toRead': ['not-on-this-page'],
        'novelMeta': novel_meta,
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-sidebar-item')
    for i in range(3):
        novel_meta = {**novel_meta, f'other-{i}': {'t': f'Other {i}'}}
        set_storage(page, {'novelMeta': novel_meta})
        time.sleep(0.2)
    expect(page.locator('.nuf-sidebar-img-placeholder')).to_have_count(1)
    expect(page.locator('.nuf-sidebar-title').first).to_have_text('Elsewhere Novel')
    meta = (get_storage(page, ['novelMeta']).get('novelMeta') or {}).get('not-on-this-page')
    assert meta.get('img') == '', f"Expected '' kept, got {meta!r}"


# ── Sidebar covers that fail to load ────────────────────────────────────────

PNG_1PX = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=')


def _to_read_with_cover(page, img, title='The Desolate Era'):
    load_listing(page, {
        'toRead': ['the-desolate-era'],
        'novelMeta': {'the-desolate-era': {'g': ['fantasy'], 'd': ['immortality'], 'l': 'chinese',
                                           'tags': ['cultivation'], 'img': img, 't': title}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-sidebar-item', state='attached')


def test_review_sidebar_broken_cover_falls_back_to_placeholder(page):
    """A sidebar cover that fails to load is swapped for the grey placeholder, not squashed alt text."""
    page.route('**/broken-cover.jpg', lambda route: route.fulfill(status=404, body=''))
    _to_read_with_cover(page, 'https://www.novelupdates.com/img/broken-cover.jpg')
    expect(page.locator('.nuf-sidebar-item img.nuf-sidebar-img')).to_have_count(0, timeout=5000)
    expect(page.locator('.nuf-sidebar-item .nuf-sidebar-img-placeholder')).to_have_count(1)


def test_review_sidebar_cover_alt_is_the_plain_title(page):
    """The cover's alt text is the title as written, not HTML-escaped a second time."""
    page.route('**/ok-cover.png', lambda route: route.fulfill(status=200, content_type='image/png', body=PNG_1PX))
    _to_read_with_cover(page, 'https://www.novelupdates.com/img/ok-cover.png', title='Swords & Sorcery')
    expect(page.locator('.nuf-sidebar-item img.nuf-sidebar-img')).to_have_attribute('alt', 'Swords & Sorcery')
