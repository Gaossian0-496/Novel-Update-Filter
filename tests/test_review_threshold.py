"""With a score threshold set, a card is shown or hidden once, when its score is
final, so cards don't appear and disappear as lookups for other novels finish."""

from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, set_storage, SERIES_PAGES
from test_review_prefetch_scroll import _isolated, PAGE1_SLUGS

# A Read reference with tags, so every card gets a score
REF = {'g': ['fantasy'], 'l': 'chinese', 'd': ['kingdom'], 'tags': ['kingdom building', 'magic'], 't': 'Reference'}
# Page-1 cards with different tags, so their scores spread across 1-10
CARD_TAGS = {
    'release-that-witch': ['kingdom building', 'magic'],
    'against-the-gods': ['harem'],
    'i-shall-seal-the-heavens': ['cultivation', 'magic'],
    'lord-of-the-mysteries': ['mystery'],
}
PAGE2_HTML = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')
PENDING = '.nuf-score-pending'
HIDDEN = '.nuf-hidden-threshold'


def _all_cards_final(threshold):
    meta = {slug: {'g': ['fantasy'], 'l': 'chinese', 'd': ['x'], 'tags': tags, 't': slug} for slug, tags in CARD_TAGS.items()}
    meta['reference-novel'] = REF
    return {'read': ['reference-novel'], 'novelMeta': meta, 'minRecommendationScore': threshold}


def _visibility(page):
    return page.evaluate('''() => Object.fromEntries([...document.querySelectorAll('.search_main_box_nu[data-nuf-slug]')]
        .map(e => [e.dataset.nufSlug, !e.classList.contains('nuf-hidden-threshold')]))''')


def test_card_waits_hidden_until_its_score_is_final(page):
    """A card whose tags are still being looked up stays hidden, even if its score so far would pass."""
    page.route(SERIES_PAGES, lambda route: None)  # lookups never answer
    load_listing(page, {'read': ['reference-novel'], 'novelMeta': {'reference-novel': REF}, 'minRecommendationScore': 1})
    page.wait_for_selector('.nuf-score-label', state='attached')
    for slug in PAGE1_SLUGS:
        card = page.locator(f'.search_main_box_nu[data-nuf-slug="{slug}"]')
        assert 'nuf-score-pending' in card.get_attribute('class'), slug
        assert 'nuf-hidden-threshold' in card.get_attribute('class'), slug


def test_card_appears_once_its_lookup_finishes(page):
    """When a card's tags arrive, it is shown (threshold 1 passes every score) and stops being pending."""
    load_listing(page, {'read': ['reference-novel'], 'novelMeta': {'reference-novel': REF}, 'minRecommendationScore': 1})
    page.wait_for_function('''() => {
        const c = document.querySelector('.search_main_box_nu[data-nuf-slug="release-that-witch"]');
        return c && !c.classList.contains('nuf-score-pending') && !c.classList.contains('nuf-hidden-threshold');
    }''', timeout=20000)


def test_card_appears_when_its_lookup_fails(page):
    """A lookup that fails still makes the score final, so the card is decided instead of hidden forever."""
    page.route(SERIES_PAGES, lambda route: route.fulfill(status=404, body=''))
    load_listing(page, {'read': ['reference-novel'], 'novelMeta': {'reference-novel': REF}, 'minRecommendationScore': 1})
    page.wait_for_function('''() => {
        const c = document.querySelector('.search_main_box_nu[data-nuf-slug="release-that-witch"]');
        return c && !c.classList.contains('nuf-score-pending') && !c.classList.contains('nuf-hidden-threshold');
    }''', timeout=20000)


def test_decided_card_keeps_its_visibility_when_scores_shift(page):
    """Once decided, a card is not shown or hidden again by a later score change (other novels' tags arriving)."""
    load_listing(page, _all_cards_final(5))
    page.wait_for_selector('.nuf-score-label', state='attached')
    assert page.locator(PENDING).count() == 0
    before = _visibility(page)
    # Flip every card's score to the other side of the threshold, as a background rescore could
    _isolated(page, '''(() => {
        for (const e of document.querySelectorAll('.search_main_box_nu[data-nuf-slug]')) {
            applyScoreThreshold(e, e.classList.contains('nuf-hidden-threshold') ? 10 : 1);
        }
    })()''')
    assert _visibility(page) == before


def test_changing_the_threshold_decides_again(page):
    """Changing the threshold is the user's own action, so cards are decided again against it."""
    load_listing(page, _all_cards_final(1))
    page.wait_for_selector('.nuf-score-label', state='attached')
    assert all(_visibility(page).values()), _visibility(page)
    set_storage(page, {'minRecommendationScore': 10})
    expect(page.locator(HIDDEN).first).to_be_attached(timeout=3000)
    assert not all(_visibility(page).values())


def test_scroll_waits_for_pending_scores_instead_of_skipping(page):
    """A newly loaded page whose cards are all still being scored waits, rather than loading the next page at once."""
    page.route(SERIES_PAGES, lambda route: None)  # lookups never answer
    # Page 2 with a next page of its own, so it is not the end of the results
    page2 = PAGE2_HTML.replace('</body>', '<div class="digg_pagination"><a rel="next" '
                               'href="https://www.novelupdates.com/series-finder/?pg=3">»</a></div></body>')
    page.route(lambda url: 'series-finder' in url and 'pg=2' in url,
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=page2))
    meta = {slug: {'g': ['fantasy'], 'l': 'chinese', 'd': ['x'], 'tags': ['magic'], 't': slug} for slug in PAGE1_SLUGS}
    meta['reference-novel'] = REF
    load_listing(page, {'read': ['reference-novel'], 'novelMeta': meta, 'minRecommendationScore': 1})
    page.wait_for_selector('.nuf-score-label', state='attached')
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    expect(page.locator('.nuf-sentinel-loading')).to_have_text('Scoring novels…', timeout=10000)


def test_first_page_all_pending_does_not_load_the_next_page(page):
    """When every card on the first page is still being scored, scroll waits instead of loading page 2."""
    page.route(SERIES_PAGES, lambda route: None)  # lookups never answer
    listing_requests = []
    page.on('request', lambda r: listing_requests.append(r.url) if 'pg=2' in r.url else None)
    load_listing(page, {'read': ['reference-novel'], 'novelMeta': {'reference-novel': REF}, 'minRecommendationScore': 1})
    page.wait_for_selector('.nuf-score-label', state='attached')
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    expect(page.locator('.nuf-sentinel-loading')).to_have_text('Scoring novels…', timeout=5000)
    page.wait_for_timeout(1500)
    assert listing_requests == [], listing_requests
