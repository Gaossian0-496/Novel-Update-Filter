'use strict';

// Live view of the background request broker (see background.js). Subscribes
// over a "nuf-queue-watch" port and re-renders on every snapshot; a timer keeps
// the relative times ticking between snapshots.

const KIND_LABEL = {
  scroll: 'Next listing page',
  user: 'Start reading',
  cover: 'Sidebar cover',
  prefetch: 'Tag lookup',
  manager: 'Manager title',
};
const KIND_ORDER = ['scroll', 'user', 'cover', 'prefetch', 'manager'];

let snapshot = null;
let clockOffset = 0;  // background clock minus this page's clock

function now() {
  return Date.now() + clockOffset;
}

function seconds(ms) {
  const s = Math.max(0, ms) / 1000;
  return s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
}

function shortPath(url) {
  if (!url) return 'Security check reported';
  try {
    const u = new URL(url);
    return decodeURIComponent(u.pathname + u.search);
  } catch {
    return url;
  }
}

function kindBadge(kind) {
  const badge = document.createElement('span');
  badge.className = 'kind';
  badge.dataset.kind = kind;
  badge.textContent = KIND_LABEL[kind] || kind;
  return badge;
}

function pathCell(url) {
  const el = document.createElement(url ? 'a' : 'span');
  el.className = 'path';
  el.textContent = shortPath(url);
  if (url) {
    el.href = url;
    el.target = '_blank';
    el.rel = 'noopener';
    el.title = url;
  }
  return el;
}

function queueRow(entry, whenText) {
  const li = document.createElement('li');
  li.className = 'row';
  const when = document.createElement('span');
  when.className = 'when';
  when.dataset.since = entry.since;
  if (entry.retryAt) {
    when.dataset.retryAt = entry.retryAt;
    when.dataset.attempt = entry.attempts + 1;
  }
  if (entry.paused) when.dataset.paused = '';
  when.textContent = whenText;
  li.append(kindBadge(entry.kind), pathCell(entry.url), when);
  return li;
}

function waitingText(retryAt, attempt, paused, since) {
  if (retryAt) return `retry ${attempt} in ${seconds(retryAt - now())}`;
  if (paused) return `waiting for its tab to be visible, ${seconds(now() - since)}`;
  return `waiting ${seconds(now() - since)}`;
}

function resultText(item) {
  if (item.outcome === 'ok') return 'Loaded';
  if (item.outcome === 'challenge') return 'Security check';
  if (item.outcome === 'blocked') return `Blocked${item.status ? ` (${item.status})` : ''}`;
  if (item.outcome === 'gone') return item.status === 401 ? 'Logged out' : `Not found${item.status ? ` (${item.status})` : ''}`;
  return item.status ? `Failed (${item.status})` : 'Network error';
}

function describeStatus(s) {
  const t = now();
  // Only lookups waiting: they go out at the slower background gap
  const pagesWaiting = s.waiting.some(e => e.kind === 'scroll' || e.kind === 'user');
  const gap = (pagesWaiting || !s.backgroundGapMs ? s.baseGapMs : s.backgroundGapMs) + s.paceDelay;
  if (s.backgroundStopped) {
    return { state: 'paused', headline: 'Background lookups stopped',
      detail: 'Novel Updates kept blocking after every cooldown, so tag, cover and title lookups stop until the browser restarts. Listing pages and Start reading still load.' };
  }
  if (s.backgroundHoldUntil > t) {
    const until = new Date(s.backgroundHoldUntil - clockOffset).toLocaleTimeString();
    return { state: 'paused', headline: `Cooling down until ${until}`,
      detail: `Several requests in a row were blocked, so background lookups wait ${seconds(s.backgroundHoldUntil - t)}. Listing pages and Start reading still load.` };
  }
  if (s.notBefore > t) {
    const until = new Date(s.notBefore - clockOffset).toLocaleTimeString();
    return { state: 'paused', headline: `Paused by Novel Updates until ${until}`,
      detail: `The site asked the extension to wait (Retry-After). Requests resume in ${seconds(s.notBefore - t)}.` };
  }
  if (s.paceDelay > 0) {
    const left = s.recoveryAfter - s.successes;
    return { state: 'slowed', headline: 'Slowed down after a block',
      detail: `Requests are at least ${seconds(gap)} apart. Back toward normal after ${left} more successful ${left === 1 ? 'request' : 'requests'}.` };
  }
  if (s.waiting.length && !s.inFlight && s.waiting.every(e => e.paused)) {
    return { state: 'paused', headline: 'Waiting for a visible Novel Updates tab',
      detail: 'Background lookups only run in a tab you can see. Switch to a Novel Updates tab and they carry on.' };
  }
  if (s.waiting.length && !s.inFlight) {
    const nextAt = s.lastGrantAt + gap;
    return { state: 'waiting', headline: `Next request in about ${seconds(nextAt - t)}`,
      detail: 'Requests are spaced out so Novel Updates does not block the extension.' };
  }
  if (s.inFlight) {
    return { state: 'waiting', headline: 'Loading', detail: 'One request at a time; the rest wait below.' };
  }
  return { state: 'idle', headline: 'Idle. Nothing is waiting', detail: 'New requests go out immediately, about a second apart.' };
}

function render() {
  if (!snapshot) return;
  const s = snapshot;

  const status = describeStatus(s);
  const statusEl = document.getElementById('status');
  statusEl.dataset.state = status.state;
  document.getElementById('status-headline').textContent = status.headline;
  document.getElementById('status-detail').textContent = status.detail;
  document.getElementById('fact-gap').textContent = s.paceDelay
    ? `${seconds(s.baseGapMs)} + ${seconds(s.paceDelay)} after blocks`
    : `about ${seconds(s.baseGapMs)}`;

  const inFlight = document.getElementById('in-flight');
  inFlight.replaceChildren(s.inFlight
    ? queueRow(s.inFlight, `for ${seconds(now() - s.inFlight.since)}`)
    : Object.assign(document.createElement('li'), { className: 'hint', textContent: 'Nothing is loading.' }));

  const waiting = document.getElementById('waiting');
  document.getElementById('waiting-count').textContent = s.waitingTotal;
  document.getElementById('waiting-summary').textContent = KIND_ORDER
    .filter(kind => s.waitingCounts[kind])
    .map(kind => `${s.waitingCounts[kind]} ${KIND_LABEL[kind].toLowerCase()}${s.waitingCounts[kind] === 1 ? '' : 's'}`)
    .join(', ');
  const rows = s.waiting.map(e => queueRow(e, waitingText(e.retryAt, e.attempts + 1, e.paused, e.since)));
  if (s.waitingTotal > s.waiting.length) {
    rows.push(Object.assign(document.createElement('li'), {
      className: 'hint', textContent: `and ${s.waitingTotal - s.waiting.length} more`,
    }));
  }
  waiting.replaceChildren(...(rows.length
    ? rows
    : [Object.assign(document.createElement('li'), { className: 'hint', textContent: 'No requests are waiting.' })]));

  const recent = document.getElementById('recent');
  document.getElementById('recent-count').textContent = s.history.length;
  document.getElementById('recent-empty').hidden = s.history.length > 0;
  recent.replaceChildren(...s.history.map(item => {
    const tr = document.createElement('tr');
    const time = document.createElement('td');
    time.textContent = new Date(item.at - clockOffset).toLocaleTimeString();
    const kind = document.createElement('td');
    kind.append(kindBadge(item.kind));
    const page = document.createElement('td');
    page.append(pathCell(item.url));
    const waited = document.createElement('td');
    waited.textContent = seconds(item.waitedMs);
    const result = document.createElement('td');
    result.className = 'result';
    result.dataset.outcome = item.outcome;
    result.textContent = resultText(item);
    tr.append(time, kind, page, waited, result);
    return tr;
  }));
}

// Only the relative times change between snapshots
function tick() {
  if (!snapshot) return;
  document.querySelectorAll('#in-flight .when').forEach(el => {
    el.textContent = `for ${seconds(now() - Number(el.dataset.since))}`;
  });
  document.querySelectorAll('#waiting .when').forEach(el => {
    el.textContent = waitingText(Number(el.dataset.retryAt) || null, el.dataset.attempt, 'paused' in el.dataset, Number(el.dataset.since));
  });
  const status = describeStatus(snapshot);
  document.getElementById('status').dataset.state = status.state;
  document.getElementById('status-headline').textContent = status.headline;
  document.getElementById('status-detail').textContent = status.detail;
}

function connect() {
  const port = chrome.runtime.connect({ name: 'nuf-queue-watch' });
  port.onMessage.addListener(msg => {
    snapshot = msg;
    clockOffset = msg.now - Date.now();
    render();
  });
  // The service worker can stop; reconnect so the view stays live
  port.onDisconnect.addListener(() => setTimeout(connect, 1000));
}

function renderPrefetch(progress) {
  const el = document.getElementById('fact-prefetch');
  if (!progress || !progress.total) { el.hidden = true; return; }
  el.hidden = false;
  document.getElementById('fact-prefetch-text').textContent = `${progress.done} / ${progress.total}`;
  document.getElementById('fact-prefetch-fill').style.width = `${Math.round((progress.done / progress.total) * 100)}%`;
}

chrome.storage.local.get('tagFetchProgress').then(({ tagFetchProgress }) => renderPrefetch(tagFetchProgress));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && 'tagFetchProgress' in changes) renderPrefetch(changes.tagFetchProgress.newValue);
});

document.getElementById('open-manager').addEventListener('click', () => {
  chrome.tabs.create({ url: chrome.runtime.getURL('manager.html') });
});

connect();
setInterval(tick, 500);
