'use strict';

// Collapsible To Read sidebar panel.

let _sidebarPanel = null;
let _sidebarTab = null;

function buildToReadSidebar() {
  const tab = document.createElement('button');
  tab.className = 'nuf-sidebar-tab';
  tab.title = 'Open To Read list';
  document.body.appendChild(tab);
  _sidebarTab = tab;

  const panel = document.createElement('div');
  panel.className = 'nuf-sidebar-panel';
  panel.setAttribute('aria-label', 'To Read list');

  const header = document.createElement('div');
  header.className = 'nuf-sidebar-header';
  header.innerHTML = '<span>To Read</span>';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'nuf-sidebar-close';
  closeBtn.title = 'Close';
  closeBtn.textContent = '✕';
  closeBtn.addEventListener('click', () => {
    panel.style.right = '-600px';
    tab.dataset.open = '';
  });
  header.appendChild(closeBtn);
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'nuf-sidebar-body';
  panel.appendChild(body);

  document.body.appendChild(panel);
  _sidebarPanel = panel;

  tab.addEventListener('click', () => {
    const isOpen = tab.dataset.open === '1';
    if (isOpen) {
      panel.style.right = '-600px';
      tab.dataset.open = '';
    } else {
      panel.style.right = '0';
      tab.dataset.open = '1';
    }
    updateSidebarTab();
  });

  // Open by default when novels are already queued
  if (state.toRead.size > 0) {
    panel.style.right = '0';
    tab.dataset.open = '1';
  }

  renderToReadSidebar();
}

function updateSidebarTab() {
  if (!_sidebarTab) return;
  const count = state.toRead.size;
  const isOpen = _sidebarTab.dataset.open === '1';
  _sidebarTab.textContent = `To Read (${count}) ${isOpen ? '◀' : '▶'}`;
}

// When To Read novels have no real cover but their cards are on this page,
// take cover, title and synopsis from the cards — no request needed
// (defect-056). captureMetadata already does this as each card is processed
// (and redraws the sidebar); this catches a cross-tab novelMeta write that
// replaced those fields. Novels known to have no cover (img === '') are
// skipped, and the page is searched once per render, not once per novel.
function _fillSidebarMetaFromCards() {
  const missing = [...state.toRead].filter(slug => {
    const img = state.novelMeta[slug]?.img;
    return img !== '' && !isRealCoverUrl(img);
  });
  if (!missing.length) return;
  const config = _activeConfig || PAGE_CONFIG;
  const cards = new Map();
  for (const card of document.querySelectorAll(`${config.entrySelector}[data-nuf-slug]`)) {
    cards.set(card.dataset.nufSlug, card);
  }
  let changed = false;
  for (const slug of missing) {
    const card = cards.get(slug);
    if (!card) continue;
    const meta = state.novelMeta[slug] || (state.novelMeta[slug] = {});
    if (applyCardSidebarFields(meta, card, config)) changed = true;
  }
  if (changed) scheduleMetaSave();
}

// Takes a novel out of the sidebar at once: the full redraw waits while a
// Start reading lookup runs, and the novel's buttons would stay clickable meanwhile
function _removeSidebarItem(item) {
  item.remove();
  if (!state.toRead.size) renderToReadSidebar();
  else updateSidebarTab();
}

function renderToReadSidebar() {
  if (!_sidebarPanel) return;
  const body = _sidebarPanel.querySelector('.nuf-sidebar-body');
  if (!body) return;

  body.innerHTML = '';

  if (state.toRead.size === 0) {
    body.innerHTML = '<div class="nuf-sidebar-empty">No novels queued yet.</div>';
    updateSidebarTab();
    return;
  }

  _fillSidebarMetaFromCards();
  for (const slug of state.toRead) {
    const meta = state.novelMeta[slug] || {};
    const title = meta.t || slug;
    const seriesUrl = `https://www.novelupdates.com/series/${slug}/`;

    const item = document.createElement('div');
    item.className = 'nuf-sidebar-item';

    // Cover image (links to Novel Updates series page)
    const coverLink = document.createElement('a');
    coverLink.href = seriesUrl;
    coverLink.target = '_blank';
    coverLink.rel = 'noopener';
    coverLink.className = 'nuf-sidebar-cover-link';
    const placeholder = () => {
      const el = document.createElement('div');
      el.className = 'nuf-sidebar-img nuf-sidebar-img-placeholder';
      return el;
    };
    if (isRealCoverUrl(meta.img)) {
      const img = document.createElement('img');
      img.className = 'nuf-sidebar-img';
      img.alt = title;  // a property, so plain text: no escaping
      // A cover that fails to load shows the placeholder, not squashed alt text
      img.addEventListener('error', () => img.replaceWith(placeholder()), { once: true });
      img.src = meta.img;
      coverLink.appendChild(img);
    } else {
      coverLink.appendChild(placeholder());
    }
    item.appendChild(coverLink);

    const info = document.createElement('div');
    info.className = 'nuf-sidebar-info';

    const titleLink = document.createElement('a');
    titleLink.href = seriesUrl;
    titleLink.target = '_blank';
    titleLink.rel = 'noopener';
    titleLink.className = 'nuf-sidebar-title';
    titleLink.textContent = title;
    info.appendChild(titleLink);

    if (meta.desc) {
      const PREVIEW_LEN = 200;
      const needsTruncation = meta.desc.length > PREVIEW_LEN;
      const descEl = document.createElement('p');
      descEl.className = 'nuf-sidebar-desc';

      const textNode = document.createTextNode(
        needsTruncation ? meta.desc.slice(0, PREVIEW_LEN) + '… ' : meta.desc
      );
      descEl.appendChild(textNode);

      if (needsTruncation) {
        const moreBtn = document.createElement('button');
        moreBtn.className = 'nuf-sidebar-desc-toggle';
        moreBtn.textContent = 'more';
        moreBtn.title = 'Show the full description';
        let expanded = false;
        moreBtn.addEventListener('click', () => {
          expanded = !expanded;
          textNode.textContent = expanded ? meta.desc + ' ' : meta.desc.slice(0, PREVIEW_LEN) + '… ';
          moreBtn.textContent = expanded ? 'less' : 'more';
          moreBtn.title = expanded ? 'Show a shorter description' : 'Show the full description';
        });
        descEl.appendChild(moreBtn);
      }

      info.appendChild(descEl);
    }

    const startBtn = document.createElement('button');
    startBtn.className = 'nuf-btn nuf-sidebar-start';
    startBtn.textContent = 'Start reading →';
    bindStartReadingButton(startBtn, slug);
    const bucketBtns = document.createElement('div');
    bucketBtns.className = 'nuf-sidebar-bucket-btns';
    const bucketDefs = [
      { key: 'read',          nufBtn: 'read', label: 'Read →' },
      { key: 'notFinished',   nufBtn: 'nf',   label: 'Not Finished →' },
      { key: 'notInterested', nufBtn: 'ni',   label: 'Not Interested →' },
    ];
    for (const { key, nufBtn, label } of bucketDefs) {
      const btn = document.createElement('button');
      btn.className = 'nuf-btn nuf-sidebar-bucket-btn';
      btn.dataset.nufBtn = nufBtn;
      btn.textContent = label;
      btn.title = `Move to ${BUCKET_LABEL[key]} — removes it from To Read`;
      btn.addEventListener('click', e => {
        e.preventDefault();
        state.toRead.delete(slug);
        // One list only, even when a second move button is pressed
        for (const other of ['read', 'notFinished', 'notInterested']) state[other].delete(slug);
        state[key].add(slug);
        saveBuckets();
        state.novelMeta[slug] = { ...(state.novelMeta[slug] || {}), ts: Date.now() };
        scheduleMetaSave();
        _removeSidebarItem(item);
      });
      bucketBtns.appendChild(btn);
    }

    const actions = document.createElement('div');
    actions.className = 'nuf-sidebar-actions';
    actions.appendChild(startBtn);
    const separator = document.createElement('span');
    separator.className = 'nuf-sidebar-actions-sep';
    separator.textContent = '|';
    actions.appendChild(separator);
    actions.appendChild(bucketBtns);
    info.appendChild(actions);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'nuf-sidebar-remove';
    removeBtn.title = 'Remove from To Read';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', e => {
      e.preventDefault();
      state.toRead.delete(slug);
      saveBuckets();
      const card = document.querySelector(`[data-nuf-slug="${slug}"]`);
      if (card) {
        card._nufWrapper?.classList.remove('nuf-to-read');
        card.classList.remove('nuf-to-read');
        card._nufControls?.querySelector('.nuf-btn[data-nuf-btn="toread"]')
          ?.classList.remove('nuf-toread-active');
      }
      if (_activeConfig) applyRecommendations(_activeConfig, true);
      _removeSidebarItem(item);
    });

    item.appendChild(removeBtn);
    item.appendChild(info);
    body.appendChild(item);
  }

  updateSidebarTab();
}
