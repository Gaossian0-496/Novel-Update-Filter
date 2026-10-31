'use strict';

// Page type detection, slug/language/rating helpers, shared display constants.

function detectPageType() {
  const p = location.pathname;
  if (p.startsWith('/series-finder'))  return 'finder';
  if (p.startsWith('/series-ranking')) return 'ranking';
  if (p.startsWith('/novelslisting'))  return 'listing';
  if (p.startsWith('/stag/'))          return 'tag';
  if (p.startsWith('/genre/'))         return 'genre';
  if (p.startsWith('/latest-series'))  return 'latest';
  return null;
}

function slugFromHref(href) {
  try {
    const url = new URL(href);
    if (!url.pathname.startsWith('/series/')) return null;
    const parts = url.pathname.replace(/\/$/, '').split('/');
    return parts[parts.length - 1] || null;
  } catch {
    return null;
  }
}

const LANG_CODE_MAP = { CN: 'chinese', JP: 'japanese', KR: 'korean' };
const LANG_DISPLAY = { chinese: 'CN', japanese: 'JP', korean: 'KR', other: '??' };

function getLangFromEntry(entry) {
  // The site shows country code as text like "KR (4.5)" below the cover image
  // Read the origin label, not the whole card: a synopsis can mention "JP"
  const origin = entry.querySelector('.search_ratings, .origin');
  const match = (origin || entry).textContent.match(/\b(CN|JP|KR)\b/);
  if (match) return LANG_CODE_MAP[match[1]] || 'other';
  return 'other';
}

function getRatingFromEntry(entry) {
  const text = entry.querySelector('.search_ratings, .origin')?.textContent || '';
  const m = text.match(/\(([\d.]+)\)/);
  return m ? parseFloat(m[1]) : null;
}

function shouldFilter(slug, entry, config) {
  if (state.read.has(slug) || state.notFinished.has(slug) || state.notInterested.has(slug)) return true;

  if (state.excludeLanguages.size > 0) {
    const lang = getLangFromEntry(entry);
    if (state.excludeLanguages.has(lang)) return true;
  }

  if (state.excludeTags.length > 0) {
    const entryTags = [...entry.querySelectorAll(config.tagSelector)]
      .map(el => el.textContent.trim().toLowerCase());
    for (const excluded of state.excludeTags) {
      if (entryTags.includes(excluded)) return true;
    }
  }

  if (state.minRating > 0) {
    const rating = getRatingFromEntry(entry);
    if (rating !== null && rating < state.minRating) return true;
  }

  return false;
}

// A Cloudflare security check, which can be served with HTTP 200. Callers also
// check the page lacks its expected content (cards, a series title), because
// Cloudflare adds a challenge-platform script to some ordinary pages too.
function _isChallengePage(doc) {
  return /just a moment|attention required/i.test(doc.title)
    || !!doc.querySelector('#challenge-running, #challenge-form, #cf-challenge-running, script[src*="/cdn-cgi/challenge-platform/"]');
}

function escHtml(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function truncate(str, max) {
  return str && str.length > max ? str.slice(0, max - 1) + '…' : str;
}

const BUCKET_LABEL = { read: 'Read', notFinished: 'Not Finished', notInterested: 'Not Interested', toRead: 'To Read' };
const BUCKET_COLOR = { read: '#276749', notFinished: '#744210', notInterested: '#9b2c2c', toRead: '#6b21a8' };
