"""Sixth review round: Undo after a card has collapsed, moving novels out of the
sidebar, scores left behind when recommendations are cleared, covers another
tab removed, waiting for scores while scrolling, rating from the series page
and importing old backups."""

import json
from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, load_series, get_storage, set_storage, wait_for_meta_save
from test_review_prefetch_scroll import _isolated
from test_manager import load_manager

CONTENT_DIR = Path(__file__).parent.parent / 'application' / 'content'
SLUG = 'release-that-witch'
CARD = f'.nuf-entry-wrap:has([data-nuf-slug="{SLUG}"])'
OLD_BACKUP = {'read': ['x'], 'novelReviews': {'x': {'rating': 9, 'comment': 'great'}}}


def _section(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


# ── Undo after the card has collapsed ───────────────────────────────────────

def test_undo_after_the_card_has_collapsed_brings_it_back(page):
    """Undo pressed after the 0.35 s collapse (the usual case) puts the card and its buttons back on the page."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.locator(f'{CARD} .nuf-btn[data-nuf-btn="read"]').click()
    page.wait_for_timeout(800)  # the card and its wrapper have been removed by now
    assert page.locator(f'.search_main_box_nu[data-nuf-slug="{SLUG}"]').count() == 0
    page.locator('.nuf-toast-undo').click()
    expect(page.locator(f'.search_main_box_nu[data-nuf-slug="{SLUG}"]')).to_be_visible()
    expect(page.locator(f'{CARD} .nuf-controls')).to_have_count(1)
    assert SLUG not in get_storage(page, ['read'])['read']


# ── Moving a novel out of the sidebar ───────────────────────────────────────

def _sidebar_with_lookup_running(page):
    load_listing(page, {'prefetchTagsEnabled': False, 'toRead': ['moved-novel', 'other-novel'],
                        'novelMeta': {'moved-novel': {'t': 'Moved', 'img': ''},
                                      'other-novel': {'t': 'Other', 'img': ''}}})
    page.wait_for_selector('.nuf-sidebar-item', state='attached')
    # A Start reading lookup in progress holds back sidebar redraws
    page.evaluate("document.querySelectorAll('.nuf-sidebar-start')[1].dataset.fetching = '1'")


def test_sidebar_move_removes_the_novel_at_once_during_a_lookup(page):
    """A moved novel leaves the sidebar straight away, even while another novel's Start reading lookup runs."""
    _sidebar_with_lookup_running(page)
    page.locator('.nuf-sidebar-item').first.locator('.nuf-sidebar-bucket-btn[data-nuf-btn="read"]').dispatch_event('click')
    expect(page.locator('.nuf-sidebar-item')).to_have_count(1)
    expect(page.locator('.nuf-sidebar-title')).to_have_text(['Other'])
    expect(page.locator('.nuf-sidebar-tab')).to_contain_text('(1)')


def test_sidebar_move_puts_the_novel_in_one_list_only(page):
    """Two move buttons pressed for the same novel leave it in the last list chosen, not in both."""
    _sidebar_with_lookup_running(page)
    item = page.locator('.nuf-sidebar-item').first
    read_btn = item.locator('.nuf-sidebar-bucket-btn[data-nuf-btn="read"]').element_handle()
    nf_btn = item.locator('.nuf-sidebar-bucket-btn[data-nuf-btn="nf"]').element_handle()
    read_btn.dispatch_event('click')
    nf_btn.dispatch_event('click')
    page.wait_for_timeout(300)
    stored = get_storage(page, ['read', 'notFinished', 'toRead'])
    assert 'moved-novel' not in stored['read'] and 'moved-novel' in stored['notFinished'], stored
    assert 'moved-novel' not in stored['toRead'], stored


def test_sidebar_remove_takes_the_novel_out_at_once_during_a_lookup(page):
    """The × button removes the novel from the sidebar straight away too."""
    _sidebar_with_lookup_running(page)
    page.locator('.nuf-sidebar-item').first.locator('.nuf-sidebar-remove').dispatch_event('click')
    expect(page.locator('.nuf-sidebar-item')).to_have_count(1)


# ── Scores left behind ──────────────────────────────────────────────────────

def test_cleared_scores_do_not_hide_cards_later(page):
    """After recommendations are switched off, setting a score threshold hides no card by its old score."""
    load_listing(page, {'prefetchTagsEnabled': False, 'read': ['reference-novel'],
                        'novelMeta': {'reference-novel': {'t': 'Reference', 'g': ['action'], 'l': 'chinese', 'd': ['cultivation']}}})
    page.wait_for_selector('.nuf-score-badge')
    set_storage(page, {'recommendationsEnabled': False})
    expect(page.locator('.nuf-score-badge')).to_have_count(0)
    set_storage(page, {'minRecommendationScore': 10})
    page.wait_for_timeout(300)
    assert page.locator('.nuf-hidden-threshold').count() == 0


# ── A cover placeholder another tab removed ─────────────────────────────────

def test_placeholder_cover_removed_by_another_tab_stays_removed(page):
    """Another tab dropped a stored placeholder cover so the cover lookup can run; this tab does not copy it back."""
    load_listing(page, {'prefetchTagsEnabled': False,
                        'novelMeta': {'x-novel': {'t': 'X', 'img': 'https://www.novelupdates.com/img/noimage.jpg'}}})
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)
    set_storage(page, {'novelMeta': {'x-novel': {'t': 'X'}}})
    page.wait_for_timeout(300)
    assert 'img' not in _isolated(page, "state.novelMeta['x-novel']")


def test_real_cover_only_this_tab_has_is_still_kept(page):
    """A real cover only this tab knows about is still filled in when another tab saves."""
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': {'x-novel': {'t': 'X'}}})
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)
    _isolated(page, "state.novelMeta['x-novel'].img = 'https://cdn.novelupdates.com/images/x.jpg'")
    set_storage(page, {'novelMeta': {'x-novel': {'t': 'X', 'tags': ['magic']}}})
    page.wait_for_timeout(300)
    assert _isolated(page, "state.novelMeta['x-novel'].img") == 'https://cdn.novelupdates.com/images/x.jpg'


# ── Waiting for scores while scrolling ──────────────────────────────────────

def test_scroll_does_not_wait_again_for_cards_it_gave_up_on():
    """Cards still unscored after a 60 s wait are not waited for again on every later page."""
    scroll = (CONTENT_DIR / 'scroll.js').read_text(encoding='utf-8')
    wait = _section(scroll, 'function _waitForScores', '\n}\n')
    assert 'scroll.scoreWaitGaveUp.add(' in wait
    guard = _section(scroll, 'async function fetchNextPage', '_waitForScores(sentinel, pending);')
    assert '!scroll.scoreWaitGaveUp.has(' in guard
    assert 'scoreWaitExpired' not in scroll


# ── Rating from the series page ─────────────────────────────────────────────

def test_series_page_current_list_opens_the_rating_toast(page):
    """Clicking the list a novel is already in opens the toast, so it can still be rated there."""
    load_series(page, {'read': [SLUG]})
    page.locator('.nuf-controls .nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.nuf-toast.nuf-toast-visible')).to_be_visible()
    page.locator('.nuf-toast-rating-btn').nth(3).click()
    page.wait_for_timeout(300)
    assert get_storage(page, ['novelReviews'])['novelReviews'][SLUG]['rating'] == 4


def test_series_page_undo_after_clicking_the_current_list_keeps_it(page):
    """Undo after clicking the current list leaves the novel in that list and its button active."""
    load_series(page, {'read': [SLUG]})
    page.locator('.nuf-controls .nuf-btn[data-nuf-btn="read"]').click()
    page.locator('.nuf-toast-undo').click()
    page.wait_for_timeout(300)
    assert SLUG in get_storage(page, ['read'])['read']
    assert 'nuf-btn-active' in page.locator('.nuf-controls .nuf-btn[data-nuf-btn="read"]').get_attribute('class')


# ── Importing old backups ───────────────────────────────────────────────────

def _popup_import(page, extension_id, payload):
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
    page.locator('#import-toggle').click()
    page.locator('#import-text').fill(json.dumps(payload))
    page.locator('#do-import').click()
    page.wait_for_timeout(400)


def _manager_import(page, extension_id, payload, storage=None):
    load_manager(page, extension_id, storage or {'ratingScaleVersion': 2})
    page.locator('#import-toggle').click()
    page.locator('#import-text').fill(json.dumps(payload))
    page.locator('#do-import').click()
    page.wait_for_timeout(400)


def test_popup_import_converts_old_ratings_as_it_saves_them(page, extension_id):
    """An old backup's 1-10 ratings are saved already converted, so open tabs never hold the old scale."""
    page._sw.evaluate('() => new Promise(r => chrome.storage.local.set({ ratingScaleVersion: 2 }, r))')
    _popup_import(page, extension_id, OLD_BACKUP)
    stored = get_storage(page, ['novelReviews', 'ratingScaleVersion'])
    assert stored['novelReviews']['x'] == {'rating': 5, 'comment': 'great'}, stored
    assert stored['ratingScaleVersion'] == 2


def test_manager_import_converts_old_ratings_as_it_saves_them(page, extension_id):
    """The manager's import converts an old backup's ratings the same way."""
    _manager_import(page, extension_id, OLD_BACKUP)
    stored = get_storage(page, ['novelReviews', 'ratingScaleVersion'])
    assert stored['novelReviews']['x']['rating'] == 5, stored
    assert stored['ratingScaleVersion'] == 2


def test_import_of_current_ratings_leaves_them_alone(page, extension_id):
    """A backup already on the 1-5 scale is imported as it is."""
    _manager_import(page, extension_id, {'read': ['x'], 'novelReviews': {'x': {'rating': 3}}, 'ratingScaleVersion': 2})
    assert get_storage(page, ['novelReviews'])['novelReviews']['x']['rating'] == 3


def test_import_without_ratings_does_not_mark_stored_ratings_old(page, extension_id):
    """A backup with an old scale number but no ratings cannot make the stored ratings get halved."""
    _manager_import(page, extension_id, {'read': ['x'], 'ratingScaleVersion': 1},
                    {'ratingScaleVersion': 2, 'novelReviews': {'y': {'rating': 4}}})
    stored = get_storage(page, ['novelReviews', 'ratingScaleVersion'])
    assert stored['ratingScaleVersion'] == 2 and stored['novelReviews']['y']['rating'] == 4, stored


def test_an_open_tab_takes_imported_ratings_on_the_current_scale(page, extension_id):
    """A listing tab open during the import holds the converted rating, so its next save is not halved again."""
    load_listing(page, {'prefetchTagsEnabled': False, 'ratingScaleVersion': 2})
    page.wait_for_selector('.nuf-controls')
    popup = page.context.new_page()
    try:
        _popup_import(popup, extension_id, OLD_BACKUP)
    finally:
        popup.close()
    page.wait_for_timeout(300)
    assert _isolated(page, "state.novelReviews.x.rating") == 5
