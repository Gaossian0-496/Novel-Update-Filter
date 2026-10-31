'use strict';

// ── State ──────────────────────────────────────────────────────────────────

let data = {
  read: [],
  notFinished: [],
  notInterested: [],
  toRead: [],
  titleCache: {},
  novelMeta: {},
  novelReviews: {},
  coverCache: {},   // slug → cover URL fetched by the manager ('' = series page has none)
};

function resolveTitle(slug) {
  return data.titleCache[slug] || data.novelMeta[slug]?.t || null;
}

// Covers come from listing cards (novelMeta, written by the content script) or
// from the manager's own series-page fetches (coverCache). The manager never
// writes novelMeta, which open listing tabs save wholesale.
function resolveCover(slug) {
  return data.novelMeta[slug]?.img || data.coverCache[slug] || null;
}

function coverUnknown(slug) {
  return !data.novelMeta[slug]?.img && data.coverCache[slug] === undefined;
}

function formatAddedDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  return 'Added ' + d.toISOString().slice(0, 10);
}

const searches = { read: '', notFinished: '', notInterested: '', toRead: '' };

// Selection lives here, not in the checkboxes, so it survives the re-render
// that every storage change triggers (e.g. a title arriving mid-selection).
const selected = { read: new Set(), notFinished: new Set(), notInterested: new Set(), toRead: new Set() };

const SORT_KEY = 'nufManagerSort';
let sortMode = 'newest';
try { sortMode = localStorage.getItem(SORT_KEY) || 'newest'; } catch { /* storage blocked */ }

const COL = {
  read:          { listId: 'list-read', cntId: 'cnt-read', selCntId: 'sel-cnt-read', statId: 'stat-read', short: 'read' },
  notFinished:   { listId: 'list-nf',   cntId: 'cnt-nf',   selCntId: 'sel-cnt-nf',   statId: 'stat-nf',   short: 'nf'   },
  notInterested: { listId: 'list-ni',   cntId: 'cnt-ni',   selCntId: 'sel-cnt-ni',   statId: 'stat-ni',   short: 'ni'   },
  toRead:        { listId: 'list-tr',   cntId: 'cnt-tr',   selCntId: 'sel-cnt-tr',   statId: 'stat-tr',   short: 'tr'   },
};

// ── Init ───────────────────────────────────────────────────────────────────

async function init() {
  const stored = await chrome.storage.local.get(['read', 'notFinished', 'notInterested', 'toRead', 'titleCache', 'novelMeta', 'novelReviews', 'coverCache']);
  data.read          = stored.read || [];
  data.notFinished   = stored.notFinished || [];
  data.notInterested = stored.notInterested || [];
  data.toRead        = stored.toRead || [];
  data.titleCache    = stored.titleCache || {};
  data.novelMeta     = stored.novelMeta || {};
  data.novelReviews  = stored.novelReviews || {};
  data.coverCache    = stored.coverCache || {};

  document.getElementById('sort-select').value = sortMode;
  renderAll();
  fetchMissingTitles();
  bindEvents();

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.read)          data.read = changes.read.newValue || [];
    if (changes.notFinished)   data.notFinished = changes.notFinished.newValue || [];
    if (changes.notInterested) data.notInterested = changes.notInterested.newValue || [];
    if (changes.toRead)        data.toRead = changes.toRead.newValue || [];
    if (changes.titleCache)    data.titleCache = changes.titleCache.newValue || {};
    if (changes.novelMeta)     data.novelMeta = changes.novelMeta.newValue || {};
    if (changes.novelReviews)  data.novelReviews = changes.novelReviews.newValue || {};
    if (changes.coverCache)    data.coverCache = changes.coverCache.newValue || {};
    renderAll();
    if (changes.read || changes.notFinished || changes.notInterested || changes.toRead) fetchMissingTitles();
  });
}

// ── Rendering ──────────────────────────────────────────────────────────────

function renderAll() {
  renderStats();
  for (const col of Object.keys(COL)) renderList(col);
}

function renderStats() {
  for (const [col, ids] of Object.entries(COL)) {
    document.getElementById(ids.cntId).textContent  = data[col].length;
    document.getElementById(ids.statId).textContent = data[col].length;
  }
}

function sortedSlugs(col) {
  const slugs = [...data[col]];
  if (sortMode === 'title') {
    return slugs.sort((a, b) => (resolveTitle(a) || a).localeCompare(resolveTitle(b) || b));
  }
  // Stored order is insertion order; added dates (when known) refine it. A
  // stable sort keeps slugs without a date in their stored order.
  const ts = s => data.novelMeta[s]?.ts || 0;
  slugs.sort((a, b) => ts(a) - ts(b));
  return sortMode === 'newest' ? slugs.reverse() : slugs;
}

function emptyHtml(col) {
  if (searches[col]) return '<div class="empty">Nothing here.<span class="empty-hint">No titles match your search.</span></div>';
  const hint = col === 'toRead'
    ? 'Use To Read on a listing card to queue a novel.'
    : `Novels you mark as ${labelFor(col)} on Novel Updates appear here.`;
  return `<div class="empty">Nothing here.<span class="empty-hint">${hint}</span></div>`;
}

function renderList(col) {
  const ids = COL[col];
  const listEl = document.getElementById(ids.listId);
  const q = searches[col].toLowerCase();

  // Drop selections for novels that left this list
  for (const slug of selected[col]) if (!data[col].includes(slug)) selected[col].delete(slug);

  const visible = sortedSlugs(col).filter(s => {
    if (!q) return true;
    const t = resolveTitle(s) || '';
    return s.toLowerCase().includes(q) || t.toLowerCase().includes(q);
  });

  if (visible.length === 0) {
    listEl.innerHTML = emptyHtml(col);
    updateSelCount(col);
    return;
  }

  listEl.innerHTML = '';
  visible.forEach(slug => {
    const title = resolveTitle(slug);
    const item = document.createElement('div');
    item.className = 'item';
    item.dataset.slug = slug;

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selected[col].has(slug);
    cb.setAttribute('aria-label', `Select ${title || slug}`);
    cb.addEventListener('change', () => {
      if (cb.checked) selected[col].add(slug);
      else selected[col].delete(slug);
      updateSelCount(col);
    });

    const cover = document.createElement('img');
    cover.className = 'item-cover';
    cover.alt = '';
    cover.loading = 'lazy';
    cover.referrerPolicy = 'no-referrer';  // image hosts may reject a chrome-extension:// referrer
    cover.addEventListener('error', () => cover.removeAttribute('src'));  // fall back to the placeholder
    const img = resolveCover(slug);
    if (img) cover.src = img;

    const info = document.createElement('div');
    info.className = 'item-info';
    info.innerHTML = itemInfoHtml(slug, title);

    const delBtn = document.createElement('button');
    delBtn.className = 'item-btn del';
    delBtn.textContent = '×';
    delBtn.title = `Remove from ${labelFor(col)}`;
    delBtn.setAttribute('aria-label', `Remove ${title || slug} from ${labelFor(col)}`);
    delBtn.addEventListener('click', () => deleteItem(slug, col));

    const itemTop = document.createElement('div');
    itemTop.className = 'item-top';
    itemTop.appendChild(info);
    itemTop.appendChild(delBtn);

    const actions = document.createElement('div');
    actions.className = 'item-actions';
    const actionsLabel = document.createElement('span');
    actionsLabel.className = 'item-actions-label';
    actionsLabel.textContent = 'Move to';
    actions.appendChild(actionsLabel);
    for (const target of Object.keys(COL).filter(c => c !== col)) {
      const btn = document.createElement('button');
      btn.className = `item-btn move-${COL[target].short}`;
      btn.textContent = labelFor(target);
      btn.title = `Move to ${labelFor(target)}`;
      btn.addEventListener('click', () => moveItem(slug, col, target));
      actions.appendChild(btn);
    }

    const body = document.createElement('div');
    body.className = 'item-body';
    body.appendChild(itemTop);
    body.appendChild(actions);

    item.appendChild(cb);
    item.appendChild(cover);
    item.appendChild(body);
    listEl.appendChild(item);
  });

  updateSelCount(col);
}

function labelFor(col) {
  if (col === 'read')          return 'Read';
  if (col === 'notFinished')   return 'Not Finished';
  if (col === 'notInterested') return 'Not Interested';
  if (col === 'toRead')        return 'To Read';
}

// Updates the "N selected" count, enables the bulk buttons only when something
// is selected, and flips Select all ⇄ Clear selection.
function updateSelCount(col) {
  const short = COL[col].short;
  const count = selected[col].size;
  document.getElementById(COL[col].selCntId).textContent = count;
  document.querySelectorAll(`#col-${short} .bulk-bar button`).forEach(btn => { btn.disabled = count === 0; });
  const visibleCount = document.querySelectorAll(`#${COL[col].listId} .item`).length;
  const allSelected = visibleCount > 0 && document.querySelectorAll(`#${COL[col].listId} .item input:checked`).length === visibleCount;
  const selBtn = document.getElementById(`sel-all-${short}`);
  selBtn.textContent = allSelected ? 'Clear selection' : 'Select all';
  selBtn.title = allSelected ? 'Unselect every novel in this list' : 'Select every novel shown in this list';
  selBtn.disabled = visibleCount === 0;
  document.getElementById(`clear-${short}`).disabled = data[col].length === 0;
}

function esc(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Bucket operations ──────────────────────────────────────────────────────

async function save() {
  await chrome.storage.local.set({
    read: data.read,
    notFinished: data.notFinished,
    notInterested: data.notInterested,
    toRead: data.toRead,
  });
}

// Removing is the only destructive action, so every removal offers Undo.
const UNDO_MS = 6000;
let undoTimer = null;
let undoSnapshot = null;

function offerUndo(message) {
  document.getElementById('undo-msg').textContent = message;
  document.getElementById('undo-toast').classList.add('visible');
  clearTimeout(undoTimer);
  undoTimer = setTimeout(hideUndo, UNDO_MS);
}

function hideUndo() {
  document.getElementById('undo-toast').classList.remove('visible');
  undoSnapshot = null;
}

// Undo puts back only the removed novels, at their old places, so moves made
// in the meantime (here or in a listing tab) are kept
function removeFrom(col, slugs, message) {
  const gone = new Set(slugs);
  undoSnapshot = { col, removed: data[col].map((slug, index) => [slug, index]).filter(([slug]) => gone.has(slug)) };
  data[col] = data[col].filter(s => !gone.has(s));
  save();
  renderStats();
  renderList(col);
  offerUndo(message);
}

function undoRemove() {
  if (!undoSnapshot) return;
  const { col, removed } = undoSnapshot;
  for (const [slug, index] of removed) {
    // Since put in a list again (here or elsewhere): leave it there
    if (Object.keys(COL).some(c => data[c].includes(slug))) continue;
    data[col].splice(Math.min(index, data[col].length), 0, slug);
  }
  save();
  renderAll();
  hideUndo();
}

function novelCountText(n) {
  return n === 1 ? '1 novel' : `${n} novels`;
}

function deleteItem(slug, col) {
  const name = resolveTitle(slug) || slug;
  removeFrom(col, [slug], `Removed "${name}" from ${labelFor(col)}.`);
}

// One write for all the novels moved: each write makes every open listing tab
// rescore, so moving 200 novels one by one meant 200 rescores
function moveItems(slugs, fromCol, toCol) {
  slugs = slugs.filter(slug => data[fromCol].includes(slug));
  if (!slugs.length) return;
  const moving = new Set(slugs);
  data[fromCol] = data[fromCol].filter(slug => !moving.has(slug));
  for (const slug of slugs) {
    if (!data[toCol].includes(slug)) data[toCol].push(slug);
    selected[fromCol].delete(slug);
  }
  save();
  renderStats();
  renderList(fromCol);
  renderList(toCol);
}

function moveItem(slug, fromCol, toCol) {
  moveItems([slug], fromCol, toCol);
}

function getSelected(col) {
  return [...selected[col]].filter(slug => data[col].includes(slug));
}

function bulkMove(fromCol, toCol) {
  moveItems(getSelected(fromCol), fromCol, toCol);
}

function bulkDelete(col) {
  const slugs = getSelected(col);
  if (!slugs.length) return;
  selected[col].clear();
  removeFrom(col, slugs, `Removed ${novelCountText(slugs.length)} from ${labelFor(col)}.`);
}

function clearAll(col) {
  const count = data[col].length;
  if (!count) return;
  if (!confirm(`Remove all ${novelCountText(count)} from ${labelFor(col)}?`)) return;
  selected[col].clear();
  removeFrom(col, [...data[col]], `Cleared ${labelFor(col)} (${novelCountText(count)}).`);
}

// Toggles: selects every novel currently shown (respecting the search), or
// clears the selection when everything shown is already selected.
function selectAll(col) {
  const shown = [...document.querySelectorAll(`#${COL[col].listId} .item`)].map(item => item.dataset.slug);
  const allSelected = shown.length > 0 && shown.every(slug => selected[col].has(slug));
  for (const slug of shown) {
    if (allSelected) selected[col].delete(slug);
    else selected[col].add(slug);
  }
  renderList(col);
}

// ── Export / Import ────────────────────────────────────────────────────────

// Keys included in the v2 full-snapshot export. Adding a new chrome.storage.local
// key that should round-trip means adding it here (and to the import validator
// below). Transient keys (tagFetchProgress) and never-persisted-on-import keys
// are intentionally omitted.
const EXPORTABLE_KEYS = [
  'read', 'notFinished', 'notInterested', 'toRead',
  'novelMeta', 'novelReviews', 'titleCache', 'coverCache',
  'excludeLanguages', 'excludeTags',
  'recommendationsEnabled', 'showReasoningChips', 'prefetchTagsEnabled',
  'expandSynopsis', 'hideReviews',
  'minRating', 'minRecommendationScore', 'recencyHalfLifeDays',
  'ratingScaleVersion',
];

async function exportAll() {
  // Read fresh from storage so the export captures the live state, not the
  // manager's in-memory `data` (which is only a subset). Sampling everything
  // means novelMeta, novelReviews, titleCache, filter selections, and
  // preferences all round-trip — see defect-053.
  const stored = await chrome.storage.local.get(EXPORTABLE_KEYS);
  const payload = JSON.stringify({ version: 2, ...stored }, null, 2);
  const btn = document.getElementById('export-btn');
  downloadJson(payload);
  const orig = btn.textContent;
  btn.textContent = 'Downloaded';
  setTimeout(() => { btn.textContent = orig; }, 1500);
  // Also on the clipboard when the browser allows it; the file is the export
  navigator.clipboard.writeText(payload).then(() => {
    btn.textContent = 'Copied & downloaded!';
  }, () => {});
}

function downloadJson(payload) {
  const today = new Date().toISOString().slice(0, 10);
  const filename = `novel-updates-filter-${today}.json`;
  const blob = new Blob([payload], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// A backup's ratings on the 1-5 scale. Backups from before it (no
// ratingScaleVersion, or 1) rated 1-10; converted as content/storage.js does.
function toCurrentRatingScale(reviews, version) {
  if ((version || 1) >= 2) return reviews;
  const converted = {};
  for (const [slug, review] of Object.entries(reviews)) {
    converted[slug] = review && review.rating != null
      ? { ...review, rating: Math.max(1, Math.min(5, Math.round(review.rating / 2))) }
      : review;
  }
  return converted;
}

function doImport() {
  const text = document.getElementById('import-text').value.trim();
  try {
    const parsed = JSON.parse(text);
    const isStringArray = v => Array.isArray(v) && v.every(s => typeof s === 'string');
    const isObject = v => v && typeof v === 'object' && !Array.isArray(v);
    const bucketKeys = ['read', 'notFinished', 'notInterested', 'toRead'];
    const bucketsOk = bucketKeys.every(k => parsed[k] === undefined || isStringArray(parsed[k]));
    if (!bucketsOk || !Array.isArray(parsed.read)) {
      alert('Invalid format.');
      return;
    }

    // Build the storage payload. v1 = buckets only. v2 = buckets + novelMeta +
    // novelReviews + titleCache + filter selections + preferences + ratingScaleVersion.
    // Any key that's missing from the import is left untouched in storage
    // (rather than zeroed) so partial / legacy imports don't wipe other state.
    const toSet = {
      read:          parsed.read,
      notFinished:   parsed.notFinished   || [],
      notInterested: parsed.notInterested || [],
      toRead:        parsed.toRead        || [],
    };
    // v2 keys (each guarded by a shape check)
    if (isObject(parsed.novelMeta))       toSet.novelMeta    = parsed.novelMeta;
    if (isObject(parsed.novelReviews))    toSet.novelReviews = parsed.novelReviews;
    if (isObject(parsed.titleCache))      toSet.titleCache   = parsed.titleCache;
    if (isObject(parsed.coverCache))      toSet.coverCache   = parsed.coverCache;
    if (isStringArray(parsed.excludeLanguages)) toSet.excludeLanguages = parsed.excludeLanguages;
    if (isStringArray(parsed.excludeTags))      toSet.excludeTags      = parsed.excludeTags;
    for (const k of ['recommendationsEnabled', 'showReasoningChips', 'prefetchTagsEnabled',
                     'expandSynopsis', 'hideReviews']) {
      if (typeof parsed[k] === 'boolean') toSet[k] = parsed[k];
    }
    for (const k of ['minRating', 'minRecommendationScore', 'recencyHalfLifeDays']) {
      if (typeof parsed[k] === 'number') toSet[k] = parsed[k];
    }
    // Ratings are saved on the 1-5 scale: open tabs take them as they arrive,
    // so a backup from before that scale is converted here, not on next load
    if (toSet.novelReviews) {
      toSet.novelReviews = toCurrentRatingScale(toSet.novelReviews, parsed.ratingScaleVersion);
      toSet.ratingScaleVersion = 2;
    }

    // Atomic write — single set() so storage.onChanged listeners fire once.
    // The manager's own storage.onChanged handler updates `data` and re-renders;
    // we only need to handle the side-effects it doesn't: kick off any missing
    // title fetches and close the import panel.
    chrome.storage.local.set(toSet).then(() => {
      fetchMissingTitles();
      document.getElementById('import-area').classList.remove('open');
      document.getElementById('import-text').value = '';
    });
  } catch {
    alert('Invalid JSON.');
  }
}

// ── Title fetching ─────────────────────────────────────────────────────────

// Lookups are jobs in the browser-wide request queue (content/request-queue.js
// → background.js) at the lowest priority; the queue owns pacing, retries,
// Retry-After and cooldowns. This page only decides which novels need one.
const TITLE_FETCH_BLOCKING = new Set([403, 429, 503]);

const titleFetchFailed = new Set();

// Slugs whose series page is worth fetching: no title from any source, or no
// cover yet. Titles and covers captured on listing pages need no request.
function needsSeriesFetch(slug) {
  return !titleFetchFailed.has(slug) && (!resolveTitle(slug) || coverUnknown(slug));
}

function missingTitleSlugs() {
  const all = [...new Set([...data.read, ...data.notFinished, ...data.notInterested, ...data.toRead])];
  return all.filter(needsSeriesFetch);
}

const _managerKey = slug => `manager:${slug}`;

// Hands every novel that needs a title or cover to the queue (duplicates are
// ignored, so calling this after each storage change or import is cheap).
function fetchMissingTitles() {
  requestQueue.add(missingTitleSlugs().map(slug => ({
    key: _managerKey(slug), kind: 'manager', url: `https://www.novelupdates.com/series/${slug}/`,
  })));
}

requestQueue.handle('manager', async job => {
  const slug = job.key.slice('manager:'.length);
  if (!needsSeriesFetch(slug)) return { outcome: 'skip' };
  const result = await fetchTitle(slug);
  if (result.ok) return { outcome: 'ok', status: 200 };
  if (TITLE_FETCH_BLOCKING.has(result.status)) return { outcome: 'blocked', status: result.status, retryAfter: result.retryAfter };
  if (result.status === 0 || result.status >= 500) return { outcome: 'error', status: result.status || null };
  return { outcome: 'gone', status: result.status };  // 404 or a page without a title
});

requestQueue.onSettled(msg => {
  if (!msg.key.startsWith('manager:')) return;
  const slug = msg.key.slice('manager:'.length);
  if ((msg.result === 'gone' || msg.result === 'failed') && !resolveTitle(slug)) markTitleFailed(slug);
});

function markTitleFailed(slug) {
  titleFetchFailed.add(slug);
  document.querySelectorAll(`.item[data-slug="${slug}"] .item-info`).forEach(info => {
    info.innerHTML = itemInfoHtml(slug, null);
  });
}

function itemInfoHtml(slug, title) {
  if (title) {
    const added = formatAddedDate(data.novelMeta[slug]?.ts);
    // Clamped: an old export can hold ratings from before the 1–5 scale
    const raw = data.novelReviews[slug]?.rating;
    const rating = typeof raw === 'number' ? Math.max(0, Math.min(5, Math.round(raw))) : null;
    const stars = rating != null
      ? `<span class="rating" title="Your rating: ${rating} out of 5">${'★'.repeat(rating)}${'☆'.repeat(5 - rating)}</span>`
      : '';
    const sub = added || stars ? [esc(added), stars].filter(Boolean).join(' &nbsp;') : esc(slug);
    const href = `https://www.novelupdates.com/series/${encodeURIComponent(slug)}/`;
    return `<a class="item-title" href="${href}" target="_blank" rel="noopener" title="Open on Novel Updates">${esc(title)}</a><span class="item-sub">${sub}</span>`;
  }
  if (titleFetchFailed.has(slug)) {
    return `<span class="item-title">${esc(slug)}</span><span class="item-sub item-failed">Title fetch failed</span>`;
  }
  return `<span class="item-loading">Fetching title…</span><span class="item-sub">${esc(slug)}</span>`;
}

function isSecurityCheckPage(doc) {
  return /just a moment|attention required/i.test(doc.title)
    || !!doc.querySelector('#challenge-running, #challenge-form, #cf-challenge-running, script[src*="/cdn-cgi/challenge-platform/"]');
}

// Returns { ok: true } on success, otherwise { status, retryAfter } —
// status 0 means a network error. Failures are logged, never thrown.
async function fetchTitle(slug) {
  try {
    const resp = await fetch(`https://www.novelupdates.com/series/${slug}/`, {
      credentials: 'include',
      headers: {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': navigator.language || 'en-US',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'no-cache',
      },
    });
    if (!resp.ok) {
      console.log('[nuf] manager title fetch failed', { slug, status: resp.status });
      return { status: resp.status, retryAfter: resp.headers.get('Retry-After') };
    }
    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    // Same checks as the content scripts (utils.js, prefetch.js): a security
    // check or the login form can come back as HTTP 200, and neither is the series
    if (isSecurityCheckPage(doc)) {
      console.log('[nuf] manager title fetch hit a security check', { slug });
      return { status: 503 };
    }
    if (doc.querySelector('#loginform, .login-form, form[action*="login"], form[id*="login"]')) {
      console.log('[nuf] manager title fetch got the login form — logged out', { slug });
      return { status: 401 };
    }
    const coverEl = doc.querySelector('.seriesimg img, .serieseditimg img');
    data.coverCache[slug] = coverEl?.dataset.src || coverEl?.getAttribute('src') || '';
    const titleEl = doc.querySelector('.seriestitlenu') || doc.querySelector('title');
    const title = titleEl?.textContent.trim().replace(/\s*[|–]\s*NovelUpdates.*$/i, '').trim();
    if (title && !resolveTitle(slug)) data.titleCache[slug] = title;
    await chrome.storage.local.set({ titleCache: data.titleCache, coverCache: data.coverCache });
    if (!resolveTitle(slug)) {
      console.log('[nuf] manager title fetch: no title element', { slug });
      return { status: resp.status };
    }
    document.querySelectorAll(`.item[data-slug="${slug}"]`).forEach(item => {
      item.querySelector('.item-info').innerHTML = itemInfoHtml(slug, resolveTitle(slug));
      const cover = item.querySelector('.item-cover');
      if (cover && resolveCover(slug)) cover.src = resolveCover(slug);
    });
    return { ok: true };
  } catch (error) {
    console.log('[nuf] manager title fetch threw', { slug, error });
    return { status: 0 };
  }
}

// ── Events ─────────────────────────────────────────────────────────────────

function bindEvents() {
  document.getElementById('sort-select').addEventListener('change', e => {
    sortMode = e.target.value;
    try { localStorage.setItem(SORT_KEY, sortMode); } catch { /* storage blocked */ }
    for (const col of Object.keys(COL)) renderList(col);
  });
  document.getElementById('undo-btn').addEventListener('click', undoRemove);
  document.getElementById('queue-btn').addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('queue.html') });
  });

  document.getElementById('export-btn').addEventListener('click', exportAll);
  document.getElementById('import-toggle').addEventListener('click', () =>
    document.getElementById('import-area').classList.toggle('open'));
  document.getElementById('do-import').addEventListener('click', doImport);
  document.getElementById('cancel-import').addEventListener('click', () =>
    document.getElementById('import-area').classList.remove('open'));

  // File-picker import path (defect-054): clicking the button opens the
  // native file picker; on selection, the file's text is fed into the existing
  // import textarea + doImport flow so the user sees what they're importing.
  document.getElementById('import-file-btn').addEventListener('click', () => {
    document.getElementById('import-file').click();
  });
  document.getElementById('import-file').addEventListener('change', async e => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    document.getElementById('import-text').value = text;
    doImport();
    e.target.value = ''; // allow re-selecting the same file later
  });

  document.getElementById('search-read').addEventListener('input', e => { searches.read = e.target.value; renderList('read'); });
  document.getElementById('search-nf').addEventListener('input',   e => { searches.notFinished = e.target.value; renderList('notFinished'); });
  document.getElementById('search-ni').addEventListener('input',   e => { searches.notInterested = e.target.value; renderList('notInterested'); });
  document.getElementById('search-tr').addEventListener('input',   e => { searches.toRead = e.target.value; renderList('toRead'); });

  document.getElementById('sel-all-read').addEventListener('click', () => selectAll('read'));
  document.getElementById('sel-all-nf').addEventListener('click',   () => selectAll('notFinished'));
  document.getElementById('sel-all-ni').addEventListener('click',   () => selectAll('notInterested'));
  document.getElementById('sel-all-tr').addEventListener('click',   () => selectAll('toRead'));

  document.getElementById('clear-read').addEventListener('click', () => clearAll('read'));
  document.getElementById('clear-nf').addEventListener('click',   () => clearAll('notFinished'));
  document.getElementById('clear-ni').addEventListener('click',   () => clearAll('notInterested'));
  document.getElementById('clear-tr').addEventListener('click',   () => clearAll('toRead'));

  document.getElementById('bulk-move-read-nf').addEventListener('click', () => bulkMove('read', 'notFinished'));
  document.getElementById('bulk-move-read-ni').addEventListener('click', () => bulkMove('read', 'notInterested'));
  document.getElementById('bulk-move-read-tr').addEventListener('click', () => bulkMove('read', 'toRead'));
  document.getElementById('bulk-del-read').addEventListener('click',     () => bulkDelete('read'));

  document.getElementById('bulk-move-nf-read').addEventListener('click', () => bulkMove('notFinished', 'read'));
  document.getElementById('bulk-move-nf-ni').addEventListener('click',   () => bulkMove('notFinished', 'notInterested'));
  document.getElementById('bulk-move-nf-tr').addEventListener('click',   () => bulkMove('notFinished', 'toRead'));
  document.getElementById('bulk-del-nf').addEventListener('click',       () => bulkDelete('notFinished'));

  document.getElementById('bulk-move-ni-read').addEventListener('click', () => bulkMove('notInterested', 'read'));
  document.getElementById('bulk-move-ni-nf').addEventListener('click',   () => bulkMove('notInterested', 'notFinished'));
  document.getElementById('bulk-move-ni-tr').addEventListener('click',   () => bulkMove('notInterested', 'toRead'));
  document.getElementById('bulk-del-ni').addEventListener('click',       () => bulkDelete('notInterested'));

  document.getElementById('bulk-move-tr-read').addEventListener('click', () => bulkMove('toRead', 'read'));
  document.getElementById('bulk-move-tr-nf').addEventListener('click',   () => bulkMove('toRead', 'notFinished'));
  document.getElementById('bulk-move-tr-ni').addEventListener('click',   () => bulkMove('toRead', 'notInterested'));
  document.getElementById('bulk-del-tr').addEventListener('click',       () => bulkDelete('toRead'));
}

init();
