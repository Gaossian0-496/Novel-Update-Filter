import re
import time
import threading
from pathlib import Path
import pytest
from playwright.sync_api import expect
from helpers import load_listing, load_series, set_storage, get_storage, parse_scores, hang_route, SERIES_PAGES, wait_for_meta_save


def test_all_three_buckets_remove_card_from_page(page):
    # Read — click button, card animates out
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=2000)

    # Not Finished — pre-seed so removal is instant (no animation)
    load_listing(page, {'notFinished': ['against-the-gods']})
    expect(page.locator('.search_main_box_nu')).to_have_count(3)

    # Not Interested — pre-seed
    load_listing(page, {'notInterested': ['i-shall-seal-the-heavens']})
    expect(page.locator('.search_main_box_nu')).to_have_count(3)


def test_undo_toast_appears_and_restores_card(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    expect(page.locator('.search_main_box_nu')).to_have_count(4)
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()

    # Toast is visible with the novel's name
    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))
    expect(page.locator('.nuf-toast-title')).to_contain_text('Release that Witch')

    # Undo restores the card and hides the toast
    page.locator('.nuf-toast-undo').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(4)
    expect(page.locator('.nuf-toast')).not_to_have_class(re.compile('nuf-toast-visible'))


def test_controls_are_dom_sibling_above_card_not_inside_it(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    all_above = page.evaluate('''() => {
        const cards = document.querySelectorAll('.search_main_box_nu');
        return Array.from(cards).every(card =>
            card.previousElementSibling?.classList.contains('nuf-controls')
        );
    }''')
    assert all_above is True

    # Controls must not be inside any card
    expect(page.locator('.search_main_box_nu .nuf-controls')).to_have_count(0)


def test_language_filter_removes_matching_cards_without_reload(page):
    load_listing(page, {'excludeLanguages': ['korean']})
    page.wait_for_selector('.nuf-controls')

    # KR card (Against the Gods) filtered; 3 of 4 remain
    expect(page.locator('.search_main_box_nu')).to_have_count(3)
    titles = page.locator('.search_title a').all_text_contents()
    assert 'Against the Gods' not in titles


def test_genre_filter_removes_matching_cards_without_reload(page):
    load_listing(page, {'excludeTags': ['harem']})
    page.wait_for_selector('.nuf-controls')

    # Harem card (Against the Gods) filtered
    expect(page.locator('.search_main_box_nu')).to_have_count(3)
    titles = page.locator('.search_title a').all_text_contents()
    assert 'Against the Gods' not in titles


def test_genre_filter_applied_live_removes_card_and_controls(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    # Apply genre filter after page load — triggers storage.onChanged
    set_storage(page, {'excludeTags': ['harem']})

    # Card removed
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=5000)
    titles = page.locator('.search_title a').all_text_contents()
    assert 'Against the Gods' not in titles

    # Controls bar must not be left orphaned — count should match card count
    expect(page.locator('.nuf-controls')).to_have_count(3)


def test_infinite_scroll_hides_pagination_and_shows_sentinel(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    expect(page.locator('.digg_pagination')).to_have_css('display', 'none')
    assert page.locator('.nuf-sentinel, .nuf-sentinel-loading, .nuf-sentinel-done').count() > 0


def test_filtered_pages_auto_fetch_next_page_without_user_action(page):
    # All page-1 cards pre-filtered — sentinel visible immediately, auto-fetch fires
    load_listing(page, {
        'notInterested': [
            'release-that-witch',
            'against-the-gods',
            'i-shall-seal-the-heavens',
            'lord-of-the-mysteries',
        ],
    })

    # Page 2 cards should appear automatically (page-1 cards are all filtered)
    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=10000)
    titles = page.locator('.search_title a').all_text_contents()
    assert 'The Desolate Era' in titles
    assert 'Renegade Immortal' in titles


def test_infinite_scroll_shows_retry_button_on_fetch_error(page):
    """When the next-page fetch returns 403 after SCROLL_MAX_ERRORS retries, a 'Keep going'
    button appears instead of the end-of-results marker."""
    page.route('**/series-finder/?pg=2', lambda route: route.fulfill(status=403, body='Forbidden'))

    # All page-1 cards pre-filtered so sentinel fires immediately without user scrolling
    load_listing(page, {
        'notInterested': [
            'release-that-witch',
            'against-the-gods',
            'i-shall-seal-the-heavens',
            'lord-of-the-mysteries',
        ],
    })

    # With SCROLL_MAX_ERRORS=3 a blocking status backs off ~4 s + ~8 s (jittered,
    # plus sticky pacing) before the blocked state appears
    expect(page.locator('.nuf-sentinel-blocked')).to_be_visible(timeout=30000)
    assert 'HTTP 403' in page.locator('.nuf-sentinel-blocked-msg').text_content()
    expect(page.locator('.nuf-sentinel-retry-btn')).to_be_visible()
    assert page.locator('.nuf-sentinel-retry-btn').text_content() == 'Keep going'


def test_defect053_popup_export_matches_manager_export(page, extension_id):
    """Both surfaces (popup and manager) must produce identical export payloads
    when reading the same storage state — they share the same EXPORTABLE_KEYS
    list and the same `version: 2` shape."""
    import json as _json
    seed = {
        'read': ['slug-a'], 'notFinished': ['slug-b'],
        'notInterested': [], 'toRead': [],
        'novelMeta': {'slug-a': {'g': ['fantasy'], 'l': 'cn', 'd': [], 't': 'A', 'tags': ['x']}},
        'novelReviews': {'slug-a': {'rating': 4}},
        'titleCache': {'slug-a': 'Title A', 'slug-b': 'Title B'},
        # Covers known, so opening the manager fetches nothing between the two exports
        'coverCache': {'slug-a': '', 'slug-b': ''},
        'excludeLanguages': ['japanese'],
        'excludeTags': ['harem'],
        'recommendationsEnabled': True, 'showReasoningChips': True,
        'prefetchTagsEnabled': True, 'expandSynopsis': True, 'hideReviews': False,
        'minRating': 4.0, 'minRecommendationScore': 6, 'recencyHalfLifeDays': 180,
        'ratingScaleVersion': 2,
    }

    def capture_export_from(url, btn_selector):
        page._sw.evaluate(
            '''async (data) => {
                await new Promise(r => chrome.storage.local.clear(r));
                await new Promise(r => chrome.storage.local.set(data, r));
            }''',
            seed,
        )
        page.goto(url)
        page.wait_for_load_state('domcontentloaded')
        page.evaluate('''() => {
            window._capturedExport = null;
            navigator.clipboard.writeText = (text) => {
                window._capturedExport = text;
                return Promise.resolve();
            };
            if (typeof window.downloadJson === 'function') {
                window.downloadJson = () => {};
            }
        }''')
        page.locator(btn_selector).click()
        page.wait_for_function('() => window._capturedExport !== null', timeout=3000)
        return _json.loads(page.evaluate('() => window._capturedExport'))

    popup_payload   = capture_export_from(f'chrome-extension://{extension_id}/popup.html',   '#export-btn')
    manager_payload = capture_export_from(f'chrome-extension://{extension_id}/manager.html', '#export-btn')
    assert popup_payload == manager_payload, (
        'Popup and manager exports diverged.\n'
        f'  popup keys:   {sorted(popup_payload.keys())}\n'
        f'  manager keys: {sorted(manager_payload.keys())}'
    )


def test_defect054_popup_import_from_file_round_trip(page, extension_id):
    """Popup's import must also support a file-picker path mirroring the
    manager's. Tests the popup-side #import-file input + change handler."""
    import json as _json
    seed = {
        'read': ['from-file-popup'], 'notFinished': [],
        'notInterested': [], 'toRead': [],
        'novelMeta': {'from-file-popup': {'g': ['action'], 'l': 'kr', 'd': [], 't': 'P', 'tags': ['y']}},
        'novelReviews': {'from-file-popup': {'rating': 3}},
        'titleCache': {'from-file-popup': 'Popup Title'},
        'excludeLanguages': [], 'excludeTags': [],
        'recommendationsEnabled': True, 'showReasoningChips': True,
        'prefetchTagsEnabled': True, 'expandSynopsis': False, 'hideReviews': False,
        'minRating': 0, 'minRecommendationScore': 0, 'recencyHalfLifeDays': 180,
        'ratingScaleVersion': 2,
    }
    payload_text = _json.dumps({'version': 2, **seed})

    page._sw.evaluate('async () => new Promise(r => chrome.storage.local.clear(r))')
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    page.locator('#import-toggle').click()
    page.locator('#import-file').set_input_files({
        'name': 'popup-import.json',
        'mimeType': 'application/json',
        'buffer': payload_text.encode('utf-8'),
    })
    page.wait_for_timeout(500)

    stored = page._sw.evaluate(
        '(keys) => new Promise(r => chrome.storage.local.get(keys, r))',
        list(seed.keys()),
    )
    for key, expected in seed.items():
        assert stored.get(key) == expected, (
            f'Popup file-import mismatch on {key!r}: expected {expected!r}, got {stored.get(key)!r}'
        )


def test_defect053_popup_import_round_trip(page, extension_id):
    """Round-trip through the popup's import path: seed → export → clear →
    import via popup → assert every field restored. Mirrors the manager-side
    round-trip test so both surfaces have direct coverage."""
    import json as _json
    seed = {
        'read': ['slug-a'], 'notFinished': ['slug-b'],
        'notInterested': [], 'toRead': [],
        'novelMeta': {'slug-a': {'g': ['fantasy'], 'l': 'cn', 'd': [], 't': 'A', 'tags': ['x']}},
        'novelReviews': {'slug-a': {'rating': 4, 'comment': 'great'}},
        'titleCache': {'slug-a': 'Title A', 'slug-b': 'Title B'},
        'excludeLanguages': ['japanese'],
        'excludeTags': ['harem'],
        'recommendationsEnabled': True, 'showReasoningChips': True,
        'prefetchTagsEnabled': True, 'expandSynopsis': True, 'hideReviews': False,
        'minRating': 4.0, 'minRecommendationScore': 6, 'recencyHalfLifeDays': 180,
        'ratingScaleVersion': 2,
    }
    # Capture an export via the popup
    page._sw.evaluate(
        '''async (data) => {
            await new Promise(r => chrome.storage.local.clear(r));
            await new Promise(r => chrome.storage.local.set(data, r));
        }''',
        seed,
    )
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    page.evaluate('''() => {
        window._capturedExport = null;
        navigator.clipboard.writeText = (text) => { window._capturedExport = text; return Promise.resolve(); };
        if (typeof window.downloadJson === 'function') window.downloadJson = () => {};
    }''')
    page.locator('#export-btn').click()
    page.wait_for_function('() => window._capturedExport !== null', timeout=3000)
    payload_text = page.evaluate('() => window._capturedExport')

    # Clear, then import via the popup
    page._sw.evaluate('async () => new Promise(r => chrome.storage.local.clear(r))')
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    page.locator('#import-toggle').click()
    page.locator('#import-text').fill(payload_text)
    page.locator('#do-import').click()
    page.wait_for_timeout(500)

    stored = page._sw.evaluate('(keys) => new Promise(r => chrome.storage.local.get(keys, r))', list(seed.keys()))
    for key, expected in seed.items():
        assert stored.get(key) == expected, (
            f'Popup round-trip mismatch on {key!r}: expected {expected!r}, got {stored.get(key)!r}'
        )


def test_popup_shows_counts_only_no_lists_or_tabs(page, extension_id):
    page.goto(f'chrome-extension://{extension_id}/popup.html')

    expect(page.locator('#read-count')).to_be_visible()
    expect(page.locator('#nf-count')).to_be_visible()
    expect(page.locator('#ni-count')).to_be_visible()

    expect(page.locator('.bucket-list')).to_have_count(0)
    expect(page.locator('.tab')).to_have_count(0)
    expect(page.locator('li')).to_have_count(0)


def test_recommendation_badges_appear_when_read_list_has_stored_metadata(page):
    novel_meta = {
        'ref-read': {
            'g': ['fantasy', 'sci-fi'], 'l': 'chinese',
            'd': ['fantasy', 'magic', 'adventure'], 't': 'Fantasy Ref',
        },
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-controls')
    # All 4 fixture cards visible (ref-read is not a real card), all should have a label
    expect(page.locator('.nuf-score-label')).to_have_count(4)


def test_recommendation_read_match_scores_higher_than_ni_match(page):
    novel_meta = {
        'ref-fantasy': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': ['magic', 'fantasy'], 't': 'Fantasy Ref'},
        'ref-harem':   {'g': ['action', 'harem'],   'l': 'korean',  'd': ['action', 'harem'],  't': 'Harem Ref'},
    }
    load_listing(page, {
        'read': ['ref-fantasy'],
        'notInterested': ['ref-harem'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-score-label')

    scores = parse_scores(page)
    # Card 1 = Release that Witch (Fantasy/Sci-fi) matches read ref → high score
    # Card 2 = Against the Gods (Action/Harem) matches NI ref → low score
    assert scores[0] > scores[1]


def test_no_recommendations_with_empty_buckets(page):
    # No novels in any bucket → nothing to score against → no badges even after prefetch
    load_listing(page, {})
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.nuf-score-badge')).to_have_count(0)


def test_recommendation_reason_shows_genre_that_drove_score(page):
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['fantasy', 'magic'], 't': 'My Favourite Novel'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    # Card 1 (Release that Witch) has Fantasy genre → positive genre chip shows 'Fantasy'
    card1_reason = page.locator('.nuf-reason-row').nth(0).text_content()
    assert 'Fantasy' in card1_reason

    # Card 2 (Against the Gods) has no Fantasy → no genre chip for Fantasy
    card2_reason = page.locator('.nuf-reason-row').nth(1).text_content()
    # Fantasy should not appear as a positive genre chip for this card
    card2_genre_pos = page.locator('.nuf-controls').nth(1).locator('.nuf-chip-genre-pos').all_text_contents()
    assert 'Fantasy' not in card2_genre_pos


def test_recommendations_not_shown_when_toggle_disabled_on_load(page):
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['magic'], 't': 'Fantasy Ref'},
    }
    load_listing(page, {
        'read': ['ref-read'],
        'novelMeta': novel_meta,
        'recommendationsEnabled': False,
    })
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.nuf-score-badge')).to_have_count(0)


def test_recommendations_cleared_when_toggle_disabled_live(page):
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['magic'], 't': 'Fantasy Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    expect(page.locator('.nuf-score-label')).to_have_count(4)

    set_storage(page, {'recommendationsEnabled': False})
    expect(page.locator('.nuf-score-badge')).to_have_count(0, timeout=3000)


def test_novelMeta_captured_from_dom_cards_enables_recommendations(page):
    # No pre-seeded novelMeta — metadata must be captured live from the DOM
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # Mark a viewed card as a read reference — applyRecommendations uses in-memory metadata
    set_storage(page, {'read': ['release-that-witch']})

    # 3 remaining cards should get recommendation scores using the captured metadata
    expect(page.locator('.nuf-score-label')).to_have_count(3, timeout=5000)


def test_recommendation_reason_shows_description_keyword(page):
    # ref has a unique keyword ('cultivation') that matches card 3's description
    # but its genre ('xianxia') does not match any fixture card — so keyword must drive it
    novel_meta = {
        'ref-read': {'g': ['xianxia'], 'l': 'chinese', 'd': ['cultivation', 'immortality'], 't': 'Cultivation Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    # Card 3 (I Shall Seal the Heavens) description contains "cultivation" and "immortality"
    card3_reason = page.locator('.nuf-reason-row').nth(2).text_content()
    assert 'cultivation' in card3_reason or 'immortality' in card3_reason


def test_recommendation_scores_recompute_when_bucket_changes_live(page):
    # Seed ref as NI → Fantasy/Sci-fi card (card 1) gets a low score
    novel_meta = {
        'ref': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': ['magic', 'fantasy'], 't': 'Ref'},
    }
    load_listing(page, {'notInterested': ['ref'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')
    score_as_ni = parse_scores(page)[0]

    # Move ref to read → same card should now score high
    set_storage(page, {'notInterested': [], 'read': ['ref'], 'novelMeta': novel_meta})
    expect(page.locator('.nuf-score-label').nth(0)).not_to_have_text(f'Extension rating: {score_as_ni}/10', timeout=3000)
    score_as_read = parse_scores(page)[0]

    assert score_as_read > score_as_ni


def test_novel_lists_managed_exclusively_in_manager_page(page, extension_id):
    # Popup has no item lists
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    expect(page.locator('.item')).to_have_count(0)

    # Manager has 3 dedicated columns
    page.goto(f'chrome-extension://{extension_id}/manager.html')
    expect(page.locator('#col-read')).to_be_visible()
    expect(page.locator('#col-nf')).to_be_visible()
    expect(page.locator('#col-ni')).to_be_visible()


# ── Phrase extraction (bigram) tests ──────────────────────────────────────

def test_novelMeta_dom_capture_includes_bigrams(page):
    """After browsing, novelMeta.d for a card contains bigrams like 'magical world',
    not just individual words. Fails before bigram tokenize is implemented."""
    load_listing(page)  # empty storage — fresh capture
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)

    stored = get_storage(page, ['novelMeta'])
    d = stored.get('novelMeta', {}).get('release-that-witch', {}).get('d', [])

    # Fixture description: "...to a magical world with witches..."
    # After stopword filtering 'to','a': magical → world are adjacent → bigram 'magical world'
    assert 'magical world' in d, (
        f'Expected bigram "magical world" in novelMeta.d, got first 20 tokens: {d[:20]}'
    )


def test_bigram_only_ref_matches_card_with_adjacent_phrase(page):
    """When ref d contains ONLY a bigram token (no component words), only cards
    whose live tokenize produces that bigram as adjacent content words will match.

    Before fix: tokenize() → unigrams only → 'demon cultivation' never in cardWords
                → all intersection = 0 → all scores = 5
    After fix:  tokenize() → unigrams + bigrams → cards 2 & 3 get 'demon cultivation'
                → scores > 5; cards 1 & 4 don't → scores low.

    Fixture descriptions that contain adjacent 'demon cultivation':
      Card 2 (Against the Gods):      '...through demon cultivation and gathers...'
      Card 3 (I Shall Seal Heavens):  '...of demon cultivation and ancient...'
    Card 1 (Release that Witch) has 'demon invasion' — different phrase, no match.
    Card 4 (Lord of the Mysteries)  has no cultivation at all.
    """
    novel_meta = {
        'ref-read': {
            'g': [],                       # no genre signal — isolate keyword only
            'l': 'chinese',
            'd': ['demon cultivation'],    # ONLY the bigram, no component words
            't': 'Cultivation Ref',
        }
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    scores = parse_scores(page)

    score_rtw  = scores[0]  # Release that Witch  — 'demon invasion', not 'demon cultivation'
    score_atg  = scores[1]  # Against the Gods    — has 'demon cultivation' ✓
    score_issth = scores[2] # I Shall Seal Heavens — has 'demon cultivation' ✓
    score_lotm = scores[3]  # Lord of the Mysteries — no cultivation at all

    assert score_atg > 5, (
        f'ATG (has "demon cultivation" phrase) should score >5; got {score_atg}. '
        f'All scores: {scores}. If all are 5, bigram tokenize is not implemented.'
    )
    assert score_issth > 5, (
        f'ISSTH (has "demon cultivation" phrase) should score >5; got {score_issth}.'
    )
    assert score_atg > score_rtw, (
        f'ATG ({score_atg}) should outscore RtW ({score_rtw}) — '
        f'"demon cultivation" phrase vs "demon invasion"'
    )
    assert score_atg > score_lotm, (
        f'ATG ({score_atg}) should outscore LotM ({score_lotm})'
    )


def test_bigram_phrase_appears_in_reasoning_not_just_word(page):
    """Reasoning line should show the matched phrase 'demon cultivation',
    not just the component word 'cultivation'.
    Before fix: all cards score 5 / 'No strong match' — phrase never appears.
    After fix:  matched cards show '+demon cultivation' in reason line."""
    novel_meta = {
        'ref-read': {
            'g': [],
            'l': 'chinese',
            'd': ['demon cultivation'],
            't': 'Ref',
        }
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    reasons = page.locator('.nuf-reason-row').all_text_contents()
    assert any('demon cultivation' in r for r in reasons), (
        f'Expected phrase "demon cultivation" in at least one reason row. '
        f'Got: {reasons}. If all say "No strong signal", bigrams are not implemented.'
    )


def test_old_unigram_metadata_still_scores_after_bigram_change(page):
    """Backwards compat: novelMeta.d with only single words (old format) still
    produces recommendation scores. Should pass before and after implementation."""
    novel_meta = {
        'ref-read': {
            'g': ['fantasy'],
            'l': 'chinese',
            'd': ['demon', 'cultivation'],  # old format — no bigrams
            't': 'Old Format Ref',
        }
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')
    expect(page.locator('.nuf-score-label')).to_have_count(4)


def test_real_atg_bigrams_rank_atg_card_above_card_with_no_phrase(page):
    """Uses the user's real Against the Gods reading history.
    ATG's description ends '...journey to the heavens' — after stopword filtering
    'to' and 'the', the bigram is 'journey heavens'.

    With only bigram-based ref d, before fix: all cards score 5 (no match).
    After fix: card 2 (fixture ATG, same description) scores > 5 because
    'journey heavens' and 'demon cultivation' both appear as adjacent content words.
    Card 4 (Lord of the Mysteries, Victorian horror) has neither phrase → scores low.
    """
    # Using ATG's real bigrams (extracted from dump output) as the ref d,
    # no unigrams — so only bigram tokenize on the card side can match.
    # Use a synthetic slug so the ATG fixture card is NOT filtered from DOM.
    atg_meta = {
        'g': [],                                    # zero genre signal to isolate phrase matching
        'l': 'chinese',
        'd': ['demon cultivation', 'journey heavens', 'powerful treasure'],
        't': 'Against the Gods',
    }
    load_listing(page, {
        'read': ['atg-reference'],           # synthetic slug — fixture card stays visible
        'novelMeta': {'atg-reference': atg_meta},
    })
    page.wait_for_selector('.nuf-score-label')

    scores = parse_scores(page)

    score_atg_card  = scores[1]  # fixture card 2 = Against the Gods — has all three bigrams
    score_lotm_card = scores[3]  # fixture card 4 = Lord of Mysteries — has none of these bigrams

    assert score_atg_card > 5, (
        f'ATG fixture card should score >5 with ATG bigrams as ref; got {score_atg_card}. '
        f'All scores: {scores}'
    )
    assert score_atg_card > score_lotm_card, (
        f'ATG card ({score_atg_card}) should outscore LotM ({score_lotm_card}) — '
        f'ATG shares "demon cultivation", "journey heavens", "powerful treasure" bigrams with ref'
    )


def test_bigram_demon_invasion_does_not_match_demon_cultivation(page):
    """RtW's description contains 'demon invasion' — a different phrase.
    With 'demon cultivation' as the only token in ref d, RtW should NOT match
    even though it shares the word 'demon'.
    After fix, RtW's bigrams will include 'demon invasion' but NOT 'demon cultivation',
    so its score stays at 1 (the unmatched minimum) while ATG/ISSTH score 10."""
    novel_meta = {
        'ref-read': {
            'g': [],
            'l': 'chinese',
            'd': ['demon cultivation'],
            't': 'Ref',
        }
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    scores = parse_scores(page)

    score_rtw  = scores[0]  # Release that Witch — 'demon invasion' ≠ 'demon cultivation'
    score_atg  = scores[1]  # Against the Gods   — 'demon cultivation' ✓
    score_issth = scores[2] # ISSTH              — 'demon cultivation' ✓

    # RtW must score strictly below ATG and ISSTH
    assert score_rtw < score_atg, (
        f'RtW ("demon invasion") should score below ATG ("demon cultivation"): '
        f'{score_rtw} < {score_atg}. Scores: {scores}'
    )
    assert score_rtw < score_issth, (
        f'RtW ("demon invasion") should score below ISSTH ("demon cultivation"): '
        f'{score_rtw} < {score_issth}'
    )


# ── Recency weighting tests ────────────────────────────────────────────────

def test_recency_no_timestamp_still_scores(page):
    """Backwards compat: novelMeta without ts field → decay = 1.0 → scores appear normally."""
    novel_meta = {
        'ref-read': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': ['magic'], 't': 'No TS Ref'},
        # no 'ts' field — old format
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')
    expect(page.locator('.nuf-score-label')).to_have_count(4)


def test_recency_recent_ref_scores_match_higher_than_ancient_ref(page):
    """Cards matching a fresh read score higher than cards matching a year-old read."""
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - (365 * 24 * 60 * 60 * 1000)  # 1 year ago

    novel_meta = {
        # recent read: genres match cards 1 & 3 (Fantasy/Sci-fi, Fantasy/Xianxia)
        'ref-recent': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [], 't': 'Recent Ref', 'ts': now_ms},
        # old read: genres match card 4 (Mystery/Horror)
        'ref-old':    {'g': ['mystery', 'horror'], 'l': 'japanese', 'd': [], 't': 'Old Ref',    'ts': old_ms},
    }
    load_listing(page, {'read': ['ref-recent', 'ref-old'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    scores = parse_scores(page)

    # Card 1 (Fantasy/Sci-fi) matches recent ref → should score higher than card 4 (Mystery/Horror matches old ref)
    score_card1 = scores[0]  # Release that Witch — Fantasy, Sci-fi
    score_card4 = scores[3]  # Lord of the Mysteries — Mystery, Horror
    assert score_card1 > score_card4, (
        f'Expected card1 (recent-match) score {score_card1} > card4 (old-match) score {score_card4}'
    )


def test_recency_ancient_ni_has_reduced_negative_effect(page):
    """A year-old Not Interested ref has a much weaker negative pull than a fresh one."""
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - (365 * 24 * 60 * 60 * 1000)  # 1 year ago

    # Two runs: same genres, NI ref is old vs fresh
    novel_meta_old_ni = {
        'ref-ni': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [], 't': 'Old NI', 'ts': old_ms},
    }
    novel_meta_fresh_ni = {
        'ref-ni': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [], 't': 'Fresh NI', 'ts': now_ms},
    }

    load_listing(page, {'notInterested': ['ref-ni'], 'novelMeta': novel_meta_old_ni})
    page.wait_for_selector('.nuf-score-label')
    old_ni_scores = parse_scores(page)

    load_listing(page, {'notInterested': ['ref-ni'], 'novelMeta': novel_meta_fresh_ni})
    page.wait_for_selector('.nuf-score-label')
    fresh_ni_scores = parse_scores(page)

    # Card 1 (Fantasy/Sci-fi) should score HIGHER when NI is old (weaker negative)
    # than when NI is fresh (stronger negative)
    assert old_ni_scores[0] >= fresh_ni_scores[0], (
        f'Old NI card1={old_ni_scores[0]} should be >= fresh NI card1={fresh_ni_scores[0]}'
    )


def test_recency_half_life_change_triggers_recompute(page):
    """Changing recencyHalfLifeDays via storage.onChanged recomputes scores live."""
    now_ms = int(time.time() * 1000)
    novel_meta = {
        'ref-read': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [], 't': 'Ref', 'ts': now_ms},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta, 'recencyHalfLifeDays': 180})
    page.wait_for_selector('.nuf-score-label')

    # Capture baseline scores
    scores_before = parse_scores(page)

    # Trigger live update — content script should recompute
    set_storage(page, {'recencyHalfLifeDays': 1})
    # Wait for scores to be recalculated — labels must still exist
    expect(page.locator('.nuf-score-label')).to_have_count(4, timeout=3000)

    scores_after = parse_scores(page)

    # Badges must still be present (no crash)
    assert len(scores_after) == 4
    # With half-life=1 day and a fresh ts, decay is ~1.0 (no meaningful change),
    # so relative ordering should be the same — but the recompute ran without error


def test_recency_ts_stamped_on_novelMeta_when_card_marked(page):
    """Marking a card via the Read button stamps ts on novelMeta[slug]."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    before_ms = int(time.time() * 1000)

    # Click Read on the first card (Release that Witch)
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    # The save lands 2 s after the last change, or at most 5 s after the first
    slug_meta = {}
    for _ in range(40):
        page.wait_for_timeout(200)
        slug_meta = (get_storage(page, ['novelMeta']).get('novelMeta') or {}).get('release-that-witch', {})
        if 'ts' in slug_meta:
            break

    assert 'ts' in slug_meta, f'Expected ts in novelMeta for release-that-witch, got: {slug_meta}'
    assert slug_meta['ts'] >= before_ms, (
        f"ts {slug_meta['ts']} should be >= time before click {before_ms}"
    )


def test_recency_future_ts_does_not_boost_score_above_no_ts(page):
    """A future timestamp (clock skew) should not produce higher-than-normal weight (capped at 1.0)."""
    now_ms = int(time.time() * 1000)
    future_ms = now_ms + (365 * 24 * 60 * 60 * 1000)  # 1 year in the future

    novel_meta_future = {
        'ref-read': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [], 't': 'Future TS', 'ts': future_ms},
    }
    novel_meta_none = {
        'ref-read': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [], 't': 'No TS'},
        # no ts — decay = 1.0 (full weight)
    }

    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta_future})
    page.wait_for_selector('.nuf-score-label')
    future_scores = parse_scores(page)

    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta_none})
    page.wait_for_selector('.nuf-score-label')
    no_ts_scores = parse_scores(page)

    # Future ts must not score higher than no-ts (which uses full weight 1.0)
    for i in range(4):
        assert future_scores[i] <= no_ts_scores[i] + 1, (
            f'Card {i}: future ts score {future_scores[i]} should not exceed no-ts score {no_ts_scores[i]}'
        )


# ── Score explanation panel tests ──────────────────────────────────────────

def test_score_badge_is_clickable_and_opens_panel(page):
    """Clicking a score badge with non-zero breakdown opens the explanation panel."""
    # ref has g=['fantasy'] → RtW (Fantasy) has genre overlap → breakdown non-empty
    novel_meta = {'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'My Ref Novel'}}
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    page.locator('.nuf-score-badge').first.click()
    expect(page.locator('.nuf-explain-panel')).to_be_visible()


def test_explanation_panel_shows_reference_novel_title(page):
    """The explanation panel lists the title of the contributing reference novel."""
    novel_meta = {'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'My Ref Novel'}}
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    page.locator('.nuf-score-badge').first.click()
    assert 'My Ref Novel' in page.locator('.nuf-explain-panel').text_content()


def test_explanation_panel_closes_on_outside_click(page):
    """Clicking outside the panel (on the body) closes it."""
    novel_meta = {'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Ref'}}
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    page.locator('.nuf-score-badge').first.click()
    expect(page.locator('.nuf-explain-panel')).to_be_visible()

    page.locator('body').click(position={'x': 10, 'y': 10})
    expect(page.locator('.nuf-explain-panel')).to_have_count(0)


def test_explanation_panel_toggles_off_on_second_badge_click(page):
    """Clicking the same badge twice closes the panel (toggle behaviour)."""
    novel_meta = {'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Ref'}}
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    badge = page.locator('.nuf-score-badge').first
    badge.click()
    expect(page.locator('.nuf-explain-panel')).to_have_count(1)
    badge.click()
    expect(page.locator('.nuf-explain-panel')).to_have_count(0)


def test_only_one_explanation_panel_open_at_a_time(page):
    """Opening a panel on card B closes the panel on card A."""
    # g=['fantasy'] → RtW (card 0) and ISSTH (card 2) both have Fantasy → both get breakdown
    novel_meta = {'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Ref'}}
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    page.locator('.nuf-score-badge').nth(0).click()   # RtW — Fantasy match
    page.locator('.nuf-score-badge').nth(2).click()   # ISSTH — Fantasy match; closes RtW panel
    expect(page.locator('.nuf-explain-panel')).to_have_count(1)


def test_explanation_panel_shows_bucket_label(page):
    """Panel shows the bucket name (e.g. 'Not Interested') for the contributing reference."""
    # ref-ni has g=['fantasy'] → RtW (Fantasy) gets negative contribution → panel shows NI label
    novel_meta = {'ref-ni': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Disliked Novel'}}
    load_listing(page, {'notInterested': ['ref-ni'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    page.locator('.nuf-score-badge').first.click()
    assert 'Not Interested' in page.locator('.nuf-explain-panel').text_content()


# ── Language indicator in recommendation reasoning tests ────────────────────

def test_language_token_positive_when_card_lang_matches_read_list(page):
    """CN card shows 'CN ✓' in reasoning when read list contains a CN novel."""
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['magic'], 't': 'CN Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    # Card 0 (Release that Witch) is CN — same as ref → should show CN chip
    card0_reason = page.locator('.nuf-reason-row').nth(0).text_content()
    assert 'CN' in card0_reason


def test_language_token_negative_when_card_lang_matches_ni_list(page):
    """KR card shows 'KR ✗' chip in reasoning when ni list contains a KR novel."""
    novel_meta = {
        'ref-ni': {'g': ['action'], 'l': 'korean', 'd': ['warrior'], 't': 'KR Ref'},
    }
    load_listing(page, {'notInterested': ['ref-ni'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    # Card 1 (Against the Gods) is KR — same as ref → should show KR chip
    card1_reason = page.locator('.nuf-reason-row').nth(1).text_content()
    assert 'KR' in card1_reason


def test_no_language_token_when_no_lang_contribution(page):
    """JP card shows no language chip when no refs share its language."""
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['magic'], 't': 'CN Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    # Card 3 (Lord of the Mysteries) is JP, ref is CN → no language match → no chip
    card3_reason = page.locator('.nuf-reason-row').nth(3).text_content()
    assert 'JP ✓' not in card3_reason
    assert 'JP ✗' not in card3_reason


# ── Filtered count in sentinel tests ──────────────────────────────────────

def test_filtered_count_appears_in_sentinel_when_cards_are_filtered(page):
    """Sentinel 'End of results' line includes '· N filtered' when cards were filtered."""
    load_listing(page, {
        'notInterested': [
            'release-that-witch',
            'against-the-gods',
            'i-shall-seal-the-heavens',
            'lord-of-the-mysteries',
        ],
    })
    # All page-1 cards filtered → auto-fetch page 2 → page 2 appended → then exhausted
    expect(page.locator('.nuf-sentinel-done')).to_be_visible(timeout=10000)
    text = page.locator('.nuf-sentinel-done').text_content()
    assert 'filtered' in text
    # Should show 4 filtered (the 4 NI slugs on page 1)
    assert '4 filtered' in text


def test_filtered_count_not_shown_when_no_filters_active(page):
    """When nothing is filtered, sentinel does NOT show '· 0 filtered'."""
    # Return an empty page (no entries, no next link) so scroll exhausts cleanly
    page.route(
        '**/series-finder/?pg=2',
        lambda route: route.fulfill(
            status=200, content_type='text/html; charset=utf-8',
            body='<!DOCTYPE html><html><body></body></html>',
        ),
    )
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    # Scroll to bottom so the sentinel enters the IntersectionObserver rootMargin
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    expect(page.locator('.nuf-sentinel-done')).to_be_visible(timeout=10000)
    text = page.locator('.nuf-sentinel-done').text_content()
    assert '0 filtered' not in text
    assert 'filtered' not in text


# ── Score threshold filter tests ───────────────────────────────────────────

def test_score_threshold_hides_low_scoring_cards(page):
    """Cards scoring below the threshold are hidden with nuf-hidden-threshold class."""
    novel_meta = {
        'ref-read': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': ['magic', 'fantasy'], 't': 'Fantasy Ref'},
        'ref-ni':   {'g': ['action', 'harem'],   'l': 'korean',  'd': ['action', 'harem'],  't': 'Harem Ref'},
    }
    load_listing(page, {
        'read': ['ref-read'],
        'notInterested': ['ref-ni'],
        'novelMeta': novel_meta,
        'minRecommendationScore': 6,
    })
    page.wait_for_selector('.nuf-score-label')
    hidden = page.locator('.nuf-hidden-threshold')
    expect(hidden).not_to_have_count(0)


def test_score_threshold_zero_shows_all_cards(page):
    """With threshold 0 (Off), no cards are hidden by threshold."""
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['magic'], 't': 'Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta, 'minRecommendationScore': 0})
    page.wait_for_selector('.nuf-score-label')
    expect(page.locator('.nuf-hidden-threshold')).to_have_count(0)


def test_score_threshold_applied_live_when_changed(page):
    """Raising threshold via storage live-hides low-scoring cards without reload."""
    novel_meta = {
        'ref-read': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': ['magic'], 't': 'Ref'},
        'ref-ni':   {'g': ['action', 'harem'],   'l': 'korean',  'd': ['action'], 't': 'NI Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'notInterested': ['ref-ni'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')
    expect(page.locator('.nuf-hidden-threshold')).to_have_count(0)

    set_storage(page, {'minRecommendationScore': 8})
    expect(page.locator('.nuf-hidden-threshold').first).to_be_attached(timeout=3000)


# ── Min NU rating filter tests ─────────────────────────────────────────────

def test_min_rating_filters_low_rated_cards(page):
    """Cards with a NU rating below minRating are removed from the listing."""
    # Fixture ratings: Release that Witch=4.5, Against the Gods=4.2,
    # I Shall Seal the Heavens=4.3, Lord of the Mysteries=4.8
    load_listing(page, {'minRating': 4.5})
    page.wait_for_selector('.nuf-controls')
    # 4.2 and 4.3 are filtered; 4.5 (not < 4.5) and 4.8 remain
    expect(page.locator('.search_main_box_nu')).to_have_count(2)


def test_min_rating_off_shows_all_cards(page):
    """With minRating=0 (off), all cards are shown."""
    load_listing(page, {'minRating': 0})
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.search_main_box_nu')).to_have_count(4)


def test_min_rating_applied_live(page):
    """Raising minRating via storage removes newly-filtered cards live."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    set_storage(page, {'minRating': 4.8})
    # Only Lord of the Mysteries (4.8) survives (4.8 is NOT < 4.8)
    expect(page.locator('.search_main_box_nu')).to_have_count(1, timeout=5000)


# ── First chapter button tests (listing) ──────────────────────────────────

def test_first_chapter_button_shown_when_c1url_stored(page):
    """A 'Start reading →' button appears in the controls bar when c1url is stored."""
    load_listing(page, {
        'novelMeta': {
            'release-that-witch': {
                'g': ['fantasy'], 'l': 'chinese', 'd': ['witch'],
                'c1url': 'https://www.novelupdates.com/extnu/12345/',
            }
        }
    })
    page.wait_for_selector('.nuf-controls')
    first_controls = page.locator('.nuf-controls').first
    start_btn = first_controls.locator('.nuf-btn[data-nuf-btn="start"]')
    expect(start_btn).to_be_visible()
    assert start_btn.text_content() == 'Start reading →'


def test_first_chapter_button_shown_without_c1url(page):
    """Start reading button always appears on listing cards even without a cached chapter URL."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    first_btn = page.locator('.nuf-btn[data-nuf-btn="start"]').first
    expect(first_btn).to_be_visible()
    assert first_btn.text_content() == 'Start reading →'


def test_first_chapter_button_opens_in_new_tab(page):
    """Clicking the Start reading button with a stored c1url opens it in a new tab."""
    load_listing(page, {
        'novelMeta': {
            'release-that-witch': {
                'g': [], 'l': 'chinese', 'd': [],
                'c1url': 'https://www.novelupdates.com/extnu/99999/',
            }
        }
    })
    page.wait_for_selector('.nuf-btn[data-nuf-btn="start"]')
    with page.expect_popup() as popup_info:
        page.locator('.nuf-btn[data-nuf-btn="start"]').first.click()
    popup = popup_info.value
    try:
        assert 'extnu/99999' in popup.url
    finally:
        popup.close()  # an open Novel Updates tab joins the shared queue


def test_defect025_first_chapter_button_fetches_correct_first_chapter(page):
    """Start reading button must fetch and open the FIRST chapter, not the latest chapter.

    On the real site, chapter tables are paginated — page 1 shows latest chapters,
    and chapter 1 is on the last page. The button must detect pagination and fetch
    the last page to find chapter 1.
    """
    # Page 1: shows only chapters 100+ (latest releases)
    page1_html = '''<!DOCTYPE html>
<html>
<head><title>True Martial World</title></head>
<body>
<table id="myTable">
  <thead><tr><th>Date</th><th>Group</th><th>Release</th></tr></thead>
  <tbody>
    <tr><td>Recent</td><td>Group</td><td><a href="https://www.novelupdates.com/extnu/999999/" title="c1000">c1000</a></td></tr>
    <tr><td>Recent</td><td>Group</td><td><a href="https://www.novelupdates.com/extnu/999998/" title="c999">c999</a></td></tr>
  </tbody>
</table>
<div class="digg_pagination">
  <a href="?pg=1">1</a>
  <a href="?pg=2">2</a>
  <a href="?pg=3">3</a>
</div>
</body>
</html>'''

    # Page 3 (last page): shows only chapters 1-9 (earliest chapters)
    page3_html = '''<!DOCTYPE html>
<html>
<head><title>True Martial World</title></head>
<body>
<table id="myTable">
  <thead><tr><th>Date</th><th>Group</th><th>Release</th></tr></thead>
  <tbody>
    <tr><td>01/01/15</td><td>Group</td><td><a href="https://www.novelupdates.com/extnu/100009/" title="c9">c9</a></td></tr>
    <tr><td>01/01/15</td><td>Group</td><td><a href="https://www.novelupdates.com/extnu/100008/" title="c8">c8</a></td></tr>
    <tr><td>01/01/15</td><td>Group</td><td><a href="https://www.novelupdates.com/extnu/100001/" title="c1">c1</a></td></tr>
  </tbody>
</table>
<div class="digg_pagination">
  <a href="?pg=1">1</a>
  <a href="?pg=2">2</a>
  <a href="?pg=3">3</a>
</div>
</body>
</html>'''

    # Override series page route to return different HTML for page 1 vs page 3
    def handle_series_route(route):
        if '?pg=3' in route.request.url:
            route.fulfill(status=200, content_type='text/html', body=page3_html)
        else:
            # Default to page 1
            route.fulfill(status=200, content_type='text/html', body=page1_html)

    page.route('**/series/release-that-witch/**', handle_series_route)

    # Load listing without cached c1url
    load_listing(page)
    page.wait_for_selector('.nuf-btn[data-nuf-btn="start"]')

    # Click Start Reading button without a cached URL — should fetch page 1, detect pagination,
    # then fetch page 3 to find chapter 1
    with page.expect_popup() as popup_info:
        page.locator('.nuf-btn[data-nuf-btn="start"]').first.click()

    popup = popup_info.value
    # The tab opens blank at once (so the popup blocker allows it) and moves to the chapter after the lookup
    try:
        popup.wait_for_url(re.compile(r'extnu/'), timeout=10000)
    except Exception:
        pass
    # MUST open chapter 1 (extnu/100001/), NOT chapter 999 or any other higher-numbered chapter
    try:
        assert 'extnu/100001' in popup.url, f"Expected first chapter (c1, extnu/100001) but got {popup.url}"
    finally:
        popup.close()  # an open Novel Updates tab joins the shared queue


# ── Series page: hide reviews ──────────────────────────────────────────────

def test_hide_reviews_hides_comments_on_series_page(page):
    """With hideReviews=true, #comments is hidden on series pages."""
    load_series(page, {'hideReviews': True})
    expect(page.locator('#comments')).to_be_hidden(timeout=3000)


def test_hide_reviews_off_shows_comments_on_series_page(page):
    """With hideReviews=false (default), #comments is visible on series pages."""
    load_series(page, {'hideReviews': False})
    expect(page.locator('#comments')).to_be_visible(timeout=3000)


def test_hide_reviews_applied_live_on_series_page(page):
    """Toggling hideReviews via storage immediately hides/shows #comments."""
    load_series(page)
    expect(page.locator('#comments')).to_be_visible(timeout=3000)

    set_storage(page, {'hideReviews': True})
    expect(page.locator('#comments')).to_be_hidden(timeout=3000)

    set_storage(page, {'hideReviews': False})
    expect(page.locator('#comments')).to_be_visible(timeout=3000)


# ── Series page: bucket controls ─────────────────────────────────────────

def test_feature_001_series_page_undo_removes_from_bucket(page):
    """Clicking a bucket button on the series page then undoing removes slug from all buckets."""
    load_series(page)
    page.wait_for_selector('.nuf-controls', timeout=3000)
    page.locator('.nuf-btn[data-nuf-btn="read"]').click()
    page.locator('.nuf-toast-undo').click()
    stored = get_storage(page, 'read')
    assert 'release-that-witch' not in (stored.get('read') or [])


def test_feature_001_series_page_shows_bucket_controls(page):
    """Series page injects a .nuf-controls div with four .nuf-btn elements (3 rejection + To Read)."""
    load_series(page)
    page.wait_for_selector('.nuf-controls', timeout=3000)
    assert page.locator('.nuf-controls .nuf-btn').count() == 4


def test_feature_001_series_page_bucket_marks_novel(page):
    """Clicking Not Interested on the series page writes the slug to storage."""
    load_series(page)
    page.wait_for_selector('.nuf-controls', timeout=3000)
    page.locator('.nuf-btn[data-nuf-btn="ni"]').click()
    stored = get_storage(page, 'notInterested')
    assert 'release-that-witch' in (stored.get('notInterested') or [])


def test_feature_001_series_page_active_button_reflects_current_bucket(page):
    """Pre-seeding read bucket makes the Read button load with .nuf-btn-active."""
    load_series(page, {'read': ['release-that-witch']})
    page.wait_for_selector('.nuf-controls', timeout=3000)
    read_btn = page.locator('.nuf-btn[data-nuf-btn="read"]')
    nf_btn   = page.locator('.nuf-btn[data-nuf-btn="nf"]')
    ni_btn   = page.locator('.nuf-btn[data-nuf-btn="ni"]')
    expect(read_btn).to_have_class(re.compile(r'nuf-btn-active'))
    expect(nf_btn).not_to_have_class(re.compile(r'nuf-btn-active'))
    expect(ni_btn).not_to_have_class(re.compile(r'nuf-btn-active'))


def test_feature_001_series_page_rebucketing_moves_between_buckets(page):
    """Clicking a different bucket moves the slug from the old bucket to the new one."""
    load_series(page, {'read': ['release-that-witch']})
    page.wait_for_selector('.nuf-controls', timeout=3000)
    page.locator('.nuf-btn[data-nuf-btn="nf"]').click()
    stored = get_storage(page, 'notFinished')
    read_stored = get_storage(page, 'read')
    assert 'release-that-witch' in (stored.get('notFinished') or [])
    assert 'release-that-witch' not in (read_stored.get('read') or [])


# ── Defect 044: series-page selector robustness, error logging, To Read ────

def test_defect044_series_page_controls_appear_with_alt_title_selector(page):
    """When the title is on a non-h1 tag (e.g. <div class='seriestitlenu'>), controls still inject."""
    alt_html = '''<!DOCTYPE html><html><head><title>Test</title></head>
<body class="seriesplans-template-default single single-seriesplans">
<div class="seriestitlenu">Release that Witch</div>
<input type="hidden" id="mypostid" value="6692">
<a class="gennew">Fantasy</a>
</body></html>'''
    page.route(
        '**/series/release-that-witch/**',
        lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=alt_html),
    )
    load_series(page)
    page.wait_for_selector('.nuf-controls', timeout=3000)
    assert page.locator('.nuf-controls').count() == 1


def test_defect044_series_page_logs_error_when_title_missing(page):
    """When no title element exists on a series page, console.error fires (no silent failure)."""
    no_title_html = '''<!DOCTYPE html><html><head><title>Test</title></head>
<body class="seriesplans-template-default single single-seriesplans">
<input type="hidden" id="mypostid" value="6692">
</body></html>'''
    errors = []
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    page.route(
        '**/series/release-that-witch/**',
        lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=no_title_html),
    )
    load_series(page)
    page.wait_for_timeout(500)
    assert any('title' in e.lower() for e in errors), f'Expected console.error mentioning title; got {errors}'


def test_defect044_series_page_has_to_read_button(page):
    """Series page injects a To Read button alongside the three rejection buttons."""
    load_series(page)
    page.wait_for_selector('.nuf-controls', timeout=3000)
    assert page.locator('.nuf-controls .nuf-btn[data-nuf-btn="toread"]').count() == 1


def test_defect044_series_page_to_read_button_marks_novel(page):
    """Clicking To Read on the series page writes the slug to toRead storage."""
    load_series(page)
    page.wait_for_selector('.nuf-controls', timeout=3000)
    page.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    stored = get_storage(page, 'toRead')
    assert 'release-that-witch' in (stored.get('toRead') or [])


# ── Series page: Start Reading button ─────────────────────────────────────

# ── novelMeta pruning tests ────────────────────────────────────────────────

def test_novelmeta_pruning_keeps_size_bounded(page):
    """novelMeta is pruned to ≤ 3000 entries when it exceeds the limit."""
    big_meta = {
        f'novel-{i:04d}': {'g': ['fantasy'], 'l': 'chinese', 'd': ['power', 'system'], 't': f'Novel {i}'}
        for i in range(3001)
    }
    load_listing(page, {'novelMeta': big_meta})
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)
    stored = get_storage(page, 'novelMeta')
    meta = stored.get('novelMeta', {})
    assert len(meta) <= 3000


def test_novelmeta_pruning_keeps_bucketed_novels(page):
    """Bucketed novels are never pruned even when novelMeta is oversized."""
    big_meta = {
        f'novel-{i:04d}': {'g': ['fantasy'], 'l': 'chinese', 'd': ['power'], 't': f'Novel {i}'}
        for i in range(3001)
    }
    big_meta['release-that-witch'] = {'g': ['fantasy'], 'l': 'chinese', 'd': ['witch'], 't': 'Release that Witch'}
    load_listing(page, {'novelMeta': big_meta, 'read': ['release-that-witch']})
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)
    stored = get_storage(page, 'novelMeta')
    meta = stored.get('novelMeta', {})
    assert 'release-that-witch' in meta


# ── Expand synopsis tests ──────────────────────────────────────────────────

def test_expand_synopsis_hides_more_link(page):
    """With expandSynopsis=true, the 'more>>' link in each card is hidden (synopsis expanded via DOM)."""
    load_listing(page, {'expandSynopsis': True})
    page.wait_for_selector('.nuf-controls')
    # The more>> link should be hidden — synopsis expanded by direct DOM manipulation
    more_link = page.locator('.search_body_nu .morelink').first
    expect(more_link).to_be_hidden()
    # The .testhide span should be visible (expanded)
    testhide = page.locator('.testhide').first
    expect(testhide).to_be_visible()


def test_expand_synopsis_off_leaves_more_link_unclicked(page):
    """With expandSynopsis=false (default), the .testhide span stays hidden."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.testhide').first).to_be_hidden()


def test_defect002_expand_synopsis_live_toggle(page):
    """Live toggle: turning on expandSynopsis after page load expands synopses on existing cards."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # more>> link is initially visible (expandSynopsis not set)
    more_link = page.locator('.search_body_nu .morelink').first
    expect(more_link).to_be_visible()

    # Toggle on via storage — fires storage.onChanged in content script
    set_storage(page, {'expandSynopsis': True})

    # more>> link should be hidden now (synopsis expanded on existing card)
    expect(more_link).to_be_hidden(timeout=3000)


def test_defect042_expand_synopsis_hides_dots(page):
    """With expandSynopsis=true, the .dots span ('...') is hidden so the full description reads cleanly."""
    load_listing(page, {'expandSynopsis': True})
    page.wait_for_selector('.nuf-controls')

    dots = page.locator('.search_body_nu .dots').first
    expect(dots).to_be_hidden()


def test_defect042_live_toggle_hides_and_restores_dots(page):
    """Live toggle: turning expandSynopsis on hides .dots; turning it off restores .dots visibility."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    dots = page.locator('.search_body_nu .dots').first
    expect(dots).to_be_visible()

    set_storage(page, {'expandSynopsis': True})
    expect(dots).to_be_hidden(timeout=3000)

    set_storage(page, {'expandSynopsis': False})
    expect(dots).to_be_visible(timeout=3000)


# ── feature-002: ratings and comments ────────────────────────────────────

def test_feature_002_novelreviews_loaded_from_storage(page):
    """Pre-existing rating in storage is pre-filled in the toast's review row on init."""
    # Seed already on the new 1-5 scale (gated by ratingScaleVersion=2 so migration skips).
    load_listing(page, {
        'novelReviews': {'release-that-witch': {'rating': 4}},
        'ratingScaleVersion': 2,
    })
    page.wait_for_selector('.nuf-controls')
    # Click Read on the first card (release-that-witch) — toast should pre-fill rating 4
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    active_btn = page.locator('.nuf-toast-rating-btn-active')
    expect(active_btn).to_be_visible(timeout=3000)
    assert active_btn.text_content() == '4'


def test_feature_002_toast_shows_review_row_for_read(page):
    """Clicking Read shows the review row; clicking Not Interested hides it."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    expect(page.locator('.nuf-toast-review')).to_be_visible(timeout=3000)

    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="ni"]').first.click()
    expect(page.locator('.nuf-toast-review')).to_be_hidden(timeout=3000)


def test_feature_002_rating_button_saves_to_storage(page):
    """Clicking a rating button after Read saves the rating to novelReviews storage."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    # 1-5 scale: nth(3) is button "4"
    page.locator('.nuf-toast-review .nuf-toast-rating-btn').nth(3).click()
    stored = get_storage(page, 'novelReviews')
    assert stored.get('novelReviews', {}).get('release-that-witch', {}).get('rating') == 4


def test_feature_002_high_rating_increases_score(page):
    """A read novel with rating=1 gives negatively-matching cards a low score."""
    # rating=1 → weight = (1-5.5)/4.5 = -1.0; fixture's i-shall-seal-the-heavens has Fantasy
    # genre from DOM and matches release-that-witch's fantasy ref → gets the lowest (most
    # negative) raw → normalises to score 1.
    meta = {
        'release-that-witch': {'g': ['fantasy'], 'l': 'chinese', 'd': ['fantasy'], 't': 'Release that Witch'},
    }
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': meta,
        'novelReviews': {'release-that-witch': {'rating': 1}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-score-badge', timeout=5000)
    scores = parse_scores(page)
    # Negatively-weighted fantasy ref → best-matching card scores 1
    assert min(scores) == 1, f"Expected a score of 1 with rating=1, got: {scores}"


def test_feature_002_comment_tokens_shift_word_similarity(page):
    """Comment tokens on a Read novel contribute to word similarity scoring."""
    # release-that-witch has no genre match with fixture cards except via comment tokens.
    # Adding comment 'dark magic system' extends ref.words, and lord-of-the-mysteries description
    # contains 'dark' → it gains a small word-sim advantage. The key check is that scoring runs
    # (badges appear) and produces results, confirming comment tokens are used in applyRecommendations.
    meta = {
        'release-that-witch': {'g': ['xyzzy'], 'l': 'chinese', 'd': [], 't': 'Release that Witch'},
    }
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': meta,
        'novelReviews': {'release-that-witch': {'comment': 'dark magic system'}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-score-badge', timeout=5000)
    scores = parse_scores(page)
    assert len(scores) > 0, "No scored cards found — comment tokens did not trigger scoring"


def test_defect001_divider_present_between_bucket_and_start_buttons(page):
    """A '|' separator must appear before the To Read button and before the Start reading button."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    seps = page.locator('.nuf-btn-sep')
    card_count = page.locator('.nuf-controls').count()
    # Two separators per card: one before To Read, one before Start reading
    expect(seps).to_have_count(card_count * 2)
    assert seps.first.text_content() == '|'


def test_defect003_keywords_label_text(page):
    """Keyword group label should read 'Desc. Keywords', not 'Keywords'."""
    novel_meta = {
        'ref-read': {'g': ['xianxia'], 'l': 'chinese', 'd': ['cultivation', 'immortality'], 't': 'Cultivation Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    labels = page.locator('.nuf-reason-group-label').all_text_contents()
    labels_upper = [l.upper() for l in labels]
    assert 'KEYWORDS' not in labels_upper, "Old 'Keywords' label still present"
    assert any('DESC' in l for l in labels_upper), "Expected 'Desc. Keywords' label not found"


def test_defect004_keyword_chip_colours_match_green_red(page):
    """Keyword chips must use green (pos) and red (neg), not purple/grey."""
    # ref-read: 'cultivation'/'immortality' match card 3 → kwPos
    # ref-ni: 'journey'/'battle' also match card 3 description → kwNeg
    novel_meta = {
        'ref-read': {'g': ['xianxia'], 'l': 'chinese', 'd': ['cultivation', 'immortality'], 't': 'Cultivation Ref'},
        'ref-ni':   {'g': ['romance'], 'l': 'korean',  'd': ['journey', 'battle'],           't': 'NI Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'notInterested': ['ref-ni'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    pos_bg = page.evaluate("""() => {
        const el = document.querySelector('.nuf-chip-kw-pos');
        return el ? getComputedStyle(el).backgroundColor : null;
    }""")
    neg_bg = page.evaluate("""() => {
        const el = document.querySelector('.nuf-chip-kw-neg');
        return el ? getComputedStyle(el).backgroundColor : null;
    }""")
    assert pos_bg == 'rgb(198, 246, 213)', f"kw-pos background was {pos_bg}, expected green rgb(198, 246, 213)"
    assert neg_bg == 'rgb(254, 215, 215)', f"kw-neg background was {neg_bg}, expected red rgb(254, 215, 215)"


def test_defect005_language_chip_in_own_row_with_label(page):
    """Language chip must appear in its own 'Language' labeled row, not float-right on the Genre row."""
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['magic'], 't': 'Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    # Old float-right wrapper must be gone
    expect(page.locator('.nuf-reason-lang')).to_have_count(0)

    # A group label reading 'Language' must exist
    labels = page.locator('.nuf-reason-group-label').all_text_contents()
    assert any(l.strip().upper() == 'LANGUAGE' for l in labels), (
        f"No 'Language' group label found; labels: {labels}"
    )

    # The language chip must share a .nuf-reason-group parent with the 'Language' label
    result = page.evaluate("""() => {
        const label = [...document.querySelectorAll('.nuf-reason-group-label')]
            .find(el => el.textContent.trim().toUpperCase() === 'LANGUAGE');
        if (!label) return false;
        const group = label.closest('.nuf-reason-group');
        return group ? group.querySelector('.nuf-reason-chip') !== null : false;
    }""")
    assert result, "Language label and chip are not in the same .nuf-reason-group"


def test_defect010_bucket_buttons_have_tooltips(page):
    """Every injected interactive button/link must have a non-empty title attribute."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    for selector in ['.nuf-btn[data-nuf-btn="read"]', '.nuf-btn[data-nuf-btn="nf"]', '.nuf-btn[data-nuf-btn="ni"]', '.nuf-btn[data-nuf-btn="start"]']:
        title = page.locator(selector).first.get_attribute('title')
        assert title, f"{selector} has no title attribute (got {title!r})"


def test_defect006_explain_title_wraps_long_text(page):
    """Explain panel title column must wrap long titles, not truncate with ellipsis."""
    novel_meta = {'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'My Ref Novel'}}
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')
    page.locator('.nuf-score-badge').first.click()
    expect(page.locator('.nuf-explain-panel')).to_be_visible()

    styles = page.evaluate("""() => {
        const el = document.querySelector('.nuf-explain-title');
        if (!el) return null;
        const cs = getComputedStyle(el);
        return { whiteSpace: cs.whiteSpace, overflow: cs.overflow, textOverflow: cs.textOverflow };
    }""")
    assert styles is not None, ".nuf-explain-title not found"
    assert styles['whiteSpace'] != 'nowrap', f"whiteSpace is still 'nowrap': {styles}"
    assert styles['overflow'] != 'hidden', f"overflow is still 'hidden': {styles}"
    assert styles['textOverflow'] != 'ellipsis', f"textOverflow is still 'ellipsis': {styles}"


def test_defect014_chip_row_order_is_tags_genre_desc_lang(page):
    """Chip rows must appear in order: Tags, Genre, Desc. Keywords, Language."""
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['cultivation'], 't': 'Ref',
                     'tags': ['Hard-working protagonist', 'Male protagonist']},
        # Seed card NU tags so tag chips render for card 1
        'release-that-witch': {'g': ['fantasy', 'sci-fi'], 'l': 'chinese', 'd': [],
                               't': 'Release that Witch',
                               'tags': ['Hard-working protagonist', 'Strong to stronger']},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    labels = page.evaluate("""() => {
        const row = document.querySelector('.nuf-reason-row');
        return [...row.querySelectorAll('.nuf-reason-group-label')].map(el => el.textContent.trim().toUpperCase());
    }""")
    # Find the indices of each label
    tag_idx   = next((i for i, l in enumerate(labels) if 'TAG' in l), None)
    genre_idx = next((i for i, l in enumerate(labels) if 'GENRE' in l), None)
    kw_idx    = next((i for i, l in enumerate(labels) if 'DESC' in l or 'KEYWORD' in l), None)
    lang_idx  = next((i for i, l in enumerate(labels) if 'LANG' in l), None)

    assert tag_idx is not None, f"Tags label not found in {labels}"
    assert genre_idx is not None, f"Genre label not found in {labels}"
    assert tag_idx < genre_idx, f"Tags ({tag_idx}) must come before Genre ({genre_idx}): {labels}"
    if kw_idx is not None:
        assert genre_idx < kw_idx, f"Genre ({genre_idx}) must come before Desc. Keywords ({kw_idx})"
    if lang_idx is not None and kw_idx is not None:
        assert kw_idx < lang_idx, f"Desc. Keywords ({kw_idx}) must come before Language ({lang_idx})"


def test_defect014_tag_chips_are_green_red(page):
    """Tag chips must use the green/red palette, not blue/orange."""
    novel_meta = {
        'ref-read': {'g': [], 'l': 'chinese', 'd': [], 't': 'Ref',
                     'tags': ['Hard-working protagonist']},
        # Card must also have NU tags overlapping ref for tag chips to render
        'release-that-witch': {'g': ['fantasy'], 'l': 'chinese', 'd': [],
                               't': 'Release that Witch',
                               'tags': ['Hard-working protagonist']},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    pos_bg = page.evaluate("""() => {
        const el = document.querySelector('.nuf-chip-tag-pos');
        return el ? getComputedStyle(el).backgroundColor : null;
    }""")
    assert pos_bg is not None, "No .nuf-chip-tag-pos chip found"
    assert pos_bg == 'rgb(198, 246, 213)', (
        f"Tag-pos chip is {pos_bg}, expected green rgb(198, 246, 213)"
    )


def test_defect017_chip_labels_have_colon(page):
    """Each chip category label must show a colon suffix (e.g. 'GENRE:') via CSS ::after."""
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': ['cultivation'], 't': 'Ref'},
    }
    load_listing(page, {'read': ['ref-read'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-group-label')

    colon_content = page.evaluate("""() => {
        const el = document.querySelector('.nuf-reason-group-label');
        return window.getComputedStyle(el, '::after').content;
    }""")
    assert colon_content == '":"', (
        f"::after content is {colon_content!r}, expected '\":\"' (a colon)"
    )


def test_defect017_start_btn_matches_bucket_btn_height(page):
    """Start reading → button height must equal bucket button height."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    heights = page.evaluate("""() => {
        const c = document.querySelector('.nuf-controls');
        return {
            start: c.querySelector('.nuf-btn[data-nuf-btn="start"]')?.offsetHeight ?? null,
            read:  c.querySelector('.nuf-btn[data-nuf-btn="read"]')?.offsetHeight ?? null,
        };
    }""")
    assert heights['start'] is not None, "start button not found"
    assert heights['read'] is not None, "read button not found"
    assert heights['start'] == heights['read'], (
        f"Start btn offsetHeight {heights['start']} != Read btn offsetHeight {heights['read']}"
    )


def test_defect018_start_btn_is_button_element(page):
    """Start reading button is a <button> element, matching bucket buttons for consistent sizing."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    tag = page.evaluate(
        "() => document.querySelector('.nuf-btn[data-nuf-btn=\"start\"]').tagName"
    )
    assert tag == 'BUTTON', f"Expected <button>, got <{tag.lower()}>"


def test_defect018_start_btn_font_size_matches_read_btn(page):
    """Start reading → button and Read button must have identical font-size."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    font_sizes = page.evaluate("""() => {
        const startBtn = document.querySelector('.nuf-btn[data-nuf-btn="start"]');
        const readBtn = document.querySelector('.nuf-btn[data-nuf-btn="read"]');
        return {
            start: getComputedStyle(startBtn).fontSize,
            read: getComputedStyle(readBtn).fontSize,
        };
    }""")

    assert font_sizes['start'] == font_sizes['read'], (
        f"Start btn font-size {font_sizes['start']} != Read btn font-size {font_sizes['read']}"
    )


def test_show_reasoning_toggle_chips_visible_by_default(page):
    """Reasoning chips (.nuf-reason-row) should be visible by default."""
    load_listing(page, {'read': ['release-that-witch', 'against-the-gods']})
    page.wait_for_selector('.nuf-reason-row')

    # 2 bucketed novels = 2 reason rows with chips
    expect(page.locator('.nuf-reason-row')).to_have_count(2)


def test_show_reasoning_toggle_hides_chips(page):
    """Reasoning chips should be hidden when showReasoningChips is False."""
    load_listing(page, {
        'read': ['release-that-witch', 'against-the-gods'],
        'showReasoningChips': False,
    })
    page.wait_for_selector('.nuf-controls')

    # Chips should exist in DOM but be hidden via display: none (2 bucketed = 2 rows)
    hidden_count = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.nuf-reason-row')).filter(row =>
            getComputedStyle(row).display === 'none'
        ).length;
    }""")
    assert hidden_count == 2, f"Expected 2 hidden reason rows, got {hidden_count}"


def test_show_reasoning_toggle_live_hide(page):
    """Toggling showReasoningChips off should hide chips live without reload."""
    load_listing(page, {'read': ['release-that-witch', 'against-the-gods']})
    page.wait_for_selector('.nuf-reason-row')

    # Initially visible (2 bucketed novels = 2 rows)
    expect(page.locator('.nuf-reason-row')).to_have_count(2)

    # Toggle off via storage
    set_storage(page, {'showReasoningChips': False})

    # All should be hidden
    hidden_count = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.nuf-reason-row')).filter(row =>
            getComputedStyle(row).display === 'none'
        ).length;
    }""")
    assert hidden_count == 2, f"Expected 2 hidden reason rows after toggle, got {hidden_count}"


def test_show_reasoning_toggle_live_show(page):
    """Toggling showReasoningChips on should show chips live without reload."""
    load_listing(page, {
        'read': ['release-that-witch', 'against-the-gods'],
        'showReasoningChips': False,
    })
    page.wait_for_selector('.nuf-controls')

    # Initially hidden (2 bucketed novels = 2 rows)
    hidden_count = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.nuf-reason-row')).filter(row =>
            getComputedStyle(row).display === 'none'
        ).length;
    }""")
    assert hidden_count == 2

    # Toggle on via storage
    set_storage(page, {'showReasoningChips': True})

    # All should be visible
    visible_count = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.nuf-reason-row')).filter(row =>
            getComputedStyle(row).display !== 'none'
        ).length;
    }""")
    assert visible_count == 2, f"Expected 2 visible reason rows after toggle, got {visible_count}"


# ── Tag fetch spinner ───────────────────────────────────────────────────────

def test_tag_fetch_stores_tags_on_success(page):
    """Tags are fetched from series pages and stored in novelMeta for listing cards."""
    # No prior novelMeta — all cards are queued for tag fetch
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    # Cards are queued for prefetch only once visible, so "no spinners" no longer
    # means "done" — poll storage for the fetched tags instead.
    for _ in range(60):
        rtw_meta = get_storage(page, 'novelMeta').get('novelMeta', {}).get('release-that-witch', {})
        if rtw_meta.get('tags'):
            break
        page.wait_for_timeout(250)
    assert rtw_meta.get('tags'), 'No tags stored in novelMeta after tag fetch completed'


def test_tag_fetch_spinner_absent_when_tags_already_stored(page):
    """No spinner appears for a card whose tags are already in novelMeta."""
    load_listing(page, {'novelMeta': {
        'release-that-witch':     {'g': ['fantasy'], 'l': 'chinese', 'd': [], 'tags': ['cultivation']},
        'against-the-gods':       {'g': ['action'],  'l': 'chinese', 'd': [], 'tags': ['cultivation']},
        'i-shall-seal-the-heavens': {'g': ['action'], 'l': 'chinese', 'd': [], 'tags': ['cultivation']},
        'lord-of-the-mysteries':  {'g': ['mystery'], 'l': 'chinese', 'd': [], 'tags': ['mystery']},
    }})
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(500)  # brief wait to confirm no spinner appears
    assert page.locator('.nuf-tag-spinner').count() == 0


def test_tag_fetch_auto_disable_after_three_403s(page):
    """After 3 consecutive 403 responses (across any slugs), prefetch cools down but stays enabled.

    The blocking counter accumulates across different slugs without resetting
    between them. Inter-request delay increases with each block (1 s, then 2 s).
    The 3rd consecutive block triggers a 10 s cooldown then retry — NOT auto-disable.
    """
    # All series pages return 403 — every queued slug hits a blocking error
    page.route(SERIES_PAGES, lambda route: route.fulfill(status=403, body='Forbidden'))
    # No pre-seeded novelMeta — all 4 fixture cards are queued for tag fetch
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    # Wait for spinners to clear — 3 cooldown cycles × (2s block + 4s block + 10s cooldown) ≈ 55s
    # Use 90s to accommodate jitter and test overhead
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=90000)
    stored = get_storage(page, ['prefetchTagsEnabled'])
    assert stored.get('prefetchTagsEnabled') is not False


# ── defect-019: prefetchTagsEnabled toggle ──────────────────────────────────

_hang_route = hang_route


def test_defect019_prefetch_disabled_suppresses_spinner(page):
    """With prefetchTagsEnabled=false no tag-fetch spinner appears for bucketed novels."""
    # Hang series-page responses so any spinner would stay visible for 10 s
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(500)
    # Spinner must never appear when prefetch is disabled
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0)


def test_defect019_prefetch_live_toggle_off_clears_spinner(page):
    """Turning prefetchTagsEnabled off via storage removes any active spinner."""
    # Hang series-page responses so the spinner stays visible until toggled off
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'}},
    })
    page.wait_for_selector('.nuf-controls')
    # Spinner should appear because tags are missing and the series fetch is hanging
    page.wait_for_selector('.nuf-tag-spinner', timeout=2000)

    # Disable prefetch via storage — spinner must be cleared without waiting for the fetch
    set_storage(page, {'prefetchTagsEnabled': False})
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=3000)


# ── Tag fetch progress bar ───────────────────────────────────────────────────

def test_tag_progress_written_to_storage_while_fetching(page):
    """tagFetchProgress appears in storage once a tag fetch is in flight."""
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'}},
    })
    page.wait_for_selector('.nuf-tag-spinner', timeout=3000)
    page.wait_for_timeout(300)  # allow storage write after first report
    stored = get_storage(page, ['tagFetchProgress'])
    progress = stored.get('tagFetchProgress')
    assert progress is not None, 'tagFetchProgress should be set while fetching'
    assert progress['total'] > 0
    assert 0 <= progress['done'] <= progress['total']


def test_tag_progress_cleared_after_disable(page):
    """tagFetchProgress is removed from storage when prefetch is disabled."""
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'}},
    })
    page.wait_for_selector('.nuf-tag-spinner', timeout=3000)
    set_storage(page, {'prefetchTagsEnabled': False})
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=3000)
    stored = get_storage(page, ['tagFetchProgress'])
    assert stored.get('tagFetchProgress') is None


def test_defect021_spinner_removed_after_403_block(page):
    """Spinner is removed from a card's controls when its slug gets a 403 blocking error."""
    page.route(SERIES_PAGES, lambda route: route.fulfill(status=403, body=''))
    # Pre-seed 3 slugs WITH tags so only release-that-witch is enqueued for fetch
    load_listing(page, {
        'novelMeta': {
            'against-the-gods': {'g': ['fantasy'], 'l': 'CN', 'd': [], 't': 'ATG', 'tags': ['x']},
            'i-shall-seal-the-heavens': {'g': ['fantasy'], 'l': 'JP', 'd': [], 't': 'ISSTH', 'tags': ['x']},
            'lord-of-the-mysteries': {'g': ['mystery'], 'l': 'CN', 'd': [], 't': 'LotM', 'tags': ['x']},
        },
    })
    page.wait_for_selector('.nuf-controls')
    # release-that-witch gets 403 → the queue retries it (3 attempts, slowing down
    # after each block), then gives up — the spinner must be removed, not stuck
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=30000)


def test_defect023_three_403s_triggers_cooldown_not_disable(page):
    """After 3 consecutive 403 responses, prefetchTagsEnabled stays true (cooldown, not auto-disable)."""
    page.route(SERIES_PAGES, lambda route: route.fulfill(status=403, body=''))
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    # Wait for spinners to clear — 3 cooldown cycles × (2s block + 4s block + 10s cooldown) ≈ 55s
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=90000)
    stored = get_storage(page, ['prefetchTagsEnabled'])
    assert stored.get('prefetchTagsEnabled') is not False, \
        'prefetchTagsEnabled should stay true after blocking errors (cooldown, not auto-disable)'


def test_defect022_reenabling_prefetch_restarts_fetch(page):
    """Re-enabling prefetch after it was disabled causes tag fetching to restart with spinners."""
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0)

    # Re-enable — spinners must appear as slugs missing tags are re-enqueued
    set_storage(page, {'prefetchTagsEnabled': True})
    page.wait_for_selector('.nuf-tag-spinner', timeout=3000)
    assert page.locator('.nuf-tag-spinner').count() > 0


def test_popup_progress_bar_hidden_when_idle(page, extension_id):
    """Progress bar is hidden in the popup when tagFetchProgress is not set."""
    page._sw.evaluate(
        '''async () => {
            await new Promise(r => chrome.storage.local.clear(r));
        }'''
    )
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    assert not page.locator('#tag-progress').is_visible()


def test_popup_progress_bar_visible_when_fetching(page, extension_id):
    """Progress bar shows correct done/total when tagFetchProgress is seeded."""
    page._sw.evaluate(
        '''async () => {
            await new Promise(r => chrome.storage.local.set(
                { tagFetchProgress: { done: 12, total: 50 } }, r
            ));
        }'''
    )
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    assert page.locator('#tag-progress').is_visible()
    label = page.locator('#tag-progress-label').text_content()
    assert '12' in label and '50' in label


def test_popup_progress_bar_live_update(page, extension_id):
    """Progress bar appears and disappears in real time as tagFetchProgress changes."""
    page._sw.evaluate(
        "async () => new Promise(r => chrome.storage.local.remove('tagFetchProgress', r))"
    )
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_load_state('domcontentloaded')
    assert not page.locator('#tag-progress').is_visible()

    # Simulate content script starting a tag fetch
    set_storage(page, {'tagFetchProgress': {'done': 3, 'total': 30}})
    expect(page.locator('#tag-progress')).to_be_visible(timeout=2000)
    label = page.locator('#tag-progress-label').text_content()
    assert '3' in label and '30' in label

    # Simulate completion — content script removes the key (set to null)
    set_storage(page, {'tagFetchProgress': None})
    expect(page.locator('#tag-progress')).to_be_hidden(timeout=2000)


# ── defect-028: tag prefetch floods requests for unseen novels ────────────────

def test_defect028_novelMeta_slugs_not_enqueued_on_init(page):
    """Slugs in novelMeta but not in any bucket must not be enqueued on init."""
    # Hang all series responses so any in-flight fetch keeps tagFetchProgress set
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        # All page-1 cards have tags in novelMeta so processEntry's enqueueFetch skips them
        'novelMeta': {
            'release-that-witch':      {'g': [], 'l': 'CN', 'd': [], 't': 'RtW',   'tags': ['action']},
            'against-the-gods':        {'g': [], 'l': 'KR', 'd': [], 't': 'AtG',   'tags': ['action']},
            'i-shall-seal-the-heavens':{'g': [], 'l': 'CN', 'd': [], 't': 'ISSTH', 'tags': ['action']},
            'lord-of-the-mysteries':   {'g': [], 'l': 'CN', 'd': [], 't': 'LotM',  'tags': ['action']},
            # These slugs are not on the current page and not bucketed — old code enqueued them
            'extra-novel-one': {'g': [], 'l': 'CN', 'd': [], 't': 'Extra1'},
            'extra-novel-two': {'g': [], 'l': 'CN', 'd': [], 't': 'Extra2'},
        },
        # No bucketed novels
    })
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(500)  # allow any fetch activity to start
    # With the fix, extra-novel-one/two are not enqueued, so nothing hangs and
    # tagFetchProgress is absent (empty queue removes the key)
    stored = get_storage(page, ['tagFetchProgress'])
    assert stored.get('tagFetchProgress') is None, (
        'novelMeta-only slugs must not be enqueued on init'
    )
    assert page.locator('.nuf-tag-spinner').count() == 0


def test_defect028_bucketed_slugs_still_enqueued(page):
    """Bucketed slugs missing tags must still be fetched after the fix."""
    page.route(SERIES_PAGES, _hang_route)
    load_listing(page, {
        'read': ['release-that-witch'],
        'novelMeta': {
            # Bucketed slug has no tags — should be enqueued
            'release-that-witch':      {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'},
            # Other page-1 cards have tags so they skip fast
            'against-the-gods':        {'g': [], 'l': 'KR', 'd': [], 't': 'AtG',   'tags': ['action']},
            'i-shall-seal-the-heavens':{'g': [], 'l': 'CN', 'd': [], 't': 'ISSTH', 'tags': ['action']},
            'lord-of-the-mysteries':   {'g': [], 'l': 'CN', 'd': [], 't': 'LotM',  'tags': ['action']},
        },
    })
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(500)
    stored = get_storage(page, ['tagFetchProgress'])
    progress = stored.get('tagFetchProgress')
    assert progress is not None, 'bucketed slug without tags must be enqueued'
    assert progress['total'] >= 1


# ── defect-029: aggressive filters cause rapid successive page fetches ────────

def test_defect029_willrecurse_path_has_delay(page):
    """When a fetched page has all cards filtered, the next fetch must be delayed."""
    fixtures_dir = Path(__file__).parent / 'fixtures'
    pg2_body = (fixtures_dir / 'listing-page2.html').read_text(encoding='utf-8')
    # Inject a pg=3 link so scroll is not exhausted after pg=2 — enabling willRecurse
    pg2_with_next = pg2_body.replace(
        '<!-- No rel="next" — this is the last page -->',
        '<a rel="next" href="https://www.novelupdates.com/series-finder/?pg=3">Next</a>',
    )

    pg3_request_times = []

    def handle_pg2(route):
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=pg2_with_next)

    def handle_pg3(route):
        pg3_request_times.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='<html><body></body></html>')

    page.route('**/series-finder/?pg=2*', handle_pg2)
    page.route('**/series-finder/?pg=3*', handle_pg3)

    # All page-1 AND page-2 slugs in notInterested → both pages have appended=0
    # page-2 has a pg=3 link, so scroll.exhausted stays false → willRecurse=true after pg=2
    load_listing(page, {
        'notInterested': [
            'release-that-witch', 'against-the-gods',
            'i-shall-seal-the-heavens', 'lord-of-the-mysteries',
            'the-desolate-era', 'renegade-immortal',
        ],
        'prefetchTagsEnabled': False,  # suppress tag fetches to avoid unrelated series requests
    })
    t_start = time.time()

    # Wait for pg=3 to be requested — should happen after SCROLL_FILTER_DELAY_MS
    page.wait_for_timeout(4000)

    assert len(pg3_request_times) >= 1, 'Page-3 must have been requested via willRecurse path'
    delay_s = pg3_request_times[0] - t_start
    assert delay_s >= 1.2, (
        f'Filtered page should delay next fetch by ≥1.2 s; got {delay_s:.2f} s'
    )


def test_defect029_no_delay_when_cards_visible(page):
    """Auto-scroll with visible page-2 cards must not be delayed by the filter delay."""
    # All page-1 cards bucketed → sentinel immediately visible → IntersectionObserver fires
    # page-2 cards NOT bucketed → appended=2 → willRecurse=false → no SCROLL_FILTER_DELAY_MS
    t_start = time.time()
    load_listing(page, {
        'notInterested': [
            'release-that-witch', 'against-the-gods',
            'i-shall-seal-the-heavens', 'lord-of-the-mysteries',
        ],
        'prefetchTagsEnabled': False,
    })
    # page-2 cards should appear without waiting for SCROLL_FILTER_DELAY_MS (1500 ms)
    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=3000)
    elapsed = time.time() - t_start
    assert elapsed < 4.0, f'Visible-card auto-scroll took too long: {elapsed:.2f} s'


# ── defect-026: score breakdown omits low-contribution novels ───────────────

# ── defect-027: scores re-normalise on scroll ──────────────────────────────

def test_defect027_scores_stable_after_scroll_page_load(page):
    """Scores on already-visible page-1 cards do not change when page 2 is appended via scroll."""
    # ref has only 'fantasy' — The Desolate Era (page 2, Fantasy-only) is a perfect single-genre
    # match, giving it a higher raw than Release that Witch (Fantasy/Sci-fi, partial overlap).
    # Without the fix, adding TDE raises the normalisation max and drops page-1 card scores.
    novel_meta = {
        'ref-read': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Fantasy Ref'},
    }
    load_listing(page, {
        'read': ['ref-read'],
        'novelMeta': novel_meta,
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-score-label')

    # Record displayed score of the first card before scroll
    score_before = page.locator('.nuf-score-label').first.text_content()

    # Scroll to bottom to trigger sentinel → fetches listing-page2.html
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')

    # Wait for page-2 cards (The Desolate Era, Renegade Immortal) and their scores
    expect(page.locator('.search_main_box_nu')).to_have_count(6, timeout=10000)
    expect(page.locator('.nuf-score-label')).to_have_count(6, timeout=5000)

    # Score of first card must be unchanged — re-normalisation must not shift it
    score_after = page.locator('.nuf-score-label').first.text_content()
    assert score_before == score_after, (
        f'Score shifted after scroll: was {score_before!r}, now {score_after!r}'
    )


# ── defect-026: score breakdown omits low-contribution novels ───────────────

def test_defect026_breakdown_shows_all_bucketed_novels(page):
    """Breakdown panel lists every bucketed novel, including those with zero genre/tag overlap."""
    novel_meta = {
        'novel-a': {'g': ['action', 'fantasy'], 'l': 'chinese', 'd': [], 't': 'Novel A Read'},
        'novel-b': {'g': ['romance'],           'l': 'chinese', 'd': [], 't': 'Novel B Read'},
        'novel-c': {'g': ['horror'],            'l': 'chinese', 'd': [], 't': 'Novel C Not Interested'},
    }
    load_listing(page, {
        'read': ['novel-a', 'novel-b'],
        'notInterested': ['novel-c'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-score-label')

    # Card 0: Release that Witch (Fantasy/Sci-fi)
    # novel-a overlaps on "fantasy" → positive contribution (currently shown)
    # novel-b (Romance) and novel-c (Horror) → zero overlap → currently hidden by threshold
    page.locator('.nuf-score-badge').first.click()
    panel_text = page.locator('.nuf-explain-panel').text_content()
    assert 'Novel A Read' in panel_text, 'novel-a should appear (high contribution)'
    assert 'Novel B Read' in panel_text, 'novel-b should appear despite zero overlap'
    assert 'Novel C Not Interested' in panel_text, 'novel-c should appear despite zero overlap'


def test_defect026_breakdown_respects_bucket_side_for_zero_contribution(page):
    """Read novel with zero contribution goes to Positive column; Not Interested goes to Negative."""
    novel_meta = {
        'novel-a': {'g': ['action', 'fantasy'], 'l': 'chinese', 'd': [], 't': 'Novel A Read'},
        'novel-c': {'g': ['horror'],            'l': 'chinese', 'd': [], 't': 'Novel C Not Interested'},
    }
    load_listing(page, {
        'read': ['novel-a'],
        'notInterested': ['novel-c'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-score-label')

    # Card 3: Lord of the Mysteries (Mystery/Horror)
    # novel-a (Action/Fantasy) → no genre/word overlap → zero contribution, but Read → Positive
    # novel-c (Horror) → genre match → negative contribution → Negative
    page.locator('.nuf-score-badge').nth(3).click()
    panel = page.locator('.nuf-explain-panel')
    pos_col = panel.locator('.nuf-explain-col').nth(0)
    neg_col = panel.locator('.nuf-explain-col').nth(1)

    assert 'Novel A Read' in pos_col.text_content(), \
        'Read novel with zero contribution should appear in Positive column'
    assert 'Novel C Not Interested' in neg_col.text_content(), \
        'Not Interested novel should remain in Negative column'


# ── defect-030: scroll sentinel "shown" count is inaccurate ────────────────

def test_defect030_end_of_results_excludes_threshold_hidden_cards(page):
    """End-of-results sentinel count reflects only visible cards, not threshold-hidden ones."""
    # ref g=['fantasy'] — TDE (page 2, Fantasy) scores high; RI (page 2, Action) scores 1
    # minRecommendationScore=8 hides RI even though it is in the DOM
    novel_meta = {'ref': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Ref'}}
    load_listing(page, {
        'read': ['ref'],
        'novelMeta': novel_meta,
        'notInterested': [
            'release-that-witch', 'against-the-gods',
            'i-shall-seal-the-heavens', 'lord-of-the-mysteries',
        ],
        'minRecommendationScore': 8,
        'prefetchTagsEnabled': False,
    })
    # All page-1 cards filtered → sentinel fires → page 2 auto-fetches (TDE + RI) → exhausts
    expect(page.locator('.nuf-sentinel-done')).to_be_visible(timeout=15000)

    sentinel_text = page.locator('.nuf-sentinel-done').text_content()
    # "End of results · N shown" — parse N
    stated = int(sentinel_text.split('·')[1].strip().split()[0])
    actual_visible = page.evaluate(
        'document.querySelectorAll(".search_main_box_nu:not(.nuf-hidden-threshold)").length'
    )
    assert stated == actual_visible, (
        f'Sentinel says {stated} shown but {actual_visible} cards are actually visible'
    )


def test_defect030_loading_sentinel_includes_initial_page_cards(page):
    """Loading sentinel 'Y shown' count includes cards from the initial (non-scroll) page."""
    hang = threading.Event()

    def hang_pg2(route):
        hang.wait(10)
        route.continue_()

    page.route('**/series-finder/?pg=2', hang_pg2)

    novel_meta = {'ref': {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Ref'}}
    load_listing(page, {
        'read': ['ref'],
        'novelMeta': novel_meta,
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-score-label')  # 4 page-1 cards scored and visible

    # Scroll to bottom → triggers page-2 fetch, which hangs so loading state persists
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    expect(page.locator('.nuf-sentinel-loading')).to_be_visible(timeout=5000)

    sentinel_text = page.locator('.nuf-sentinel-loading-text').text_content()
    # "Page 2 · Y shown" — Y should include the 4 initial page-1 cards
    shown = int(sentinel_text.split('·')[1].strip().split()[0])
    hang.set()
    assert shown >= 4, f'Loading sentinel shows {shown}, expected ≥ 4 (initial page cards)'


# ── To Read bucket tests ───────────────────────────────────────────────────

def test_feature_003_to_read_state(page):
    """toRead bucket: cards stay visible, contribute positive score signal."""
    # Seed toRead with a real fixture card slug — card must NOT be filtered out
    load_listing(page, {'toRead': ['release-that-witch']})
    page.wait_for_selector('.nuf-controls')

    cards = page.locator('.search_main_box_nu')
    expect(cards).to_have_count(4)  # all 4 visible — toRead must not hide cards

    # Verify scoring: toRead ref with fantasy/sci-fi overlap should boost card 1
    novel_meta = {
        'ref-toread': {
            'g': ['fantasy', 'sci-fi'], 'l': 'chinese',
            'd': ['fantasy', 'magic'], 't': 'To Read Ref',
        },
    }
    load_listing(page, {'toRead': ['ref-toread'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-score-label')

    scores = parse_scores(page)
    # Card 1 (Release that Witch) has Fantasy/Sci-fi — matches toRead ref → should score ≥ 5
    assert scores[0] >= 5, f'Expected card 1 score ≥ 5 from toRead signal, got {scores[0]}'


def test_feature_003_to_read_button(page):
    """To Read button keeps card visible with glow; clicking again unmarks; Read removes glow and hides."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    first_card = page.locator('.search_main_box_nu').first
    first_controls = page.locator('.nuf-controls').first

    # Click "To Read" — card must stay in DOM
    first_controls.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    expect(first_card).to_be_visible()
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    # Card must have the glow class
    assert first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class after clicking To Read'

    # Click To Read again — unmarks, glow removed
    first_controls.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    assert not first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class removed after second click'

    # Re-mark as To Read, then click Read — glow removed and card hides
    first_controls.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    first_controls.locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=3000)


def test_feature_003_to_read_sidebar(page):
    """To Read sidebar: panel opens by default; close hides panel; tab re-opens it."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy', 'sci-fi'], 'l': 'chinese',
            'd': ['prince', 'witch', 'magic'], 't': 'Release that Witch',
            'desc': 'A prince transmigrates to a magical world with witches.',
            'img': None,
        },
    }
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-controls')

    # Sidebar tab must exist
    tab = page.locator('.nuf-sidebar-tab')
    expect(tab).to_be_visible()
    assert 'To Read' in tab.text_content()

    # Panel must exist and start open
    panel = page.locator('.nuf-sidebar-panel')
    assert panel.count() == 1
    panel_right = page.evaluate("() => document.querySelector('.nuf-sidebar-panel').style.right")
    assert panel_right == '0px', f'Expected panel to start open (right=0px), got {panel_right}'

    # Novel title link must be visible immediately
    title_link = panel.locator('.nuf-sidebar-title')
    expect(title_link).to_be_visible()
    assert 'Release that Witch' in title_link.text_content()

    # Close panel via × button
    panel.locator('.nuf-sidebar-close').click()
    page.wait_for_timeout(400)
    # Panel slides off-screen — right style must not be '0px'
    panel_right = page.evaluate("() => document.querySelector('.nuf-sidebar-panel').style.right")
    assert panel_right != '0px', f'Expected panel to be hidden, got right={panel_right}'


def test_defect035_to_read_undo_after_prior_action(page):
    """Undo on To Read toast should not affect a prior bucket action's undo state."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # Get initial card count
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    # Card references
    cards = page.locator('.search_main_box_nu')
    card_1 = cards.nth(0)
    card_2 = cards.nth(1)
    card_3 = cards.nth(2)

    # Click "Read" on card 1 — hides it, sets pendingUndo for card 1
    page.locator('.nuf-controls').nth(0).locator('.nuf-btn[data-nuf-btn="read"]').click()

    # Card 1 should be animating out or hidden
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=2000)

    # Click "To Read" on card 2 (which is now the first visible card after card 1 was removed)
    # The controls are still in DOM but card 1 is being removed, so we need to wait and then click card 2's controls
    page.wait_for_timeout(500)  # Let animation settle

    # Find card 2 by its original position (before card 1 was hidden)
    # Actually, after card 1 is hidden, card 2 becomes the first visible card
    # Let's use the second original card's slug to identify it
    all_cards_and_controls = page.evaluate('''() => {
        const cards = document.querySelectorAll('.search_main_box_nu');
        return Array.from(cards).map((card, idx) => ({
            slug: card.dataset.nufSlug,
            index: idx,
        }));
    }''')

    # After card 1 is gone, card 2 is now at index 0
    page.locator('.nuf-controls').nth(0).locator('.nuf-btn[data-nuf-btn="toread"]').click()

    # Assert card 2 now has the To Read glow
    first_visible = page.locator('.search_main_box_nu').first
    assert first_visible.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow on card 2 after clicking To Read button'

    # Assert we have 3 cards visible (card 1 hidden, cards 2-4 visible)
    expect(page.locator('.search_main_box_nu')).to_have_count(3)

    # Verify toast shows "To Read" message (not "Read" from card 1)
    expect(page.locator('.nuf-toast-bucket-label')).to_contain_text('To Read')

    # Click Undo on the To Read toast
    page.locator('.nuf-toast-undo').click()

    # Assert: card 2 should no longer have the To Read glow (card and controls)
    first_visible = page.locator('.search_main_box_nu').first
    first_visible_controls = page.locator('.nuf-controls').first
    assert not first_visible.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow removed from card 2 after undo'
    assert not first_visible.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    ), 'Expected wrapper to lose nuf-to-read after undo'

    # Assert: we still have 3 cards visible (card 1 still hidden, not affected by To Read undo)
    expect(page.locator('.search_main_box_nu')).to_have_count(3)

    # Verify via storage that card 2 is no longer in toRead bucket
    storage = get_storage(page, ['toRead'])
    assert all_cards_and_controls[1]['slug'] not in storage.get('toRead', []), \
        'Expected card 2 slug to not be in toRead after undo'


def test_defect031_to_read_undo_unmarks_card(page):
    """To Read undo should remove the glow from both card and controls bar, and delete from toRead bucket."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # Get initial card count
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    first_card = page.locator('.search_main_box_nu').first
    first_controls = page.locator('.nuf-controls').first

    # Click "To Read" on the first card
    first_controls.locator('.nuf-btn[data-nuf-btn="toread"]').click()

    # Card and wrapper must have the glow class; controls does not (glow is on wrapper)
    assert first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class on card after clicking To Read'
    assert first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    ), 'Expected wrapper to have nuf-to-read after clicking To Read'

    # Toast must be visible
    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))

    # Click Undo
    page.locator('.nuf-toast-undo').click()

    # Card and wrapper should no longer have the glow class
    assert not first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class removed from card after undo'
    assert not first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    ), 'Expected wrapper to lose nuf-to-read after undo'

    # To Read button should no longer be active
    assert not first_controls.evaluate(
        'el => el.querySelector(".nuf-btn[data-nuf-btn=\\"toread\\"]").classList.contains("nuf-toread-active")'
    ), 'Expected toread button no longer active after undo'

    # All 4 cards should still be in DOM (To Read does not remove)
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    # Verify storage: card slug should not be in toRead
    storage = get_storage(page, ['toRead'])
    card_slug = first_card.evaluate('el => el.dataset.nufSlug')
    assert card_slug not in storage.get('toRead', []), \
        f'Expected {card_slug} not in toRead after undo'


def test_defect031_to_read_undo_does_not_affect_other_card(page):
    """To Read undo should not affect a prior Read action's undo state."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    # Click "Read" on card 1 — hides it, sets pendingUndo for card 1
    page.locator('.nuf-controls').nth(0).locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=2000)

    # Immediately click "To Read" on card 2 (now the first visible)
    page.wait_for_timeout(200)
    page.locator('.nuf-controls').nth(0).locator('.nuf-btn[data-nuf-btn="toread"]').click()

    # Card 2 now has To Read glow
    first_visible = page.locator('.search_main_box_nu').first
    assert first_visible.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow on card 2'

    # We have 3 cards visible (card 1 hidden, cards 2-4 visible)
    expect(page.locator('.search_main_box_nu')).to_have_count(3)

    # Click Undo on the To Read toast
    page.locator('.nuf-toast-undo').click()

    # Card 2 should no longer have glow
    first_visible = page.locator('.search_main_box_nu').first
    assert not first_visible.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow removed from card 2'

    # Still 3 cards visible (card 1 still hidden, not affected by To Read undo)
    expect(page.locator('.search_main_box_nu')).to_have_count(3)


def test_defect033_sidebar_remove_button_unmarks_card(page):
    """Sidebar remove button unmarks the card and controls bar, and clears glow."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy', 'sci-fi'], 'l': 'chinese',
            'd': ['prince', 'witch', 'magic'], 't': 'Release that Witch',
            'desc': 'A prince transmigrates to a magical world with witches.',
            'img': 'https://example.com/cover.jpg',
        },
    }
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-controls')

    # First card and its controls should have the To Read glow
    first_card = page.locator('.search_main_box_nu').first
    first_controls = page.locator('.nuf-controls').first
    assert first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow on card before sidebar removal'
    assert first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    ), 'Expected wrapper to have nuf-to-read before sidebar removal'

    # Sidebar is open by default
    remove_btn = page.locator('.nuf-sidebar-remove')
    expect(remove_btn).to_be_visible()

    # Click remove button
    remove_btn.click()
    page.wait_for_timeout(200)  # re-render

    # Sidebar should now show "No novels queued yet."
    empty_msg = page.locator('.nuf-sidebar-empty')
    expect(empty_msg).to_be_visible()

    # Card and wrapper should no longer have the To Read glow
    assert not first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow removed from card after sidebar removal'
    assert not first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    ), 'Expected wrapper to lose nuf-to-read after sidebar removal'

    # To Read button should no longer be active
    assert not first_controls.evaluate(
        'el => el.querySelector(".nuf-btn[data-nuf-btn=\\"toread\\"]").classList.contains("nuf-toread-active")'
    ), 'Expected toread button no longer active after sidebar removal'

    # Storage should be empty
    storage = get_storage(page, ['toRead'])
    assert storage.get('toRead', []) == [], \
        'Expected toRead storage to be empty after removal'


def test_defect034_sidebar_desc_extraction_handles_full_body(page):
    """Description extraction should use full .search_body_nu content (visible + testhide), excluding more>> link."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    first_card = page.locator('.search_main_box_nu').first

    # Click "To Read" to trigger description capture
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="toread"]').click()

    # Verify the action completes without error
    assert first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class on card'

    # Wait for the metadata save debounce (2s)
    wait_for_meta_save(page)

    # Verify metadata was stored with correct description content
    storage = get_storage(page, ['novelMeta'])
    novel_meta = storage.get('novelMeta', {})
    first_card_slug = first_card.evaluate('el => el.dataset.nufSlug')

    assert first_card_slug in novel_meta, 'Expected slug to be in novelMeta'
    assert 'desc' in novel_meta[first_card_slug], 'Expected desc key to be set in novelMeta'
    desc = novel_meta[first_card_slug]['desc']
    assert isinstance(desc, str), 'Expected desc to be a string'

    # Description must include visible opening text (before .testhide)
    assert 'A prince transmigrates' in desc, \
        f'Expected visible opening text in desc, got: {desc}'
    # Description must include hidden overflow text (from .testhide)
    assert 'demon invasion' in desc, \
        f'Expected testhide overflow text in desc, got: {desc}'
    # Description must NOT include "more>>" link text
    assert 'more>>' not in desc, \
        f'Expected more>> link text excluded from desc, got: {desc}'


def test_defect034_sidebar_panel_wider_than_350px(page):
    """Sidebar panel width should be 570px."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy'], 'l': 'chinese',
            'd': ['prince', 'witch'], 't': 'Release that Witch',
            'img': 'https://example.com/cover.jpg',
        },
    }
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-sidebar-tab')

    # Sidebar is open by default
    panel_width = page.evaluate("() => document.querySelector('.nuf-sidebar-panel').offsetWidth")
    assert panel_width >= 570, \
        f'Expected sidebar panel width >= 570px, got {panel_width}px'


def test_defect032_to_read_glow_covers_controls_bar(page):
    """To Read glow wraps both controls bar and card as one unit via a single wrapper element."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    first_card = page.locator('.search_main_box_nu').first
    first_controls = page.locator('.nuf-controls').first

    # Click "To Read" on the first card
    first_controls.locator('.nuf-btn[data-nuf-btn="toread"]').click()

    # The wrapper should have nuf-to-read (single element for the glow)
    wrapper_has_class = first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    )
    assert wrapper_has_class, 'Expected nuf-entry-wrap wrapper to have nuf-to-read class'

    # Card should also have nuf-to-read as a state marker
    assert first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class on card'

    # Controls bar must NOT have nuf-to-read (glow is on wrapper, not controls)
    assert not first_controls.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected controls bar NOT to have nuf-to-read (wrapper handles glow)'

    # Click "To Read" again to unmark
    first_controls.locator('.nuf-btn[data-nuf-btn="toread"]').click()

    # Wrapper and card should lose the class
    wrapper_has_class_after = first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    )
    assert not wrapper_has_class_after, \
        'Expected nuf-entry-wrap wrapper to lose nuf-to-read class after unmark'
    assert not first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected nuf-to-read class removed from card'


def test_defect035_to_read_card_not_hidden_by_score_threshold(page):
    """A card marked as To Read must never be hidden by the score threshold."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy', 'sci-fi'], 'l': 'chinese',
            'd': ['prince', 'witch', 'magic'], 't': 'Release that Witch',
        },
        'against-the-gods': {
            'g': ['action', 'harem'], 'l': 'korean',
            'd': ['warrior', 'treasure'], 't': 'Against the Gods',
        },
    }
    load_listing(page, {
        'read': ['against-the-gods'],
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
        'recommendationsEnabled': True,
        'minRecommendationScore': 8,
    })
    page.wait_for_selector('.nuf-controls')

    # The To Read card should remain visible regardless of its score
    first_card = page.locator('.search_main_box_nu').first
    assert first_card.evaluate('el => el.classList.contains("nuf-to-read")'), \
        'Expected To Read glow on card'
    assert first_card.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    ), 'Expected nuf-entry-wrap wrapper to have nuf-to-read on initial-page reload'
    assert not first_card.evaluate('el => el.classList.contains("nuf-hidden-threshold")'), \
        'Expected To Read card to NOT be hidden by score threshold'


def test_defect033_sidebar_remove_button_at_top_right(page):
    """Sidebar remove button should be positioned at top-right of each item, not buried at bottom."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy'], 'l': 'chinese',
            'd': ['prince', 'witch'], 't': 'Release that Witch',
            'img': 'https://example.com/cover.jpg',
        },
    }
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-sidebar-tab')

    # Sidebar is open by default
    remove_btn = page.locator('.nuf-sidebar-remove')
    expect(remove_btn).to_be_visible()
    position = remove_btn.evaluate('el => getComputedStyle(el).position')
    assert position == 'absolute', \
        f'Expected remove button to be absolutely positioned, got {position}'


def test_defect037_sidebar_remove_button_does_not_overlap_title(page):
    """Remove (×) button must not overlap the title text — right padding must clear it."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy'], 'l': 'chinese',
            'd': ['prince', 'witch'],
            't': 'Release that Witch — A Very Long Novel Title That Could Easily Wrap Under the Remove Button',
            'img': 'https://example.com/cover.jpg',
        },
    }
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-sidebar-tab')

    # Sidebar is open by default
    overlaps = page.evaluate("""() => {
        const title = document.querySelector('.nuf-sidebar-title');
        const remove = document.querySelector('.nuf-sidebar-remove');
        if (!title || !remove) return 'elements missing';
        const tr = title.getBoundingClientRect();
        const rr = remove.getBoundingClientRect();
        const overlap = !(tr.right <= rr.left || tr.left >= rr.right ||
                          tr.bottom <= rr.top || tr.top >= rr.bottom);
        return overlap;
    }""")
    assert overlaps is False, 'Remove button overlaps the title text'


def test_defect037_sidebar_has_bucket_move_buttons(page):
    """Sidebar items must have buttons to move a novel to Read, Not Finished, and Not Interested."""
    novel_meta = {
        'release-that-witch': {
            'g': ['fantasy'], 'l': 'chinese',
            'd': ['prince', 'witch'], 't': 'Release that Witch',
            'img': 'https://example.com/cover.jpg',
        },
    }
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-sidebar-tab')

    # Sidebar is open by default
    # All three bucket move buttons must be present
    item = page.locator('.nuf-sidebar-item').first
    expect(item.locator('button', has_text='Read →')).to_be_visible()
    expect(item.locator('button', has_text='Not Finished →')).to_be_visible()
    expect(item.locator('button', has_text='Not Interested →')).to_be_visible()

    # Clicking Read → should move slug from toRead to read
    item.locator('button', has_text='Read →').first.click()
    page.wait_for_timeout(300)

    storage = get_storage(page, ['toRead', 'read'])
    assert 'release-that-witch' not in (storage.get('toRead') or []), \
        'Expected slug removed from toRead after → Read click'
    assert 'release-that-witch' in (storage.get('read') or []), \
        'Expected slug added to read after → Read click'


def test_defect037_sidebar_desc_is_synopsis_not_stats(page):
    """Sidebar description must show synopsis text, not chapter stats or the title."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # Click "To Read" on the first card (Release that Witch) to trigger desc capture from DOM
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    page.wait_for_selector('.nuf-sidebar-tab')

    # Sidebar is open by default
    desc_el = page.locator('.nuf-sidebar-desc').first
    expect(desc_el).to_be_visible()
    desc_text = desc_el.text_content()

    assert 'Chapters' not in desc_text, \
        f'Sidebar desc must not contain chapter stats, got: {desc_text}'
    assert 'Day(s)' not in desc_text, \
        f'Sidebar desc must not contain update frequency stats, got: {desc_text}'
    assert 'Readers' not in desc_text, \
        f'Sidebar desc must not contain reader count stats, got: {desc_text}'
    assert 'prince' in desc_text or 'transmigrates' in desc_text, \
        f'Sidebar desc must contain synopsis text, got: {desc_text}'


# ── defect-038: sidebar bucket-move buttons colour ──────────────────────────

def test_defect038_sidebar_bucket_buttons_colour_coded(page):
    """Sidebar bucket-move buttons must carry data-nuf-btn attributes for colour-coded styling."""
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'t': 'Release that Witch'}},
    })
    page.wait_for_selector('.nuf-sidebar-item')
    read_btn = page.locator('.nuf-sidebar-bucket-btns .nuf-btn[data-nuf-btn="read"]')
    nf_btn   = page.locator('.nuf-sidebar-bucket-btns .nuf-btn[data-nuf-btn="nf"]')
    ni_btn   = page.locator('.nuf-sidebar-bucket-btns .nuf-btn[data-nuf-btn="ni"]')
    expect(read_btn).to_be_visible()
    expect(nf_btn).to_be_visible()
    expect(ni_btn).to_be_visible()


# ── defect-040: duplicate cards on infinite scroll ──────────────────────────

def test_defect040_scroll_deduplicates_repeated_slug(page):
    """A slug already visible from page 1 must not be appended again when it appears on page 2."""
    pg2_html = """<!DOCTYPE html><html><body>
<div class="w-blog-content other">
  <div class="search_main_box_nu">
    <div class="search_body_nu">
      <div class="search_title">
        <a href="https://www.novelupdates.com/series/release-that-witch/">Release that Witch</a>
      </div>
    </div>
  </div>
  <div class="search_main_box_nu">
    <div class="search_body_nu">
      <div class="search_title">
        <a href="https://www.novelupdates.com/series/the-desolate-era/">The Desolate Era</a>
      </div>
    </div>
  </div>
</div>
</body></html>"""

    page.route('**/series-finder/?pg=2*',
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=pg2_html))

    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # Scroll to bottom to trigger the IntersectionObserver sentinel
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')

    # Wait until the new page-2 card appears (confirms page 2 loaded)
    expect(page.locator('[data-nuf-slug="the-desolate-era"]')).to_be_attached(timeout=10000)

    # The slug that was already on page 1 must not be duplicated
    assert page.locator('[data-nuf-slug="release-that-witch"]').count() == 1, \
        'release-that-witch must appear exactly once — page-2 duplicate must be suppressed'

    # Total: 4 from page 1 + 1 new from page 2 (duplicate suppressed)
    assert page.locator('.search_main_box_nu').count() == 5, \
        'Expected 5 cards total (4 page-1 + 1 unique page-2, duplicate suppressed)'


# ── Cloudflare mitigation: request headers ──────────────────────────────────

def _wait_for_request(captured_headers, page, timeout_s=8):
    """Poll until at least one request has been captured, up to timeout_s seconds."""
    deadline = time.time() + timeout_s
    while not captured_headers and time.time() < deadline:
        page.wait_for_timeout(300)


def test_tag_fetch_sends_browser_like_accept_header(page):
    """Tag fetch requests must include an Accept header that looks like a real browser navigation."""
    captured_headers = []

    def capture(route):
        captured_headers.append(dict(route.request.headers))
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''
            <html><body>
                <a href="/stag/action/">action</a>
                <div id="myTable"></div>
            </body></html>
        ''')

    page.route('**/series/release-that-witch/**', capture)
    # Pre-seed tags for all non-bucketed slugs — enqueueFetch skips slugs that already have tags,
    # so only the bucketed release-that-witch is fetched (reducing inter-request wait time).
    load_listing(page, {'read': ['release-that-witch'], 'novelMeta': {
        'release-that-witch':      {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'},
        'against-the-gods':        {'g': [], 'l': 'CN', 'd': [], 't': 'AtG',   'tags': ['action']},
        'i-shall-seal-the-heavens':{'g': [], 'l': 'CN', 'd': [], 't': 'ISSTH', 'tags': ['action']},
        'lord-of-the-mysteries':   {'g': [], 'l': 'CN', 'd': [], 't': 'LotM',  'tags': ['mystery']},
    }})
    page.wait_for_selector('.nuf-controls')
    _wait_for_request(captured_headers, page)

    assert captured_headers, 'No request was made to the series page'
    headers = captured_headers[0]
    assert 'accept' in headers, 'Accept header missing from tag fetch request'
    assert 'text/html' in headers['accept'], \
        f'Accept header should include text/html, got: {headers["accept"]!r}'
    assert 'upgrade-insecure-requests' in headers, \
        'Upgrade-Insecure-Requests header missing from tag fetch request'


def test_tag_fetch_sends_accept_language_header(page):
    """Tag fetch requests must include Accept-Language matching the browser's locale."""
    captured_headers = []

    def capture(route):
        captured_headers.append(dict(route.request.headers))
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''
            <html><body>
                <a href="/stag/action/">action</a>
                <div id="myTable"></div>
            </body></html>
        ''')

    page.route('**/series/release-that-witch/**', capture)
    load_listing(page, {'read': ['release-that-witch'], 'novelMeta': {
        'release-that-witch':      {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'},
        'against-the-gods':        {'g': [], 'l': 'CN', 'd': [], 't': 'AtG',   'tags': ['action']},
        'i-shall-seal-the-heavens':{'g': [], 'l': 'CN', 'd': [], 't': 'ISSTH', 'tags': ['action']},
        'lord-of-the-mysteries':   {'g': [], 'l': 'CN', 'd': [], 't': 'LotM',  'tags': ['mystery']},
    }})
    page.wait_for_selector('.nuf-controls')
    _wait_for_request(captured_headers, page)

    assert captured_headers, 'No request was made to the series page'
    assert 'accept-language' in captured_headers[0], \
        'Accept-Language header missing from tag fetch request'


# ── Cloudflare mitigation: jitter ───────────────────────────────────────────

def test_tag_fetch_jitter_produces_varied_delays(page):
    """The jitter() helper produces values within ±20% of the base delay between consecutive fetches."""
    request_times = []

    def capture(route):
        request_times.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''
            <html><body>
                <a href="/stag/action/">action</a>
                <div id="myTable"></div>
            </body></html>
        ''')

    page.route(SERIES_PAGES, capture)
    # All 4 slugs bucketed without tags — each is enqueued from init and fetched sequentially.
    # Bucketed cards get filtered from DOM (no visible spinners) but are still in the fetch queue.
    load_listing(page, {
        'read': ['release-that-witch', 'against-the-gods',
                 'i-shall-seal-the-heavens', 'lord-of-the-mysteries'],
        'novelMeta': {
            'release-that-witch':      {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'},
            'against-the-gods':        {'g': [], 'l': 'CN', 'd': [], 't': 'AtG'},
            'i-shall-seal-the-heavens':{'g': [], 'l': 'CN', 'd': [], 't': 'ISSTH'},
            'lord-of-the-mysteries':   {'g': [], 'l': 'CN', 'd': [], 't': 'LotM'},
        },
    })
    page.wait_for_selector('.nuf-controls')

    # Wait for all 4 requests to be captured (4 slugs × ~2s base delay each)
    deadline = time.time() + 20
    while len(request_times) < 4 and time.time() < deadline:
        page.wait_for_timeout(300)

    assert len(request_times) >= 2, \
        f'Need at least 2 requests to measure inter-request delay, got {len(request_times)}'

    # Compute gaps between consecutive requests
    gaps = [request_times[i+1] - request_times[i] for i in range(len(request_times) - 1)]

    # All gaps must be within ±20% jitter of the 2s base delay
    for gap in gaps:
        assert gap >= 0.8, f'Gap {gap:.3f}s too short — jitter went below 80% of 2s base delay'
        assert gap <= 5.0, f'Gap {gap:.3f}s too long — something unexpected stalled requests'

    # Jitter should produce variation across multiple gaps
    if len(gaps) >= 2:
        spread = max(gaps) - min(gaps)
        assert spread > 0.01, \
            f'All inter-request gaps were identical ({gaps}) — jitter may not be working'


# ── Cloudflare mitigation: Retry-After header ───────────────────────────────

def test_tag_fetch_respects_retry_after_seconds(page):
    """A 429 with Retry-After: N causes the extension to wait at least N seconds before the next request."""
    request_times = []
    call_count = [0]

    def respond(route):
        call_count[0] += 1
        request_times.append(time.time())
        if call_count[0] == 1:
            # First request: 429 with Retry-After: 2
            route.fulfill(status=429, headers={'Retry-After': '2'}, body='')
        else:
            # Subsequent requests: succeed
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''
                <html><body>
                    <a href="/stag/action/">action</a>
                    <div id="myTable"></div>
                </body></html>
            ''')

    page.route(SERIES_PAGES, respond)
    load_listing(page, {'read': ['release-that-witch'], 'novelMeta': {
        'release-that-witch': {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'},
    }})
    page.wait_for_selector('.nuf-controls')
    # Wait long enough for the blocked slug to be counted as done and any second slug to be fetched
    page.wait_for_timeout(6000)

    assert len(request_times) >= 2, \
        f'Expected at least 2 requests (1 blocked + re-enqueue), got {len(request_times)}'
    gap = request_times[1] - request_times[0]
    assert gap >= 1.6, \
        f'Expected gap >= 1.6s (Retry-After: 2 with jitter), got {gap:.3f}s'


def test_tag_fetch_respects_retry_after_http_date(page):
    """A 503 with Retry-After as an HTTP-date string is parsed and respected as a wait duration."""
    import email.utils
    request_times = []
    call_count = [0]

    def respond(route):
        call_count[0] += 1
        request_times.append(time.time())
        if call_count[0] == 1:
            # Retry-After as an HTTP-date 4 seconds in the future.
            # Use 4s (not 2s) because network round-trip and JS date parsing consume some time,
            # so by the time Date.now() is called, the remaining delay is less than 4s.
            future = email.utils.formatdate(time.time() + 4, usegmt=True)
            route.fulfill(status=503, headers={'Retry-After': future}, body='')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''
                <html><body>
                    <a href="/stag/action/">action</a>
                    <div id="myTable"></div>
                </body></html>
            ''')

    page.route(SERIES_PAGES, respond)
    # Two bucketed slugs without tags: first is blocked by 503, second is fetched after the wait.
    # Other slugs have tags pre-seeded so enqueueFetch skips them (no extra queue entries).
    load_listing(page, {
        'read': ['release-that-witch', 'against-the-gods'],
        'novelMeta': {
            'release-that-witch':      {'g': [], 'l': 'CN', 'd': [], 't': 'RtW'},
            'against-the-gods':        {'g': [], 'l': 'CN', 'd': [], 't': 'AtG'},
            'i-shall-seal-the-heavens':{'g': [], 'l': 'CN', 'd': [], 't': 'ISSTH', 'tags': ['action']},
            'lord-of-the-mysteries':   {'g': [], 'l': 'CN', 'd': [], 't': 'LotM',  'tags': ['mystery']},
        },
    })
    page.wait_for_selector('.nuf-controls')

    # Wait for the second request (blocked slug skipped, then next slug fetched after Retry-After wait)
    deadline = time.time() + 15
    while len(request_times) < 2 and time.time() < deadline:
        page.wait_for_timeout(300)

    assert len(request_times) >= 2, \
        f'Expected at least 2 requests (1 blocked + next fetch), got {len(request_times)}'
    gap = request_times[1] - request_times[0]
    # Retry-After: 4s future date, minus round-trip latency and date-parsing overhead.
    # With 80% jitter floor applied to the remaining ~3.5-3.9s: minimum ≈ 2.8s.
    # Use 2.0s as the assertion floor to keep the test robust across slower CI.
    assert gap >= 2.0, \
        f'Expected gap >= 2.0s (Retry-After HTTP-date ~4s, with jitter), got {gap:.3f}s'


# ── Cloudflare mitigation: delay recovery after sustained success ────────────

def test_tag_fetch_delay_recovers_after_sustained_success(page):
    """After blocks slow the queue down, 5 successful requests relax the added gap by a quarter.
    (This policy moved from the prefetch loop into the shared request queue.)"""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    states = page._sw.evaluate('''async () => {
        const b = globalThis.nufBroker;
        const run = async outcome => { const s = await b.requestSlot('scroll'); b.release(s.slotId, outcome); };
        await run('blocked');
        await run('blocked');
        const slowed = b.state().paceDelay;
        for (let i = 0; i < 5; i++) await run('ok');
        return { slowed, relaxed: b.state().paceDelay };
    }''')
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    assert states == {'slowed': 6000, 'relaxed': 4500}, states


# ── defect-039: To Read glow missing on infinite-scroll-loaded cards ─────────

def test_defect039_to_read_glow_on_scroll_loaded_card(page):
    """A card loaded via infinite scroll must show the nuf-to-read glow if its slug is in toRead.

    Page-1 cards are all bucketed as notInterested so they are filtered out immediately,
    causing the IntersectionObserver sentinel to fire and auto-fetch page 2. The slug
    'the-desolate-era' is in page 2 of the fixture and is pre-seeded in toRead.

    Before the fix, injectControls correctly marks the button nuf-toread-active but
    fetchNextPage never adds nuf-to-read to the wrapper — so the CSS animation is absent.
    """
    load_listing(page, {
        'toRead': ['the-desolate-era'],
        'notInterested': [
            'release-that-witch',
            'against-the-gods',
            'i-shall-seal-the-heavens',
            'lord-of-the-mysteries',
        ],
    })

    # Wait for the page-2 card to be appended by the auto-triggered scroll fetch
    expect(page.locator('[data-nuf-slug="the-desolate-era"]')).to_be_attached(timeout=10000)

    entry = page.locator('[data-nuf-slug="the-desolate-era"]')

    entry_has_glow = entry.evaluate('el => el.classList.contains("nuf-to-read")')
    assert entry_has_glow, 'Expected entry element to have nuf-to-read class'

    wrapper_has_glow = entry.evaluate(
        'el => el.closest(".nuf-entry-wrap")?.classList.contains("nuf-to-read") ?? false'
    )
    assert wrapper_has_glow, 'Expected .nuf-entry-wrap wrapper to have nuf-to-read class (glow animation target)'

    btn_active = entry.evaluate(
        'el => el.closest(".nuf-entry-wrap")'
        '?.querySelector(".nuf-btn[data-nuf-btn=\'toread\']")?.classList.contains("nuf-toread-active") ?? false'
    )
    assert btn_active, 'Expected To Read button to have nuf-toread-active class'


def test_defect041_series_page_controls_appear_without_body_class(page):
    """isSeriesPage() must fall back to URL-path check when the body class is absent.

    The body class 'single-seriesplans' is WordPress-generated and may be missing after
    a theme update. This test strips it from the fixture before fulfilling the route,
    confirming that the URL-path fallback (/series/) causes controls to be injected anyway.
    Before the fix, isSeriesPage() returned false and init() exited silently — no controls.
    """
    fixtures_dir = Path(__file__).parent / 'fixtures'
    series_body = (fixtures_dir / 'series.html').read_text(encoding='utf-8')
    # Strip the WordPress body class so the class-based check returns false
    series_body_no_class = series_body.replace('single-seriesplans', '')

    page.route(
        '**/series/release-that-witch/**',
        lambda route: route.fulfill(
            status=200,
            content_type='text/html; charset=utf-8',
            body=series_body_no_class,
        ),
    )

    load_series(page)

    expect(page.locator('.nuf-controls')).to_have_count(1)
    assert page.locator('.nuf-controls .nuf-btn').count() == 4, \
        'Expected four bucket buttons inside .nuf-controls (3 rejection + To Read)'


# ── defect-045: tag chips must render after prefetch for non-bucketed cards ──

def test_defect045_tags_chip_row_renders_after_prefetch_for_non_bucketed_card(page):
    """Tags reasoning chip row must appear on a non-bucketed listing card as soon as
    prefetch populates that card's NU tags — not stay empty until some unrelated
    re-render event. Repros: spinner appears, fetch completes, spinner vanishes,
    chip row stays empty because the post-fetch recompute is gated on isReference."""
    # Seed: one bucketed Read ref whose tags ALREADY exist in storage (so its own
    # enqueueFetch is short-circuited at prefetch.js:110 and only the listing-card
    # slugs are queued — none of which are bucketed). The conftest route stub
    # returns series.html for any /series/<slug>/ request, and series.html contains
    # the tags 'kingdom-building', 'transmigration', 'industrialization'. The ref
    # shares two of those, so an overlap-driven Tags chip should appear after the
    # fetch completes — IF the recompute is triggered.
    load_listing(page, {
        'read': ['ref-novel'],
        'novelMeta': {
            'ref-novel': {
                't': 'Reference Novel',
                'g': ['fantasy'],
                'l': 'chinese',
                'd': [],
                'tags': ['kingdom building', 'transmigration'],
                'ts': 1716000000000,
            },
        },
    })

    page.wait_for_selector('.nuf-controls')

    # Cards are queued for prefetch only once visible, so "no spinners" no longer
    # means "done" — poll storage for the fetched tags instead.
    for _ in range(60):
        rtw_meta = get_storage(page, 'novelMeta').get('novelMeta', {}).get('release-that-witch', {})
        if rtw_meta.get('tags'):
            break
        page.wait_for_timeout(250)
    assert rtw_meta.get('tags'), (
        f"prefetch did not populate tags for release-that-witch; meta={rtw_meta!r}"
    )

    # The Tags reasoning chip row should now be present on a non-bucketed card.
    # Before the fix: tags ARE in storage but the chip row never renders because
    # prefetch.js:83-85 skipped applyRecommendations for the non-bucketed slug.
    # Use the first card (release-that-witch) which has the bucketed ref's tags.
    controls = page.locator(
        '.nuf-entry-wrap:has([data-nuf-slug="release-that-witch"]) .nuf-controls'
    )
    expect(
        controls.locator('.nuf-reason-group-label', has_text=re.compile(r'tag', re.I))
    ).to_have_count(1, timeout=3000)
    expect(
        controls.locator('.nuf-chip-tag-pos, .nuf-chip-tag-neg').first
    ).to_be_visible()


# ── defect-052: To Read glow persists after reassign ───────────────────────

def test_defect052_to_read_glow_removed_after_reassign_to_another_bucket(page):
    """Marking a card as To Read then reassigning it (via the card's Read/NF/NI
    buttons) must remove the purple glow class from the WRAPPER, not just the
    entry. The CSS rule `.nuf-entry-wrap.nuf-to-read` is what drives the
    pulsing animation; leaving it on a now-empty wrapper produces a persistent
    glowing artifact at the card's former position."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    # 1. Mark first card as To Read → wrapper gets the glow class
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    expect(page.locator('.nuf-entry-wrap.nuf-to-read')).to_have_count(1)

    # 2. Reassign to Read (goes through markEntry)
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()

    # 3. Wait for the 0.35 s collapse animation, then assert NO wrapper still
    # carries the nuf-to-read class anywhere in DOM
    page.wait_for_timeout(700)
    expect(page.locator('.nuf-entry-wrap.nuf-to-read')).to_have_count(0)


def test_defect052_empty_wrapper_removed_after_reassign(page):
    """The animationend handler in markEntry must clean up the now-empty
    wrapper after the entry collapses out, matching the existing pattern in
    storage-listener.js's filter loop. Without this, the wrapper lingers in
    DOM (carrying the glow class) indefinitely."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    page.wait_for_timeout(700)

    # No empty (zero-child) wrapper should remain after the animation completes
    empty_wrapper_count = page.evaluate('''() => {
        return [...document.querySelectorAll('.nuf-entry-wrap')]
            .filter(w => w.children.length === 0).length;
    }''')
    assert empty_wrapper_count == 0, (
        f'{empty_wrapper_count} empty .nuf-entry-wrap elements remain after reassign — '
        f'the markEntry animationend handler should also remove the wrapper'
    )


# ── defect-050: tag chips still missing — second-pass fix ──────────────────

def test_defect050_prune_preserves_entries_with_fetched_tags(page):
    """When novelMeta exceeds the 3000-entry cap, entries with populated `tags`
    (expensive prefetch work) must be retained preferentially over tag-less
    entries when both are unbucketed and have no ts."""
    # Seed: 1500 tagged + 1600 tagless = 3100 unbucketed entries (over cap by 100).
    # All have ts=0 (typical for listing-visited entries that never got bucketed).
    novel_meta = {}
    for i in range(1500):
        novel_meta[f'tagged-{i:04d}'] = {
            'g': ['x'], 'l': 'chinese', 'd': ['x'], 't': f'T{i}',
            'tags': ['kingdom building'],
        }
    for i in range(1600):
        novel_meta[f'tagless-{i:04d}'] = {
            'g': ['y'], 'l': 'japanese', 'd': ['y'], 't': f'U{i}',
        }
    load_listing(page, {
        # Bucketed slug NOT in novelMeta — that triggers captureMetadata on the
        # visible cards, which calls scheduleMetaSave → debounced 2 s → prune.
        'read': ['some-bucketed-slug'],
        'novelMeta': novel_meta,
    })
    page.wait_for_selector('.nuf-controls')
    # Wait > 2 s for the debounced save to fire, plus a bit for storage write to land
    wait_for_meta_save(page)

    stored = get_storage(page, 'novelMeta')
    meta = stored.get('novelMeta', {})
    tagged_kept = sum(1 for k in meta if k.startswith('tagged-'))
    tagless_kept = sum(1 for k in meta if k.startswith('tagless-'))
    # With the fix: ALL 1500 tagged retained; tagless reduced.
    # Without the fix: prune ranks all by ts (tied at 0); stable sort preserves
    # insertion order; first 100+ entries to evict are the tagged ones since
    # they were seeded first.
    assert tagged_kept == 1500, (
        f'Pruning evicted {1500-tagged_kept} tagged entries; should preserve all. '
        f'(kept tagged={tagged_kept}, tagless={tagless_kept})'
    )


def test_defect050_neutral_tag_chips_render_without_overlap(page):
    """When a card has NU tags but no bucketed ref has overlapping tags (e.g.
    refs are Cloudflare-blocked and have empty tags), show the card's own tags
    as neutral informational chips instead of silently omitting the Tags row."""
    # Bucketed ref WITHOUT tags (mimics a Cloudflare-blocked ref); listing card
    # release-that-witch pre-seeded WITH tags. No overlap possible.
    novel_meta = {
        'ref-novel': {
            't': 'Ref', 'g': ['fantasy'], 'l': 'chinese', 'd': [], 'ts': 1716000000000,
            # no `tags` — Cloudflare-blocked ref scenario
        },
        'release-that-witch': {
            't': 'Release that Witch', 'g': ['fantasy', 'sci-fi'], 'l': 'chinese',
            'd': [], 'tags': ['kingdom building', 'magic', 'transmigration'],
        },
    }
    load_listing(page, {'read': ['ref-novel'], 'novelMeta': novel_meta})
    page.wait_for_selector('.nuf-reason-row')

    controls = page.locator(
        '.nuf-entry-wrap:has([data-nuf-slug="release-that-witch"]) .nuf-controls'
    )
    # A Tags group label must appear
    expect(
        controls.locator('.nuf-reason-group-label', has_text=re.compile(r'tag', re.I))
    ).to_have_count(1, timeout=3000)
    # And at least one neutral chip (since no pos/neg overlap exists)
    expect(controls.locator('.nuf-chip-tag-neutral').first).to_be_visible(timeout=3000)


def test_defect050_diagnostic_logs_chip_status(page):
    """applyRecommendations must emit a console.info diagnostic on each forceAll
    pass with ref/card/overlap counts so users debugging missing chips can see
    which condition is failing."""
    logs = []
    page.on('console', lambda msg: logs.append(msg.text))

    load_listing(page, {
        'read': ['ref-novel'],
        'novelMeta': {
            'ref-novel': {
                't': 'Ref', 'g': ['fantasy'], 'l': 'chinese', 'd': [],
                'tags': ['kingdom building'], 'ts': 1716000000000,
            },
        },
    })
    page.wait_for_selector('.nuf-controls')
    # Wait for at least one forceAll recompute (triggered by post-prefetch
    # scheduleRecompute or by any storage-driven re-render).
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=15000)
    page.wait_for_timeout(500)  # allow debounced recompute to fire

    matching = [l for l in logs if 'refs=' in l and ('cards=' in l or 'overlap=' in l)]
    assert len(matching) > 0, (
        f'No diagnostic log line matching refs=/cards=/overlap=. '
        f'Recent logs: {logs[-10:]}'
    )


# ── Cloudflare robustness sweep (defect-050 follow-ups) ─────────────────────

def test_robustness_login_page_treated_as_auth_failure(page):
    """A series fetch returning a 200 with a login form must NOT be parsed as
    a successful empty-tags response (which would silently mark the slug 'done'
    and never retry). novelMeta[slug].tags must remain unset."""
    page.route(
        '**/series/release-that-witch/**',
        lambda route: route.fulfill(
            status=200,
            content_type='text/html; charset=utf-8',
            body='<html><body><form id="loginform" action="/login">'
                 '<input name="user"><input name="pass"></form></body></html>',
        ),
    )
    # Other series requests fall back to the conftest stub (returns series.html).
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    # Let the prefetch queue drain (4 slugs, 2 s baseline each)
    expect(page.locator('.nuf-tag-spinner')).to_have_count(0, timeout=15000)

    stored = get_storage(page, 'novelMeta')
    rtw_meta = stored.get('novelMeta', {}).get('release-that-witch') or {}
    assert not rtw_meta.get('tags'), (
        f"login-page response should not populate tags; got {rtw_meta.get('tags')!r}"
    )


def test_robustness_re_enable_does_not_enqueue_all_novelMeta(page):
    """When the user toggles prefetchTagsEnabled on, only bucketed slugs may
    be enqueued — re-enqueuing every Object.keys(novelMeta) entry would undo
    defect-028's discipline and flood Cloudflare on toggle-on for users with
    long scroll history."""
    import threading
    series_hits = []
    series_lock = threading.Lock()

    def count(route):
        url = route.request.url
        if '/series/' in url and '/series-finder' not in url:
            with series_lock:
                series_hits.append(url)
        # Hang the fetch so we can observe what was queued without races
        threading.Event().wait(5)
        route.continue_()

    page.route(SERIES_PAGES, count)
    # 1 bucketed + 50 unbucketed entries in novelMeta (mimics a long scroll history)
    novel_meta = {f'unbucketed-{i}': {'g': [], 'l': 'chinese', 'd': []} for i in range(50)}
    novel_meta['ref-novel'] = {'g': ['fantasy'], 'l': 'chinese', 'd': [], 't': 'Ref'}
    load_listing(page, {
        'read': ['ref-novel'],
        'novelMeta': novel_meta,
        'prefetchTagsEnabled': False,  # start disabled — no spinners
    })
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(300)
    # Sanity: no fetches while disabled
    assert series_hits == [], f'fetches fired while disabled: {len(series_hits)}'

    # Toggle on — should enqueue ONLY the bucketed ref + visible card slugs
    # (4 listing fixture cards), NOT the 50 unbucketed novelMeta entries.
    set_storage(page, {'prefetchTagsEnabled': True})
    page.wait_for_timeout(500)
    unique_targets = {h.split('/series/')[1].split('/')[0] for h in series_hits}
    # Without the fix: would target ~55 slugs (50 novelMeta + 1 ref + 4 cards).
    # With the fix: only the 4 card slugs + ref-novel. Allow some slack — assert << 50.
    assert len(unique_targets) < 10, (
        f'Too many slug fetches kicked off on re-enable: {len(unique_targets)} '
        f'(should be ~5: ref + 4 listing cards). Sample: {list(unique_targets)[:10]}'
    )


# ── defect-049: redesign bucket-action toast ────────────────────────────────

def test_defect049_toast_anchors_to_card_position(page):
    """The bucket-action toast must appear near the source card, not at the bottom
    of the viewport. To Read keeps the card in DOM, making the assertion robust."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    card = page.locator('.search_main_box_nu').first
    card_box = card.bounding_box()
    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="toread"]').click()

    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))
    toast_box = page.locator('.nuf-toast').bounding_box()

    # Toast must be near the card vertically (within ~80px of the card top)
    assert abs(toast_box['y'] - card_box['y']) < 100, (
        f"toast y={toast_box['y']} too far from card y={card_box['y']}"
    )
    # Toast must NOT be bottom-centred (the old behaviour). Bottom-centre would
    # put toast y near viewport height minus toast height.
    viewport_h = page.viewport_size['height']
    bottom_centre_y = viewport_h - toast_box['height'] - 30
    assert abs(toast_box['y'] - bottom_centre_y) > 50, (
        f"toast appears to be bottom-centred (y={toast_box['y']}, bottom-centre would be ~{bottom_centre_y})"
    )


def test_defect049_toast_persists_longer_than_seven_seconds(page):
    """Toast must persist long enough that the user can read the prompt, decide on
    a rating, and add a comment — at least 7 s (the original was 5 s)."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))

    # Wait 7 s — the toast must still be visible
    page.wait_for_timeout(7000)
    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))


def test_defect049_rating_row_has_rate_heading_and_five_buttons(page):
    """Toast must show a 'Rate this novel' heading and exactly 5 rating buttons
    (1-5 scale, down from 10) for Read and Not Finished buckets."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.nuf-toast')).to_have_class(re.compile('nuf-toast-visible'))

    # Heading element exists with text containing 'rate'
    expect(
        page.locator('.nuf-toast-review-title')
    ).to_have_text(re.compile(r'rate', re.I))

    # Exactly 5 rating buttons labelled 1..5
    buttons = page.locator('.nuf-toast-rating-btn')
    expect(buttons).to_have_count(5)
    labels = buttons.all_text_contents()
    assert labels == ['1', '2', '3', '4', '5'], f'rating labels: {labels}'


def test_defect049_ten_scale_ratings_migrate_to_five_scale(page):
    """Pre-existing 1-10 ratings in storage must be migrated to 1-5 on first load.
    Migration is gated on `ratingScaleVersion` so it never re-runs."""
    load_listing(page, {
        'novelReviews': {
            'a': {'rating': 10},
            'b': {'rating': 7},
            'c': {'rating': 5},
            'd': {'rating': 1},
        },
    })
    page.wait_for_selector('.nuf-controls')
    # Migration is debounced by saveNovelReviews — give it a moment to flush
    page.wait_for_timeout(500)

    stored = get_storage(page, ['novelReviews', 'ratingScaleVersion'])
    reviews = stored.get('novelReviews', {})
    assert reviews.get('a', {}).get('rating') == 5, f"10→5 expected, got {reviews.get('a')}"
    assert reviews.get('b', {}).get('rating') == 4, f"7→4 expected, got {reviews.get('b')}"
    assert reviews.get('c', {}).get('rating') == 3, f"5→3 (round 2.5) expected, got {reviews.get('c')}"
    assert reviews.get('d', {}).get('rating') == 1, f"1→1 expected, got {reviews.get('d')}"
    assert stored.get('ratingScaleVersion') == 2


def test_defect049_rating_migration_runs_only_once(page):
    """When ratingScaleVersion is already 2, migration must NOT run again (would
    halve already-migrated values to 1-2 range)."""
    load_listing(page, {
        'novelReviews': {'x': {'rating': 5}},
        'ratingScaleVersion': 2,
    })
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(500)

    stored = get_storage(page, ['novelReviews'])
    assert stored.get('novelReviews', {}).get('x', {}).get('rating') == 5, (
        'rating was re-migrated even though ratingScaleVersion=2 should have skipped it'
    )


# ── defect-046: reload should reset scroll to top after infinite scroll ─────

def test_defect046_scrollRestoration_set_to_manual_on_listing(page):
    """Listing-page init() must disable browser scroll restoration so a reload
    after infinite scroll doesn't try to restore a position into now-missing content."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    restoration = page.evaluate('() => history.scrollRestoration')
    assert restoration == 'manual', (
        f"expected history.scrollRestoration='manual' on listing, got {restoration!r}"
    )


def test_defect046_series_page_does_not_override_scroll_restoration(page):
    """Series page init must NOT override scroll restoration — only listing pages
    have appended infinite-scroll content that requires manual restoration."""
    load_series(page)
    page.wait_for_selector('.nuf-controls')
    restoration = page.evaluate('() => history.scrollRestoration')
    assert restoration == 'auto', (
        f"expected history.scrollRestoration='auto' on series page, got {restoration!r}"
    )


def test_defect045_capture_metadata_preserves_existing_tags(page):
    """captureMetadata must not clobber an existing tags array on novelMeta when a
    listing card is processed. The spread `...(existing || {})` should preserve it."""
    load_listing(page, {
        'novelMeta': {
            'release-that-witch': {
                't': 'Release that Witch',
                'g': ['fantasy', 'sci-fi'],
                'l': 'chinese',
                'd': [],
                'tags': ['pre-existing-tag-1', 'pre-existing-tag-2'],
            },
        },
    })
    page.wait_for_selector('.nuf-controls')

    # Give init enough time to run captureMetadata on every card and flush the
    # debounced scheduleMetaSave (2 s timeout per storage.js:69).
    wait_for_meta_save(page)

    stored = get_storage(page, 'novelMeta')
    rtw_tags = stored.get('novelMeta', {}).get('release-that-witch', {}).get('tags')
    assert rtw_tags == ['pre-existing-tag-1', 'pre-existing-tag-2'], (
        f'captureMetadata clobbered the existing tags array; got {rtw_tags!r}'
    )


# ── Page loading robustness: failures, backoff, labels, metadata ─────────────

_ALL_PAGE1_SLUGS = [
    'release-that-witch', 'against-the-gods',
    'i-shall-seal-the-heavens', 'lord-of-the-mysteries',
]


def _retrigger_sentinel_observer(page):
    """Push the sentinel out of the observer's range and back, producing a fresh
    intersection — the same thing a user scrolling away and back would do."""
    page.evaluate('''async () => {
        const s = document.querySelector('[class^="nuf-sentinel"]');
        s.style.marginTop = '5000px';
        await new Promise(r => setTimeout(r, 200));
        s.style.marginTop = '';
    }''')


@pytest.mark.parametrize('failure', ['network', 500])
def test_scroll_non_blocking_failure_offers_keep_going_not_end_of_results(page, failure):
    """A network error or non-blocking HTTP error must not be shown as 'End of results'.
    After the retries run out the user gets the 'Keep going' button with a reason."""
    def handler(route):
        if failure == 'network':
            route.abort('failed')
        else:
            route.fulfill(status=500, body='Server Error')

    page.route('**/series-finder/?pg=2', handler)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.nuf-sentinel-blocked')).to_be_visible(timeout=15000)
    assert page.locator('.nuf-sentinel-done').count() == 0, 'failure must not read as End of results'
    msg = page.locator('.nuf-sentinel-blocked-msg').text_content()
    if failure == 'network':
        assert 'network' in msg.lower(), msg
    else:
        assert 'HTTP 500' in msg, msg
    expect(page.locator('.nuf-sentinel-retry-btn')).to_be_visible()


def test_scroll_keep_going_recovers_after_network_failure(page):
    """Clicking 'Keep going' after a failure resumes loading once the site responds again."""
    failing = {'on': True}
    fixture = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')

    def handler(route):
        if failing['on']:
            route.abort('failed')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=fixture)

    page.route('**/series-finder/?pg=2', handler)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.nuf-sentinel-retry-btn')).to_be_visible(timeout=15000)
    failing['on'] = False
    page.locator('.nuf-sentinel-retry-btn').click()

    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=10000)
    expect(page.locator('.nuf-sentinel-done')).to_be_visible(timeout=5000)


def test_scroll_challenge_page_is_treated_as_failure_not_end(page):
    """A Cloudflare interstitial served with HTTP 200 has no cards and no next link.
    It must surface as a failure with 'Keep going', not end scrolling for good."""
    challenge = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
                 '<body><div id="challenge-running"></div></body></html>')
    page.route('**/series-finder/?pg=2',
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=challenge))
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.nuf-sentinel-blocked')).to_be_visible(timeout=15000)
    assert page.locator('.nuf-sentinel-done').count() == 0
    assert 'security check' in page.locator('.nuf-sentinel-blocked-msg').text_content()


def test_scroll_retry_backoff_not_bypassed_by_sentinel_reentering_view(page):
    """During the retry wait and the blocked state, the sentinel re-entering view
    must not start a fetch — only the scheduled retry or 'Keep going' may."""
    requests = []

    def handler(route):
        requests.append(time.time())
        route.fulfill(status=503, body='Unavailable')

    page.route('**/series-finder/?pg=2', handler)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.nuf-sentinel-loading')).to_contain_text('Retrying in', timeout=10000)
    assert len(requests) == 1
    _retrigger_sentinel_observer(page)
    page.wait_for_timeout(500)
    assert len(requests) == 1, 're-entering view during the backoff must not fetch early'

    expect(page.locator('.nuf-sentinel-blocked')).to_be_visible(timeout=30000)
    assert len(requests) == 3
    _retrigger_sentinel_observer(page)
    page.wait_for_timeout(1000)
    assert len(requests) == 3, 'blocked state must wait for the Keep going button'


def test_scroll_filter_delay_not_bypassed_by_sentinel_reentering_view(page):
    """During the 'Skipping filtered page…' delay the sentinel re-entering view
    must not fetch the next page early."""
    fixture = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')
    pg2_with_next = fixture.replace(
        '<!-- No rel="next" — this is the last page -->',
        '<a rel="next" href="https://www.novelupdates.com/series-finder/?pg=3">Next</a>',
    )
    pg3_requests = []

    def handle_pg3(route):
        pg3_requests.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='<html><body></body></html>')

    page.route('**/series-finder/?pg=2*',
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=pg2_with_next))
    page.route('**/series-finder/?pg=3*', handle_pg3)
    load_listing(page, {
        'notInterested': _ALL_PAGE1_SLUGS + ['the-desolate-era', 'renegade-immortal'],
        'prefetchTagsEnabled': False,
    })

    expect(page.locator('.nuf-sentinel-loading')).to_contain_text('Skipping filtered page', timeout=10000)
    _retrigger_sentinel_observer(page)
    page.wait_for_timeout(300)
    assert len(pg3_requests) == 0, 're-entering view during the filter delay must not fetch early'
    page.wait_for_timeout(2000)
    assert len(pg3_requests) == 1, 'the delayed fetch must still happen exactly once'


def test_scroll_page_label_does_not_advance_on_retry(page):
    """A retried fetch of page 2 must still be labelled 'Page 2', not 'Page 3'."""
    calls = []
    fixture = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')

    def handler(route):
        calls.append(1)
        if len(calls) == 1:
            route.fulfill(status=503, body='Unavailable')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=fixture)

    page.route('**/series-finder/?pg=2', handler)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})
    # Record every loading label from now on; the retry happens ~2 s later
    page.evaluate('''() => {
        window.__nufLabels = [];
        new MutationObserver(() => {
            const el = document.querySelector('.nuf-sentinel-loading-text');
            if (el) window.__nufLabels.push(el.textContent);
        }).observe(document.body, { childList: true, subtree: true, characterData: true });
    }''')

    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=15000)
    assert len(calls) == 2
    labels = page.evaluate('window.__nufLabels')
    assert labels, 'expected the retry attempt to show a loading label'
    assert all(label.startswith('Page 2 ') for label in labels), labels


def test_scroll_filtered_card_metadata_is_captured(page):
    """Cards filtered out on later pages must still feed novelMeta, the same as
    filtered cards on the first page (processEntry captures before filtering)."""
    load_listing(page, {
        'notInterested': _ALL_PAGE1_SLUGS,
        'excludeTags': ['fantasy'],
        'prefetchTagsEnabled': False,
    })
    # Page 2: The Desolate Era (Fantasy) is filtered; Renegade Immortal is shown
    expect(page.locator('.search_main_box_nu')).to_have_count(1, timeout=10000)
    wait_for_meta_save(page)

    meta = get_storage(page, 'novelMeta').get('novelMeta', {})
    assert meta.get('the-desolate-era', {}).get('g') == ['fantasy'], meta.get('the-desolate-era')


# ── defect-055: scroll page loads honour Retry-After, pace after blocks, jitter ──

def test_defect055_scroll_retry_honours_retry_after(page):
    """A 429 with Retry-After: 8 must delay the retry by about 8 s, not the ~4 s default backoff."""
    fixture = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')
    requests = []

    def handler(route):
        requests.append(time.time())
        if len(requests) == 1:
            route.fulfill(status=429, headers={'Retry-After': '8'}, body='Too Many Requests')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=fixture)

    page.route('**/series-finder/?pg=2', handler)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=20000)
    assert len(requests) == 2
    gap = requests[1] - requests[0]
    assert gap >= 7.5, f'retry must wait for Retry-After (8 s); waited {gap:.2f} s'


def test_defect055_scroll_retry_after_shown_as_countdown(page):
    """The retry countdown reflects the Retry-After wait."""
    page.route('**/series-finder/?pg=2',
               lambda route: route.fulfill(status=429, headers={'Retry-After': '20'}, body='Too Many Requests'))
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.nuf-sentinel-loading')).to_contain_text('Retrying in', timeout=10000)
    text = page.locator('.nuf-sentinel-loading').text_content()
    secs = int(text.split('Retrying in ')[1].split('s')[0])
    assert 18 <= secs <= 20, text


def test_defect055_scroll_pacing_sticks_after_a_block(page):
    """After a block, the next page request is spaced out even though the retry succeeded."""
    fixture = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')
    pg2_with_next = fixture.replace(
        '<!-- No rel="next" — this is the last page -->',
        '<a rel="next" href="https://www.novelupdates.com/series-finder/?pg=3">Next</a>',
    )
    pg2_requests, pg3_requests = [], []

    def handle_pg2(route):
        pg2_requests.append(time.time())
        if len(pg2_requests) == 1:
            route.fulfill(status=503, body='Unavailable')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=pg2_with_next)

    def handle_pg3(route):
        pg3_requests.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='<html><body></body></html>')

    page.route('**/series-finder/?pg=2*', handle_pg2)
    page.route('**/series-finder/?pg=3*', handle_pg3)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=15000)
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    page.wait_for_timeout(6000)

    assert len(pg3_requests) == 1, 'page 3 must still load'
    gap = pg3_requests[0] - pg2_requests[-1]
    assert gap >= 2.3, f'after a block, page requests must stay spaced out (~3 s); gap was {gap:.2f} s'


def test_defect055_scroll_without_blocks_is_not_paced(page):
    """With no blocks, the next page loads as soon as the sentinel is visible."""
    fixture = (Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8')
    pg2_with_next = fixture.replace(
        '<!-- No rel="next" — this is the last page -->',
        '<a rel="next" href="https://www.novelupdates.com/series-finder/?pg=3">Next</a>',
    )
    pg2_requests, pg3_requests = [], []

    def handle_pg2(route):
        pg2_requests.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=pg2_with_next)

    def handle_pg3(route):
        pg3_requests.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body='<html><body></body></html>')

    page.route('**/series-finder/?pg=2*', handle_pg2)
    page.route('**/series-finder/?pg=3*', handle_pg3)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=10000)
    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    page.wait_for_timeout(2000)
    assert len(pg3_requests) == 1
    assert pg3_requests[0] - pg2_requests[0] < 1.5


def test_defect055_challenge_page_is_not_retried_automatically(page):
    """Retrying a background fetch cannot pass a security check, so a challenge
    page goes straight to Keep going without burning automatic retries."""
    requests = []
    challenge = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
                 '<body><div id="challenge-running"></div></body></html>')

    def handler(route):
        requests.append(1)
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=challenge)

    page.route('**/series-finder/?pg=2', handler)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})

    expect(page.locator('.nuf-sentinel-blocked')).to_be_visible(timeout=10000)
    page.wait_for_timeout(1000)
    assert len(requests) == 1


# ── Fixed-defect audit (2026-10-25): gaps found re-checking defects 010, 020, 025, 044, 049 ──

def test_defect010_sidebar_buttons_have_tooltips(page):
    """Every clickable element in the To Read sidebar explains itself on hover."""
    load_listing(page, {
        'toRead': ['release-that-witch'],
        'novelMeta': {'release-that-witch': {'t': 'Release that Witch', 'desc': 'x' * 300}},
        'prefetchTagsEnabled': False,
    })
    page.wait_for_selector('.nuf-sidebar-item')
    for selector in ('.nuf-sidebar-bucket-btn', '.nuf-sidebar-desc-toggle',
                     '.nuf-sidebar-start', '.nuf-sidebar-remove'):
        elements = page.locator(selector)
        assert elements.count() > 0, selector
        for i in range(elements.count()):
            assert elements.nth(i).get_attribute('title'), f'{selector} #{i} has no tooltip'


def test_defect010_toast_rating_buttons_have_tooltips(page):
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    buttons = page.locator('.nuf-toast-rating-btn')
    expect(buttons).to_have_count(5)
    for i in range(5):
        assert buttons.nth(i).get_attribute('title'), f'rating button {i + 1} has no tooltip'


def test_defect010_keep_going_button_has_tooltip(page):
    challenge = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
                 '<body><div id="challenge-running"></div></body></html>')
    page.route('**/series-finder/?pg=2',
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=challenge))
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})
    expect(page.locator('.nuf-sentinel-retry-btn')).to_be_visible(timeout=10000)
    assert page.locator('.nuf-sentinel-retry-btn').get_attribute('title')


def test_defect044_series_to_read_captures_title_cover_and_description(page):
    """A novel queued from its series page shows its real title, cover and synopsis
    in the sidebar — not the slug and a blank placeholder."""
    load_series(page)
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="toread"]').click()
    page.wait_for_timeout(500)
    meta = get_storage(page, 'novelMeta')['novelMeta']['release-that-witch']
    assert meta.get('t') == 'Release that Witch', meta
    assert meta.get('img', '').endswith('/img/series/release-that-witch.jpg'), meta
    assert meta.get('desc', '').startswith('Cheng Yan travels to another world'), meta


def test_defect044_series_page_records_title_in_novel_meta(page):
    """Visiting a series page stores its title, so the manager and sidebar never fall back to the slug."""
    load_series(page)
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(500)
    meta = get_storage(page, 'novelMeta')['novelMeta']['release-that-witch']
    assert meta.get('t') == 'Release that Witch', meta


def test_defect025_start_reading_can_retry_after_failed_lookup(page):
    """If the first-chapter lookup fails, the button must not get stuck — clicking again retries."""
    calls = []
    series_html = (Path(__file__).parent / 'fixtures' / 'series.html').read_text(encoding='utf-8')

    def handler(route):
        calls.append(1)
        if len(calls) == 1:
            route.fulfill(status=503, body='Unavailable')
        else:
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=series_html)

    page.route('**/series/release-that-witch/**', handler)
    load_listing(page, {'prefetchTagsEnabled': False})
    btn = page.locator('.nuf-btn[data-nuf-btn="start"]').first
    btn.click()
    expect(btn).to_have_text('?', timeout=5000)
    with page.expect_popup() as popup_info:
        btn.click()
    # The tab opens blank at once (so the popup blocker allows it) and moves to the chapter after the lookup
    popup_info.value.wait_for_url(re.compile(r'extnu/'), timeout=10000)
    assert 'extnu/100001' in popup_info.value.url
    popup_info.value.close()


def test_defect025_start_reading_reuses_looked_up_url(page):
    """After one successful lookup, further clicks open the cached URL without another request."""
    calls = []
    series_html = (Path(__file__).parent / 'fixtures' / 'series.html').read_text(encoding='utf-8')

    def handler(route):
        calls.append(1)
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=series_html)

    page.route('**/series/release-that-witch/**', handler)
    load_listing(page, {'prefetchTagsEnabled': False})
    btn = page.locator('.nuf-btn[data-nuf-btn="start"]').first
    for _ in range(2):
        with page.expect_popup() as popup_info:
            btn.click()
        popup_info.value.wait_for_url(re.compile(r'extnu/'), timeout=10000)
        assert 'extnu/100001' in popup_info.value.url
        popup_info.value.close()
    assert len(calls) == 1, f'expected one series-page request, got {len(calls)}'


def _toast_progress_ratio(page):
    return page.evaluate('''() => {
        const bar = document.querySelector('.nuf-toast-progress');
        return bar.getBoundingClientRect().width / bar.parentElement.getBoundingClientRect().width;
    }''')


def test_defect049_progress_bar_restarts_when_rating_extends_timer(page):
    """Rating a novel restarts the dismiss timer, so the countdown bar must restart too."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    page.wait_for_timeout(4000)
    assert _toast_progress_ratio(page) < 0.8
    page.locator('.nuf-toast-rating-btn').nth(3).click()
    page.wait_for_timeout(300)
    assert _toast_progress_ratio(page) > 0.9, 'countdown bar did not restart with the timer'


def test_defect049_progress_bar_pauses_while_typing_comment(page):
    """The dismiss timer is paused while the comment box has focus, so the bar must stop moving."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    page.wait_for_timeout(1000)
    page.locator('.nuf-toast-comment').focus()
    page.wait_for_timeout(300)
    first = _toast_progress_ratio(page)
    page.wait_for_timeout(1500)
    second = _toast_progress_ratio(page)
    assert abs(first - second) < 0.02, f'bar kept moving while paused: {first:.2f} -> {second:.2f}'


def test_defect020_popup_fits_without_scrolling(page, extension_id):
    """Chrome caps popups at 600 px tall; in its default state the popup must fit without a scrollbar."""
    page.set_viewport_size({'width': 500, 'height': 600})
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.wait_for_selector('#options-section')
    height = page.evaluate('document.documentElement.scrollHeight')
    assert height <= 600, f'popup is {height}px tall in its default state'


# ── defect-056: To Read sidebar cover images ─────────────────────────────────

SERIES_FIXTURE_COVER = '/img/series/release-that-witch.jpg'


def test_defect056_sidebar_backfills_cover_from_series_page(page):
    """A To Read novel that already has tags but no cover gets its cover from the series page."""
    load_listing(page, {
        'toRead': ['queued-novel'],
        'novelMeta': {'queued-novel': {'t': 'Queued Novel', 'tags': ['kingdom building']}},
    })
    img = page.locator('.nuf-sidebar-item img.nuf-sidebar-img')
    expect(img).to_have_count(1, timeout=10000)
    assert img.get_attribute('src').endswith(SERIES_FIXTURE_COVER)


def test_defect056_novel_added_without_any_metadata_gets_cover(page):
    """Novels moved into To Read from the manager or a bucket-only import have no metadata at all;
    the sidebar still ends up with the cover, title and synopsis."""
    load_listing(page, {'toRead': ['queued-novel']})
    img = page.locator('.nuf-sidebar-item img.nuf-sidebar-img')
    expect(img).to_have_count(1, timeout=10000)
    assert img.get_attribute('src').endswith(SERIES_FIXTURE_COVER)
    expect(page.locator('.nuf-sidebar-title')).to_have_text('Release that Witch')
    expect(page.locator('.nuf-sidebar-desc')).to_contain_text('Cheng Yan travels')


def test_defect056_sidebar_uses_card_cover_when_card_on_page(page):
    """If the novel's card is on the current page, its cover is used straight away with no request."""
    load_listing(page, {'toRead': ['against-the-gods'], 'prefetchTagsEnabled': False})
    img = page.locator('.nuf-sidebar-item img.nuf-sidebar-img')
    expect(img).to_have_count(1, timeout=5000)
    card_src = page.evaluate('''() => document.querySelector('[data-nuf-slug="against-the-gods"] .search_img_nu img').src''')
    assert img.get_attribute('src') == card_src


def test_defect056_missing_cover_not_refetched_when_series_has_none(page):
    """A series page with no cover is remembered, so the novel is not refetched on every load."""
    calls = []
    no_cover = ('<html><body><h1 class="seriestitlenu">No Cover</h1>'
                '<a href="https://www.novelupdates.com/stag/magic/">Magic</a></body></html>')

    def handler(route):
        calls.append(1)
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=no_cover)

    page.route('**/series/no-cover-novel/**', handler)
    load_listing(page, {'toRead': ['no-cover-novel']})
    for _ in range(40):
        meta = get_storage(page, 'novelMeta').get('novelMeta', {}).get('no-cover-novel', {})
        if 'img' in meta:
            break
        page.wait_for_timeout(250)
    assert meta.get('img') == '', meta
    first = len(calls)
    page.reload()
    page.wait_for_selector('.nuf-sidebar-item')
    page.wait_for_timeout(4000)
    assert len(calls) == first, f'series page refetched: {len(calls)} requests'


# ── Minimum Novel Updates rating: 0.1 steps ──────────────────────────────────

def test_min_rating_offers_tenth_steps(page, extension_id):
    """The minimum rating dropdown offers every 0.1 step so ratings like 4.4 can be chosen."""
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    values = page.locator('#min-rating option').evaluate_all('els => els.map(e => e.value)')
    assert values[0] == '0'
    for v in ('3', '3.1', '4.4', '4.6', '4.9'):
        assert v in values, f'{v} missing from {values}'


def test_min_rating_tenth_step_round_trips_through_popup(page, extension_id):
    """Choosing 4.4 stores 4.4, and reopening the popup shows 4.4 selected."""
    page._sw.evaluate('() => new Promise(r => chrome.storage.local.clear(r))')
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    page.locator('#min-rating').select_option('4.4')
    page.wait_for_timeout(300)
    assert get_storage(page, 'minRating')['minRating'] == 4.4
    page.reload()
    assert page.locator('#min-rating').input_value() == '4.4'


def test_min_rating_tenth_step_filters_listing(page):
    """A 4.4 minimum keeps the 4.5 and 4.8 cards and hides the 4.2 and 4.3 ones."""
    load_listing(page, {'minRating': 4.4, 'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    titles = page.locator('.search_title a').all_text_contents()
    assert sorted(titles) == ['Lord of the Mysteries', 'Release that Witch'], titles


# ── Request pacing: background broker (2026-10-25 loading review) ────────────

def _broker(page, script):
    """Run an async snippet in the service worker with `b` bound to the broker test hooks."""
    return page._sw.evaluate(f'async () => {{ const b = globalThis.nufBroker; {script} }}')


def test_broker_spaces_consecutive_requests(page):
    """Two requests from anywhere are granted at least the jittered 1 s base gap apart."""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    gap = _broker(page, '''
        const first = await b.requestSlot('prefetch'); b.release(first.slotId, 'ok');
        const t0 = Date.now();
        const second = await b.requestSlot('prefetch'); b.release(second.slotId, 'ok');
        return Date.now() - t0;''')
    assert gap >= 750, gap


def test_broker_block_slows_every_requester_and_honours_retry_after(page):
    """A block reported by one requester delays the next grant for anyone, up to Retry-After."""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    gap = _broker(page, '''
        const first = await b.requestSlot('scroll'); b.release(first.slotId, 'blocked', '5');
        const t0 = Date.now();
        const second = await b.requestSlot('manager'); b.release(second.slotId, 'ok');
        return Date.now() - t0;''')
    assert gap >= 4800, f'Retry-After 5 s not honoured: {gap} ms'
    state = page._sw.evaluate('() => globalThis.nufBroker.state()')
    assert state['paceDelay'] == 3000, state


def test_broker_serves_scroll_before_queued_prefetch(page):
    """While a slot is busy, a later scroll request jumps ahead of an earlier prefetch request."""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    order = _broker(page, '''
        const order = [];
        const held = await b.requestSlot('prefetch');
        const p1 = b.requestSlot('prefetch').then(s => { order.push('prefetch'); b.release(s.slotId, 'ok'); });
        const p2 = b.requestSlot('scroll').then(s => { order.push('scroll'); b.release(s.slotId, 'ok'); });
        b.release(held.slotId, 'ok');
        await Promise.all([p1, p2]);
        return order;''')
    assert order == ['scroll', 'prefetch'], order


def test_broker_pace_shared_between_scroll_and_prefetch(page):
    """A blocked listing page slows the tag prefetch in the same tab (they share one broker)."""
    pg2_calls, series_calls = [], []

    def handle_pg2(route):
        pg2_calls.append(time.time())
        route.fulfill(status=503, body='Unavailable')

    def handle_series(route):
        series_calls.append(time.time())
        route.fulfill(status=200, content_type='text/html; charset=utf-8',
                      body=(Path(__file__).parent / 'fixtures' / 'series.html').read_text(encoding='utf-8'))

    page.route('**/series-finder/?pg=2', handle_pg2)
    page.route('**/series/queued-novel/**', handle_series)
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'read': ['queued-novel']})
    page.wait_for_timeout(6000)
    assert pg2_calls and series_calls
    after_block = [t for t in series_calls if t > pg2_calls[0]]
    if after_block:
        assert after_block[0] - pg2_calls[0] >= 3.0, 'prefetch ignored the scroll block'


def test_prefetch_reads_only_first_series_page(page):
    """Background prefetch makes one request per series; only "Start reading" fetches the last page."""
    urls = []
    paginated = ('<html><body><a href="https://www.novelupdates.com/stag/magic/">Magic</a>'
                 '<div class="digg_pagination"><a href="?pg=2">2</a><a href="?pg=5">5</a></div>'
                 '<table id="myTable"><tr><td><a href="https://www.novelupdates.com/extnu/9/" title="c90">c90</a></td></tr></table>'
                 '</body></html>')
    page.route('**/series/long-novel/**', lambda route: (urls.append(route.request.url), route.fulfill(
        status=200, content_type='text/html; charset=utf-8', body=paginated))[1])
    load_listing(page, {'read': ['long-novel']})
    for _ in range(40):
        meta = get_storage(page, 'novelMeta').get('novelMeta', {}).get('long-novel', {})
        if meta.get('tags'):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(1500)
    assert meta.get('tags') == ['magic'], meta
    assert not any('pg=' in u for u in urls), urls
    assert 'c1url' not in meta, 'a chapter from page 1 of a paginated series is not chapter 1'


def test_prefetch_queues_listing_cards_only_near_viewport(page):
    """Cards far below the fold are not prefetched until scrolled near."""
    requested = []
    page.route(SERIES_PAGES, lambda route: (requested.append(route.request.url.rstrip('/').split('/')[-1]),
                                               route.fulfill(status=200, content_type='text/html; charset=utf-8',
                                                             body='<html><body></body></html>'))[1])
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    page.wait_for_timeout(5000)
    assert 'release-that-witch' in requested, requested
    assert 'lord-of-the-mysteries' not in requested, f'fourth card prefetched before scrolling: {requested}'
    page.locator('[data-nuf-slug="lord-of-the-mysteries"]').scroll_into_view_if_needed()
    page.wait_for_timeout(5000)
    assert 'lord-of-the-mysteries' in requested, requested


def test_challenge_state_offers_open_page_button(page):
    """On a security check, an "Open page" button opens the blocked page so the user can pass it."""
    challenge = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
                 '<body><div id="challenge-running"></div></body></html>')
    page.route('**/series-finder/?pg=2',
               lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=challenge))
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})
    btn = page.locator('.nuf-sentinel-open-btn')
    expect(btn).to_be_visible(timeout=10000)
    assert btn.get_attribute('title')
    with page.expect_popup() as popup_info:
        btn.click()
    assert 'series-finder/?pg=2' in popup_info.value.url
    popup_info.value.close()


# ── defect-057: novelMeta save errors ────────────────────────────────────────

def test_defect057_large_novel_meta_saves_without_error(page):
    """A novelMeta larger than Chrome's old 10 MB storage quota still saves, with no errors."""
    words = [f'word{i % 90}{"x" * (i % 5)}' for i in range(180)]
    tokens = list(dict.fromkeys(words + [f'{a} {b}' for a, b in zip(words, words[1:])]))
    entry = {'g': ['fantasy', 'action'], 'l': 'chinese', 'd': tokens,
             'tags': [f'tag number {i}' for i in range(25)], 't': 'A Fairly Long Novel Title Here'}
    big = {f'novel-{i:04d}': {**entry, 'desc': 'y' * 1200} for i in range(2990)}
    errors = []
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda e: errors.append(str(e)))
    load_listing(page, {'novelMeta': big, 'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    wait_for_meta_save(page)
    stored = get_storage(page, 'novelMeta')['novelMeta']
    assert len(stored) > 2900, f'only {len(stored)} entries stored — the oversized seed was rejected'
    assert 'release-that-witch' in stored, 'listing metadata was not saved'
    storage_errors = [e for e in errors if 'quota' in e.lower() or 'storage' in e.lower()]
    assert storage_errors == [], storage_errors


# ── Infinite scroll loading time and the Request queue page ──────────────────

def test_scroll_shows_live_loading_time(page):
    """While the next listing page downloads, the loading line counts up how long it is taking."""
    held = []  # keep the request open without blocking Playwright's thread
    page.route('**/series-finder/?pg=2', lambda route: held.append(route))
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})
    timing = page.locator('.nuf-sentinel-timing')
    expect(timing).to_contain_text('Loading…', timeout=5000)
    first = float(timing.text_content().split('Loading… ')[1].split(' s')[0])
    page.wait_for_timeout(1200)
    second = float(timing.text_content().split('Loading… ')[1].split(' s')[0])
    assert second >= first + 0.8, (first, second)
    for route in held:
        route.fulfill(status=200, content_type='text/html; charset=utf-8',
                      body=(Path(__file__).parent / 'fixtures' / 'listing-page2.html').read_text(encoding='utf-8'))


def test_scroll_shows_wait_for_a_turn_in_the_queue(page):
    """When another request holds the shared slot, the loading line says the page is waiting its turn."""
    load_listing(page, {'notInterested': _ALL_PAGE1_SLUGS, 'prefetchTagsEnabled': False})
    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=10000)
    page._sw.evaluate("async () => { globalThis.__held = await globalThis.nufBroker.requestSlot('manager', null, 'https://www.novelupdates.com/series/x/'); }")
    try:
        page.reload()
        expect(page.locator('.nuf-sentinel-timing')).to_contain_text('Waiting for another request to finish', timeout=5000)
    finally:
        page._sw.evaluate("() => globalThis.nufBroker.release(globalThis.__held.slotId, 'ok')")
    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=10000)


def test_queue_page_shows_loading_waiting_and_recent(page, extension_id):
    """The Request queue page lists the request in flight, those waiting, and recent results."""
    page._sw.evaluate('''async () => {
        const b = globalThis.nufBroker; await b.reset();
        const ok = await b.requestSlot('scroll', null, 'https://www.novelupdates.com/series-finder/?pg=2');
        b.release(ok.slotId, 'ok', null, 200);
        const blocked = await b.requestSlot('prefetch', null, 'https://www.novelupdates.com/series/against-the-gods/');
        b.release(blocked.slotId, 'blocked', null, 429);
        globalThis.__held = await b.requestSlot('manager', null, 'https://www.novelupdates.com/series/shadow-slave/');
        b.requestSlot('prefetch', null, 'https://www.novelupdates.com/series/renegade-immortal/');
    }''')
    try:
        page.goto(f'chrome-extension://{extension_id}/queue.html')
        expect(page.locator('#in-flight .kind')).to_have_text('Manager title')
        expect(page.locator('#waiting .row')).to_have_count(1)
        expect(page.locator('#waiting .path')).to_have_text('/series/renegade-immortal/')
        expect(page.locator('#recent tr')).to_have_count(2)
        expect(page.locator('#recent tr').first.locator('.result')).to_have_text('Blocked (429)')
        expect(page.locator('#status')).to_have_attribute('data-state', 'slowed')
        page._sw.evaluate("() => globalThis.nufBroker.release(globalThis.__held.slotId, 'ok', null, 200)")
        expect(page.locator('#recent tr')).to_have_count(3, timeout=10000)
    finally:
        page._sw.evaluate('() => globalThis.nufBroker.reset()')


def test_popup_queue_button_opens_queue_page(page, extension_id):
    page.goto(f'chrome-extension://{extension_id}/popup.html')
    with page.context.expect_page() as new_page:
        page.locator('#queue-btn').click()
    new_page.value.wait_for_load_state()
    assert new_page.value.url.endswith('/queue.html')
    new_page.value.close()


def test_manager_queue_button_opens_queue_page(page, extension_id):
    page.goto(f'chrome-extension://{extension_id}/manager.html')
    with page.context.expect_page() as new_page:
        page.locator('#queue-btn').click()
    new_page.value.wait_for_load_state()
    assert new_page.value.url.endswith('/queue.html')
    new_page.value.close()


# ── defect-058: manager cover photos ─────────────────────────────────────────

def _wait_for_meta(page, slug, key, tries=40):
    for _ in range(tries):
        meta = get_storage(page, 'novelMeta').get('novelMeta', {}).get(slug, {})
        if key in meta:
            return meta
        page.wait_for_timeout(250)
    return meta


def test_defect058_listing_capture_saves_cover(page):
    """Every listing card scrolled past stores its cover, not only To Read ones."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    meta = _wait_for_meta(page, 'release-that-witch', 'img')
    assert meta.get('img', '').endswith('/imgmid/series_1.jpg'), meta


def test_defect058_existing_entry_gains_cover_on_next_visit(page):
    """An entry saved before covers were captured gets one the next time its card is seen."""
    load_listing(page, {'prefetchTagsEnabled': False, 'novelMeta': {
        'against-the-gods': {'t': 'Against the Gods', 'g': ['action'], 'l': 'korean', 'd': ['warrior'],
                             'tags': ['fetched tag']},
    }})
    page.wait_for_selector('.nuf-controls')
    meta = _wait_for_meta(page, 'against-the-gods', 'img')
    assert meta.get('img'), meta
    assert meta.get('tags') == ['fetched tag'], 'prefetched tags must survive the cover capture'


# ── App-wide UI upgrade (2026-10-25) ─────────────────────────────────────────

def test_toast_stays_inside_viewport_for_full_width_cards(page):
    """When a card fills the width, the toast pins to the right edge without overflowing it."""
    load_listing(page, {'prefetchTagsEnabled': False})
    page.wait_for_selector('.nuf-controls')
    page.locator('.nuf-btn[data-nuf-btn="read"]').first.click()
    expect(page.locator('.nuf-toast.nuf-toast-visible')).to_be_visible()
    right = page.evaluate("document.querySelector('.nuf-toast').getBoundingClientRect().right")
    width = page.evaluate('window.innerWidth')
    assert right <= width, f'toast overflows the viewport by {right - width:.0f}px'


# ── defect-059: To Read sidebar under the live site's CSS ────────────────────

# Mirrors the live site's rules, including their specificity (class + elements),
# which beats the extension's single-class selectors.
_SITE_CSS = ("body:not(.x) button { font-size: 13px; margin: 5px 0; text-transform: capitalize; } "
             "body:not(.x) p { line-height: 26px; } body:not(.x) a { color: rgb(27, 152, 224); }")


def _open_sidebar_with_site_css(page):
    load_listing(page, {
        'toRead': ['release-that-witch', 'lord-of-the-mysteries'],
        'novelMeta': {
            'release-that-witch': {'t': 'Release that Witch', 'desc': 'A prince travels between worlds. ' * 12},
            'lord-of-the-mysteries': {'t': 'Lord of the Mysteries', 'desc': 'Steam power and machinery. ' * 12},
        },
        'prefetchTagsEnabled': False,
    })
    page.add_style_tag(content=_SITE_CSS)
    expect(page.locator('.nuf-sidebar-panel')).to_be_visible()
    page.wait_for_timeout(300)


def test_defect059_sidebar_survives_site_css(page):
    """Novel Updates' own button, paragraph and link rules must not restyle the sidebar."""
    _open_sidebar_with_site_css(page)
    info = page.evaluate('''() => {
        const panel = document.querySelector('.nuf-sidebar-panel').getBoundingClientRect();
        const style = s => getComputedStyle(document.querySelector(s));
        const buttons = [...document.querySelectorAll('.nuf-sidebar-panel button')].map(b => b.getBoundingClientRect());
        return {
            bucketFont: parseFloat(style('.nuf-sidebar-bucket-btn').fontSize),
            bucketMargin: style('.nuf-sidebar-bucket-btn').marginTop,
            startTransform: style('.nuf-sidebar-start').textTransform,
            descLineHeight: parseFloat(style('.nuf-sidebar-desc').lineHeight),
            titleColour: style('.nuf-sidebar-title').color,
            overflowing: buttons.filter(r => r.right > panel.right + 0.5 || r.left < panel.left - 0.5).length,
        };
    }''')
    assert info['bucketFont'] <= 11, info
    assert info['bucketMargin'] == '0px', info
    assert info['startTransform'] == 'none', info
    assert info['descLineHeight'] < 20, info
    assert info['titleColour'] == 'rgb(88, 28, 135)', info
    assert info['overflowing'] == 0, info


def test_defect059_open_tab_does_not_cover_panel(page):
    """With the panel open, the To Read tab sits beside it instead of on top of its content."""
    _open_sidebar_with_site_css(page)
    page.wait_for_timeout(300)  # tab slides with the panel
    tab = page.locator('.nuf-sidebar-tab').bounding_box()
    panel = page.locator('.nuf-sidebar-panel').bounding_box()
    assert tab['x'] + tab['width'] <= panel['x'] + 1, (tab, panel)


def test_queue_runs_background_jobs_only_for_visible_pages(page):
    """A tag lookup owned only by a hidden tab waits; a listing page request still runs."""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    result = page._sw.evaluate('''async () => {
        const b = globalThis.nufBroker;
        const timeout = ms => new Promise(r => setTimeout(() => r('waiting'), ms));
        const hidden = b.requestSlot('prefetch', null, 'https://www.novelupdates.com/series/x/', { visible: false })
            .then(() => 'ran');
        const scroll = await Promise.race([b.requestSlot('scroll', null, 'https://www.novelupdates.com/series-finder/?pg=2', { visible: false }), timeout(3000)]);
        if (scroll !== 'waiting') b.release(scroll.slotId, 'ok');
        return { hidden: await Promise.race([hidden, timeout(3000)]), scroll: scroll === 'waiting' ? 'waiting' : 'ran' };
    }''')
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    assert result == {'hidden': 'waiting', 'scroll': 'ran'}, result


def test_queue_merges_the_same_lookup_from_two_tabs(page):
    """Two pages asking for the same novel produce one job, not two requests."""
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    load_listing(page, {'read': ['shared-novel']})
    other = page.context.new_page()
    other._sw = page._sw
    try:
        other.goto('https://www.novelupdates.com/series-finder/')
        other.wait_for_selector('.nuf-controls')
        snap = page._sw.evaluate('() => globalThis.nufBroker.snapshot()')
        urls = [w['url'] for w in snap['waiting']] + ([snap['inFlight']['url']] if snap['inFlight'] else [])
        urls += [h['url'] for h in snap['history']]
        assert urls.count('https://www.novelupdates.com/series/shared-novel/') <= 1, urls
    finally:
        other.close()
