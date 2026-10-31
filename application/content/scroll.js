'use strict';

// IntersectionObserver-based infinite scroll with retry logic. Each page
// download is a job in the browser-wide request queue (request-queue.js →
// background.js), which applies the shared pacing, slowdown after blocks and
// Retry-After. See wiki: request-pacing.

const SCROLL_BASE_DELAY_MS       = 2000;   // retry wait after a network or server error (doubles per attempt)
const SCROLL_BLOCK_DELAY_MS      = 4000;   // retry wait after a 403/429/503 (doubles per attempt)
const SCROLL_MAX_ERRORS          = 3;
const SCROLL_FILTER_DELAY_MS     = 1500;   // delay between back-to-back all-filtered page fetches
const SCROLL_KEEP_GOING_DELAY_MS = 5000;   // minimum wait after pressing "Keep going"
const SCROLL_MAX_RETRY_AFTER_MS  = 120000; // cap on an honoured Retry-After header

const scroll = {
  nextUrl: null,
  loading: false,
  exhausted: false,
  observer: null,
  pagesFetched: 0,       // successfully loaded pages after page 1
  scoreWaitGaveUp: new WeakSet(),  // cards a wait for scores gave up on: not waited for again
  totalFiltered: 0,
  consecutiveErrors: 0,  // independent from tag-fetch error counter
  waiting: false,        // a retry, filter delay or blocked state is pending — the observer is ignored
  notBefore: 0,          // Retry-After deadline, so retry countdowns show the real wait
  // The page being appended and the slugs from it already handled, so a retry
  // after a failure partway through neither duplicates nor recounts cards
  appendingUrl: null,
  appendedSlugs: new Set(),
};

function findNextLink(doc) {
  return (
    doc.querySelector('a[rel="next"]') ||
    doc.querySelector('a.next.page-numbers') ||
    doc.querySelector('.digg_pagination a.next') ||
    null
  );
}

function setupInfiniteScroll(config, container) {
  const nextLink = findNextLink(document);
  if (!nextLink) { scroll.exhausted = true; return; }
  scroll.nextUrl = nextLink.href;

  // Hide native pagination
  const paginationEl = nextLink.closest('.digg_pagination, .nav-links, [class*="pagination"]');
  if (paginationEl) paginationEl.style.display = 'none';

  // Sentinel
  const sentinel = document.createElement('div');
  sentinel.className = 'nuf-sentinel';
  container.after(sentinel);

  scroll.observer = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) fetchNextPage(config, container, sentinel);
  }, { rootMargin: '400px' });

  scroll.observer.observe(sentinel);
}

// Cards on screen now, including ones shown once their score settled
function _shownCount() {
  return document.querySelectorAll(`${PAGE_CONFIG.entrySelector}[data-nuf-slug]:not(.nuf-hidden-threshold)`).length;
}

function _renderLoading(sentinel, pageNum, suffix = '') {
  sentinel.className = 'nuf-sentinel-loading';
  sentinel.innerHTML = `<div class="nuf-spinner"><div class="nuf-spinner-dot"></div><div class="nuf-spinner-dot"></div><div class="nuf-spinner-dot"></div></div><div class="nuf-sentinel-loading-text">Page ${pageNum} · ${_shownCount()} shown${suffix}</div>`;
}

function _secs(ms) {
  return `${(Math.max(0, ms) / 1000).toFixed(1)} s`;
}

// Live line under the loading label showing how long this page is taking:
// first its estimated wait for a turn in the shared request queue, then the
// time the download itself has taken. timer.onWait is the queue's wait callback.
function _startLoadTimer(sentinel) {
  const line = document.createElement('div');
  line.className = 'nuf-sentinel-timing';
  sentinel.appendChild(line);
  const timer = { startedAt: Date.now(), loadStartedAt: null, turnStartsAt: null, behind: null };
  const render = () => {
    const now = Date.now();
    if (timer.loadStartedAt) {
      line.textContent = `Loading… ${_secs(now - timer.loadStartedAt)}`;
    } else if (timer.turnStartsAt !== null) {
      line.textContent = `Waiting for its turn · starts in about ${Math.max(1, Math.ceil((timer.turnStartsAt - now) / 1000))} s`;
    } else if (timer.behind) {
      line.textContent = `Waiting for another request to finish · ${_secs(now - timer.startedAt)}`;
    } else {
      line.textContent = `Loading… ${_secs(now - timer.startedAt)}`;
    }
  };
  timer.onWait = info => {
    if (info === null) {
      timer.loadStartedAt = Date.now();
    } else {
      // The field name comes from the queue's 'waiting' message (background.js):
      // the estimated wait in milliseconds
      timer.turnStartsAt = info.waitMs === null ? null : Date.now() + info.waitMs;
      timer.behind = info.behind;
    }
    render();
  };
  render();
  const interval = setInterval(render, 200);
  timer.stop = () => clearInterval(interval);
  return timer;
}

// Runs fetchNextPage after `ms`. scroll.waiting stays set until then, so the
// sentinel re-entering view cannot start the request early.
function _scheduleFetch(config, container, sentinel, ms, label) {
  scroll.waiting = true;
  sentinel.className = 'nuf-sentinel-loading';
  const deadline = Date.now() + ms;
  const render = () => {
    sentinel.textContent = label(Math.max(1, Math.ceil((deadline - Date.now()) / 1000)));
  };
  render();
  const tick = setInterval(render, 1000);
  setTimeout(() => {
    clearInterval(tick);
    scroll.waiting = false;
    fetchNextPage(config, container, sentinel);
  }, ms);
}

// The broker slows every requester after a block; this only keeps the retry
// countdown honest about a Retry-After deadline.
function _recordRetryAfter(retryAfterHeader) {
  const retryAfterMs = retryAfterHeader ? parseRetryAfter(retryAfterHeader) : null;
  if (retryAfterMs !== null) {
    scroll.notBefore = Math.max(scroll.notBefore, Date.now() + Math.min(retryAfterMs, SCROLL_MAX_RETRY_AFTER_MS));
  }
}

function _failureMessage(failure) {
  switch (failure.kind) {
    case 'blocked':   return `Page load blocked by Novel Updates (HTTP ${failure.status}).`;
    case 'challenge': return 'Novel Updates is showing a security check. Open the page to pass it, then press Keep going.';
    case 'http':      return `Page load failed (HTTP ${failure.status}).`;
    case 'network':   return 'Page load failed — network error. Check your connection, then press Keep going.';
    default:          return 'Page load failed (unexpected error).';
  }
}

function _showBlocked(config, container, sentinel, failure) {
  scroll.waiting = true;  // only the button may resume loading
  sentinel.className = 'nuf-sentinel-blocked';
  sentinel.innerHTML = '';
  const msg = document.createElement('div');
  msg.className = 'nuf-sentinel-blocked-msg';
  msg.textContent = _failureMessage(failure);
  const btn = document.createElement('button');
  btn.className = 'nuf-sentinel-retry-btn';
  btn.textContent = 'Keep going';
  btn.title = 'Try loading the next page again';
  sentinel.appendChild(msg);
  if (failure.kind === 'challenge') {
    // Passing the check in a real tab sets the clearance cookie that later
    // background requests reuse.
    const openBtn = document.createElement('button');
    openBtn.className = 'nuf-sentinel-open-btn';
    openBtn.textContent = 'Open page';
    openBtn.title = 'Open the blocked page in a new tab to pass the security check';
    openBtn.addEventListener('click', () => window.open(scroll.nextUrl, '_blank'));
    sentinel.appendChild(openBtn);
  }
  sentinel.appendChild(btn);
  btn.addEventListener('click', () => {
    btn.disabled = true;
    const wait = Math.max(jitter(SCROLL_KEEP_GOING_DELAY_MS), scroll.notBefore - Date.now());
    _scheduleFetch(config, container, sentinel, wait, secs => `Retrying in ${secs}s…`);
  });
}

function _handleFailure(config, container, sentinel, failure) {
  // A challenge page will not clear by retrying a background fetch — the user
  // has to pass it in a tab — so it goes straight to the Keep going state.
  if (failure.kind !== 'challenge') scroll.consecutiveErrors++;
  if (failure.kind === 'challenge' || scroll.consecutiveErrors >= SCROLL_MAX_ERRORS) {
    console.log(`[nuf] Page load failed (${failure.kind}) — waiting for Keep going`);
    scroll.consecutiveErrors = 0;
    _showBlocked(config, container, sentinel, failure);
    return;
  }
  const base = failure.kind === 'blocked' ? SCROLL_BLOCK_DELAY_MS : SCROLL_BASE_DELAY_MS;
  const delay = Math.max(
    jitter(base * Math.pow(2, scroll.consecutiveErrors - 1)),
    scroll.notBefore - Date.now(),
  );
  const attempt = scroll.consecutiveErrors;
  console.log(`[nuf] Page load failed (${failure.kind}, ${attempt}/${SCROLL_MAX_ERRORS}), retrying in ${delay}ms`);
  _scheduleFetch(config, container, sentinel, delay,
    secs => `Retrying in ${secs}s… (attempt ${attempt}/${SCROLL_MAX_ERRORS})`);
}

// Holds infinite scroll while a page's cards wait for their final scores:
// carries on once any of them is shown, none is still pending, or after
// SCORE_WAIT_MAX_MS so a stuck lookup can never stop scrolling for good.
const SCORE_WAIT_MAX_MS = 60000;
function _waitForScores(sentinel, entries) {
  scroll.waiting = true;
  sentinel.className = 'nuf-sentinel-loading';
  sentinel.textContent = 'Scoring novels…';
  const started = Date.now();
  const timer = setInterval(() => {
    const live = entries.filter(e => e.isConnected);
    const shown = live.some(e => !e.classList.contains('nuf-hidden-threshold'));
    const pending = live.some(e => e.classList.contains('nuf-score-pending'));
    if (pending && !shown && Date.now() - started < SCORE_WAIT_MAX_MS) return;
    // Gave up waiting: these cards are not waited for again, on this page or later ones
    if (pending && !shown) {
      for (const e of live) if (e.classList.contains('nuf-score-pending')) scroll.scoreWaitGaveUp.add(e);
    }
    clearInterval(timer);
    scroll.waiting = false;
    sentinel.className = 'nuf-sentinel';
    sentinel.textContent = '';
    scroll.observer.unobserve(sentinel);
    scroll.observer.observe(sentinel);
  }, 500);
}

async function fetchNextPage(config, container, sentinel) {
  if (scroll.loading || scroll.waiting || scroll.exhausted) return;
  // Nothing on the page is shown yet because every card is still being scored
  // (the first page with a threshold set): wait for them, not for page after page
  const pending = [...container.querySelectorAll(`${config.entrySelector}.nuf-score-pending`)]
    .filter(e => !scroll.scoreWaitGaveUp.has(e));
  if (pending.length && !container.querySelector(`${config.entrySelector}[data-nuf-slug]:not(.nuf-hidden-threshold)`)) {
    _waitForScores(sentinel, pending);
    return;
  }
  scroll.loading = true;
  const pageNum = scroll.pagesFetched + 2;  // page 1 is the page the user opened
  _renderLoading(sentinel, pageNum);
  const loadTimer = _startLoadTimer(sentinel);

  let failure         = null;  // { kind: 'blocked' | 'challenge' | 'http' | 'network' | 'error', status }
  let willRecurse     = false;
  let appendedEntries = [];

  try {
    // The page download is one job in the browser-wide request queue, which
    // decides when it runs. A security check served with HTTP 200 is
    // reported to the queue as a block.
    const page = await requestQueue.request('scroll', scroll.nextUrl, async job => {
      let resp;
      try {
        resp = await fetch(job.url, { credentials: 'include', headers: FETCH_HEADERS });
      } catch {
        return { outcome: 'error', value: { failure: { kind: 'network' } } };
      }
      if (!resp.ok) {
        const blocking = BLOCKING_STATUSES.has(resp.status);
        const retryAfter = resp.headers.get('Retry-After');
        return {
          outcome: blocking ? 'blocked' : 'error', status: resp.status, retryAfter,
          value: { failure: { kind: blocking ? 'blocked' : 'http', status: resp.status }, retryAfter },
        };
      }
      const doc = new DOMParser().parseFromString(await resp.text(), 'text/html');
      // A security check has no cards and no next link, so without this it reads as the last page
      if (!doc.querySelectorAll(config.entrySelector).length && _isChallengePage(doc)) {
        return { outcome: 'blocked', status: resp.status, value: { failure: { kind: 'challenge' } } };
      }
      return { outcome: 'ok', status: resp.status, value: { doc } };
    }, loadTimer.onWait);

    if (!page) {
      failure = { kind: 'network' };
    } else if (page.failure) {
      failure = page.failure;
      if (failure.kind === 'blocked') _recordRetryAfter(page.retryAfter);
    } else {
      const doc = page.doc;
      const newEntries = doc.querySelectorAll(config.entrySelector);
      const nextLink = findNextLink(doc);
      const nextUrl = nextLink ? nextLink.href : null;
      let appended = 0;
      if (scroll.appendingUrl !== scroll.nextUrl) {
        scroll.appendingUrl = scroll.nextUrl;
        scroll.appendedSlugs = new Set();
      }

      newEntries.forEach(entry => {
        const imported = document.adoptNode(entry);
        const linkEl = imported.querySelector(config.titleLinkSelector);
        if (!linkEl) return;
        const slug = slugFromHref(linkEl.href);
        if (!slug) return;
        // Handled by an earlier attempt at this page: skip without counting again
        if (scroll.appendedSlugs.has(slug)) return;
        // Left half-added by a failed attempt at this page: remove and redo it
        const partial = container.querySelector(`[data-nuf-slug="${slug}"][data-nuf-appending]`);
        if (partial) (partial._nufWrapper || partial).remove();
        imported.dataset.nufSlug = slug;
        imported._nufOriginalIdx = _entryIdx++;
        const title = linkEl.textContent.trim();
        // Captured before filtering, the same as processEntry on page 1
        captureMetadata(imported, slug, title, config);

        if (shouldFilter(slug, imported, config)) {
          scroll.totalFiltered++;
          scroll.appendedSlugs.add(slug);
          return;
        }

        if (container.querySelector(`[data-nuf-slug="${slug}"]`)) {
          scroll.totalFiltered++;
          scroll.appendedSlugs.add(slug);
          return;
        }

        imported.dataset.nufAppending = '1';
        container.appendChild(imported);
        injectControls(imported, slug, title);
        if (state.toRead.has(slug)) {
          imported.classList.add('nuf-to-read');
          imported._nufWrapper?.classList.add('nuf-to-read');
        }
        // Tag lookups, the same as page-1 cards in processEntry
        enqueueWhenVisible(imported, slug);
        if (state.expandSynopsis) expandSynopsisOnEntry(imported);
        delete imported.dataset.nufAppending;
        scroll.appendedSlugs.add(slug);
        appendedEntries.push(imported);
        appended++;
      });

      // Advanced only after the cards are in, so a failure above refetches this page
      scroll.nextUrl = nextUrl;
      if (!nextUrl) scroll.exhausted = true;
      scroll.pagesFetched++;
      scroll.consecutiveErrors = 0;
      willRecurse = appended === 0 && !scroll.exhausted;
    }
  } catch (err) {
    console.log('[nuf] Page load failed:', err);
    failure = { kind: 'error' };
  } finally {
    loadTimer.stop();
    scroll.loading = false;
  }

  if (failure ? appendedEntries.length > 0 : !willRecurse) applyRecommendations(config);

  if (failure) {
    _handleFailure(config, container, sentinel, failure);
    return;
  }

  if (willRecurse) {
    _scheduleFetch(config, container, sentinel, jitter(SCROLL_FILTER_DELAY_MS), () => 'Skipping filtered page…');
    return;
  }

  if (scroll.exhausted) {
    const total = document.querySelectorAll(`${config.entrySelector}:not(.nuf-hidden-threshold)`).length;
    sentinel.className = 'nuf-sentinel-done';
    sentinel.textContent = `End of results · ${total} shown${scroll.totalFiltered > 0 ? ` · ${scroll.totalFiltered} filtered` : ''}`;
    scroll.observer?.disconnect();
    return;
  }

  // Re-observe so the observer re-fires if the sentinel is still in viewport
  // (IntersectionObserver only fires on transition, not on persistent intersection).
  // If all newly appended cards were hidden by score threshold, delay re-observation
  // to avoid immediate re-fire and rapid back-to-back page fetches.
  const anyVisible = appendedEntries.some(e => !e.classList.contains('nuf-hidden-threshold'));
  const anyPending = appendedEntries.some(e => e.classList.contains('nuf-score-pending'));
  if (!anyVisible && anyPending) {
    // Still being scored, not filtered: wait for the scores rather than
    // loading page after page while the lookups queue up behind them
    _waitForScores(sentinel, appendedEntries);
  } else if (anyVisible) {
    sentinel.className = 'nuf-sentinel';
    sentinel.textContent = '';
    setTimeout(() => {
      scroll.observer.unobserve(sentinel);
      scroll.observer.observe(sentinel);
    }, 0);
  } else {
    scroll.waiting = true;
    sentinel.className = 'nuf-sentinel-loading';
    sentinel.textContent = 'Skipping filtered page…';
    setTimeout(() => {
      scroll.waiting = false;
      sentinel.className = 'nuf-sentinel';
      sentinel.textContent = '';
      scroll.observer.unobserve(sentinel);
      scroll.observer.observe(sentinel);
    }, jitter(SCROLL_FILTER_DELAY_MS));
  }
}
