'use strict';

// Chrome storage load/save and novelMeta pruning.

// Set when the extension is reloaded or updated under an open tab: from then on
// every chrome.* call in this content script throws "Extension context
// invalidated", so storage writes and background fetching stop (defect-057).
let extensionContextLost = false;
let _quotaWarned = false;

// Classifies a failed chrome.* call and logs it once instead of letting it
// surface as an uncaught error. Returns 'lost' | 'quota' | 'other'.
function noteChromeError(err) {
  const message = String(err?.message || err);
  if (/context invalidated/i.test(message)) {
    if (!extensionContextLost) console.log('[nuf] The extension was reloaded — reload this tab to turn the filter back on');
    extensionContextLost = true;
    return 'lost';
  }
  if (/quota/i.test(message)) {
    if (!_quotaWarned) console.log('[nuf] Extension storage is full; trimming saved novel data', message);
    _quotaWarned = true;
    return 'quota';
  }
  console.log('[nuf] Storage write failed', message);
  return 'other';
}

// All content-script storage writes go through these two helpers. They never
// reject: they resolve to null on success or to the error kind on failure.
function storageSet(items) {
  if (extensionContextLost) return Promise.resolve('lost');
  try {
    return chrome.storage.local.set(items).then(() => null, noteChromeError);
  } catch (err) {
    return Promise.resolve(noteChromeError(err));
  }
}

function storageRemove(keys) {
  if (extensionContextLost) return Promise.resolve('lost');
  try {
    return chrome.storage.local.remove(keys).then(() => null, noteChromeError);
  } catch (err) {
    return Promise.resolve(noteChromeError(err));
  }
}

async function loadState() {
  const data = await chrome.storage.local.get([
    'read', 'notFinished', 'notInterested', 'toRead', 'excludeLanguages', 'excludeTags',
    'novelMeta', 'novelReviews', 'recommendationsEnabled', 'recencyHalfLifeDays', 'minRecommendationScore',
    'minRating', 'expandSynopsis', 'hideReviews', 'showReasoningChips', 'prefetchTagsEnabled',
    'ratingScaleVersion',
  ]);
  state.read          = new Set(data.read || []);
  state.notFinished   = new Set(data.notFinished || []);
  state.notInterested = new Set(data.notInterested || []);
  state.toRead        = new Set(data.toRead || []);
  state.excludeLanguages = new Set((data.excludeLanguages || []).map(l => l.toLowerCase()));
  state.excludeTags   = (data.excludeTags || []).map(t => t.toLowerCase());
  state.novelMeta     = data.novelMeta || {};
  state.novelReviews  = data.novelReviews || {};

  // One-time migration: rating scale 1-10 → 1-5 (defect-049).
  // Maps via Math.round(old / 2) clamped to [1, 5]: 10→5, 7→4, 5→3, 1→1.
  // Gated on ratingScaleVersion so subsequent loads skip.
  if ((data.ratingScaleVersion || 1) < 2) {
    let migrated = false;
    for (const review of Object.values(state.novelReviews)) {
      if (review && review.rating != null) {
        review.rating = Math.max(1, Math.min(5, Math.round(review.rating / 2)));
        migrated = true;
      }
    }
    storageSet({
      novelReviews: state.novelReviews,
      ratingScaleVersion: 2,
    });
    if (migrated) bumpNovelMetaVersion();
  }

  // Migrate pre-bigram entries: stored d arrays contain only unigrams in text order,
  // so we can derive bigrams from adjacent pairs right now without re-browsing.
  let migrated = false;
  for (const meta of Object.values(state.novelMeta)) {
    if (!meta.d || meta.d.length < 2 || meta.d.some(t => t.includes(' '))) continue;
    const words = meta.d; // unigrams in insertion (text) order
    const len = words.length;
    for (let i = 0; i < len - 1; i++) {
      meta.d.push(words[i] + ' ' + words[i + 1]);
    }
    migrated = true;
  }
  if (migrated) scheduleMetaSave();

  state.recommendationsEnabled = data.recommendationsEnabled !== false;
  state.recencyHalfLifeDays = parseInt(data.recencyHalfLifeDays, 10) || 180;
  state.minRecommendationScore = data.minRecommendationScore || 0;
  state.minRating      = parseFloat(data.minRating) || 0;
  state.expandSynopsis = !!data.expandSynopsis;
  state.hideReviews    = !!data.hideReviews;
  state.showReasoningChips = data.showReasoningChips !== false;
  state.prefetchTagsEnabled = data.prefetchTagsEnabled !== false;
}

function saveBuckets() {
  storageSet({
    read: [...state.read],
    notFinished: [...state.notFinished],
    notInterested: [...state.notInterested],
    toRead: [...state.toRead],
  });
}

function saveNovelMeta(isRetry = false) {
  pruneNovelMeta();
  bumpNovelMetaVersion();
  _suppressNovelMetaListener = true;
  return storageSet({ novelMeta: state.novelMeta }).then(failure => {
    if (!failure) {
      // Writing an unchanged value fires no onChanged event: clear the flag
      // anyway so the next change from another tab is not ignored
      setTimeout(() => { _suppressNovelMetaListener = false; }, NOVEL_META_SUPPRESS_MS);
      return;
    }
    _suppressNovelMetaListener = false;  // no onChanged event will arrive to clear it
    // Storage full (unlimitedStorage missing or revoked): keep the most useful
    // half of the unbucketed entries and try once more.
    if (failure === 'quota' && !isRetry && _novelMetaCap > 500) {
      _novelMetaCap = Math.floor(_novelMetaCap / 2);
      return saveNovelMeta(true);
    }
  });
}

function saveNovelReviews() {
  bumpNovelMetaVersion();
  return storageSet({ novelReviews: state.novelReviews });
}

// Saves 2 s after the last change, but never more than 5 s after the first
// unsaved one: lookups arriving every couple of seconds would otherwise keep
// pushing the save back, and only the unload flush below would write it.
const META_SAVE_DELAY_MS = 2000;
const META_SAVE_MAX_WAIT_MS = 5000;
let _metaTimer = null;
let _metaPendingSince = 0;
// Another tab saved its whole novelMeta copy. Its values win, but this tab
// fills in what that copy lacks (tags, a first-chapter link, a cover or
// synopsis it fetched and has not saved yet) and keeps the newer recency date.
// Entries missing from that copy were removed there (pruned, or replaced by an
// import), so they come back only while this tab has its own save pending. A
// placeholder cover is not filled in: tabs drop it on purpose so the cover
// lookup runs.
// Anything filled in is saved, or it would live only in this tab's memory.
function mergeNovelMeta(incoming, local) {
  let filledIn = false;
  const pendingSave = !!_metaTimer;
  for (const [slug, mine] of Object.entries(local)) {
    if (!mine) continue;
    const theirs = incoming[slug];
    if (!theirs) {
      if (pendingSave) { incoming[slug] = mine; filledIn = true; }
      continue;
    }
    for (const [key, value] of Object.entries(mine)) {
      if (key === 'img' && value && !isRealCoverUrl(value)) continue;
      const lacking = theirs[key] === undefined
        || (Array.isArray(theirs[key]) && !theirs[key].length && Array.isArray(value) && value.length > 0);
      if (lacking && value !== undefined) { theirs[key] = value; filledIn = true; }
    }
    if ((mine.ts || 0) > (theirs.ts || 0)) { theirs.ts = mine.ts; filledIn = true; }
  }
  if (filledIn) scheduleMetaSave();
  return incoming;
}

function scheduleMetaSave() {
  bumpNovelMetaVersion();
  clearTimeout(_metaTimer);
  const now = Date.now();
  if (!_metaPendingSince) _metaPendingSince = now;
  const wait = Math.max(0, Math.min(META_SAVE_DELAY_MS, _metaPendingSince + META_SAVE_MAX_WAIT_MS - now));
  _metaTimer = setTimeout(() => {
    _metaTimer = null;
    _metaPendingSince = 0;
    saveNovelMeta();
  }, wait);
}

// Best-effort flush of a pending novelMeta debounce on tab unload. Without
// this a quick reload after a tag fetch loses the just-fetched tags (the
// 2 s debounce hadn't fired yet). chrome.storage.local.set is async — there
// is no synchronous storage API — so this kicks off the write and hopes the
// browser holds the unload long enough for it to land. Chrome typically does
// for chrome.storage writes from extension contexts.
window.addEventListener('beforeunload', () => {
  if (_metaTimer) {
    clearTimeout(_metaTimer);
    _metaTimer = null;
    _metaPendingSince = 0;
    saveNovelMeta();
  }
});

const NOVEL_META_MAX = 3000;
let _novelMetaCap = NOVEL_META_MAX;  // lowered for the session if storage fills up

function pruneNovelMeta() {
  const entries = Object.entries(state.novelMeta);
  if (entries.length <= _novelMetaCap) return;

  const bucketed = new Set([...state.read, ...state.notFinished, ...state.notInterested, ...state.toRead]);
  const unbucketed = entries.filter(([slug]) => !bucketed.has(slug));
  // Eviction order: tag-less entries first, then by timestamp (oldest first).
  // Preserves the expensive Novel Updates tag prefetch work — without this preference,
  // listing-visited entries (which never get a `ts` stamp) would all tie at
  // 0 and freshly-fetched tags could be evicted by chance (defect-050).
  unbucketed.sort((a, b) => {
    const aHasTags = a[1].tags?.length ? 1 : 0;
    const bHasTags = b[1].tags?.length ? 1 : 0;
    if (aHasTags !== bHasTags) return aHasTags - bHasTags;
    return (a[1].ts || 0) - (b[1].ts || 0);
  });
  const toRemove = unbucketed.slice(0, entries.length - _novelMetaCap);
  for (const [slug] of toRemove) delete state.novelMeta[slug];
}
