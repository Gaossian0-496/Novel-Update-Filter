'use strict';

// Tag prefetch and series-page lookups. This file decides WHAT to look up
// (bucketed novels, cards near the viewport, missing sidebar covers) and how
// to read a series page; the browser-wide request queue (request-queue.js →
// background.js) decides WHEN each request runs, retries and cooldowns.

// Debounce score recomputes triggered by tag-fetch success. A long run of
// lookups can produce many back-to-back successes; coalescing them into one
// applyRecommendations pass still renders chips within ~150 ms.
let _recomputeScheduled = false;
function scheduleRecompute() {
  if (_recomputeScheduled || !_activeConfig) return;
  _recomputeScheduled = true;
  setTimeout(() => {
    _recomputeScheduled = false;
    if (_activeConfig) applyRecommendations(_activeConfig, true);
    // A rescore can hide more cards below the threshold
    scheduleHiddenCardLookups();
  }, 150);
}

// Debounce To Read sidebar redraws after lookups: a run of lookups redraws it
// once. The redraw waits while a sidebar "Start reading" is looking up its
// first chapter, because redrawing would replace that button mid-lookup.
const SIDEBAR_RENDER_DELAY_MS = 200;
let _sidebarRenderTimer = null;
function scheduleSidebarRender() {
  clearTimeout(_sidebarRenderTimer);
  _sidebarRenderTimer = setTimeout(() => {
    _sidebarRenderTimer = null;
    if (document.querySelector('.nuf-sidebar-panel [data-fetching]')) {
      scheduleSidebarRender();
      return;
    }
    renderToReadSidebar();
  }, SIDEBAR_RENDER_DELAY_MS);
}

const FETCH_HEADERS = {
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
  'Accept-Language': navigator.language || 'en-US',
  'Upgrade-Insecure-Requests': '1',
  'Cache-Control': 'no-cache',
};

const BLOCKING_STATUSES = new Set([403, 429, 503]);

function jitter(ms) {
  // ±20% random jitter to avoid predictable timing patterns
  return Math.round(ms * (0.8 + Math.random() * 0.4));
}

function parseRetryAfter(headerValue) {
  // Retry-After is either a delay-seconds integer or an HTTP-date string
  const seconds = parseInt(headerValue, 10);
  if (!isNaN(seconds)) return seconds * 1000;
  const date = Date.parse(headerValue);
  if (!isNaN(date)) return Math.max(0, date - Date.now());
  return null;
}

function _isLoginPage(doc) {
  // novelupdates.com returns a 200 with the login form when the session cookie
  // is missing/expired. Detecting this prevents counting the result as a
  // successful "no tags found" fetch.
  return !!doc.querySelector('#loginform, .login-form, form[action*="login"], form[id*="login"]');
}

// Title, cover and synopsis from a series page — what the To Read sidebar shows.
// Used on the live series page (series.js) and on fetched ones (below). The
// cover is '' when the page has none, so the novel is not refetched for it.
function readSeriesSidebarMeta(doc) {
  const titleEl = doc.querySelector('h1.seriestitlenu') || doc.querySelector('.seriestitlenu');
  const imgEl = doc.querySelector('.seriesimg img, .serieseditimg img');
  const descEl = doc.querySelector('#editdescription');
  const paragraphs = descEl ? [...descEl.querySelectorAll('p')].map(p => p.textContent.trim()).filter(Boolean) : [];
  return {
    t: titleEl?.textContent.trim() || null,
    img: [imgEl?.dataset.src, imgEl?.getAttribute('src')].find(isRealCoverUrl) || '',
    desc: (paragraphs.length ? paragraphs.join(' ') : descEl?.textContent || '').replace(/\s+/g, ' ').trim(),
  };
}

// A To Read novel whose cover has never been looked up (marked from the
// manager, a bucket-only import, or before covers were captured).
function needsSidebarMeta(slug) {
  return state.toRead.has(slug) && state.novelMeta[slug]?.img === undefined;
}

function seriesUrl(slug) {
  return `https://www.novelupdates.com/series/${slug}/`;
}

// Fetches one series page. Only called while the queue has given this page
// its turn. Returns { doc } or { status, retryAfter } (status 0 = network error).
async function fetchSeriesDoc(url) {
  let resp;
  try {
    resp = await fetch(url, { credentials: 'include', headers: FETCH_HEADERS });
  } catch {
    return { status: 0 };
  }
  if (!resp.ok) return { status: resp.status, retryAfter: resp.headers.get('Retry-After') };
  const doc = new DOMParser().parseFromString(await resp.text(), 'text/html');
  // A security check served with HTTP 200 is a block, not a series page with no
  // tags or cover: reading it as one would store img = '' for good
  if (!doc.querySelector('.seriestitlenu') && _isChallengePage(doc)) return { status: 503 };
  if (_isLoginPage(doc)) return { status: 401 };
  return { doc, status: resp.status };
}

// How the queue should treat a fetchSeriesDoc result
function queueOutcome(result) {
  if (result.doc) return { outcome: 'ok', status: result.status };
  if (BLOCKING_STATUSES.has(result.status)) return { outcome: 'blocked', status: result.status, retryAfter: result.retryAfter };
  if (result.status === 404 || result.status === 401) {
    if (result.status === 401) console.log('[nuf] Series page returned the login form — logged out; not retrying');
    return { outcome: 'gone', status: result.status };
  }
  return { outcome: 'error', status: result.status || null };
}

function _lastChapterPage(doc) {
  let last = 1;
  for (const link of doc.querySelectorAll('a[href*="?pg="]')) {
    const match = link.href.match(/\?pg=(\d+)/);
    if (match) last = Math.max(last, parseInt(match[1], 10));
  }
  return last;
}

function _firstChapterLink(doc) {
  let best = null;
  let lowest = Infinity;
  for (const link of doc.querySelectorAll('#myTable a[href*="/extnu/"]')) {
    const match = link.title?.match(/c(\d+)/);
    if (match && parseInt(match[1], 10) < lowest) {
      lowest = parseInt(match[1], 10);
      best = link;
    }
  }
  return best?.href || null;
}

function _removeSpinner(slug) {
  document.querySelector(`[data-nuf-slug="${slug}"]`)?._nufControls
    ?.querySelector('.nuf-tag-spinner')?.remove();
}

// Stores what a series page says about a novel: tags, genres (only for new
// entries — listing metadata wins), the chapter 1 URL when chapterDoc is the
// page that holds chapter 1, and, when missing, the title and cover for a
// novel in a list (the manager shows them) and the synopsis for a To Read
// novel (only the sidebar shows it). Novels in no list store none of these.
function applySeriesPage(slug, doc, chapterDoc) {
  const tags   = [...doc.querySelectorAll('a[href*="/stag/"]')].map(el => el.textContent.trim().toLowerCase()).filter(Boolean);
  const genres = [...doc.querySelectorAll('a.gennew')].map(el => el.textContent.trim().toLowerCase());
  const isNew = !state.novelMeta[slug];
  if (isNew) state.novelMeta[slug] = {};
  const meta = state.novelMeta[slug];
  if (tags.length) meta.tags = tags;
  if (isNew && genres.length) meta.g = genres;
  const c1url = chapterDoc ? _firstChapterLink(chapterDoc) : null;
  if (c1url && !meta.c1url) meta.c1url = c1url;
  const isToRead = state.toRead.has(slug);
  const isReference = isToRead || state.read.has(slug) || state.notFinished.has(slug)
    || state.notInterested.has(slug);
  if (isReference) {
    const sidebarMeta = readSeriesSidebarMeta(doc);
    if (!meta.t && sidebarMeta.t) meta.t = sidebarMeta.t;
    if (!isRealCoverUrl(meta.img)) meta.img = sidebarMeta.img;  // also replaces a stored placeholder
    if (isToRead && !meta.desc && sidebarMeta.desc) meta.desc = sidebarMeta.desc;
  }
  bumpNovelMetaVersion();
  scheduleMetaSave();
  // The storage listener ignores this tab's own saves, so refresh the sidebar here
  if (isToRead) scheduleSidebarRender();
  _removeSpinner(slug);
  // Recompute when the slug is a reference (affects every score) or has a card
  // on this page (its own Tags chip row). Debounced across a run of lookups.
  if (isReference || document.querySelector(`[data-nuf-slug="${slug}"]`)) scheduleRecompute();
  return c1url;
}

// "Start reading": page 1 of the series, then — if the chapters are paginated —
// the last page, where chapter 1 is. Two queued requests at Start reading's
// priority. Returns { c1url } or { status }.
async function fetchC1UrlForSlug(slug) {
  const url = seriesUrl(slug);
  const first = await requestQueue.request('user', url, async () => {
    const result = await fetchSeriesDoc(url);
    return { ...queueOutcome(result), value: result };
  });
  if (!first?.doc) return { status: first?.status ?? 0 };
  const lastPage = _lastChapterPage(first.doc);
  let chapterDoc = first.doc;
  if (lastPage > 1) {
    const lastUrl = `${url}?pg=${lastPage}`;
    const last = await requestQueue.request('user', lastUrl, async () => {
      const result = await fetchSeriesDoc(lastUrl);
      return { ...queueOutcome(result), value: result };
    });
    chapterDoc = last?.doc || null;
  }
  const c1url = applySeriesPage(slug, first.doc, chapterDoc);
  return { c1url: c1url || state.novelMeta[slug]?.c1url || null };
}

// ── Background lookups ──────────────────────────────────────────────────────

// Tag lookups and sidebar cover lookups use separate keys. The queue keeps a
// job's priority when the same key is added again, so a novel moved to To Read
// while its tag lookup waits gets its own, higher-priority cover job; the tag
// job then finds nothing left to fetch and skips.
const _lookupKey = slug => `series:${slug}`;
const _coverKey = slug => `cover:${slug}`;
const _slugFromKey = key => key.slice(key.indexOf(':') + 1);
const _isLookupKey = key => key.startsWith('series:') || key.startsWith('cover:');

// Tags (and missing covers) for one novel. Page 1 only: the chapter 1 URL is
// stored here just for single-page series; "Start reading" fetches the rest.
// Cover lookups do not depend on the prefetch setting: the sidebar needs them.
async function _runLookup(job) {
  const slug = _slugFromKey(job.key);
  if (job.kind === 'cover') {
    if (!needsSidebarMeta(slug)) return { outcome: 'skip' };
  } else if (!state.prefetchTagsEnabled || state.novelMeta[slug]?.tags?.length) {
    return { outcome: 'skip' };
  }
  const result = await fetchSeriesDoc(job.url);
  if (result.doc) applySeriesPage(slug, result.doc, _lastChapterPage(result.doc) === 1 ? result.doc : null);
  return queueOutcome(result);
}

requestQueue.handle('prefetch', _runLookup);
requestQueue.handle('cover', _runLookup);
requestQueue.onSettled(msg => {
  if (!_isLookupKey(msg.key)) return;
  const slug = _slugFromKey(msg.key);
  _removeSpinner(slug);
  // Found tags or not, this card's score is now final: rescore (which also
  // picks up tags that just arrived) so a card waiting on the threshold is decided
  const entry = document.querySelector(`[data-nuf-slug="${slug}"]`);
  // A lookup withdrawn (prefetch switched off) fetched nothing, so it does not count
  if (entry && msg.result !== 'cancelled') {
    entry._nufLookupDone = true;
    scheduleRecompute();
  }
});

// Unbucketed listing cards are prefetched (for their own tag chips) only when
// they come near the viewport, not all at page load. Bucketed novels still go
// straight to enqueueFetch because they drive every card's score.
let _prefetchObserver = null;
function enqueueWhenVisible(entry, slug) {
  if (!state.prefetchTagsEnabled || state.novelMeta[slug]?.tags?.length) return;
  _prefetchObserver ??= new IntersectionObserver(entries => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      _prefetchObserver.unobserve(e.target);
      enqueueFetch(e.target.dataset.nufSlug);
    }
  }, { rootMargin: '200px' });
  _prefetchObserver.observe(entry);
  // Scoring runs after the card is processed and may hide it
  scheduleHiddenCardLookups();
}

// Cards waiting for a final score are hidden (display:none), so they never
// come into view; queue their tag lookups directly, at the lowest priority a
// page asks for. Cards already decided keep their place and need none.
let _hiddenLookupsScheduled = false;
function scheduleHiddenCardLookups() {
  if (_hiddenLookupsScheduled) return;
  _hiddenLookupsScheduled = true;
  setTimeout(() => {
    _hiddenLookupsScheduled = false;
    enqueueHiddenCards();
  }, 0);
}

function enqueueHiddenCards() {
  if (!state.prefetchTagsEnabled) return;
  document.querySelectorAll('.nuf-score-pending[data-nuf-slug]').forEach(entry => {
    const slug = entry.dataset.nufSlug;
    if (state.novelMeta[slug]?.tags?.length) return;
    _prefetchObserver?.unobserve(entry);
    enqueueFetch(slug);
  });
}

// Hands a novel's lookup to the queue (once). Missing sidebar covers use the
// higher-priority 'cover' kind because the gap is visible right away; they run
// even with tag prefetch switched off.
function enqueueFetch(slug) {
  if (extensionContextLost) return;
  let spec = null;
  if (needsSidebarMeta(slug)) {
    if (!requestQueue.has(_coverKey(slug))) spec = { key: _coverKey(slug), kind: 'cover', url: seriesUrl(slug) };
  } else if (state.prefetchTagsEnabled && !state.novelMeta[slug]?.tags?.length
    && !requestQueue.has(_lookupKey(slug)) && !requestQueue.has(_coverKey(slug))) {
    spec = { key: _lookupKey(slug), kind: 'prefetch', url: seriesUrl(slug) };
  }
  if (!spec) return;
  const entry = document.querySelector(`[data-nuf-slug="${slug}"]`);
  if (entry?._nufControls && !entry._nufControls.querySelector('.nuf-tag-spinner')) {
    const spinner = document.createElement('span');
    spinner.className = 'nuf-tag-spinner';
    const badge = entry._nufControls.querySelector('.nuf-score-badge');
    if (badge) badge.appendChild(spinner);
    else entry._nufControls.prepend(spinner);
  }
  requestQueue.add([spec]);
}

// Prefetch switched off: withdraw this tab's tag lookups. Sidebar cover
// lookups carry on, since they do not depend on the prefetch setting.
function stopPrefetch() {
  requestQueue.cancelKinds(['prefetch']);
  document.querySelectorAll('.nuf-tag-spinner').forEach(spinner => {
    const slug = spinner.closest('.nuf-entry-wrap')?.querySelector('[data-nuf-slug]')?.dataset.nufSlug;
    if (!slug || !requestQueue.has(_coverKey(slug))) spinner.remove();
  });
}
