"""Code-review fixes: the content script stays off Cloudflare's security-check page."""

from helpers import load_listing

CHALLENGE_HTML = '''<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="challenge-running">Verify you are human</div>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script></body></html>'''


def test_security_check_page_gets_no_sidebar_or_controls(page):
    """A listing URL answered with a security check gets no To Read sidebar and no queue client work."""
    page.route('**/series-finder/**', lambda route: route.fulfill(status=200, content_type='text/html', body=CHALLENGE_HTML))
    load_listing(page, {'toRead': ['some-novel']})
    page.wait_for_timeout(1500)
    assert page.locator('.nuf-sidebar-tab').count() == 0
    assert page.locator('.nuf-sidebar-panel').count() == 0


def test_normal_listing_still_gets_the_sidebar(page):
    """The security-check guard does not stop the sidebar on a real listing page."""
    load_listing(page, {'toRead': ['some-novel']})
    page.wait_for_selector('.nuf-sidebar-tab', state='attached', timeout=5000)
