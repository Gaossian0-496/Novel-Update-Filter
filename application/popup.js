'use strict';

// ── State ──────────────────────────────────────────────────────────────────

let data = {
  read: [],
  notFinished: [],
  notInterested: [],
  toRead: [],
  excludeLanguages: [],
  excludeTags: [],
  recommendationsEnabled: true,
  minRecommendationScore: 0,
  minRating: 0,
  expandSynopsis: false,
  hideReviews: false,
  prefetchTagsEnabled: true,
};

// ── Init ───────────────────────────────────────────────────────────────────

async function init() {
  buildMinRatingOptions();
  await loadData();
  bindEvents();
}

// Reads every setting the popup shows and redraws it. Also run after an
// import, so the popup never saves stale filters over imported ones.
async function loadData() {
  const stored = await chrome.storage.local.get([
    'read', 'notFinished', 'notInterested', 'toRead', 'excludeLanguages', 'excludeTags',
    'recommendationsEnabled', 'minRecommendationScore',
    'minRating', 'expandSynopsis', 'hideReviews', 'showReasoningChips', 'prefetchTagsEnabled',
    'tagFetchProgress',
  ]);
  data.read             = stored.read || [];
  data.notFinished      = stored.notFinished || [];
  data.notInterested    = stored.notInterested || [];
  data.toRead           = stored.toRead || [];
  data.excludeLanguages = stored.excludeLanguages || [];
  data.excludeTags      = stored.excludeTags || [];
  data.recommendationsEnabled = stored.recommendationsEnabled !== false;
  data.minRecommendationScore = stored.minRecommendationScore || 0;
  data.minRating      = parseFloat(stored.minRating) || 0;
  data.expandSynopsis = !!stored.expandSynopsis;
  data.hideReviews  = !!stored.hideReviews;
  data.showReasoningChips = stored.showReasoningChips !== false;
  data.prefetchTagsEnabled = stored.prefetchTagsEnabled !== false;
  document.getElementById('expand-synopsis-toggle').checked  = data.expandSynopsis;
  document.getElementById('hide-reviews-toggle').checked     = data.hideReviews;
  document.getElementById('show-reasoning-toggle').checked   = data.showReasoningChips;
  document.getElementById('prefetch-tags-toggle').checked    = data.prefetchTagsEnabled;
  renderTagProgress(stored.tagFetchProgress || null);

  renderBadge();
  renderFilters();
  renderRecToggle();
  renderThreshold();
  renderMinRating();
}

// ── Badge ──────────────────────────────────────────────────────────────────

function renderBadge() {
  document.getElementById('read-count').textContent     = `Read ${data.read.length}`;
  document.getElementById('nf-count').textContent       = `Not Finished ${data.notFinished.length}`;
  document.getElementById('ni-count').textContent       = `Not Interested ${data.notInterested.length}`;
  document.getElementById('tr-count').textContent       = `To Read ${data.toRead.length}`;
}

// ── Filters ────────────────────────────────────────────────────────────────

function renderFilters() {
  document.querySelectorAll('.lang-chip').forEach(chip => {
    chip.classList.toggle('active', data.excludeLanguages.includes(chip.dataset.lang));
  });
  document.querySelectorAll('.genre-chip').forEach(chip => {
    chip.classList.toggle('active', data.excludeTags.includes(chip.dataset.tag));
  });
}

function saveFilters() {
  chrome.storage.local.set({
    excludeLanguages: data.excludeLanguages,
    excludeTags: data.excludeTags,
  });
}

// ── Recommendations toggle ─────────────────────────────────────────────────

function renderRecToggle() {
  document.getElementById('rec-toggle').checked = data.recommendationsEnabled;
}

// ── Minimum Novel Updates rating ──────────────────────────────────────────────────────────

// Novel Updates shows ratings to one decimal, so offer every 0.1 step.
const MIN_RATING_FROM = 2.0;
const MIN_RATING_TO   = 4.9;

function buildMinRatingOptions() {
  const sel = document.getElementById('min-rating');
  for (let tenths = MIN_RATING_FROM * 10; tenths <= MIN_RATING_TO * 10; tenths++) {
    const value = tenths / 10;
    const opt = document.createElement('option');
    opt.value = String(value);          // "2", "2.1" … matches minRating.toString()
    opt.textContent = `${value.toFixed(1)}+`;
    sel.appendChild(opt);
  }
}

function renderMinRating() {
  const sel = document.getElementById('min-rating');
  // Select the closest option value ≤ data.minRating
  const opt = [...sel.options].filter(o => parseFloat(o.value) <= data.minRating + 1e-9).pop();
  sel.value = opt ? opt.value : '0';
}

// ── Score threshold ────────────────────────────────────────────────────────

function renderThreshold() {
  const sel = document.getElementById('score-threshold');
  const val = data.minRecommendationScore.toString();
  sel.value = [...sel.options].find(o => o.value === val) ? val : '0';
}

// ── Tag fetch progress ─────────────────────────────────────────────────────

function renderTagProgress(progress) {
  const el = document.getElementById('tag-progress');
  if (!progress || !progress.total) { el.style.display = 'none'; return; }
  const pct = Math.round((progress.done / progress.total) * 100);
  el.style.display = '';
  document.getElementById('tag-progress-fill').style.width = `${pct}%`;
  document.getElementById('tag-progress-label').textContent =
    `Fetching tags: ${progress.done} / ${progress.total}`;
}

// ── Export / Import ────────────────────────────────────────────────────────

// Mirror of manager.js EXPORTABLE_KEYS — both surfaces must produce equal
// payloads so users can export from either and import into either.
const EXPORTABLE_KEYS = [
  'read', 'notFinished', 'notInterested', 'toRead',
  'novelMeta', 'novelReviews', 'titleCache', 'coverCache',
  'excludeLanguages', 'excludeTags',
  'recommendationsEnabled', 'showReasoningChips', 'prefetchTagsEnabled',
  'expandSynopsis', 'hideReviews',
  'minRating', 'minRecommendationScore', 'recencyHalfLifeDays',
  'ratingScaleVersion',
];

async function exportData() {
  // Read fresh from storage — see defect-053. The popup's in-memory `data`
  // only holds a subset; reading from chrome.storage.local captures everything.
  const stored = await chrome.storage.local.get(EXPORTABLE_KEYS);
  const payload = JSON.stringify({ version: 2, ...stored }, null, 2);
  const btn = document.getElementById('export-btn');
  downloadJson(payload);
  const orig = btn.textContent;
  btn.textContent = 'Exported';
  setTimeout(() => { btn.textContent = orig; }, 1500);
  // Also on the clipboard when the browser allows it; the file is the export
  navigator.clipboard.writeText(payload).catch(() => {});
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
      alert('Invalid format. Expected at least { read: [...] } plus optional bucket arrays.');
      return;
    }

    const toSet = {
      read:          parsed.read,
      notFinished:   parsed.notFinished   || [],
      notInterested: parsed.notInterested || [],
      toRead:        parsed.toRead        || [],
    };
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

    chrome.storage.local.set(toSet).then(loadData);
    document.getElementById('import-panel').classList.remove('open');
    document.getElementById('import-text').value = '';
  } catch {
    alert('Invalid JSON.');
  }
}

// ── Events ─────────────────────────────────────────────────────────────────

function bindEvents() {
  // Language chips
  document.querySelectorAll('.lang-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      chip.classList.toggle('active');
      const lang = chip.dataset.lang;
      if (chip.classList.contains('active')) {
        if (!data.excludeLanguages.includes(lang)) data.excludeLanguages.push(lang);
      } else {
        data.excludeLanguages = data.excludeLanguages.filter(l => l !== lang);
      }
      saveFilters();
    });
  });

  // Genre chips
  document.querySelectorAll('.genre-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      chip.classList.toggle('active');
      const tag = chip.dataset.tag;
      if (chip.classList.contains('active')) {
        if (!data.excludeTags.includes(tag)) data.excludeTags.push(tag);
      } else {
        data.excludeTags = data.excludeTags.filter(t => t !== tag);
      }
      saveFilters();
    });
  });

  // Recommendations toggle
  document.getElementById('rec-toggle').addEventListener('change', e => {
    data.recommendationsEnabled = e.target.checked;
    chrome.storage.local.set({ recommendationsEnabled: data.recommendationsEnabled });
  });

  // Score threshold dropdown
  document.getElementById('score-threshold').addEventListener('change', e => {
    data.minRecommendationScore = parseInt(e.target.value, 10) || 0;
    chrome.storage.local.set({ minRecommendationScore: data.minRecommendationScore });
  });

  // Minimum Novel Updates rating select
  document.getElementById('min-rating').addEventListener('change', e => {
    data.minRating = parseFloat(e.target.value) || 0;
    chrome.storage.local.set({ minRating: data.minRating });
  });

  // Expand synopsis toggle
  document.getElementById('expand-synopsis-toggle').addEventListener('change', e => {
    data.expandSynopsis = e.target.checked;
    chrome.storage.local.set({ expandSynopsis: data.expandSynopsis });
  });

  // Hide series reviews toggle
  document.getElementById('hide-reviews-toggle').addEventListener('change', e => {
    data.hideReviews = e.target.checked;
    chrome.storage.local.set({ hideReviews: data.hideReviews });
  });

  // Show reasoning toggle
  document.getElementById('show-reasoning-toggle').addEventListener('change', e => {
    data.showReasoningChips = e.target.checked;
    chrome.storage.local.set({ showReasoningChips: data.showReasoningChips });
  });

  // Prefetch Novel Updates tags toggle
  document.getElementById('prefetch-tags-toggle').addEventListener('change', e => {
    data.prefetchTagsEnabled = e.target.checked;
    chrome.storage.local.set({ prefetchTagsEnabled: data.prefetchTagsEnabled });
  });

  // Reload active tab — spin icon until load completes
  document.getElementById('reload-btn').addEventListener('click', () => {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
      if (!tabs[0]) return;
      const tabId = tabs[0].id;
      const btn = document.getElementById('reload-btn');
      btn.classList.add('reloading');
      btn.disabled = true;
      const timeout = setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(onUpdated);
        btn.classList.remove('reloading');
        btn.disabled = false;
      }, 15000);
      function onUpdated(updatedId, info) {
        if (updatedId === tabId && info.status === 'complete') {
          clearTimeout(timeout);
          chrome.tabs.onUpdated.removeListener(onUpdated);
          btn.classList.remove('reloading');
          btn.disabled = false;
        }
      }
      chrome.tabs.onUpdated.addListener(onUpdated);
      chrome.tabs.reload(tabId);
    });
  });

  // Manage Lists
  document.getElementById('manage-btn').addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('manager.html') });
  });

  // Request queue page
  document.getElementById('queue-btn').addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('queue.html') });
  });

  // Export / Import
  document.getElementById('export-btn').addEventListener('click', exportData);
  document.getElementById('import-toggle').addEventListener('click', () => {
    document.getElementById('import-panel').classList.toggle('open');
  });
  document.getElementById('do-import').addEventListener('click', doImport);
  document.getElementById('cancel-import').addEventListener('click', () => {
    document.getElementById('import-panel').classList.remove('open');
  });

  // File-picker import path (defect-054): mirrors the manager-side flow.
  document.getElementById('import-file-btn').addEventListener('click', () => {
    document.getElementById('import-file').click();
  });
  document.getElementById('import-file').addEventListener('change', async e => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    document.getElementById('import-text').value = text;
    doImport();
    e.target.value = '';
  });

  // Storage changes (badge update when content script modifies buckets)
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.read)          data.read = changes.read.newValue || [];
    if (changes.notFinished)   data.notFinished = changes.notFinished.newValue || [];
    if (changes.notInterested) data.notInterested = changes.notInterested.newValue || [];
    if (changes.toRead)        { data.toRead = changes.toRead.newValue || []; }
    if (changes.read || changes.notFinished || changes.notInterested || changes.toRead) renderBadge();
    if ('tagFetchProgress' in changes) renderTagProgress(changes.tagFetchProgress.newValue || null);
  });

}

init();
