"""Code-review fixes: the manager never stores a security-check or login page as a
novel's title or cover, survives out-of-range ratings, and round-trips coverCache."""

import json
from playwright.sync_api import expect
from helpers import get_storage
from test_manager import load_manager

CHALLENGE_HTML = '''<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="challenge-running">Verify you are human</div></body></html>'''
LOGIN_HTML = '''<!DOCTYPE html><html><head><title>Login | NovelUpdates</title></head>
<body><form id="loginform" action="/wp-login.php"><input name="log"></form></body></html>'''


def _serve(page, slug, html):
    calls = []
    page.route(f'**/series/{slug}/**', lambda route: (calls.append(1), route.fulfill(status=200, content_type='text/html', body=html))[1])
    return calls


def test_manager_security_check_page_is_not_saved_as_title_or_cover(page, extension_id):
    """A security-check page answered with HTTP 200 leaves the title and cover unknown, not "Just a moment..."."""
    calls = _serve(page, 'checked-slug', CHALLENGE_HTML)
    try:
        load_manager(page, extension_id, {'read': ['checked-slug']})
        page.wait_for_timeout(2500)
        stored = get_storage(page, ['titleCache', 'coverCache'])
        assert 'checked-slug' not in (stored.get('titleCache') or {}), stored
        assert 'checked-slug' not in (stored.get('coverCache') or {}), stored
        assert calls, 'the manager should have asked for the series page'
        expect(page.locator('#list-read .item[data-slug="checked-slug"]')).not_to_contain_text('Just a moment')
    finally:
        page._sw.evaluate('() => globalThis.nufBroker.reset()')


def test_manager_login_page_is_not_saved_as_title(page, extension_id):
    """A logged-out series page (the login form) is not stored as the novel's title."""
    _serve(page, 'login-slug', LOGIN_HTML)
    try:
        load_manager(page, extension_id, {'read': ['login-slug']})
        item = page.locator('#list-read .item[data-slug="login-slug"]')
        expect(item.locator('.item-failed')).to_be_visible(timeout=10000)
        stored = get_storage(page, ['titleCache', 'coverCache'])
        assert 'login-slug' not in (stored.get('titleCache') or {}), stored
        assert 'login-slug' not in (stored.get('coverCache') or {}), stored
    finally:
        page._sw.evaluate('() => globalThis.nufBroker.reset()')


def test_manager_out_of_range_rating_does_not_break_the_list(page, extension_id):
    """A rating above 5 (an old 1-10 export) still renders the list, with the stars capped at five."""
    load_manager(page, extension_id, {
        'read': ['rated-high', 'rated-ok'],
        'titleCache': {'rated-high': 'High', 'rated-ok': 'Fine'},
        'coverCache': {'rated-high': '', 'rated-ok': ''},
        'novelReviews': {'rated-high': {'rating': 9}, 'rated-ok': {'rating': 3}},
    })
    expect(page.locator('#list-read .item')).to_have_count(2)
    stars = page.locator('#list-read .item[data-slug="rated-high"] .rating').text_content()
    assert stars == '★★★★★', stars


def test_manager_import_restores_cover_cache(page, extension_id):
    """coverCache is exported, so importing restores it too."""
    load_manager(page, extension_id, {'read': []})
    page.locator('#import-toggle').click()
    page.fill('#import-text', json.dumps({'read': ['a'], 'titleCache': {'a': 'A'}, 'coverCache': {'a': 'https://cdn.novelupdates.com/images/a.jpg'}}))
    page.locator('#do-import').click()
    page.wait_for_timeout(500)
    assert get_storage(page, ['coverCache'])['coverCache'] == {'a': 'https://cdn.novelupdates.com/images/a.jpg'}
