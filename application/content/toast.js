'use strict';

// Undo toast and error banner.

const TOAST_DISMISS_MS = 10000; // 10 s — enough time to rate + comment without rush
const TOAST_WIDTH_PX   = 320;
const TOAST_ANCHOR_GAP = 16;

let toastEl = null;
let toastTimer = null;
let pendingUndo = null; // { slug, bucket, el, parent, nextSibling, wrapperParent?, wrapperNext?, previous?, reviewBefore?, onUndo? }

function ensureToast() {
  if (toastEl) return;
  toastEl = document.createElement('div');
  toastEl.className = 'nuf-toast';
  toastEl.innerHTML = `
    <div class="nuf-toast-head">
      <div class="nuf-toast-head-text">
        <div class="nuf-toast-heading">Marked as <span class="nuf-toast-bucket-label"></span></div>
        <div class="nuf-toast-title"></div>
      </div>
      <button class="nuf-toast-close" title="Dismiss">×</button>
    </div>
    <div class="nuf-toast-review">
      <div class="nuf-toast-review-title">Rate this novel</div>
      <div class="nuf-toast-rating"></div>
      <input class="nuf-toast-comment" type="text" placeholder="Optional review (helps recommendations)" maxlength="280">
    </div>
    <div class="nuf-toast-actions">
      <button class="nuf-toast-undo" title="Undo this action">Undo</button>
      <button class="nuf-toast-save" title="Save and close">Save</button>
    </div>
    <div class="nuf-toast-progress"></div>
  `;
  toastEl.querySelector('.nuf-toast-undo').addEventListener('click', undoLast);
  toastEl.querySelector('.nuf-toast-close').addEventListener('click', _dismissToast);
  toastEl.querySelector('.nuf-toast-save').addEventListener('click', _saveAndDismiss);

  // Populate rating buttons 1–5
  const ratingRow = toastEl.querySelector('.nuf-toast-rating');
  for (let n = 1; n <= 5; n++) {
    const btn = document.createElement('button');
    btn.className = 'nuf-toast-rating-btn';
    btn.textContent = String(n);
    btn.title = `Rate ${n} out of 5`;
    btn.addEventListener('click', () => {
      const slug = toastEl.dataset.nufSlug;
      if (!slug) return;
      state.novelReviews[slug] = { ...(state.novelReviews[slug] || {}), rating: n };
      saveNovelReviews();
      ratingRow.querySelectorAll('.nuf-toast-rating-btn').forEach(b => b.classList.remove('nuf-toast-rating-btn-active'));
      btn.classList.add('nuf-toast-rating-btn-active');
      _resumeToastTimer(); // give user more time to also add a comment
    });
    ratingRow.appendChild(btn);
  }

  // Comment input: pause timer on focus, persist on blur/Enter, save explicit via Save button
  // The slug is remembered on focus: a blur that arrives after the toast has
  // hidden, or after it has been reused for another novel, must not save the
  // text or restart the countdown.
  const commentInput = toastEl.querySelector('.nuf-toast-comment');
  commentInput.addEventListener('focus', () => {
    commentInput.dataset.nufFocusSlug = toastEl.dataset.nufSlug || '';
    clearTimeout(toastTimer);
    _freezeToastProgress();
  });
  commentInput.addEventListener('blur', () => {
    const focusSlug = commentInput.dataset.nufFocusSlug;
    delete commentInput.dataset.nufFocusSlug;
    if (!toastEl.classList.contains('nuf-toast-visible')) return;
    if (focusSlug !== (toastEl.dataset.nufSlug || '')) return;
    _persistComment();
    _resumeToastTimer();
  });
  commentInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.isComposing) _saveAndDismiss();
  });

  document.body.appendChild(toastEl);
}

function _persistComment() {
  const commentInput = toastEl.querySelector('.nuf-toast-comment');
  const slug = toastEl.dataset.nufSlug;
  if (!slug) return;
  const val = commentInput.value.trim();
  if (val) {
    state.novelReviews[slug] = { ...(state.novelReviews[slug] || {}), comment: val };
  } else if (state.novelReviews[slug]) {
    delete state.novelReviews[slug].comment;
  }
  saveNovelReviews();
}

function _saveAndDismiss() {
  _persistComment();
  _dismissToast();
}

function _dismissToast() {
  clearTimeout(toastTimer);
  toastEl.classList.remove('nuf-toast-visible');
  pendingUndo = null;
  _releaseCommentFocus();
}

// Called after the toast is hidden, so the blur handler sees a hidden toast
// and does nothing.
function _releaseCommentFocus() {
  const commentInput = toastEl.querySelector('.nuf-toast-comment');
  if (document.activeElement === commentInput) commentInput.blur();
}

// (Re)starts the full dismiss countdown; the progress bar restarts with it so
// it always shows the time actually left.
function _resumeToastTimer() {
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.classList.remove('nuf-toast-visible');
    pendingUndo = null;
  }, TOAST_DISMISS_MS);
  const progress = toastEl.querySelector('.nuf-toast-progress');
  progress.style.transition = 'none';
  progress.style.width = '100%';
  void progress.offsetWidth; // commit the reset before animating
  progress.style.transition = `width ${TOAST_DISMISS_MS}ms linear`;
  progress.style.width = '0%';
}

// Holds the progress bar where it is while the timer is paused.
function _freezeToastProgress() {
  const progress = toastEl.querySelector('.nuf-toast-progress');
  const width = getComputedStyle(progress).width;
  progress.style.transition = 'none';
  progress.style.width = width;
}

function _positionToast(anchor) {
  // Reset inline overrides so an absent anchor falls back to CSS bottom-centre.
  toastEl.style.left = '';
  toastEl.style.top = '';
  toastEl.style.bottom = '';
  toastEl.style.transform = '';

  if (!anchor) return;

  const rect = typeof anchor.getBoundingClientRect === 'function'
    ? anchor.getBoundingClientRect()
    : anchor;
  if (!rect || (rect.width === 0 && rect.height === 0)) return;

  // Prefer the right of the card. If the card fills most of the viewport,
  // try the left. If neither fits, pin to the viewport right edge at the
  // card's vertical position — still near the click, not bottom-centred.
  let left = rect.right + TOAST_ANCHOR_GAP;
  if (left + TOAST_WIDTH_PX > window.innerWidth - 8) {
    const altLeft = rect.left - TOAST_WIDTH_PX - TOAST_ANCHOR_GAP;
    left = altLeft >= 8 ? altLeft : (window.innerWidth - TOAST_WIDTH_PX - 8);
  }

  toastEl.style.left = Math.max(8, left) + 'px';
  // Beside the card, but never below the window's bottom edge (Undo must stay reachable)
  const height = toastEl.offsetHeight || 200;
  toastEl.style.top = Math.max(8, Math.min(rect.top, window.innerHeight - height - 8)) + 'px';
  toastEl.style.bottom = 'auto';
  toastEl.style.transform = 'none';
}

function showError(msg) {
  let el = document.getElementById('nuf-error-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'nuf-error-banner';
    el.className = 'nuf-error-banner';
    el.innerHTML = '<span class="nuf-error-msg"></span><button class="nuf-error-close" title="Close">✕</button>';
    el.querySelector('.nuf-error-close').addEventListener('click', () => el.remove());
    document.body.appendChild(el);
  }
  el.querySelector('.nuf-error-msg').textContent = msg;
}

function showToast(title, bucket, slug, anchor) {
  ensureToast();
  toastEl.querySelector('.nuf-toast-title').textContent = title;
  toastEl.querySelector('.nuf-toast-bucket-label').textContent = BUCKET_LABEL[bucket] || bucket;

  // Apply bucket-specific accent class (left border colour + bucket label colour)
  toastEl.classList.remove('nuf-toast-read', 'nuf-toast-nf', 'nuf-toast-ni', 'nuf-toast-tr');
  const bucketCls = { read: 'nuf-toast-read', notFinished: 'nuf-toast-nf', notInterested: 'nuf-toast-ni', toRead: 'nuf-toast-tr' }[bucket];
  if (bucketCls) toastEl.classList.add(bucketCls);

  toastEl.classList.add('nuf-toast-visible');

  // Show review row + Save button only for Read and Not Finished
  const showReview = bucket === 'read' || bucket === 'notFinished';
  toastEl.querySelector('.nuf-toast-review').style.display = showReview ? '' : 'none';
  toastEl.querySelector('.nuf-toast-save').style.display = showReview ? '' : 'none';

  // Store slug for rating/comment/save handlers
  toastEl.dataset.nufSlug = slug || '';
  if (pendingUndo?.slug === slug && !('reviewBefore' in pendingUndo)) {
    pendingUndo.reviewBefore = state.novelReviews[slug] ? { ...state.novelReviews[slug] } : null;
  }

  // Reset review row state and pre-fill from existing review if present
  const ratingRow = toastEl.querySelector('.nuf-toast-rating');
  ratingRow.querySelectorAll('.nuf-toast-rating-btn').forEach(b => b.classList.remove('nuf-toast-rating-btn-active'));
  const commentInput = toastEl.querySelector('.nuf-toast-comment');
  commentInput.value = '';
  if (slug && showReview) {
    const existing = state.novelReviews[slug];
    if (existing?.rating != null) {
      const btn = ratingRow.querySelectorAll('.nuf-toast-rating-btn')[existing.rating - 1];
      if (btn) btn.classList.add('nuf-toast-rating-btn-active');
    }
    if (existing?.comment) commentInput.value = existing.comment;
  }

  _positionToast(anchor);
  // Starts the dismiss timer and the horizontal countdown bar across the bottom edge.
  _resumeToastTimer();
}

// Puts back what the mark replaced: the list the novel was in, and To Read
function _restorePrevious(slug) {
  const previous = pendingUndo.previous;
  if (previous?.bucket) state[previous.bucket].add(slug);
  if (previous?.toRead) state.toRead.add(slug);
}

function undoLast() {
  if (!pendingUndo) return;
  const { slug, bucket, el, controls, parent, nextSibling } = pendingUndo;

  // A rating or comment given in this toast goes too (it would keep weighting scores)
  if ('reviewBefore' in pendingUndo) {
    if (pendingUndo.reviewBefore) state.novelReviews[slug] = pendingUndo.reviewBefore;
    else delete state.novelReviews[slug];
    saveNovelReviews();
  }

  if (bucket === 'toRead') {
    // To Read does not remove the element from DOM, just toggle state and styling.
    // el is null on series-page undo (no card to manipulate); guard accordingly.
    state.toRead.delete(slug);
    _restorePrevious(slug);
    saveBuckets();
    el?._nufWrapper?.classList.remove('nuf-to-read');
    el?.classList.remove('nuf-to-read');
    if (controls) {
      controls.querySelector('.nuf-btn[data-nuf-btn="toread"]')
        ?.classList.remove('nuf-toread-active');
    }
    pendingUndo.onUndo?.();
  } else {
    // Other buckets: remove from bucket and re-insert element
    state[bucket].delete(slug);
    _restorePrevious(slug);
    saveBuckets();
    if (parent) {
      // After the collapse animation the card's wrapper has been removed too: put it back first
      const { wrapperParent, wrapperNext } = pendingUndo;
      if (!parent.isConnected && parent === el._nufWrapper && wrapperParent) {
        wrapperParent.insertBefore(parent, wrapperNext?.parentNode === wrapperParent ? wrapperNext : null);
      }
      // Re-insert controls then entry
      parent.insertBefore(el, nextSibling);
      if (controls) parent.insertBefore(controls, el);
      el.classList.remove('nuf-removing');
      // Guard against stale storage.onChanged from the original markEntry write
      // re-filtering and removing this card before the undo write's onChanged arrives
      el._nufUndoGuard = true;
      setTimeout(() => { delete el._nufUndoGuard; }, 1000);
      if (pendingUndo.previous?.toRead) {
        el.classList.add('nuf-to-read');
        el._nufWrapper?.classList.add('nuf-to-read');
        controls?.querySelector('.nuf-btn[data-nuf-btn="toread"]')?.classList.add('nuf-toread-active');
      }
    }
    pendingUndo.onUndo?.();
  }

  // Dismiss toast
  clearTimeout(toastTimer);
  toastEl.classList.remove('nuf-toast-visible');
  pendingUndo = null;
  _releaseCommentFocus();
}
