"""Visual regression screenshot tests for novel-updates-filter extension.

Each test captures a distinct UI state and compares it against a stored baseline PNG.

Create / update baselines:
    PLAYWRIGHT_UPDATE_SNAPSHOTS=all python -m pytest tests/test_screenshots.py -v

Regression check (normal use):
    python -m pytest tests/test_screenshots.py -v

Baselines are stored in:
    tests/test_screenshots.py-snapshots/<name>-win32.png
"""

import io
import os
import platform
import pytest
import numpy as np
from pathlib import Path
from PIL import Image
from helpers import load_listing, set_storage


# ── Snapshot helper ─────────────────────────────────────────────────────────

_SNAP_DIR = Path(__file__).parent / 'test_screenshots.py-snapshots'
_PLATFORM = platform.system().lower()  # 'windows', 'linux', 'darwin'

# Maximum allowed mean pixel difference (0–255 scale).
# Chrome's sub-pixel font rendering introduces ~1–3 pixel noise between runs;
# this threshold accepts that while still catching real visual regressions.
_PIXEL_THRESHOLD = 5.0


def _png_to_array(data: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(data)).convert('RGB'), dtype=np.float32)


def assert_screenshot(page, name):
    """Compare page screenshot against a stored baseline using pixel diff.

    Tolerates minor sub-pixel rendering noise (threshold=5/255 mean diff).
    Set PLAYWRIGHT_UPDATE_SNAPSHOTS=all to overwrite existing baselines.
    """
    snap_path = _SNAP_DIR / f'{name}-{_PLATFORM}.png'
    png_bytes = page.screenshot()

    update = os.environ.get('PLAYWRIGHT_UPDATE_SNAPSHOTS') == 'all'

    if update or not snap_path.exists():
        _SNAP_DIR.mkdir(exist_ok=True)
        snap_path.write_bytes(png_bytes)
        return  # baseline written

    baseline_bytes = snap_path.read_bytes()
    current = _png_to_array(png_bytes)
    baseline = _png_to_array(baseline_bytes)

    if current.shape != baseline.shape:
        actual_path = _SNAP_DIR / f'{name}-{_PLATFORM}-actual.png'
        actual_path.write_bytes(png_bytes)
        pytest.fail(
            f'Screenshot size changed for {name!r}: '
            f'baseline={baseline.shape} actual={current.shape}.\n'
            f'  actual: {actual_path}'
        )

    mean_diff = float(np.abs(current - baseline).mean())
    if mean_diff > _PIXEL_THRESHOLD:
        actual_path = _SNAP_DIR / f'{name}-{_PLATFORM}-actual.png'
        actual_path.write_bytes(png_bytes)
        pytest.fail(
            f'Screenshot mismatch for {name!r}: mean_diff={mean_diff:.2f} > {_PIXEL_THRESHOLD}.\n'
            f'  baseline: {snap_path}\n'
            f'  actual:   {actual_path}\n'
            f'To update: PLAYWRIGHT_UPDATE_SNAPSHOTS=all python -m pytest tests/test_screenshots.py -v'
        )


# ── Shared storage fixtures ─────────────────────────────────────────────────

_REC_STORAGE = {
    'read': ['ref-fantasy'],
    'notInterested': ['ref-harem'],
    'novelMeta': {
        'ref-fantasy': {
            'g': ['action', 'fantasy', 'romance'],
            'l': 'chinese',
            'd': ['kingdom', 'magic', 'adventure', 'fantasy'],
            't': 'Fantasy Reference',
        },
        'ref-harem': {
            'g': ['action', 'harem', 'martial arts'],
            'l': 'korean',
            'd': ['cultivation', 'harem', 'powerful'],
            't': 'Harem Reference',
        },
    },
}


def _clear_and_seed(page, data):
    """Clear chrome.storage.local and seed with data, without navigating."""
    page._sw.evaluate(
        '''async (data) => {
            await new Promise(r => chrome.storage.local.clear(r));
            if (Object.keys(data).length) {
                await new Promise(r => chrome.storage.local.set(data, r));
            }
        }''',
        data,
    )


# ── Listing page states ─────────────────────────────────────────────────────

def test_screenshot_default_listing(page):
    """4 cards, no filters, no recommendations — clean baseline view."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    assert_screenshot(page, '01-default-listing')


def test_screenshot_recommendations(page):
    """Score badges and reason chips visible on all cards."""
    load_listing(page, _REC_STORAGE)
    page.wait_for_selector('.nuf-score-label')
    assert_screenshot(page, '02-recommendations')


def test_screenshot_explain_panel(page):
    """Score breakdown panel open after clicking the first score badge."""
    load_listing(page, _REC_STORAGE)
    page.wait_for_selector('.nuf-score-badge')
    page.locator('.nuf-score-badge').first.click()
    page.locator('.nuf-explain-panel').wait_for(state='visible')
    assert_screenshot(page, '03-explain-panel')


def test_screenshot_reasoning_chips_hidden(page):
    """Score badges visible but chip row hidden (showReasoningChips=false)."""
    load_listing(page, {**_REC_STORAGE, 'showReasoningChips': False})
    page.wait_for_selector('.nuf-score-label')
    assert_screenshot(page, '04-reasoning-chips-hidden')


def test_screenshot_language_filter(page):
    """CN and KR cards hidden; only JP card (I Shall Seal the Heavens) remains."""
    load_listing(page, {'excludeLanguages': ['chinese', 'korean']})
    page.wait_for_selector('.nuf-controls')
    assert_screenshot(page, '05-language-filter')


def test_screenshot_genre_filter(page):
    """Harem card (Against the Gods) hidden by genre filter; 3 cards remain."""
    load_listing(page, {'excludeTags': ['harem']})
    page.wait_for_selector('.nuf-controls')
    assert_screenshot(page, '06-genre-filter')


def test_screenshot_expand_synopsis(page):
    """All card descriptions auto-expanded (more>> link hidden)."""
    load_listing(page, {'expandSynopsis': True})
    page.wait_for_selector('.nuf-controls')
    assert_screenshot(page, '07-expand-synopsis')


def test_screenshot_score_threshold(page):
    """Low-scoring cards hidden by minRecommendationScore=6 threshold."""
    load_listing(page, {**_REC_STORAGE, 'minRecommendationScore': 6})
    page.wait_for_selector('.nuf-score-label')
    assert_screenshot(page, '08-score-threshold')


def test_screenshot_min_rating(page):
    """Cards with NU rating < 4.5 filtered; Release that Witch + Lord of Mysteries remain."""
    load_listing(page, {'minRating': 4.5})
    page.wait_for_selector('.nuf-controls')
    assert_screenshot(page, '09-min-rating')


def test_screenshot_undo_toast(page):
    """Undo toast visible immediately after bucketing the first card."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    page.locator('.nuf-toast').wait_for(state='visible')
    assert_screenshot(page, '10-undo-toast')


# ── Series page ─────────────────────────────────────────────────────────────

def test_screenshot_hide_reviews(page):
    """Series page with #comments section hidden (hideReviews=true)."""
    _clear_and_seed(page, {'hideReviews': True})
    page.goto('https://www.novelupdates.com/series/release-that-witch/')
    page.wait_for_load_state('domcontentloaded')
    page.wait_for_timeout(500)
    assert_screenshot(page, '11-hide-reviews')


# ── Extension pages ─────────────────────────────────────────────────────────

def test_screenshot_popup(page, extension_id):
    """Popup with active language/genre filters and non-zero bucket counts."""
    _clear_and_seed(page, {
        'read': ['release-that-witch', 'lord-of-the-mysteries'],
        'notFinished': ['against-the-gods'],
        'notInterested': [],
        'excludeLanguages': ['korean'],
        'excludeTags': ['harem'],
    })
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    assert_screenshot(page, '12-popup')


def test_screenshot_manager(page, extension_id):
    """Manager page with novels spread across all three buckets."""
    _clear_and_seed(page, {
        'read': ['release-that-witch', 'lord-of-the-mysteries'],
        'notFinished': ['against-the-gods'],
        'notInterested': ['i-shall-seal-the-heavens'],
        'titleCache': {
            'release-that-witch':       'Release that Witch',
            'lord-of-the-mysteries':    'Lord of the Mysteries',
            'against-the-gods':         'Against the Gods',
            'i-shall-seal-the-heavens': 'I Shall Seal the Heavens',
        },
    })
    page.goto(f'chrome-extension://{extension_id}/manager.html')
    page.wait_for_load_state('domcontentloaded')
    assert_screenshot(page, '13-manager')
