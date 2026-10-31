from playwright.sync_api import expect
from helpers import load_listing


def test_popup_is_500px_wide(page, extension_id):
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    width = page.evaluate('() => document.body.offsetWidth')
    assert width == 500


def test_language_and_genre_filters_rendered_as_chips(page, extension_id):
    page.goto(f'chrome-extension://{extension_id}/popup.html')

    expect(page.locator('.lang-chip')).to_have_count(4)    # Chinese, Japanese, Korean, Other
    expect(page.locator('.genre-chip')).to_have_count(35)  # full genre list


def test_controls_bar_is_right_aligned(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    justify = page.locator('.nuf-controls').first.evaluate(
        'el => getComputedStyle(el).justifyContent'
    )
    assert justify == 'flex-end'


def test_bucket_buttons_have_distinct_colours(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    colours = page.evaluate('''() => {
        const c = document.querySelector('.nuf-controls');
        const bg = el => getComputedStyle(el).backgroundColor;
        return {
            read: bg(c.querySelector('.nuf-btn[data-nuf-btn="read"]')),
            nf:   bg(c.querySelector('.nuf-btn[data-nuf-btn="nf"]')),
            ni:   bg(c.querySelector('.nuf-btn[data-nuf-btn="ni"]')),
        };
    }''')

    assert colours['read'] == 'rgb(209, 250, 229)'  # #d1fae5 green-100
    assert colours['nf']   == 'rgb(254, 243, 199)'  # #fef3c7 amber-100
    assert colours['ni']   == 'rgb(254, 226, 226)'  # #fee2e2 red-100


def test_defect008_start_reading_button_is_light_blue(page):
    """Start reading button should be light blue (#dbeafe) — blue=navigate in the semantic palette."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    bg = page.evaluate(
        """() => getComputedStyle(document.querySelector('.nuf-btn[data-nuf-btn="start"]')).backgroundColor"""
    )
    assert bg == 'rgb(219, 234, 254)', f"Start reading button was {bg}, expected light blue rgb(219, 234, 254)"


def test_defect020_popup_fits_in_chrome_viewport(page, extension_id):
    """Popup body height does not exceed 600 px (Chrome extension popup viewport limit)."""
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    height = page.evaluate('() => document.body.scrollHeight')
    assert height <= 600, f'Popup is {height}px tall — exceeds 600px Chrome popup limit'


def test_defect020_filters_collapsed_by_default(page, extension_id):
    """The Filters section is collapsed by default to save vertical space."""
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    is_open = page.evaluate("() => document.querySelector('details.filters')?.open ?? true")
    assert not is_open, 'Filters <details> section should be collapsed by default'


def test_defect020_options_expanded_by_default(page, extension_id):
    """Options section is expanded by default (open attribute set)."""
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    is_open = page.evaluate("() => document.querySelector('#options-section')?.open ?? false")
    assert is_open, 'Options <details> section should be expanded by default'


def test_defect020_options_has_logical_groups(page, extension_id):
    """Options section contains Scoring, Display, and Data sub-groups."""
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    labels = page.locator('.options-group-label').all_text_contents()
    labels_lower = [l.strip().lower() for l in labels]
    assert 'scoring' in labels_lower, f'Missing Scoring group, got: {labels}'
    assert 'display' in labels_lower, f'Missing Display group, got: {labels}'
    assert 'data' in labels_lower, f'Missing Data group, got: {labels}'


def test_defect020_prefetch_label_says_novel_updates(page, extension_id):
    """Prefetch toggle label reads 'Prefetch Novel Updates tags', not 'Prefetch NU tags'."""
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    label = page.evaluate(
        "() => document.querySelector('#prefetch-tags-toggle').closest('.option-row').querySelector('span').textContent"
    )
    assert 'Novel Updates' in label, f'Expected "Novel Updates" in prefetch label, got: {label!r}'
