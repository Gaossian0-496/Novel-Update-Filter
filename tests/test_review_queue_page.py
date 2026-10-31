"""Code-review fixes: the Request queue page keeps retry countdowns ticking and
explains jobs waiting for a visible tab."""

from playwright.sync_api import expect

FAKE = '''(extra) => {
    const t = Date.now();
    const job = (over) => ({ key: 'k' + Math.random(), kind: 'prefetch', url: 'https://www.novelupdates.com/series/x/',
                             since: t - 2000, attempts: 1, retryAt: null, paused: false, ...over });
    return {
        now: t, baseGapMs: 1000, paceDelay: 0, successes: 0, recoveryAfter: 5,
        backgroundStopped: false, backgroundHoldUntil: 0, notBefore: 0, lastGrantAt: t - 5000,
        inFlight: null, history: [], waitingCounts: { prefetch: extra.length }, waitingTotal: extra.length,
        waiting: extra.map(job), progress: { done: 0, total: 0 },
    };
}'''


def _open_queue_page(page, extension_id):
    page._sw.evaluate('() => globalThis.nufBroker.reset()')
    page.goto(f'chrome-extension://{extension_id}/queue.html')
    expect(page.locator('#status')).to_have_attribute('data-state', 'idle')


def test_queue_page_retry_countdown_keeps_ticking(page, extension_id):
    """A waiting job's "retry in" countdown counts down between snapshots instead of freezing."""
    _open_queue_page(page, extension_id)
    page.evaluate(f'''() => {{
        snapshot = ({FAKE})([{{ retryAt: Date.now() + 8000 }}]);
        clockOffset = 0;
        render();
    }}''')
    when = page.locator('#waiting .when')
    first = when.text_content()
    assert first.startswith('retry 2 in'), first
    page.wait_for_timeout(1300)
    later = when.text_content()
    assert later.startswith('retry 2 in') and later != first, (first, later)


def test_queue_page_explains_jobs_waiting_for_a_visible_tab(page, extension_id):
    """When every waiting job belongs to a hidden tab, the status says so instead of counting down from zero."""
    _open_queue_page(page, extension_id)
    status = page.evaluate(f'''() => {{
        snapshot = ({FAKE})([{{ paused: true }}, {{ paused: true }}]);
        clockOffset = 0;
        render();
        return {{ headline: document.getElementById('status-headline').textContent,
                  row: document.querySelector('#waiting .when').textContent }};
    }}''')
    assert 'visible' in status['headline'].lower(), status
    assert 'Next request in' not in status['headline'], status
    assert 'tab' in status['row'].lower(), status


def test_queue_page_runnable_job_still_shows_next_request_time(page, extension_id):
    """A job that can run still gets the "Next request in" countdown."""
    _open_queue_page(page, extension_id)
    headline = page.evaluate(f'''() => {{
        snapshot = ({FAKE})([{{}}]);
        clockOffset = 0;
        render();
        return document.getElementById('status-headline').textContent;
    }}''')
    assert headline.startswith('Next request in'), headline
