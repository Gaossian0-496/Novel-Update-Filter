'use strict';

// Series page handling: hide reviews toggle, metadata capture, and bucket controls.

function isSeriesPage() {
  return document.body?.classList.contains('single-seriesplans') ||
         location.pathname.startsWith('/series/');
}

// Resilient title lookup: live-site DOM has changed before (defect 041, 044).
// Try the historically-correct selector first, then degrade to class-only,
// then to any h1 heading. manager.js already uses the class-only form.
function _findSeriesTitleEl() {
  return document.querySelector('h1.seriestitlenu') ||
         document.querySelector('.seriestitlenu') ||
         document.querySelector('h1');
}

function applyHideReviews() {
  const comments = document.querySelector('#comments');
  if (comments) comments.style.display = state.hideReviews ? 'none' : '';
}

function _setSeriesActiveBtn(wrap, bucket) {
  ['read', 'nf', 'ni'].forEach(k => {
    wrap.querySelector(`.nuf-btn[data-nuf-btn="${k}"]`)?.classList.remove('nuf-btn-active');
  });
  if (bucket) {
    const map = { read: 'read', notFinished: 'nf', notInterested: 'ni' };
    const key = map[bucket];
    if (key) wrap.querySelector(`.nuf-btn[data-nuf-btn="${key}"]`)?.classList.add('nuf-btn-active');
  }
}

function _setSeriesToReadBtn(wrap, isToRead) {
  wrap.querySelector('.nuf-btn[data-nuf-btn="toread"]')?.classList.toggle('nuf-toread-active', !!isToRead);
}

function markSeriesPage(slug, title, bucket, wrap) {
  // Clicking the list it is already in still opens the toast, the only place
  // to rate it here; Undo then puts back that same list
  const prevBucket = ['read', 'notFinished', 'notInterested'].find(b => state[b].has(slug)) || null;
  const wasToRead = state.toRead.has(slug);
  ['read', 'notFinished', 'notInterested'].forEach(b => state[b].delete(slug));
  state.toRead.delete(slug);
  state[bucket].add(slug);
  saveBuckets();
  _setSeriesActiveBtn(wrap, bucket);
  _setSeriesToReadBtn(wrap, false);
  pendingUndo = {
    slug, bucket, el: null, controls: null, parent: null, nextSibling: null,
    previous: { bucket: prevBucket, toRead: wasToRead },
    onUndo: () => {
      _setSeriesActiveBtn(wrap, prevBucket);
      _setSeriesToReadBtn(wrap, wasToRead);
    },
  };
  showToast(title, bucket, slug, wrap);
}

// The To Read sidebar shows title, cover and synopsis. Listing cards supply
// these via markToRead; on the series page they come from the page itself.
function _captureSeriesSidebarMeta(slug, title) {
  const { img, desc } = readSeriesSidebarMeta(document);
  state.novelMeta[slug] = { ...(state.novelMeta[slug] || {}), t: title, img, desc };
  saveNovelMeta();
}

function markSeriesToRead(slug, title, wrap) {
  // Toggle off if already in toRead — silent (mirrors listing-page behaviour)
  if (state.toRead.has(slug)) {
    state.toRead.delete(slug);
    saveBuckets();
    _setSeriesToReadBtn(wrap, false);
    return;
  }
  // Toggle on — and clear any rejection bucket since the two are mutually exclusive
  const prevBucket = ['read', 'notFinished', 'notInterested'].find(b => state[b].has(slug)) || null;
  ['read', 'notFinished', 'notInterested'].forEach(b => state[b].delete(slug));
  state.toRead.add(slug);
  saveBuckets();
  _captureSeriesSidebarMeta(slug, title);
  _setSeriesActiveBtn(wrap, null);
  _setSeriesToReadBtn(wrap, true);
  pendingUndo = {
    slug, bucket: 'toRead', el: null, controls: wrap, parent: null, nextSibling: null,
    previous: { bucket: prevBucket },
    onUndo: () => {
      _setSeriesActiveBtn(wrap, prevBucket);
      _setSeriesToReadBtn(wrap, false);
    },
  };
  showToast(title, 'toRead', slug, wrap);
  enqueueFetch(slug);
}

function injectSeriesPageControls(slug, title) {
  const titleEl = _findSeriesTitleEl();
  if (!titleEl) {
    console.error('[nuf] series page title element not found (tried h1.seriestitlenu, .seriestitlenu, h1) — bucket controls cannot inject');
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'nuf-controls';

  wrap.appendChild(makeBucketBtn('Read',           'read', 'Mark as Read',           () => markSeriesPage(slug, title, 'read',          wrap)));
  wrap.appendChild(makeBucketBtn('Not Finished',   'nf',   'Mark as Not Finished',   () => markSeriesPage(slug, title, 'notFinished',   wrap)));
  wrap.appendChild(makeBucketBtn('Not Interested', 'ni',   'Mark as Not Interested', () => markSeriesPage(slug, title, 'notInterested', wrap)));
  wrap.appendChild(makeBucketBtn('To Read',        'toread', 'Mark as To Read',     () => markSeriesToRead(slug, title, wrap)));

  titleEl.insertAdjacentElement('afterend', wrap);

  const currentBucket = ['read', 'notFinished', 'notInterested'].find(b => state[b].has(slug)) || null;
  _setSeriesActiveBtn(wrap, currentBucket);
  _setSeriesToReadBtn(wrap, state.toRead.has(slug));

  return wrap;
}

async function initSeriesPage() {
  await loadState();
  applyHideReviews();
  const slug = location.pathname.replace(/^\/series\//, '').replace(/\/$/, '');
  if (!slug) return;
  // A security check served at the series URL: no controls, and its heading is not the title
  if (!document.querySelector('.seriestitlenu') && _isChallengePage(document)) return;

  const title = _findSeriesTitleEl()?.textContent.trim() || slug;
  const wrap = injectSeriesPageControls(slug, title);

  // Capture title, tags and genres from the series page DOM. The title lets the
  // manager and sidebar show a real name instead of the slug.
  const tags   = [...document.querySelectorAll('a[href*="/stag/"]')].map(el => el.textContent.trim().toLowerCase()).filter(Boolean);
  const genres = [...document.querySelectorAll('a.gennew')].map(el => el.textContent.trim().toLowerCase());
  const hasTitle = title !== slug;
  if (hasTitle || tags.length || genres.length) {
    if (!state.novelMeta[slug]) state.novelMeta[slug] = {};
    if (hasTitle)      state.novelMeta[slug].t    = title;
    if (tags.length)   state.novelMeta[slug].tags = tags;
    if (genres.length) state.novelMeta[slug].g    = genres;
    // Save immediately — debounce risks losing data if user navigates away within 2s
    saveNovelMeta();
  }

  // React to live toggle changes on series page
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if ('hideReviews' in changes) {
      state.hideReviews = !!changes.hideReviews.newValue;
      applyHideReviews();
    }
    // Sync active button when buckets change externally (e.g. from manager page)
    if (wrap && (changes.read || changes.notFinished || changes.notInterested || changes.toRead)) {
      if (changes.read)          state.read          = new Set(changes.read.newValue || []);
      if (changes.notFinished)   state.notFinished   = new Set(changes.notFinished.newValue || []);
      if (changes.notInterested) state.notInterested = new Set(changes.notInterested.newValue || []);
      if (changes.toRead)        state.toRead        = new Set(changes.toRead.newValue || []);
      const current = ['read', 'notFinished', 'notInterested'].find(b => state[b].has(slug)) || null;
      _setSeriesActiveBtn(wrap, current);
      _setSeriesToReadBtn(wrap, state.toRead.has(slug));
    }
  });
}
