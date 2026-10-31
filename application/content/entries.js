'use strict';

// Card control injection, bucket marking, synopsis expansion, and entry processing.

function markToRead(entry, slug, title) {
  // A real cover only (a missing or placeholder one would stop the cover
  // lookup), the synopsis for the sidebar, and when it was added
  const meta = state.novelMeta[slug] = { ...(state.novelMeta[slug] || {}) };
  applyCardSidebarFields(meta, entry, _activeConfig || PAGE_CONFIG);
  meta.ts = Date.now();

  state.toRead.add(slug);
  saveBuckets();
  scheduleMetaSave();

  entry.classList.add('nuf-to-read');
  entry._nufWrapper?.classList.add('nuf-to-read');
  // Update button appearance to active state
  entry._nufControls?.querySelector('.nuf-btn[data-nuf-btn="toread"]')
    ?.classList.add('nuf-toread-active');

  // Record undo info for To Read action
  pendingUndo = {
    slug,
    bucket: 'toRead',
    el: entry,
    controls: entry._nufControls || null,
    parent: entry._nufWrapper || entry.parentNode,
    nextSibling: null,
  };

  showToast(title, 'toRead', slug, entry);
  enqueueFetch(slug);
  if (_activeConfig) applyRecommendations(_activeConfig, true);
}

// Wires a "Start reading →" button (listing card or sidebar). Opens the cached
// first-chapter URL when known; otherwise looks it up on click, which caches it
// in novelMeta so later clicks make no request. A failed lookup shows "?" and
// stays clickable so the user can retry.
//
// The lookup is queued and can take several seconds (pacing, plus a second
// request for paginated series) — longer than the click's user activation
// lasts, so a window.open after it would be popup-blocked. So the tab is opened
// blank inside the click and pointed at the chapter once the lookup resolves.
function bindStartReadingButton(btn, slug) {
  btn.title = 'Open first chapter in a new tab';
  btn.addEventListener('click', async e => {
    e.preventDefault(); e.stopPropagation();
    const cached = state.novelMeta[slug]?.c1url;
    if (cached) {
      window.open(cached, '_blank', 'noopener');
      return;
    }
    if (btn.dataset.fetching) return;
    // No 'noopener' here: it would make window.open return null, and the tab
    // is needed to navigate later. The opener link is cut by hand instead.
    const tab = window.open('about:blank', '_blank');
    if (tab) tab.opener = null;
    btn.dataset.fetching = '1';
    btn.textContent = '…';
    btn.style.opacity = '0.6';
    let result;
    try {
      result = await fetchC1UrlForSlug(slug);
    } catch (err) {
      console.log('[nuf] Start reading lookup failed:', err);
      result = {};
    }
    delete btn.dataset.fetching;
    btn.style.opacity = '';
    if (result.c1url) {
      btn.textContent = 'Start reading →';
      btn.title = 'Open first chapter in a new tab';
      if (tab && !tab.closed) tab.location.href = result.c1url;
      else if (!tab) window.open(result.c1url, '_blank', 'noopener');
    } else {
      if (tab && !tab.closed) tab.close();
      btn.textContent = '?';
      btn.title = 'First chapter not found — click to try again';
    }
  });
}

function makeBucketBtn(label, nufBtn, title, onClick) {
  const btn = document.createElement('button');
  btn.className = 'nuf-btn';
  btn.dataset.nufBtn = nufBtn;
  btn.textContent = label;
  btn.title = title;
  btn.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation();
    onClick();
  });
  return btn;
}

function injectControls(entry, slug, title) {
  if (entry.dataset.nufInjected) return;
  entry.dataset.nufInjected = '1';

  const wrap = document.createElement('div');
  wrap.className = 'nuf-controls';

  const t = title || slug;
  wrap.appendChild(makeBucketBtn('Read',           'read', 'Mark as Read — hides this novel',           () => markEntry(entry, slug, t, 'read')));
  wrap.appendChild(makeBucketBtn('Not Finished',   'nf',   'Mark as Not Finished — hides this novel',   () => markEntry(entry, slug, t, 'notFinished')));
  wrap.appendChild(makeBucketBtn('Not Interested', 'ni',   'Mark as Not Interested — hides this novel', () => markEntry(entry, slug, t, 'notInterested')));

  const sep = document.createElement('span');
  sep.className = 'nuf-btn-sep';
  sep.textContent = '|';
  wrap.appendChild(sep);

  // "To Read" button — keeps card visible with glow
  const toReadBtn = document.createElement('button');
  toReadBtn.className = 'nuf-btn';
  toReadBtn.dataset.nufBtn = 'toread';
  toReadBtn.textContent = 'To Read';
  toReadBtn.title = 'Mark as To Read — keeps card visible';
  if (state.toRead.has(slug)) toReadBtn.classList.add('nuf-toread-active');
  toReadBtn.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation();
    if (state.toRead.has(slug)) {
      state.toRead.delete(slug);
      saveBuckets();
      entry._nufWrapper?.classList.remove('nuf-to-read');
      entry.classList.remove('nuf-to-read');
      toReadBtn.classList.remove('nuf-toread-active');
      if (_activeConfig) applyRecommendations(_activeConfig, true);
    } else {
      markToRead(entry, slug, title || slug);
    }
  });
  wrap.appendChild(toReadBtn);

  const sep2 = document.createElement('span');
  sep2.className = 'nuf-btn-sep';
  sep2.textContent = '|';
  wrap.appendChild(sep2);

  // "Start reading →" button — always shown; resolves URL lazily on first click if not cached
  const startBtn = document.createElement('button');
  startBtn.className = 'nuf-btn';
  startBtn.dataset.nufBtn = 'start';
  startBtn.textContent = 'Start reading →';
  bindStartReadingButton(startBtn, slug);
  wrap.appendChild(startBtn);

  // Wrap controls + card together so glow can be applied to the single wrapper
  const wrapper = document.createElement('div');
  wrapper.className = 'nuf-entry-wrap';
  entry.parentNode.insertBefore(wrapper, entry);
  wrapper.appendChild(wrap);
  wrapper.appendChild(entry);
  entry._nufControls = wrap;
  entry._nufWrapper  = wrapper;
}

function markEntry(entry, slug, title, bucket) {
  const controls = entry._nufControls || null;
  // Capture the card's rect BEFORE the remove animation starts so the toast
  // can anchor to where the card was. The element will animate out and be
  // removed; the toast stays at the captured viewport coords.
  const anchorRect = entry.getBoundingClientRect();

  // Record undo info before removal. The wrapper goes too once the animation
  // ends, so where it sat is recorded for Undo to put it back.
  const wrapper = entry._nufWrapper;
  pendingUndo = {
    slug, bucket, el: entry, controls,
    parent: entry.parentNode,
    nextSibling: entry.nextSibling,
    wrapperParent: wrapper?.parentNode || null,
    wrapperNext: wrapper?.nextSibling || null,
    previous: { toRead: state.toRead.has(slug) },
  };

  // If moving from To Read to another bucket, remove the glow. The glow CSS
  // lives on the WRAPPER (`.nuf-entry-wrap.nuf-to-read`, not the entry), so
  // dropping the class only from the entry leaves the wrapper still pulsing
  // — visible as a persistent glow artifact at the card's former position
  // after the entry animates out. Remove from both (defect-052).
  if (state.toRead.has(slug)) {
    state.toRead.delete(slug);
    entry.classList.remove('nuf-to-read');
    entry._nufWrapper?.classList.remove('nuf-to-read');
  }

  // Add to bucket
  state[bucket].add(slug);
  saveBuckets();

  // Stamp recency timestamp on novelMeta
  if (!state.novelMeta[slug]) {
    state.novelMeta[slug] = { g: [], l: null, d: [], t: null, ts: Date.now() };
  } else {
    state.novelMeta[slug].ts = Date.now();
  }
  scheduleMetaSave();

  // Remove controls bar immediately, animate entry out, then also clean up
  // the wrapper if it's empty — otherwise an orphan wrapper lingers in DOM
  // (matching the pattern in storage-listener.js's filter loop).
  if (controls) controls.remove();
  entry.classList.add('nuf-removing');
  entry.addEventListener('animationend', () => {
    entry.remove();
    if (wrapper && wrapper.parentNode && !wrapper.children.length) wrapper.remove();
  }, { once: true });

  showToast(title, bucket, slug, anchorRect);
}

function expandSynopsisOnEntry(entry) {
  // NovelUpdates truncates descriptions with a hidden .testhide span and a ".morelink" show/hide toggle.
  // Directly manipulate the DOM instead of relying on the site's click handler.
  const body = entry.querySelector('.search_body_nu');
  if (!body) return;
  const testhide = body.querySelector('.testhide');
  if (testhide) testhide.style.display = 'block';
  const moreLink = body.querySelector('.morelink');
  if (moreLink) moreLink.style.display = 'none';
  const dotsEl = body.querySelector('.dots');
  if (dotsEl) dotsEl.style.display = 'none';
}

function processEntry(entry, config) {
  const linkEl = entry.querySelector(config.titleLinkSelector);
  if (!linkEl) return;
  const slug = slugFromHref(linkEl.href);
  if (!slug) return;

  entry.dataset.nufSlug = slug;
  _entryIdx++;
  const title = linkEl.textContent.trim();
  captureMetadata(entry, slug, title, config);

  if (shouldFilter(slug, entry, config)) {
    scroll.totalFiltered++;
    entry.remove();
    return;
  }

  injectControls(entry, slug, title);
  if (state.toRead.has(slug)) {
    entry.classList.add('nuf-to-read');
    entry._nufWrapper?.classList.add('nuf-to-read');
  }
  enqueueWhenVisible(entry, slug);
  if (state.expandSynopsis) expandSynopsisOnEntry(entry);
}

function processContainer(container, config) {
  const entries = container
    ? container.querySelectorAll(config.entrySelector)
    : document.querySelectorAll(config.entrySelector);
  entries.forEach(e => processEntry(e, config));
}
