"""Code-review fixes: importing in the popup refreshes what the popup shows and holds."""

import json
from playwright.sync_api import expect
from helpers import get_storage


def _open_popup(page, extension_id, seed=None):
    page._sw.evaluate('''async (data) => {
        await new Promise(r => chrome.storage.local.clear(r));
        if (Object.keys(data).length) await new Promise(r => chrome.storage.local.set(data, r));
    }''', seed or {})
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_selector('#import-toggle', state='attached')
    page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")


def _import(page, payload):
    page.locator('#import-toggle').click()
    page.locator('#import-text').fill(json.dumps(payload))
    page.locator('#do-import').click()


def test_popup_import_then_chip_click_keeps_imported_filters(page, extension_id):
    """Clicking a language chip after an import does not overwrite the imported genre filters."""
    _open_popup(page, extension_id, {'excludeTags': ['harem']})
    _import(page, {'read': [], 'excludeTags': ['romance', 'yaoi'], 'excludeLanguages': ['korean']})
    page.locator('.lang-chip[data-lang="chinese"]').click()
    page.wait_for_timeout(300)
    stored = get_storage(page, ['excludeTags', 'excludeLanguages'])
    assert stored['excludeTags'] == ['romance', 'yaoi'], stored
    assert sorted(stored['excludeLanguages']) == ['chinese', 'korean'], stored


def test_popup_import_refreshes_chips_and_toggles(page, extension_id):
    """After an import, the chips and toggles show the imported settings."""
    _open_popup(page, extension_id)
    _import(page, {'read': ['a'], 'excludeTags': ['adult'], 'excludeLanguages': ['japanese'],
                   'recommendationsEnabled': False, 'hideReviews': True})
    expect(page.locator('.genre-chip[data-tag="adult"]')).to_have_class('genre-chip active')
    expect(page.locator('.lang-chip[data-lang="japanese"]')).to_have_class('lang-chip active')
    expect(page.locator('#rec-toggle')).not_to_be_checked()
    expect(page.locator('#hide-reviews-toggle')).to_be_checked()
    expect(page.locator('#read-count')).to_have_text('Read 1')


def test_popup_export_shows_exported_without_clipboard(page, extension_id):
    """The Export button confirms the download even when the clipboard write is refused."""
    _open_popup(page, extension_id, {'read': ['a']})
    page.evaluate("() => { navigator.clipboard.writeText = () => Promise.reject(new Error('Document is not focused')); }")
    with page.expect_download():
        page.locator('#export-btn').click()
    expect(page.locator('#export-btn')).to_have_text('Exported')


def test_popup_comments_use_no_acronyms():
    """The popup script's comments spell out Novel Updates."""
    from pathlib import Path
    popup = (Path(__file__).parent.parent / 'application' / 'popup.js').read_text(encoding='utf-8')
    assert ' NU ' not in popup


# ── Ko-fi support link ──────────────────────────────────────────────────────

KOFI_URL = 'https://ko-fi.com/gaossian0496'


def test_popup_has_a_quiet_support_link(page, extension_id):
    """The popup footer has a small Ko-fi link that opens in a new tab."""
    _open_popup(page, extension_id)
    link = page.locator('#support-link')
    expect(link).to_be_visible()
    expect(link).to_have_attribute('href', KOFI_URL)
    expect(link).to_have_attribute('target', '_blank')
    expect(link).to_have_attribute('rel', 'noopener')


def test_support_link_appears_in_readme_and_funding_file():
    """The README and GitHub's FUNDING.yml point to the same Ko-fi page."""
    from pathlib import Path
    root = Path(__file__).parent.parent
    assert KOFI_URL in (root / 'README.md').read_text(encoding='utf-8')
    assert 'ko_fi: gaossian0496' in (root / '.github' / 'FUNDING.yml').read_text(encoding='utf-8')
