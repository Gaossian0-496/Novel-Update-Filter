"""Third review round: edge cases in the request client, the score threshold,
infinite scroll's wait for scores, and how novelMeta is saved and shared."""

from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, set_storage, SERIES_PAGES
from test_review_prefetch_scroll import _isolated

APPLICATION_DIR = Path(__file__).parent.parent / 'application'
CONTENT_DIR = APPLICATION_DIR / 'content'
REF = {'g': ['fantasy'], 'l': 'chinese', 'd': ['kingdom'], 'tags': ['magic'], 't': 'Reference'}


def _read(name):
    return (CONTENT_DIR / name).read_text(encoding='utf-8')


def _section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def test_one_off_settled_before_its_turn_runs_with_its_url():
    """A page download cancelled before its turn is run with its own address, not the settled message (which has none)."""
    settled = _section(_read('request-queue.js'), "} else if (msg.type === 'settled') {", 'for (const cb of settledListeners)')
    assert 'single.run({ key: msg.key, kind: single.kind, url: single.url })' in settled
    assert 'single.run(msg)' not in settled


def test_queue_settles_background_lookups_added_after_they_stopped():
    """Once background lookups have stopped for the session, new ones settle as failed at once instead of waiting forever."""
    background = (APPLICATION_DIR / 'background.js').read_text(encoding='utf-8')
    add_job = _section(background, 'function addJob(client, spec)', '\n}\n')
    assert 'pace.backgroundStopped && QUEUE_BACKGROUND_KINDS.has(spec.kind)' in add_job
    assert "result: 'failed'" in add_job


def test_cancelled_lookup_does_not_make_a_score_final():
    """A lookup withdrawn because prefetch was switched off does not count as finished."""
    settled = _section(_read('prefetch.js'), 'requestQueue.onSettled(msg => {', '});')
    assert "msg.result !== 'cancelled'" in settled


def test_running_background_lookup_stays_in_the_handover_list():
    """Only one-off jobs leave the hand-over list when they start; background jobs the queue may retry stay in it."""
    run = _section(_read('request-queue.js'), "if (msg.type === 'run') {", "} else if (msg.type === 'waiting')")
    assert 'if (single) pending.delete(msg.key);' in run


def test_meta_save_timer_is_cleared_once_it_has_saved():
    """After a save, the unload handler finds no pending save and does not rewrite novelMeta again."""
    storage = _read('storage.js')
    timer = _section(storage, '_metaTimer = setTimeout(() => {', '}, wait);')
    assert '_metaTimer = null;' in timer


def test_bigram_migration_skips_descriptions_too_short_to_pair():
    """Descriptions of fewer than two words can never gain a bigram, so they do not trigger a save on every load."""
    migration = _section(_read('storage.js'), '// Migrate pre-bigram entries', 'if (migrated) scheduleMetaSave();')
    assert 'meta.d.length < 2' in migration


def test_shown_count_counts_the_cards_visible_now():
    """The loading line's 'N shown' counts the cards visible now, including ones shown after their score settled."""
    scroll = _read('scroll.js')
    assert '${_shownCount()} shown' in _section(scroll, 'function _renderLoading', '\n}\n')
    assert ':not(.nuf-hidden-threshold)' in _section(scroll, 'function _shownCount', '\n}\n')


def test_no_references_left_clears_waiting_and_hidden_cards(page):
    """When the last reference novel is removed there are no scores, so no card stays hidden or waiting."""
    page.route(SERIES_PAGES, lambda route: None)  # lookups never answer, so cards stay pending
    load_listing(page, {'read': ['reference-novel'], 'novelMeta': {'reference-novel': REF}, 'minRecommendationScore': 5})
    expect(page.locator('.nuf-score-pending').first).to_be_attached(timeout=5000)
    set_storage(page, {'read': []})
    expect(page.locator('.nuf-score-pending')).to_have_count(0, timeout=3000)
    expect(page.locator('.search_main_box_nu.nuf-hidden-threshold')).to_have_count(0)


def test_another_tabs_save_keeps_tags_this_tab_has_not_saved_yet(page):
    """A novelMeta change from another tab keeps tags this tab fetched but has not saved yet."""
    load_listing(page, {'prefetchTagsEnabled': False, 'read': ['reference-novel'], 'novelMeta': {'reference-novel': REF}})
    page.wait_for_selector('.nuf-controls')
    # As a lookup does: store the tags and schedule the save
    _isolated(page, "state.novelMeta['local-novel'] = { t: 'Local', tags: ['found here'] }; scheduleMetaSave()")
    set_storage(page, {'novelMeta': {'reference-novel': REF, 'other-novel': {'t': 'Other', 'tags': ['found there']}}})
    page.wait_for_timeout(500)
    meta = _isolated(page, 'state.novelMeta')
    assert meta.get('local-novel', {}).get('tags') == ['found here'], meta
    assert meta.get('other-novel', {}).get('tags') == ['found there'], meta
