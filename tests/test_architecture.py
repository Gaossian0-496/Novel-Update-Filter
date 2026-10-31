import re
from pathlib import Path
import pytest
from playwright.sync_api import expect
from helpers import load_listing, get_storage, set_storage

APPLICATION_DIR = Path(__file__).parent.parent / 'application'


def test_novel_identity_key_is_url_slug_not_full_url(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    page.locator('.nuf-controls').first.locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=5000)

    result = get_storage(page, 'read')
    assert 'release-that-witch' in result['read']
    assert all('/' not in s for s in result['read'])  # slugs only, no path separators


def test_each_bucket_persisted_under_correct_storage_key(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    controls = page.locator('.nuf-controls')

    # Read
    controls.nth(0).locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=5000)
    assert 'release-that-witch' in get_storage(page, 'read')['read']

    # Not Finished
    controls.nth(0).locator('.nuf-btn[data-nuf-btn="nf"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=5000)
    assert 'against-the-gods' in get_storage(page, 'notFinished')['notFinished']

    # Not Interested
    controls.nth(0).locator('.nuf-btn[data-nuf-btn="ni"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(1, timeout=5000)
    assert 'i-shall-seal-the-heavens' in get_storage(page, 'notInterested')['notInterested']


def test_controls_element_is_previous_sibling_of_card(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    result = page.evaluate('''() => {
        const cards = document.querySelectorAll('.search_main_box_nu');
        return Array.from(cards).every(card =>
            card.previousElementSibling?.classList.contains('nuf-controls')
        );
    }''')
    assert result is True


def test_badge_text_is_always_empty(page, sw):
    """Badge text must be empty — r/nf/ni format was removed because Chrome clips long badge strings."""
    load_listing(page)
    page.wait_for_selector('.nuf-controls')

    controls = page.locator('.nuf-controls')
    controls.nth(0).locator('.nuf-btn[data-nuf-btn="read"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=5000)
    controls.nth(0).locator('.nuf-btn[data-nuf-btn="nf"]').click()
    expect(page.locator('.search_main_box_nu')).to_have_count(2, timeout=5000)

    badge_text = sw.evaluate('() => new Promise(r => chrome.action.getBadgeText({}, r))')
    assert badge_text == ''


def test_storage_change_removes_card_live_without_reload(page):
    load_listing(page)
    page.wait_for_selector('.nuf-controls')
    expect(page.locator('.search_main_box_nu')).to_have_count(4)

    # Simulate an external write — triggers storage.onChanged in the content script
    set_storage(page, {'notInterested': ['lord-of-the-mysteries']})

    expect(page.locator('.search_main_box_nu')).to_have_count(3, timeout=5000)
    titles = page.locator('.search_title a').all_text_contents()
    assert 'Lord of the Mysteries' not in titles


def test_manage_button_opens_manager_as_new_full_tab(page, ctx, extension_id):
    page.goto(f'chrome-extension://{extension_id}/popup.html')

    with ctx.expect_event('page') as event_info:
        page.locator('#manage-btn').click()

    new_page = event_info.value
    new_page.wait_for_load_state('domcontentloaded')
    assert new_page.url.endswith('manager.html')
    # Close it: an open manager tab joins the shared request queue and could run
    # later tests' lookups, which those tests mock only on their own page.
    new_page.close()


# ── defect-048: welcome page + pin prompt on first install ──────────────────

def test_defect048_welcome_html_exists_with_pin_instructions():
    """A welcome.html must exist with pinning instructions referencing both the
    puzzle-piece icon and the pin action so new users discover how to pin."""
    welcome = APPLICATION_DIR / 'welcome.html'
    assert welcome.exists(), f'{welcome} does not exist'
    body = welcome.read_text(encoding='utf-8').lower()
    assert 'puzzle' in body, 'welcome.html should mention the puzzle-piece icon'
    assert 'pin' in body, 'welcome.html should mention the pin action'


def test_defect048_background_opens_welcome_tab_on_install():
    """background.js must open welcome.html via chrome.tabs.create when
    chrome.runtime.onInstalled fires with reason='install'."""
    bg = (APPLICATION_DIR / 'background.js').read_text(encoding='utf-8')
    assert 'chrome.tabs.create' in bg, 'background.js does not call chrome.tabs.create'
    assert 'welcome.html' in bg, "background.js does not reference 'welcome.html'"
    # Must be gated on the install reason so Chrome auto-updates don't reopen the tab.
    assert re.search(r"reason\s*===\s*['\"]install['\"]", bg), (
        'tabs.create must be guarded by details.reason === "install" so updates do not reopen the welcome tab'
    )


# ── defect-051: welcome page Got-it broken + needs visual examples ──────────

def test_defect051_welcome_close_handler_in_external_js():
    """The close handler must live in an external JS file referenced via
    <script src="..."> — MV3's default CSP (`script-src 'self'`) blocks
    inline <script> blocks AND onclick="" attributes silently. An inline
    handler in welcome.html would not execute on the live page.

    The external file must also use the chrome.tabs.* API to close the tab
    (bare window.close() is unreliable on chrome.tabs.create-opened tabs).
    """
    welcome = (APPLICATION_DIR / 'welcome.html').read_text(encoding='utf-8')
    # External script reference — not an inline <script> block.
    assert re.search(r'<script\s+src\s*=\s*["\'][^"\']+\.js["\']', welcome), (
        'welcome.html must reference an external .js file via <script src="…"> '
        '(MV3 CSP blocks inline scripts)'
    )
    # No inline onclick attribute on the close button.
    assert not re.search(r'<button[^>]+onclick\s*=', welcome), (
        'welcome.html still uses an onclick="" attribute — also blocked by MV3 CSP'
    )
    # The external script file must actually exist and use chrome.tabs.*
    welcome_js_path = APPLICATION_DIR / 'welcome.js'
    assert welcome_js_path.exists(), 'application/welcome.js does not exist'
    welcome_js = welcome_js_path.read_text(encoding='utf-8')
    assert 'chrome.tabs.remove' in welcome_js or 'chrome.tabs.getCurrent' in welcome_js, (
        'welcome.js should use chrome.tabs.* APIs to close the tab reliably'
    )


def test_defect051_welcome_close_button_has_id():
    """The close button must have an id (so the JS handler can target it)
    rather than relying on inline onclick."""
    welcome = (APPLICATION_DIR / 'welcome.html').read_text(encoding='utf-8')
    # Some id attribute on a button — exact id name not enforced but the
    # button must be addressable by getElementById.
    assert re.search(r'<button[^>]+id\s*=\s*["\'][a-zA-Z0-9_-]+["\']', welcome), (
        'welcome.html close button needs an id attribute so JS can attach a handler'
    )


def test_defect053_exportable_keys_match_between_manager_and_popup():
    """manager.js and popup.js each declare an EXPORTABLE_KEYS constant. They
    must stay in lockstep — adding a key to one and forgetting the other would
    make the two surfaces produce different export payloads, breaking the
    'export-from-either, import-into-either' contract."""
    def extract_keys(path):
        src = (APPLICATION_DIR / path).read_text(encoding='utf-8')
        m = re.search(r'const EXPORTABLE_KEYS = \[(.*?)\];', src, re.DOTALL)
        assert m, f'{path} has no EXPORTABLE_KEYS const'
        return tuple(re.findall(r"'([^']+)'", m.group(1)))

    mgr_keys = extract_keys('manager.js')
    pop_keys = extract_keys('popup.js')
    assert mgr_keys == pop_keys, (
        f'EXPORTABLE_KEYS drift between manager.js and popup.js.\n'
        f'  manager: {mgr_keys}\n'
        f'  popup:   {pop_keys}'
    )


def test_defect051_welcome_features_have_visual_examples():
    """The 'What it does' section must include visual examples (inline SVG or
    a feature-visual div) alongside each feature description — not just text bullets."""
    welcome = (APPLICATION_DIR / 'welcome.html').read_text(encoding='utf-8')
    # Either inline SVGs (one per feature) or named visual containers exist
    svg_count = len(re.findall(r'<svg\b', welcome))
    visual_div_count = len(re.findall(r'class\s*=\s*["\'][^"\']*feature-visual', welcome))
    visual_total = svg_count + visual_div_count
    # Six features in the current copy — expect a visual for each (or close).
    assert visual_total >= 5, (
        f'welcome.html has {svg_count} inline SVGs and {visual_div_count} '
        f'feature-visual divs; expected at least 5 visual examples '
        f'(one per main feature). Currently the section is text-only.'
    )


# ── Scroll and prefetch ordering contracts ──────────────────────────────────

def test_scroll_advances_next_url_only_after_cards_are_appended():
    """If appending a page's cards throws, the retry must refetch the same page.
    So scroll.nextUrl may only be advanced after the append loop."""
    src = (APPLICATION_DIR / 'content' / 'scroll.js').read_text(encoding='utf-8')
    loop_pos = src.index('newEntries.forEach(')
    advance_pos = src.index('scroll.nextUrl = nextUrl')
    assert advance_pos > loop_pos


def test_every_novel_updates_request_goes_through_the_broker():
    """Every request to the site runs as a job in the one browser-wide queue: scroll and
    Start reading as one-off requests, tag/cover lookups and manager titles as handled jobs."""
    content = APPLICATION_DIR / 'content'
    prefetch = (content / 'prefetch.js').read_text(encoding='utf-8')
    scroll = (content / 'scroll.js').read_text(encoding='utf-8')
    manager = (APPLICATION_DIR / 'manager.js').read_text(encoding='utf-8')
    assert "requestQueue.request('scroll'" in scroll
    assert "requestQueue.request('user'" in prefetch
    assert "requestQueue.handle('prefetch'" in prefetch and "requestQueue.handle('cover'" in prefetch
    assert "requestQueue.handle('manager'" in manager
    everything = prefetch + scroll + manager
    assert 'brokeredFetch' not in everything and 'nuf-slot' not in everything
    # The only fetch calls are the ones a queue job runs
    assert prefetch.count('await fetch(') == 1 and 'async function fetchSeriesDoc' in prefetch
    assert scroll.count('await fetch(') == 1
    assert manager.count('await fetch(') == 1


# ── defect-055: scroll pacing ───────────────────────────────────────────────

def test_defect055_scroll_waits_are_jittered():
    """Every scroll wait goes through jitter() so request timing is not a fixed pattern."""
    src = (APPLICATION_DIR / 'content' / 'scroll.js').read_text(encoding='utf-8')
    for constant in ('SCROLL_FILTER_DELAY_MS', 'SCROLL_KEEP_GOING_DELAY_MS'):
        assert f'jitter({constant}' in src, f'{constant} must be jittered'
    assert 'jitter(base * Math.pow(2' in src, 'retry backoff must be jittered'
    background = (APPLICATION_DIR / 'background.js').read_text(encoding='utf-8')
    assert '(0.8 + nextJitter * 0.4)' in background, 'the queue gap must be jittered'


def test_defect055_scroll_reads_retry_after():
    """Scroll honours the Retry-After header, parsed by the shared parseRetryAfter helper."""
    src = (APPLICATION_DIR / 'content' / 'scroll.js').read_text(encoding='utf-8')
    assert "headers.get('Retry-After')" in src
    assert 'parseRetryAfter(' in src


# ── Request pacing (2026-10-25 loading review) ──────────────────────────────

def test_prefetch_cooldown_escalates():
    """Repeated blocks pause background lookups for 30 s, then 60 s, then 120 s — in the queue."""
    background = (APPLICATION_DIR / 'background.js').read_text(encoding='utf-8')
    prefetch = (APPLICATION_DIR / 'content' / 'prefetch.js').read_text(encoding='utf-8')
    assert 'const QUEUE_COOLDOWNS_MS = [30000, 60000, 120000];' in background
    assert 'TAG_FETCH_' not in prefetch, 'prefetch no longer keeps its own backoff'


def test_prefetch_waits_while_tab_is_hidden():
    """Listing tabs report their visibility to the queue, and the queue runs background
    jobs only in a visible page. (Playwright reports every page as visible, so the page
    side is source-checked; the queue side has a behaviour test in test_ux.py.)"""
    client = (APPLICATION_DIR / 'content' / 'request-queue.js').read_text(encoding='utf-8')
    init = (APPLICATION_DIR / 'content' / 'init.js').read_text(encoding='utf-8')
    background = (APPLICATION_DIR / 'background.js').read_text(encoding='utf-8')
    assert "document.addEventListener('visibilitychange'" in client
    assert "type: 'visible', visible: !document.hidden" in client
    assert 'requestQueue.trackVisibility()' in init
    assert 'if (!background || client.visible) return client;' in background


def test_content_scripts_declare_each_global_name_once():
    """The content scripts share one global scope: a name declared at top level in two files
    silently replaces the earlier one (function) or stops the later file loading (const/let)."""
    seen, duplicates = {}, []
    for path in sorted((APPLICATION_DIR / 'content').glob('*.js')):
        for name in re.findall(r'^(?:async\s+)?(?:function\s*\*?\s*|const\s+|let\s+|var\s+|class\s+)([A-Za-z_$][\w$]*)',
                               path.read_text(encoding='utf-8'), re.M):
            if name in seen and seen[name] != path.name:
                duplicates.append(f'{name}: {seen[name]} and {path.name}')
            seen.setdefault(name, path.name)
    assert duplicates == [], duplicates


def test_extension_uses_no_list_or_site_acronyms():
    """Code, comments and UI text spell out Novel Updates and the list names (project rule: no acronyms)."""
    offenders = []
    for path in sorted(APPLICATION_DIR.rglob('*')):
        if path.suffix not in ('.js', '.html', '.css'):
            continue
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if re.search(r'\b(NU|NI|NF|ETA)\b', line):
                offenders.append(f'{path.relative_to(APPLICATION_DIR)}:{n}')
    assert offenders == [], offenders


def test_manifest_description_fits_the_store_summary():
    """The manifest description is the store's one-line summary: at most 132 characters, saying what it does."""
    import json
    description = json.loads((APPLICATION_DIR / 'manifest.json').read_text(encoding='utf-8'))['description']
    assert len(description) <= 132, len(description)
    for phrase in ('Hide', 'suggestions', 'To Read', 'Novel Updates'):
        assert phrase in description, (phrase, description)


def test_content_css_has_no_rules_for_classes_nothing_uses():
    """Every nuf- class styled in content.css is added by a content script, so old designs leave no dead rules."""
    css = (APPLICATION_DIR / 'content.css').read_text(encoding='utf-8')
    scripts = '\n'.join(p.read_text(encoding='utf-8') for p in (APPLICATION_DIR / 'content').glob('*.js'))
    styled = set(re.findall(r'\.(nuf-[a-z0-9-]+)', css))
    dead = sorted(c for c in styled if c not in scripts)
    assert dead == [], dead


def _queue_client():
    return (APPLICATION_DIR / 'content' / 'request-queue.js').read_text(encoding='utf-8')


def test_queue_client_does_not_rerun_a_job_already_running():
    """A one-off job the queue gives up on while it is still running is not fetched a second time."""
    client = _queue_client()
    assert 'single.running = true;' in client
    assert 'if (single && !single.running)' in client


def test_queue_client_does_not_hand_over_a_running_job_after_a_restart():
    """A job that has started running is no longer handed to a restarted queue, which would run it a second time."""
    client = _queue_client()
    run_branch = client[client.index("if (msg.type === 'run') {"):client.index("} else if (msg.type === 'waiting')")]
    assert 'pending.delete(msg.key);' in run_branch.split('try {')[0], 'remove the job from pending before running it'


def test_queue_client_waits_for_reconnect_instead_of_fetching_directly():
    """Between a service worker restart and the reconnect, one-off requests wait for the queue."""
    client = _queue_client()
    assert 'if (gone) {' in client
    assert 'if (gone || !port)' not in client


def test_queue_client_runs_waiting_requests_when_the_extension_is_reloaded():
    """Requests still waiting when the extension is reloaded run directly instead of hanging."""
    client = _queue_client()
    assert 'function runStranded()' in client
    assert client.count('runStranded()') >= 2


def _background():
    return (APPLICATION_DIR / 'background.js').read_text(encoding='utf-8')


def test_queue_drops_failed_jobs_nobody_wants():
    """A job cancelled mid-fetch that then fails is settled, not kept for a retry that can never run."""
    assert 'if (retryable && job.owners.size && job.attempts < job.maxAttempts' in _background()


def test_queue_cooldown_count_resets_after_full_recovery():
    """Once pacing is fully relaxed, earlier cooldowns no longer count towards stopping lookups."""
    assert 'if (!pace.paceDelay) pace.cooldownCycles = 0;' in _background()


def test_queue_abandon_timer_counts_from_when_the_job_started():
    """Unrelated queue events do not push back the timeout of a job that never reports back."""
    background = _background()
    assert 'now - running.startedAt >= QUEUE_RUN_TIMEOUT_MS' in background
    assert 'setTimeout(pump, running.startedAt + QUEUE_RUN_TIMEOUT_MS - now)' in background


def test_queue_closing_a_tab_counts_its_dropped_lookups_as_done():
    """Lookups dropped with a closed tab advance the progress bar so it does not stick."""
    background = _background()
    start = background.index('function forgetClient(client)')
    body = background[start:background.index('\n}\n', start)]
    assert 'progress.done++' in body and 'writeProgress(true)' in body


def test_queue_writes_progress_once_per_batch():
    """Adding a batch of lookups writes the progress bar once, not once per job."""
    background = _background()
    start = background.index('function addJob(client, spec)')
    assert 'writeProgress' not in background[start:background.index('\n}\n', start)]
    assert "if (msg.jobs.some(j => j.kind === 'prefetch' || j.kind === 'cover')) writeProgress();" in background


def test_unchanged_novel_meta_save_does_not_leave_cross_tab_updates_ignored():
    """A save that changes nothing fires no onChanged event, so the suppress flag is cleared on a timer."""
    storage = (APPLICATION_DIR / 'content' / 'storage.js').read_text(encoding='utf-8')
    assert 'setTimeout(() => { _suppressNovelMetaListener = false; }, NOVEL_META_SUPPRESS_MS);' in storage


def test_queue_client_port_errors_are_not_reported_as_storage_failures():
    """Only a lost extension context goes to noteChromeError; other port errors are logged as such."""
    client = _queue_client()
    assert "console.log('[nuf] request queue port error'" in client


# ── defect-057: storage write failures ───────────────────────────────────────

def test_defect057_manifest_requests_unlimited_storage():
    """Without unlimitedStorage, chrome.storage.local caps at 10 MB and a full novelMeta exceeds it."""
    import json
    manifest = json.loads((APPLICATION_DIR / 'manifest.json').read_text(encoding='utf-8'))
    assert 'unlimitedStorage' in manifest['permissions']


def test_defect057_content_scripts_never_call_storage_set_directly():
    """Every content-script write goes through storageSet/storageRemove, which never reject."""
    offenders = []
    for path in sorted((APPLICATION_DIR / 'content').glob('*.js')):
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            code = line.split('//')[0]
            if re.search(r'chrome\.storage\.local\.(set|remove)\(', code) and 'then(() => null, noteChromeError)' not in code:
                offenders.append(f'{path.name}:{n}')
    assert offenders == [], offenders


# ── defect-024 guard: Chrome lists console.warn / console.error as extension errors ──

def test_no_console_warn_or_error_for_expected_conditions():
    """Expected conditions (failed fetches, logged-out pages, blocks) log with console.log.
    console.error is reserved for the one genuine failure: series-page controls cannot inject."""
    allowed = {('series.js', "console.error('[nuf] series page title element not found")}
    offenders = []
    for path in sorted(APPLICATION_DIR.rglob('*.js')):
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if re.search(r'console\.(warn|error)\(', line):
                if not any(path.name == f and snippet in line for f, snippet in allowed):
                    offenders.append(f'{path.name}:{n}')
    assert offenders == [], offenders


def test_welcome_page_has_no_abbreviated_bucket_names():
    """Project rule: write "Not Finished", never "NotFin" / "Not Fin." — including inside SVG mockups."""
    html = (APPLICATION_DIR / 'welcome.html').read_text(encoding='utf-8')
    for abbreviation in ('NotFin', 'NotInt', 'ToRead', 'NOT FIN', 'NOT INT', 'Not Fin.', 'Not Int.', '>Gen<'):
        assert abbreviation not in html, abbreviation


def test_extension_pages_share_one_stylesheet():
    """Popup, manager, queue and welcome pages all load ui.css, the single source of colours."""
    for page in ('popup.html', 'manager.html', 'queue.html', 'welcome.html'):
        html = (APPLICATION_DIR / page).read_text(encoding='utf-8')
        assert '<link rel="stylesheet" href="ui.css">' in html, page
        assert '--read:' not in html, f'{page} redefines the shared colour tokens'
