'use strict';

// Shared mutable state and page configuration.

// DOM element properties attached to entry nodes (set by various modules):
//   _nufControls     — the .nuf-controls bar injected before the entry
//   _nufWrapper      — the .nuf-entry-wrap div containing controls + entry
//   _nufScore        — normalised score integer (1–10) from last render
//   _nufRaw          — pre-normalisation raw score float
//   _nufBreakdown    — { pos, neg } arrays of per-reference contributions
//   _nufUndoGuard    — truthy for ~1s after undo to suppress re-filter
//   _nufOriginalIdx  — insertion order index assigned during infinite scroll
//   _nufOpen         — truthy when the explanation panel is open

let _activeConfig = null;
let _entryIdx = 0;

// Bumped on any in-memory mutation of novelMeta or novelReviews. computeIDF
// caches its result against this version; per-card token caches are cleared
// only when their own slug's tags/desc change, so this counter doesn't gate them.
let _novelMetaVersion = 0;
function bumpNovelMetaVersion() { _novelMetaVersion++; }

// Set by saveNovelMeta() right before chrome.storage.local.set so the same-tab
// onChanged listener can skip replacing state.novelMeta with a snapshot it just
// wrote — preserving any in-flight mutations made between the set() and the
// listener firing. Cross-tab writes still apply normally.
let _suppressNovelMetaListener = false;
const NOVEL_META_SUPPRESS_MS = 1000;

const state = {
  read: new Set(),
  notFinished: new Set(),
  notInterested: new Set(),
  toRead: new Set(),
  excludeLanguages: new Set(),
  excludeTags: [],        // lowercase strings
  novelMeta: {},          // slug → { g, l, d, t, ts?, c1url?, img?, desc? }
  recommendationsEnabled: true,
  recencyHalfLifeDays: 180,
  minRecommendationScore: 0,
  minRating: 0,
  expandSynopsis: false,
  hideReviews: false,
  novelReviews: {},         // slug → { rating?: number /* 1-5 */, comment?: string }
};

const PAGE_CONFIG = {
  entrySelector: '.search_main_box_nu',
  titleLinkSelector: '.search_title a',
  containerSelector: '.w-blog-content.other',
  tagSelector: 'a.gennew',
  hasPagination: true,
};
