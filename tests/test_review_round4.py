"""Fourth review round: merging another tab's novelMeta, the manager's Undo and
bulk move, covers and recency dates when moving novels in and out of To Read."""

import time
from pathlib import Path
from playwright.sync_api import expect
from helpers import load_listing, set_storage, get_storage, wait_for_meta_save
from test_review_prefetch_scroll import _isolated
from test_manager import load_manager

CONTENT_DIR = Path(__file__).parent.parent / 'application' / 'content'


def _listing_without_lookups(page, meta):
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': meta})
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)  # nothing of this tab's left unsaved


def test_another_tabs_save_keeps_newer_local_fields(page):
    """Another tab's save keeps fields only this tab has (first-chapter link) and the newer recency date."""
    _listing_without_lookups(page, {'x-novel': {'t': 'X', 'ts': 1000}})
    _isolated(page, "Object.assign(state.novelMeta['x-novel'], { ts: 5000, c1url: 'https://www.novelupdates.com/extnu/1/' }); scheduleMetaSave()")
    set_storage(page, {'novelMeta': {'x-novel': {'t': 'X', 'ts': 1000, 'tags': ['magic']}}})
    page.wait_for_timeout(300)
    meta = _isolated(page, "state.novelMeta['x-novel']")
    assert meta['ts'] == 5000 and meta['c1url'].endswith('/extnu/1/') and meta['tags'] == ['magic'], meta


def test_merged_fields_are_saved_back(page):
    """What the merge kept from this tab is written to storage too, not only kept in memory."""
    _listing_without_lookups(page, {'x-novel': {'t': 'X', 'ts': 1000}})
    _isolated(page, "Object.assign(state.novelMeta['x-novel'], { c1url: 'https://www.novelupdates.com/extnu/2/' })")
    set_storage(page, {'novelMeta': {'x-novel': {'t': 'X', 'ts': 1000, 'tags': ['magic']}}})
    wait_for_meta_save(page)
    stored = get_storage(page, ['novelMeta'])['novelMeta']['x-novel']
    assert stored.get('c1url', '').endswith('/extnu/2/') and stored.get('tags') == ['magic'], stored


def test_entry_removed_elsewhere_is_not_brought_back(page):
    """An entry another tab or the manager removed stays removed when this tab has nothing unsaved."""
    _listing_without_lookups(page, {'keep-novel': {'t': 'Keep'}, 'gone-novel': {'t': 'Gone', 'tags': ['magic']}})
    set_storage(page, {'novelMeta': {'keep-novel': {'t': 'Keep'}}})
    page.wait_for_timeout(300)
    assert _isolated(page, "'gone-novel' in state.novelMeta") is False


def test_manager_undo_restores_only_the_removed_novel(page, extension_id):
    """Undo puts back what was removed without reverting a move made since."""
    load_manager(page, extension_id, {'read': ['a-novel', 'b-novel'], 'toRead': [],
                                      'titleCache': {'a-novel': 'A', 'b-novel': 'B'},
                                      'coverCache': {'a-novel': '', 'b-novel': ''}})
    page.locator('#list-read .item[data-slug="a-novel"] .item-btn.del').click()
    page.locator('#list-read .item[data-slug="b-novel"] .item-btn.move-tr').click()
    page.locator('#undo-btn').click()
    page.wait_for_timeout(300)
    stored = get_storage(page, ['read', 'toRead'])
    assert stored['read'] == ['a-novel'] and stored['toRead'] == ['b-novel'], stored


def test_manager_bulk_move_saves_once(page, extension_id):
    """Moving many selected novels at once writes the lists once, not once per novel."""
    slugs = [f'n{i}' for i in range(5)]
    load_manager(page, extension_id, {'read': slugs, 'titleCache': {s: s.upper() for s in slugs},
                                      'coverCache': {s: '' for s in slugs}})
    page._sw.evaluate('''() => { globalThis.__readWrites = 0;
        chrome.storage.onChanged.addListener(c => { if (c.read) globalThis.__readWrites++; }); }''')
    page.locator('#sel-all-read').click()
    page.locator('#bulk-move-read-tr').click()
    page.wait_for_timeout(500)
    assert sorted(get_storage(page, ['toRead'])['toRead']) == slugs  # order follows the display sort
    assert page._sw.evaluate('() => globalThis.__readWrites') == 1


def test_to_read_from_a_card_without_a_cover_still_gets_its_cover_looked_up(page):
    """A card with no cover image does not store img: null, which would stop the series-page cover lookup."""
    _listing_without_lookups(page, {})
    page.evaluate("document.querySelector('[data-nuf-slug=\"release-that-witch\"] .search_img_nu img')?.remove()")
    # The controls sit beside the card, inside its wrapper
    page.locator('.nuf-entry-wrap').filter(has=page.locator('[data-nuf-slug="release-that-witch"]')).locator('.nuf-btn[data-nuf-btn="toread"]').click()
    meta = _isolated(page, "state.novelMeta['release-that-witch'] || {}")
    assert 'img' not in meta or meta['img'] not in (None, ''), meta
    # The cover lookup runs and fills in the series page's cover (img: null used to block it)
    for _ in range(40):
        if _isolated(page, "isRealCoverUrl(state.novelMeta['release-that-witch']?.img)"):
            break
        page.wait_for_timeout(250)
    assert _isolated(page, "isRealCoverUrl(state.novelMeta['release-that-witch']?.img)") is True


def test_sidebar_move_records_when_it_happened(page):
    """Moving a novel from the To Read sidebar to Read stamps its recency date, like marking it on a card."""
    load_listing(page, {'prefetchTagsEnabled': False, 'toRead': ['moved-novel'],
                        'novelMeta': {'moved-novel': {'t': 'Moved', 'img': '', 'ts': 1000}}})
    page.wait_for_selector('.nuf-sidebar-item', state='attached')
    page.locator('.nuf-sidebar-tab').click()
    before = int(time.time() * 1000)
    # What the move records, not the panel's layout: click without waiting for it to be on screen
    page.locator('.nuf-sidebar-item .nuf-sidebar-bucket-btn[data-nuf-btn="read"]').first.dispatch_event('click')
    page.wait_for_timeout(300)
    assert _isolated(page, "state.novelMeta['moved-novel'].ts") >= before


def test_to_read_list_change_redraws_the_sidebar_without_breaking_start_reading():
    """A To Read change redraws the sidebar through the scheduler that waits for a Start reading in progress."""
    listener = (CONTENT_DIR / 'storage-listener.js').read_text(encoding='utf-8')
    assert 'if (changes.toRead || changes.novelMeta) scheduleSidebarRender();' in listener
    assert 'renderToReadSidebar()' not in listener
