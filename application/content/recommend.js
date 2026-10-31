'use strict';

// IDF-weighted recommendation scoring, score rendering, and explanation panel.

function getDescriptionFromEntry(entry) {
  const body = entry.querySelector('.search_body_nu');
  if (!body) return '';
  const clone = body.cloneNode(true);
  clone.querySelectorAll('.search_title, .search_stats, .search_genre, .dots, .morelink').forEach(el => el.remove());
  return clone.textContent.replace(/\s+/g, ' ').trim();
}

const STOPWORDS = new Set([
  'a','an','the','and','or','but','of','to','in','is','it','that','was','he','she','they',
  'for','on','with','his','her','as','at','by','from','are','been','be','have','has','had',
  'do','does','did','will','would','could','should','may','might','this','those','these',
  'than','then','also','into','not','no','up','out','about','over','when','where','how',
  'who','which','we','you','i','me','my','our','your','its','their','there','here','were',
  'am','after','before','all','can','just','more','one','so','very','what','if','him',
  // quantifiers / determiners
  'some','any','few','many','much','most','less','least','several','every','each','both',
  // common verbs
  'get','got','make','let','want','know','take','say','said','come','came','went','see',
  'saw','look','looked','find','found','give','gave','use','used','keep','kept','try',
  // common adjectives / adverbs
  'new','old','good','bad','big','small','long','short','high','well','able','real','true',
  // discourse / connective
  'like','such','other','own','only','even','still','yet','never','always','through',
  'while','within','around','because','though','since','however','else','away','back',
  // numbers / ordinals
  'two','three','four','five','first','last',
]);

function tokenize(text) {
  const words = text.toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 2 && !STOPWORDS.has(w));

  const tokens = new Set(words);
  for (let i = 0; i < words.length - 1; i++) {
    tokens.add(words[i] + ' ' + words[i + 1]);
  }
  return tokens;
}

function recencyDecay(ts) {
  if (!ts) return 1.0;
  const ageDays = (Date.now() - ts) / (1000 * 60 * 60 * 24);
  return Math.min(1.0, Math.pow(0.5, ageDays / state.recencyHalfLifeDays));
}

let _idfCache = null;
let _idfCacheVersion = -1;

function computeIDF() {
  if (_idfCache && _idfCacheVersion === _novelMetaVersion) return _idfCache;
  const allEntries = Object.entries(state.novelMeta);
  const N = allEntries.length;
  if (N === 0) {
    _idfCache = { tags: {}, nuTags: {}, words: {} };
    _idfCacheVersion = _novelMetaVersion;
    return _idfCache;
  }
  const tagDf = {}, nuTagDf = {}, wordDf = {};
  for (const [slug, meta] of allEntries) {
    for (const t of (meta.g || [])) tagDf[t] = (tagDf[t] || 0) + 1;
    for (const t of (meta.tags || [])) nuTagDf[t] = (nuTagDf[t] || 0) + 1;
    for (const t of (meta.d || [])) wordDf[t] = (wordDf[t] || 0) + 1;
    // Comment tokens count toward word document frequency
    for (const t of tokenize(state.novelReviews[slug]?.comment || '')) wordDf[t] = (wordDf[t] || 0) + 1;
  }
  const calc = df => Object.fromEntries(
    Object.entries(df).map(([t, count]) => [t, Math.log(1 + N / count)])
  );
  _idfCache = { tags: calc(tagDf), nuTags: calc(nuTagDf), words: calc(wordDf) };
  _idfCacheVersion = _novelMetaVersion;
  return _idfCache;
}

// Weighted Jaccard using IDF scores — terms absent from corpus get weight 0.
// When corpus is tiny all IDF values are equal, so this degrades to plain Jaccard.
function weightedSim(setA, setB, idf) {
  let inter = 0, unionW = 0;
  for (const v of setA) {
    const w = idf[v] || 0;
    if (setB.has(v)) inter += w;
    unionW += w;
  }
  for (const v of setB) {
    const w = idf[v] || 0;
    if (!setA.has(v)) unionW += w;
  }
  return unionW === 0 ? 0 : inter / unionW;
}

// ── Card cover, title and synopsis (shared by captureMetadata and the sidebar) ──
// meta.img is a real cover URL, '' when the series page says the novel has no
// cover, or missing when nobody has looked yet (needsSidebarMeta then queues a
// series-page lookup). Lazy-load placeholders and the site's no-image picture
// are never stored: they would hide the real cover and stop that lookup.

const PLACEHOLDER_COVER_PATTERN = /noimage|no[-_]image|nocover|no[-_]cover|placeholder|blank\.(gif|png)|spacer\.gif/i;

function isRealCoverUrl(url) {
  return typeof url === 'string' && url.length > 0
    && !url.startsWith('data:') && !PLACEHOLDER_COVER_PATTERN.test(url);
}

// The card's real cover URL, or null when it shows none (or only a placeholder)
function coverFromCard(entry) {
  const imgEl = entry?.querySelector('.search_img_nu img');
  if (!imgEl) return null;
  for (const url of [imgEl.dataset.src, imgEl.src]) {
    if (isRealCoverUrl(url)) return url;
  }
  return null;
}

// Copies what the To Read sidebar shows from a card into meta: a real cover
// replaces a missing, '' or placeholder one; title and synopsis fill gaps only.
// A stored placeholder with nothing better on the card is dropped so the
// series-page lookup runs. Returns true when any of these fields changed.
function applyCardSidebarFields(meta, entry, config, { withDesc = true } = {}) {
  let changed = false;
  const img = coverFromCard(entry);
  if (img) {
    if (!isRealCoverUrl(meta.img)) { meta.img = img; changed = true; }
  } else if (meta.img && !isRealCoverUrl(meta.img)) {
    delete meta.img;
    changed = true;
  }
  if (!meta.t) {
    const title = entry.querySelector(config.titleLinkSelector)?.textContent.trim();
    if (title) { meta.t = title; changed = true; }
  }
  if (withDesc && !meta.desc) {
    const desc = getDescriptionFromEntry(entry);
    if (desc) { meta.desc = desc; changed = true; }
  }
  return changed;
}

function captureMetadata(entry, slug, title, config) {
  const existing = state.novelMeta[slug];
  const meta = { ...(existing || {}) };
  if (title) meta.t = title;
  const titleChanged = !!title && title !== existing?.t;
  // The cover is kept for the manager and sidebar (defect-058); the synopsis
  // text only for To Read novels, the only ones whose synopsis is shown
  const isToRead = state.toRead.has(slug);
  const sidebarChanged = applyCardSidebarFields(meta, entry, config, { withDesc: isToRead }) || titleChanged;
  const needsScoring = !existing || !existing.g?.length || !existing.d?.length;
  if (!needsScoring && !sidebarChanged) return;
  if (needsScoring) {
    const genres = [...entry.querySelectorAll(config.tagSelector)].map(el => el.textContent.trim().toLowerCase());
    const desc   = [...tokenize(getDescriptionFromEntry(entry))];
    meta.g = genres.length ? genres : (existing?.g || []);
    meta.l = getLangFromEntry(entry) || existing?.l || 'other';
    meta.d = desc.length   ? desc   : (existing?.d || []);
  }
  state.novelMeta[slug] = meta;
  scheduleMetaSave();
  // The storage listener skips this tab's own saves, so redraw here
  // (scheduleSidebarRender lives in prefetch.js, loaded after this file)
  if (isToRead && sidebarChanged) scheduleSidebarRender();
}

function applyRecommendations(config, forceAll = false) {
  if (!state.recommendationsEnabled) return;

  // Build reference set from bucketed novels that have stored metadata
  const BUCKET_WEIGHTS = { read: 1.0, notInterested: -0.8, notFinished: -0.3, toRead: 0.3 };
  const refs = [];
  for (const [key, baseWeight] of Object.entries(BUCKET_WEIGHTS)) {
    for (const slug of state[key]) {
      const meta = state.novelMeta[slug];
      if (!meta) continue;
      const review = state.novelReviews[slug];
      // Rating overrides fixed bucket weight for read/notFinished; maps 1→−1.0, 5.5→0, 10→+1.0
      // Rating maps to a signed weight on the 1-5 scale: 1 → -1.0, 3 → 0, 5 → +1.0.
      // (Changed from 1-10 in defect-049 — see storage.js migration for the upgrade.)
      const weight = (review?.rating != null && key !== 'notInterested')
        ? (review.rating - 3) / 2 * recencyDecay(meta.ts)
        : baseWeight * recencyDecay(meta.ts);
      // Comment tokens are unioned with description tokens for word similarity
      const words = new Set([...(meta.d || []), ...tokenize(review?.comment || '')]);
      refs.push({ tags: new Set(meta.g), nuTags: new Set(meta.tags || []), words, lang: meta.l, weight, title: meta.t || slug, bucket: key });
    }
  }
  // No signal: clear any scores, threshold decisions and waiting cards left
  // from before the last reference novel was removed
  if (!refs.length) {
    clearRecommendations(config);
    return;
  }

  const { tags: tagIdf, nuTags: nuTagIdf, words: wordIdf } = computeIDF();

  // Score all visible cards — on scroll appends only score newly added entries
  const allEntries = [...document.querySelectorAll(`${config.entrySelector}[data-nuf-slug]`)];
  if (!allEntries.length) return;

  // Diagnostic log on each full-recompute pass — lets a user (or test) see
  // why chips might be missing: too few refs, refs without Novel Updates tags, or no
  // tag overlap with their cards (defect-050).
  if (forceAll) {
    const refsWithTags  = refs.filter(r => r.nuTags.size > 0).length;
    const cardsWithTags = allEntries.filter(e => state.novelMeta[e.dataset.nufSlug]?.tags?.length).length;
    console.info(`[nuf] score recompute: refs=${refs.length} refsWithTags=${refsWithTags} cards=${allEntries.length} cardsWithTags=${cardsWithTags}`);
  }

  const entries = forceAll ? allEntries : allEntries.filter(e => e._nufRaw === undefined);
  const existingRaws = forceAll ? [] : allEntries.filter(e => e._nufRaw !== undefined).map(e => e._nufRaw);
  if (!entries.length) return;

  const scored = entries.map(entry => {
    const slug = entry.dataset.nufSlug;
    // Genres and description don't change after first DOM read — cache to avoid
    // re-querying / re-tokenizing on every applyRecommendations call.
    const cardTags = entry._nufCardTags ??= new Set(
      [...entry.querySelectorAll(config.tagSelector)].map(el => el.textContent.trim().toLowerCase())
    );
    const cardWords = entry._nufCardWords ??= tokenize(getDescriptionFromEntry(entry));
    // Novel Updates tags arrive asynchronously from prefetch — read fresh from state each pass.
    const cardNuTags = new Set(state.novelMeta[slug]?.tags || []);

    let raw = 0;
    const genreScores  = {};
    const nuTagScores  = {};
    const wordScores   = {};
    const breakdown    = [];

    for (const ref of refs) {
      const tagSim   = weightedSim(cardTags, ref.tags, tagIdf);
      const nuTagSim = weightedSim(cardNuTags, ref.nuTags, nuTagIdf);
      const wordSim  = weightedSim(cardWords, ref.words, wordIdf);
      const hasTags  = cardNuTags.size > 0 && ref.nuTags.size > 0;
      const sim = hasTags
        ? tagSim * 0.35 + nuTagSim * 0.45 + wordSim * 0.20
        : tagSim * 0.64 + wordSim * 0.36;
      const contribution = sim * ref.weight;
      raw += contribution;
      let interW = 0, refW = 0;
      for (const v of ref.words) {
        const w = wordIdf[v] || 0;
        refW += w;
        if (cardWords.has(v)) interW += w;
      }
      const wordRecall = refW === 0 ? 0 : interW / refW;
      breakdown.push({
        title: ref.title,
        bucket: ref.bucket,
        tagSim:   Math.round(tagSim   * 100),
        nuTagSim: hasTags ? Math.round(nuTagSim * 100) : null,
        wordSim:  Math.round(wordRecall * 100),
        contribution,
      });
      for (const genre of cardTags) {
        if (ref.tags.has(genre)) genreScores[genre] = (genreScores[genre] || 0) + ref.weight * (tagIdf[genre] || 0);
      }
      for (const tag of cardNuTags) {
        if (ref.nuTags.has(tag)) nuTagScores[tag] = (nuTagScores[tag] || 0) + ref.weight * (nuTagIdf[tag] || 0);
      }
      for (const word of cardWords) {
        if (ref.words.has(word)) wordScores[word] = (wordScores[word] || 0) + ref.weight * (wordIdf[word] || 0);
      }
    }
    breakdown.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
    entry._nufBreakdown = {
      pos: breakdown.filter(r => r.contribution > 0 || (r.contribution === 0 && r.bucket === 'read')),
      neg: breakdown.filter(r => r.contribution < 0 || (r.contribution === 0 && r.bucket !== 'read')),
    };

    // Build structured reasoning grouped by signal type
    const cap = g => g[0].toUpperCase() + g.slice(1);
    const gs  = Object.entries(genreScores);
    const ts  = Object.entries(nuTagScores);
    const ws  = Object.entries(wordScores).filter(([w]) => w.split(' ').every(p => !cardTags.has(p)));
    const genrePos = gs.filter(([, s]) => s > 0).sort(([, a], [, b]) => b - a).slice(0, 2).map(([g]) => cap(g));
    const genreNeg = gs.filter(([, s]) => s < 0).sort(([, a], [, b]) => a - b).slice(0, 2).map(([g]) => cap(g));
    const tagPos   = ts.filter(([, s]) => s > 0).sort(([, a], [, b]) => b - a).slice(0, 3).map(([t]) => cap(t));
    const tagNeg   = ts.filter(([, s]) => s < 0).sort(([, a], [, b]) => a - b).slice(0, 2).map(([t]) => cap(t));
    // Neutral fallback: when the card has Novel Updates tags but no bucketed ref had
    // overlapping tags (e.g. all refs are Cloudflare-blocked and have empty
    // tags arrays), show the card's own top-3 tags as informational chips so
    // the user can see something instead of an empty Tags row (defect-050).
    const tagNeutral = (tagPos.length === 0 && tagNeg.length === 0 && cardNuTags.size > 0)
      ? [...cardNuTags].slice(0, 3).map(cap)
      : [];
    const kwPos    = ws.filter(([, s]) => s > 0).sort(([, a], [, b]) => b - a).slice(0, 2).map(([w]) => w);
    const kwNeg    = ws.filter(([, s]) => s < 0).sort(([, a], [, b]) => a - b).slice(0, 1).map(([w]) => w);
    const cardLang = getLangFromEntry(entry);
    let langScore = 0;
    for (const ref of refs) {
      if (ref.lang === cardLang) langScore += ref.weight;
    }
    const lang = Math.abs(langScore) > 0.1
      ? { code: LANG_DISPLAY[cardLang] || '??', positive: langScore > 0 }
      : null;

    return { entry, raw, reasoning: { genrePos, genreNeg, tagPos, tagNeg, tagNeutral, kwPos, kwNeg, lang } };
  });

  // Min-max normalise to 1–10; include previously stored raws so existing scores stay stable
  const raws = [...scored.map(s => s.raw), ...existingRaws];
  const min = Math.min(...raws);
  const max = Math.max(...raws);

  for (const { entry, raw, reasoning } of scored) {
    entry._nufRaw = raw;
    const score = max === min ? 5 : Math.round(1 + ((raw - min) / (max - min)) * 9);
    renderScore(entry, score, reasoning);
  }
}

function toggleExplanationPanel(entry) {
  const controls = entry._nufControls;
  if (!controls) return;

  const wasOpen = !!controls.querySelector('.nuf-explain-panel');
  document.querySelectorAll('.nuf-explain-panel').forEach(p => p.remove());
  if (wasOpen) return;

  const breakdown = entry._nufBreakdown;
  if (!breakdown?.pos?.length && !breakdown?.neg?.length) return;

  const makeRow = ref => {
    const sign  = ref.contribution >= 0 ? '+' : '−';
    const color = BUCKET_COLOR[ref.bucket] || '#4a5568';
    const safeTitle = escHtml(ref.title);
    return `
      <div class="nuf-explain-row">
        <span class="nuf-explain-title" title="${safeTitle}">${safeTitle}</span>
        <span class="nuf-explain-bucket" style="color:${color}">${BUCKET_LABEL[ref.bucket] || ref.bucket}</span>
        <span class="nuf-explain-sim">${ref.nuTagSim !== null ? `G:${ref.tagSim}% T:${ref.nuTagSim}% K:${ref.wordSim}%` : `G:${ref.tagSim}% K:${ref.wordSim}%`}</span>
        <span class="nuf-explain-contrib" style="color:${color}">${sign}${Math.abs(ref.contribution).toFixed(2)}</span>
      </div>`;
  };

  const NONE = '<div class="nuf-explain-none">None</div>';
  const posHTML = breakdown.pos?.length ? breakdown.pos.map(makeRow).join('') : NONE;
  const negHTML = breakdown.neg?.length ? breakdown.neg.map(makeRow).join('') : NONE;

  const panel = document.createElement('div');
  panel.className = 'nuf-explain-panel';
  panel.innerHTML = `
    <div class="nuf-explain-header">Score breakdown <span class="nuf-explain-close" title="Close">✕</span></div>
    <div class="nuf-explain-scroll">
      <div class="nuf-explain-columns">
        <div class="nuf-explain-col">
          <div class="nuf-explain-col-head" style="color:#276749">Positive</div>
          ${posHTML}
        </div>
        <div class="nuf-explain-col">
          <div class="nuf-explain-col-head" style="color:#9b2c2c">Negative</div>
          ${negHTML}
        </div>
      </div>
    </div>
    <div class="nuf-explain-footer">G = genre Jaccard · T = tag Jaccard · K = % of reference keywords matched</div>
  `;
  panel.querySelector('.nuf-explain-close').addEventListener('click', e => {
    e.stopPropagation();
    panel.remove();
  });

  controls.appendChild(panel);
}

function buildReasonChips(r) {
  const chips = (items, cls) => items.map(t => `<span class="nuf-reason-chip ${cls}">${escHtml(t)}</span>`).join('');

  // Tags row: prefer pos/neg from ref overlap; fall back to neutral chips
  // showing the card's own Novel Updates tags when there's no overlap (defect-050).
  let tagsLine = '';
  if (r.tagPos.length || r.tagNeg.length) {
    tagsLine = `<div class="nuf-reason-line"><span class="nuf-reason-group"><span class="nuf-reason-group-label">Tags</span>${chips(r.tagPos, 'nuf-chip-tag-pos')}${chips(r.tagNeg, 'nuf-chip-tag-neg')}</span></div>`;
  } else if (r.tagNeutral?.length) {
    tagsLine = `<div class="nuf-reason-line"><span class="nuf-reason-group"><span class="nuf-reason-group-label">Tags</span>${chips(r.tagNeutral, 'nuf-chip-tag-neutral')}</span></div>`;
  }
  const genreLine = (r.genrePos.length || r.genreNeg.length)
    ? `<div class="nuf-reason-line"><span class="nuf-reason-group"><span class="nuf-reason-group-label">Genre</span>${chips(r.genrePos, 'nuf-chip-genre-pos')}${chips(r.genreNeg, 'nuf-chip-genre-neg')}</span></div>`
    : '';

  const langChips = r.lang
    ? `<span class="nuf-reason-group"><span class="nuf-reason-group-label">Language</span><span class="nuf-reason-chip ${r.lang.positive ? 'nuf-chip-lang-pos' : 'nuf-chip-lang-neg'}">${r.lang.code}</span></span>`
    : '';

  const kwChips = (r.kwPos.length || r.kwNeg.length)
    ? `<span class="nuf-reason-group"><span class="nuf-reason-group-label">Desc. Keywords</span>${chips(r.kwPos, 'nuf-chip-kw-pos')}${chips(r.kwNeg, 'nuf-chip-kw-neg')}</span>`
    : '';

  const kwLine   = kwChips   ? `<div class="nuf-reason-line">${kwChips}</div>`   : '';
  const langLine = langChips ? `<div class="nuf-reason-line">${langChips}</div>` : '';

  if (!tagsLine && !genreLine && !kwLine && !langLine) {
    return '<div class="nuf-reason-line"><span class="nuf-reason-none">No strong signal</span></div>';
  }
  return tagsLine + genreLine + kwLine + langLine;
}

function renderScore(entry, score, reasoning) {
  const hue = Math.round(((score - 1) / 9) * 120);
  const bg = `hsl(${hue}, 55%, 93%)`;
  entry.style.setProperty('--nuf-score-bg', bg);
  entry.classList.add('nuf-scored');

  const controls = entry._nufControls;
  if (!controls) return;

  controls.style.setProperty('--nuf-score-bg', bg);
  controls.classList.add('nuf-scored');

  let badge = controls.querySelector('.nuf-score-badge');
  if (!badge) {
    badge = document.createElement('div');
    badge.className = 'nuf-score-badge';
    badge.title = 'Click to see score breakdown';
    badge.innerHTML = '<span class="nuf-score-label"></span>';
    controls.insertBefore(badge, controls.firstChild);
    badge.addEventListener('click', e => {
      e.stopPropagation();
      toggleExplanationPanel(entry);
    });
    // Move any existing spinner into the badge (it may have been prepended before the badge existed)
    const orphanSpinner = controls.querySelector('.nuf-tag-spinner');
    if (orphanSpinner) badge.appendChild(orphanSpinner);
  }
  const color = `hsl(${hue}, 60%, 30%)`;
  const labelEl = badge.querySelector('.nuf-score-label');
  labelEl.textContent = `Extension rating: ${score}/10`;
  labelEl.style.color = color;

  // Reason chips row (full-width second line)
  let reasonRow = controls.querySelector('.nuf-reason-row');
  if (!reasonRow) {
    reasonRow = document.createElement('div');
    reasonRow.className = 'nuf-reason-row';
    controls.appendChild(reasonRow);
  }
  reasonRow.innerHTML = buildReasonChips(reasoning);
  reasonRow.style.display = state.showReasoningChips ? '' : 'none';

  entry._nufScore = score;
  applyScoreThreshold(entry, score);
}

function clearRecommendations(config) {
  document.querySelectorAll(`${config.entrySelector}[data-nuf-slug]`).forEach(entry => {
    delete entry._nufRaw;
    delete entry._nufScore;  // or a later threshold change would hide cards by it
    delete entry._nufThresholdHidden;
    entry.classList.remove('nuf-scored', 'nuf-hidden-threshold', 'nuf-score-pending');
    entry.style.removeProperty('--nuf-score-bg');
    if (entry._nufControls) {
      entry._nufControls.classList.remove('nuf-scored', 'nuf-hidden-threshold', 'nuf-score-pending');
      entry._nufControls.style.removeProperty('--nuf-score-bg');
      entry._nufControls.querySelector('.nuf-score-badge')?.remove();
      entry._nufControls.querySelector('.nuf-reason-row')?.remove();
    }
  });
}

// With a score threshold, a card is shown or hidden once, when its score is
// final, and then stays that way. Scores are scaled against the other cards
// on the page, so every finished lookup shifts them all a little; deciding
// again each time made cards appear and disappear at random. Until its score
// is final a card waits hidden (nuf-score-pending). The user's own changes
// (the threshold, switching prefetch on) decide every card again.
function scoreIsFinal(entry) {
  const slug = entry.dataset.nufSlug;
  return !state.prefetchTagsEnabled || extensionContextLost
    || !!state.novelMeta[slug]?.tags?.length || !!entry._nufLookupDone;
}

function applyScoreThreshold(entry, score, redecide = false) {
  const slug = entry.dataset.nufSlug;
  if (redecide) delete entry._nufThresholdHidden;
  let hide = false;
  let pending = false;
  if (state.minRecommendationScore > 0 && !state.toRead.has(slug)) {
    if (entry._nufThresholdHidden !== undefined) hide = entry._nufThresholdHidden;
    else if (!scoreIsFinal(entry)) hide = pending = true;
    else hide = entry._nufThresholdHidden = score < state.minRecommendationScore;
  } else {
    delete entry._nufThresholdHidden;
  }
  for (const el of [entry, entry._nufControls]) {
    if (!el) continue;
    el.classList.toggle('nuf-hidden-threshold', hide);
    el.classList.toggle('nuf-score-pending', pending);
  }
}

function applyAllThresholds(config, redecide = true) {
  document.querySelectorAll(`${config.entrySelector}[data-nuf-slug]`).forEach(entry => {
    if (entry._nufScore !== undefined) applyScoreThreshold(entry, entry._nufScore, redecide);
  });
}
