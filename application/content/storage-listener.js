'use strict';

// chrome.storage.onChanged handler for listing pages.

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.read)             state.read          = new Set(changes.read.newValue || []);
  if (changes.notFinished)      state.notFinished   = new Set(changes.notFinished.newValue || []);
  if (changes.notInterested)    state.notInterested = new Set(changes.notInterested.newValue || []);
  const previousToRead = state.toRead;
  if (changes.toRead)           state.toRead        = new Set(changes.toRead.newValue || []);
  if (changes.excludeLanguages) state.excludeLanguages = new Set((changes.excludeLanguages.newValue || []).map(l => l.toLowerCase()));
  if (changes.excludeTags)      state.excludeTags   = (changes.excludeTags.newValue || []).map(t => t.toLowerCase());
  if (changes.novelMeta) {
    // Skip if this event is from our own saveNovelMeta() — preserves any
    // mutations made between set() and the listener firing. Cross-tab writes
    // still apply because they don't set the suppress flag.
    if (_suppressNovelMetaListener) {
      _suppressNovelMetaListener = false;
    } else {
      state.novelMeta = mergeNovelMeta(changes.novelMeta.newValue || {}, state.novelMeta);
      bumpNovelMetaVersion();
      // Cross-tab tag arrivals must trigger a re-score on this tab — otherwise
      // tab A fetches a tag, tab B's cards stay scored from before until the
      // user manually interacts. Debounced inside scheduleRecompute.
      if (_activeConfig) scheduleRecompute();
    }
  }
  if (changes.novelReviews) {
    state.novelReviews = changes.novelReviews.newValue || {};
    bumpNovelMetaVersion();
    if (_activeConfig) applyRecommendations(_activeConfig, true);
  }
  if (changes.recencyHalfLifeDays) state.recencyHalfLifeDays = parseInt(changes.recencyHalfLifeDays.newValue, 10) || 180;
  if (changes.minRating)       state.minRating      = parseFloat(changes.minRating.newValue) || 0;
  if (changes.expandSynopsis)  state.expandSynopsis = !!changes.expandSynopsis.newValue;
  if (changes.hideReviews) {
    state.hideReviews = !!changes.hideReviews.newValue;
    applyHideReviews();
  }

  // Re-apply to any visible unprocessed entries (e.g. tag/lang filter changes)
  // Already-removed entries cannot be restored without a reload
  if (!detectPageType()) return;
  const config = PAGE_CONFIG;
  if (changes.expandSynopsis) {
    const expand = !!changes.expandSynopsis.newValue;
    document.querySelectorAll(config.entrySelector).forEach(entry => {
      if (expand) {
        expandSynopsisOnEntry(entry);
      } else {
        const body = entry.querySelector('.search_body_nu');
        if (!body) return;
        const testhide = body.querySelector('.testhide');
        if (testhide) testhide.style.display = 'none';
        const moreLink = body.querySelector('.morelink');
        if (moreLink) moreLink.style.display = '';
        const dotsEl = body.querySelector('.dots');
        if (dotsEl) dotsEl.style.display = '';
      }
    });
  }
  document.querySelectorAll(`${config.entrySelector}:not([data-nuf-injected])`).forEach(e => processEntry(e, config));
  // Also re-check visible entries for new language/tag filters
  document.querySelectorAll(`${config.entrySelector}[data-nuf-slug]`).forEach(entry => {
    if (entry._nufUndoGuard) return; // skip recently-undone entries awaiting storage sync
    const slug = entry.dataset.nufSlug;
    if (shouldFilter(slug, entry, config)) {
      const wrapper = entry._nufWrapper;
      if (entry._nufControls) entry._nufControls.remove();
      // Only add an animationend listener if not already being removed — markEntry's listener
      // handles removal for cards the user just bucketed; adding a second listener would remove
      // the wrapper, orphaning the undo re-insertion and making undo silently fail.
      if (!entry.classList.contains('nuf-removing')) {
        entry.classList.add('nuf-removing');
        entry.addEventListener('animationend', () => {
          entry.remove();
          if (wrapper && wrapper.parentNode && !wrapper.children.length) wrapper.remove();
        }, { once: true });
      }
    }
  });

  // Redraw the sidebar when the To Read list or novelMeta (cover, synopsis)
  // changes; the redraw is debounced and waits for a sidebar Start reading
  if (changes.toRead || changes.novelMeta) scheduleSidebarRender();
  // Novels added to To Read elsewhere (manager, another tab) need their cover
  if (changes.toRead) {
    for (const slug of state.toRead) if (!previousToRead.has(slug)) enqueueFetch(slug);
  }

  // Recompute recommendations when buckets, recency setting, or toggle change
  // forceAll = true: signal data changed, so all existing scores must be recalculated
  if (changes.read || changes.notFinished || changes.notInterested || changes.toRead || changes.recencyHalfLifeDays) {
    applyRecommendations(config, true);
  }
  if (changes.recommendationsEnabled) {
    state.recommendationsEnabled = changes.recommendationsEnabled.newValue !== false;
    if (state.recommendationsEnabled) applyRecommendations(config, true);
    else clearRecommendations(config);
  }
  if (changes.minRecommendationScore) {
    state.minRecommendationScore = changes.minRecommendationScore.newValue || 0;
    if (detectPageType()) applyAllThresholds(PAGE_CONFIG);
  }
  if (changes.showReasoningChips) {
    state.showReasoningChips = changes.showReasoningChips.newValue !== false;
    if (detectPageType()) {
      document.querySelectorAll('.nuf-reason-row').forEach(row => {
        row.style.display = state.showReasoningChips ? '' : 'none';
      });
    }
  }
  if (changes.prefetchTagsEnabled) {
    state.prefetchTagsEnabled = changes.prefetchTagsEnabled.newValue !== false;
    if (!state.prefetchTagsEnabled) {
      stopPrefetch();
      // No tags are coming, so cards waiting on them are decided now
      applyAllThresholds(PAGE_CONFIG, false);
    } else {
      // Re-enqueue bucketed slugs (need tags for scoring) AND currently-visible
      // card slugs (need tags for their own reasoning chip row). The previous
      // `for slug of Object.keys(state.novelMeta)` loop was the wrong shape:
      // it included thousands of unbucketed-history slugs no longer on the
      // page — defect-028's lesson. Targeting `[data-nuf-slug]` on the live
      // DOM gives us the right set: bucketed refs (off-page) + visible cards.
      for (const key of ['read', 'notFinished', 'notInterested', 'toRead']) {
        for (const slug of state[key]) enqueueFetch(slug);
      }
      // The user's own change: cards without tags wait for them again before
      // the threshold decides
      applyAllThresholds(PAGE_CONFIG);
      // enqueueWhenVisible also queues cards hidden by the score threshold,
      // which never come into view
      document.querySelectorAll(`${PAGE_CONFIG.entrySelector}[data-nuf-slug]`).forEach(el => {
        enqueueWhenVisible(el, el.dataset.nufSlug);
      });
    }
  }

  // Rescoring above (bucket, threshold or recommendation changes) can hide
  // cards below the threshold; they never come into view, so queue them now
  scheduleHiddenCardLookups();
});
