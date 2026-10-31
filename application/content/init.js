'use strict';

// Extension entry point.

async function init() {
  if (isSeriesPage()) {
    initSeriesPage();
    return;
  }

  const pageType = detectPageType();
  if (!pageType) return;
  // Cloudflare's security check is served at the listing URL: leave it alone
  // (Cloudflare's script also appears on normal pages, so only when there are no cards)
  if (_isChallengePage(document) && !document.querySelector(PAGE_CONFIG.entrySelector)) return;

  // Disable browser scroll restoration on listing pages — appended infinite-scroll
  // cards are not present after reload, so restoring scroll position lands the
  // user in a now-shorter document. Pinning at top is more useful (defect-046).
  if (history.scrollRestoration) history.scrollRestoration = 'manual';
  window.scrollTo(0, 0);

  await loadState();

  const config = PAGE_CONFIG;
  _activeConfig = config;
  const container = document.querySelector(config.containerSelector);

  document.addEventListener('click', () => {
    document.querySelectorAll('.nuf-explain-panel').forEach(p => p.remove());
  });

  requestQueue.trackVisibility();  // background lookups run only while this tab is visible
  buildToReadSidebar();
  processContainer(container, config);
  applyRecommendations(config);
  // Only bucketed slugs — unbucketed novelMeta slugs would flood Cloudflare
  for (const key of ['read', 'notFinished', 'notInterested', 'toRead']) {
    for (const slug of state[key]) enqueueFetch(slug);
  }

  if (config.hasPagination && container) {
    setupInfiniteScroll(config, container);
  }
}

init();
