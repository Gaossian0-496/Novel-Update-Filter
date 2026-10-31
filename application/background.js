'use strict';

async function updateBadge() {
  await chrome.action.setBadgeText({ text: '' });
  await chrome.action.setBadgeBackgroundColor({ color: '#2b6cb0' });
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if ('read' in changes || 'notFinished' in changes || 'notInterested' in changes) {
    updateBadge();
  }
});

chrome.runtime.onStartup.addListener(updateBadge);
chrome.runtime.onInstalled.addListener(details => {
  updateBadge();
  // Open the welcome tab exactly once per fresh install — never on Chrome
  // auto-updates or version bumps. Chrome MV3 has no API to pin an extension
  // programmatically; the welcome page explains how the user can pin it.
  //
  // The 1500 ms delay is deliberate: it lets the browser settle from the
  // install before grabbing focus with a new tab, and (incidentally) keeps the
  // tab out of the way of automated test harnesses that load the extension
  // fresh per session.
  if (details.reason === 'install') {
    setTimeout(() => {
      chrome.tabs.create({ url: chrome.runtime.getURL('welcome.html') });
    }, 1500);
  }
});

updateBadge();


// ── Request queue ──────────────────────────────────────────────────────────
// The single queue for every request to novelupdates.com: infinite scroll and
// "Start reading" in every tab, tag and cover prefetch, and the manager's
// title/cover lookups. Pages hand over all of their work up front; this queue
// decides order, pacing, retries, cooldowns and when to give up, and tells a
// page when to run each job. Pages still perform the fetch themselves so
// requests keep the page's own origin and fetch metadata. See wiki:
// request-pacing and request-queue-page.

const QUEUE_PRIORITY = { scroll: 0, user: 1, cover: 2, prefetch: 3, manager: 4 };
// Minimum jittered gap after a request before the next one of this kind
const QUEUE_GAP_MS = { scroll: 1000, user: 1000, cover: 2000, prefetch: 2000, manager: 2000 };
const QUEUE_BACKGROUND_KINDS = new Set(['cover', 'prefetch', 'manager']);
const QUEUE_DEFAULT_ATTEMPTS = 3;             // background jobs; scroll and user run once
const QUEUE_RETRY_BASE_MS = 4000;             // error retry backoff, doubling per attempt
const QUEUE_PACE_STEP_MS = 3000;              // added to every gap per block
const QUEUE_PACE_MAX_MS = 30000;
const QUEUE_RECOVERY_AFTER = 5;               // successes before the added gap relaxes
const QUEUE_MAX_RETRY_AFTER_MS = 120000;      // cap on an honoured Retry-After
const QUEUE_BLOCKS_BEFORE_COOLDOWN = 3;       // consecutive blocks → background cooldown
const QUEUE_COOLDOWNS_MS = [30000, 60000, 120000];
const QUEUE_RUN_TIMEOUT_MS = 30000;           // a job never reported back is abandoned
const QUEUE_HISTORY_MAX = 50;
const QUEUE_SNAPSHOT_WAITING_MAX = 100;
const QUEUE_RECENT_MS = 5 * 60 * 1000;        // a finished lookup asked for again is answered from memory

// Persisted so pacing survives the service worker being stopped
const pace = {
  paceDelay: 0, notBefore: 0, lastGrantAt: 0, successes: 0,
  consecutiveBlocks: 0, cooldownCycles: 0, backgroundHoldUntil: 0, backgroundStopped: false,
};
const jobs = new Map();       // key → job
let running = null;           // the job currently being fetched
let queueTimer = null;
let queueSeq = 0;
let nextJitter = Math.random();
const history = [];           // newest first
const watchers = new Set();   // Request queue page ports
const progress = { done: 0, total: 0 };  // tag/cover prefetch, shown in the popup and queue page
// Background lookups finished recently (key → time). Another tab can ask for
// the same novel before the first tab's results reach storage (saves are
// debounced); answering from here avoids fetching the same page twice.
const recentlyFinished = new Map();

const paceLoaded = chrome.storage.session.get('nufQueuePace').then(({ nufQueuePace }) => {
  if (nufQueuePace) Object.assign(pace, nufQueuePace);
}).catch(() => {});

function savePace() {
  chrome.storage.session.set({ nufQueuePace: { ...pace } }).catch(() => {});
}

function parseRetryAfterMs(value) {
  const seconds = parseInt(value, 10);
  if (!isNaN(seconds)) return seconds * 1000;
  const date = Date.parse(value);
  if (!isNaN(date)) return Math.max(0, date - Date.now());
  return null;
}

function gapFor(kind) {
  return Math.round(((QUEUE_GAP_MS[kind] ?? 2000) + pace.paceDelay) * (0.8 + nextJitter * 0.4));
}

function byPriority(a, b) {
  return a.priority - b.priority || a.seq - b.seq;
}

// A job can run when its own retry delay has passed, a suitable page is
// available to run it, and (for background kinds) no cooldown is active.
// Background jobs only run in a visible page: a hidden tab spends the budget
// on nothing the user can see.
function runnerFor(job, now) {
  if (job.notBefore > now) return null;
  const background = QUEUE_BACKGROUND_KINDS.has(job.kind);
  if (background && (pace.backgroundStopped || pace.backgroundHoldUntil > now)) return null;
  for (const client of job.owners) {
    if (!background || client.visible) return client;
  }
  return null;
}

function orderedWaiting() {
  return [...jobs.values()].filter(j => j !== running).sort(byPriority);
}

function postTo(client, msg) {
  try { client.post(msg); } catch { forgetClient(client); }
}

function pump() {
  clearTimeout(queueTimer);
  queueTimer = null;
  const now = Date.now();
  if (running && now - running.startedAt >= QUEUE_RUN_TIMEOUT_MS) {
    finishJob(running, 'error', null, null);
    return;
  }
  if (running) {
    queueTimer = setTimeout(pump, running.startedAt + QUEUE_RUN_TIMEOUT_MS - now);
    tellWaiters(null);
    notifyWatchers();
    return;
  }
  const waiting = orderedWaiting();
  const next = waiting.find(j => runnerFor(j, now));
  if (!next) {
    // Wake when the earliest blocked job could become runnable
    const wakeAt = Math.min(
      ...waiting.map(j => j.notBefore).filter(t => t > now),
      pace.backgroundHoldUntil > now ? pace.backgroundHoldUntil : Infinity,
    );
    if (wakeAt !== Infinity) queueTimer = setTimeout(pump, wakeAt - now);
    notifyWatchers();
    return;
  }
  const wait = Math.max(pace.notBefore, pace.lastGrantAt + gapFor(next.kind)) - now;
  if (wait > 0) {
    queueTimer = setTimeout(pump, wait);
    tellWaiters(wait);
    notifyWatchers();
    return;
  }
  const client = runnerFor(next, now);
  running = next;
  next.runner = client;
  next.attempts++;
  next.startedAt = now;
  next.waitedMs = now - next.since;
  pace.previousGrantAt = pace.lastGrantAt;
  pace.lastGrantAt = now;
  nextJitter = Math.random();
  savePace();
  postTo(client, { type: 'run', key: next.key, kind: next.kind, url: next.url, attempt: next.attempts });
  queueTimer = setTimeout(pump, QUEUE_RUN_TIMEOUT_MS);
  tellWaiters(gapFor('scroll'));
  notifyWatchers();
}

// Scroll and Start reading show their wait to the user, so they hear their
// place in line and a rough estimate (null while another request is loading).
function tellWaiters(headWaitMs) {
  const waiting = orderedWaiting();
  waiting.forEach((job, i) => {
    if (QUEUE_BACKGROUND_KINDS.has(job.kind)) return;
    const waitMs = headWaitMs === null ? null : Math.max(0, headWaitMs) + i * gapFor(job.kind);
    for (const client of job.owners) {
      postTo(client, { type: 'waiting', key: job.key, position: i + 1, waitMs, behind: running?.kind || null });
    }
  });
}

function addJob(client, spec) {
  const finishedAt = recentlyFinished.get(spec.key);
  if (finishedAt && Date.now() - finishedAt < QUEUE_RECENT_MS) {
    postTo(client, { type: 'settled', key: spec.key, kind: spec.kind, result: 'ok' });
    return;
  }
  // Background lookups stopped for the session: nothing would ever run this
  // one, so settle it now rather than leave its card waiting forever
  if (pace.backgroundStopped && QUEUE_BACKGROUND_KINDS.has(spec.kind)) {
    postTo(client, { type: 'settled', key: spec.key, kind: spec.kind, result: 'failed' });
    return;
  }
  const existing = jobs.get(spec.key);
  if (existing) {
    existing.owners.add(client);  // the same novel wanted by two tabs is one job
    return;
  }
  const background = QUEUE_BACKGROUND_KINDS.has(spec.kind);
  jobs.set(spec.key, {
    key: spec.key, kind: spec.kind, url: spec.url || null,
    priority: QUEUE_PRIORITY[spec.kind] ?? QUEUE_PRIORITY.prefetch, seq: ++queueSeq,
    attempts: 0, maxAttempts: spec.maxAttempts ?? (background ? QUEUE_DEFAULT_ATTEMPTS : 1),
    notBefore: 0, since: Date.now(), owners: new Set([client]),
  });
  if (spec.kind === 'prefetch' || spec.kind === 'cover') progress.total++;
}

function settle(job, result) {
  jobs.delete(job.key);
  if (QUEUE_BACKGROUND_KINDS.has(job.kind) && (result === 'ok' || result === 'gone')) {
    recentlyFinished.set(job.key, Date.now());
  }
  if (running === job) running = null;
  for (const client of job.owners) postTo(client, { type: 'settled', key: job.key, kind: job.kind, result });
  if (job.kind === 'prefetch' || job.kind === 'cover') {
    progress.done++;
    writeProgress();
  }
}

let progressClearTimer = null;
function clearProgress() {
  progress.done = 0;
  progress.total = 0;
  chrome.storage.local.remove('tagFetchProgress').catch(() => {});
}

// immediate: lookups were cancelled (prefetch switched off), so the bar goes
// at once instead of briefly showing completion.
function writeProgress(immediate = false) {
  clearTimeout(progressClearTimer);
  // A running lookup nobody wants any more (cancelled mid-fetch) does not count
  const remaining = [...jobs.values()].some(j => (j.kind === 'prefetch' || j.kind === 'cover') && j.owners.size);
  if (!progress.total) return;
  if (!remaining && immediate) {
    clearProgress();
    return;
  }
  chrome.storage.local.set({ tagFetchProgress: { done: progress.done, total: progress.total } }).catch(() => {});
  if (!remaining) progressClearTimer = setTimeout(clearProgress, 1500);
}

// outcome: 'ok' | 'skip' (no request was needed) | 'gone' (404, logged out —
// retrying will not help) | 'blocked' (403/429/503 or a security check) |
// 'error' (network or server error — retried with backoff)
function finishJob(job, outcome, status, retryAfter) {
  const now = Date.now();
  if (outcome === 'skip') {
    pace.lastGrantAt = pace.previousGrantAt ?? pace.lastGrantAt;  // nothing was sent
    settle(job, 'ok');
    savePace();
    pump();
    return;
  }
  history.unshift({ kind: job.kind, url: job.url, at: job.startedAt, waitedMs: job.waitedMs, outcome, status });
  history.length = Math.min(history.length, QUEUE_HISTORY_MAX);
  running = null;
  job.runner = null;

  if (outcome === 'blocked') {
    pace.successes = 0;
    pace.paceDelay = Math.min(QUEUE_PACE_MAX_MS, pace.paceDelay + QUEUE_PACE_STEP_MS);
    const retryAfterMs = retryAfter ? parseRetryAfterMs(retryAfter) : null;
    if (retryAfterMs !== null) {
      pace.notBefore = Math.max(pace.notBefore, now + Math.min(retryAfterMs, QUEUE_MAX_RETRY_AFTER_MS));
    }
    if (++pace.consecutiveBlocks >= QUEUE_BLOCKS_BEFORE_COOLDOWN) {
      pace.consecutiveBlocks = 0;
      if (pace.cooldownCycles >= QUEUE_COOLDOWNS_MS.length) {
        pace.backgroundStopped = true;
        console.log('[nuf] queue: still blocked after every cooldown — background lookups stop until the browser restarts');
        for (const j of [...jobs.values()]) if (QUEUE_BACKGROUND_KINDS.has(j.kind) && j !== job) settle(j, 'failed');
      } else {
        const cooldown = QUEUE_COOLDOWNS_MS[pace.cooldownCycles++];
        pace.backgroundHoldUntil = now + cooldown;
        console.log(`[nuf] queue: ${QUEUE_BLOCKS_BEFORE_COOLDOWN} blocks in a row — background lookups pause for ${cooldown / 1000}s`);
      }
    }
    console.log(`[nuf] queue: blocked — requests now at least ${(QUEUE_GAP_MS.scroll + pace.paceDelay) / 1000}s apart`);
  } else if (outcome === 'ok') {
    pace.consecutiveBlocks = 0;
    if (pace.paceDelay > 0 && ++pace.successes >= QUEUE_RECOVERY_AFTER) {
      pace.successes = 0;
      const relaxed = Math.round(pace.paceDelay * 0.75);
      pace.paceDelay = relaxed < 1000 ? 0 : relaxed;
      if (!pace.paceDelay) pace.cooldownCycles = 0;  // fully recovered: earlier cooldowns no longer count
    }
  }

  const retryable = outcome === 'blocked' || outcome === 'error';
  if (retryable && job.owners.size && job.attempts < job.maxAttempts && !(pace.backgroundStopped && QUEUE_BACKGROUND_KINDS.has(job.kind))) {
    job.notBefore = outcome === 'error' ? now + QUEUE_RETRY_BASE_MS * 2 ** (job.attempts - 1) : 0;
  } else {
    settle(job, outcome === 'ok' ? 'ok' : outcome === 'gone' ? 'gone' : 'failed');
  }
  savePace();
  pump();
}

// ── Pages connect over one long-lived "nuf-jobs" port each ────────────────
// page → queue: { type: 'add', jobs: [{ key, kind, url, maxAttempts? }] }
//               { type: 'cancel', keys } · { type: 'cancel-kinds', kinds }
//               { type: 'done', key, outcome, status, retryAfter }
//               { type: 'visible', visible }
// queue → page: { type: 'run', key, kind, url, attempt }
//               { type: 'waiting', key, position, waitMs, behind }
//               { type: 'settled', key, kind, result: 'ok' | 'gone' | 'failed' | 'cancelled' }

const clients = new Set();

function forgetClient(client) {
  if (!clients.delete(client)) return;
  for (const job of [...jobs.values()]) {
    job.owners.delete(client);
    if (job === running && job.runner === client) {
      running = null;  // abandoned mid-run: no outcome, so no effect on pace
      job.runner = null;
    }
    if (!job.owners.size && job !== running) {
      jobs.delete(job.key);
      if (job.kind === 'prefetch' || job.kind === 'cover') progress.done++;
    }
  }
  writeProgress(true);
  pump();
}

function cancelJobs(client, predicate) {
  for (const job of [...jobs.values()]) {
    if (!job.owners.has(client) || !predicate(job)) continue;
    job.owners.delete(client);
    postTo(client, { type: 'settled', key: job.key, kind: job.kind, result: 'cancelled' });
    if (!job.owners.size && job !== running) {
      jobs.delete(job.key);
      if (job.kind === 'prefetch' || job.kind === 'cover') progress.done++;
    }
  }
  writeProgress(true);
  pump();
}

chrome.runtime.onConnect.addListener(port => {
  if (port.name !== 'nuf-jobs') return;
  const client = { visible: true, post: msg => port.postMessage(msg) };
  clients.add(client);
  port.onMessage.addListener(async msg => {
    await paceLoaded;
    if (msg.type === 'add') {
      for (const spec of msg.jobs) addJob(client, spec);
      if (msg.jobs.some(j => j.kind === 'prefetch' || j.kind === 'cover')) writeProgress();
      pump();
    } else if (msg.type === 'cancel') {
      const keys = new Set(msg.keys);
      cancelJobs(client, job => keys.has(job.key));
    } else if (msg.type === 'cancel-kinds') {
      const kinds = new Set(msg.kinds);
      cancelJobs(client, job => kinds.has(job.kind));
    } else if (msg.type === 'done') {
      const job = jobs.get(msg.key);
      if (job && job === running && job.runner === client) finishJob(job, msg.outcome, msg.status ?? null, msg.retryAfter ?? null);
    } else if (msg.type === 'visible') {
      client.visible = !!msg.visible;
      pump();
    }
  });
  port.onDisconnect.addListener(() => forgetClient(client));
});

// ── The Request queue page subscribes and gets a snapshot on every change ──

function snapshot() {
  const now = Date.now();
  const waiting = orderedWaiting();
  const describe = j => ({
    kind: j.kind, url: j.url, since: j.since, attempts: j.attempts,
    retryAt: j.notBefore > now ? j.notBefore : null,
    paused: !runnerFor(j, Math.max(now, j.notBefore)) && !(j.notBefore > now),
  });
  const counts = {};
  for (const j of waiting) counts[j.kind] = (counts[j.kind] || 0) + 1;
  return {
    now,
    baseGapMs: QUEUE_GAP_MS.scroll,
    backgroundGapMs: QUEUE_GAP_MS.prefetch,
    paceDelay: pace.paceDelay,
    notBefore: pace.notBefore,
    lastGrantAt: pace.lastGrantAt,
    successes: pace.successes,
    recoveryAfter: QUEUE_RECOVERY_AFTER,
    backgroundHoldUntil: pace.backgroundHoldUntil,
    backgroundStopped: pace.backgroundStopped,
    inFlight: running ? { kind: running.kind, url: running.url, since: running.startedAt } : null,
    waitingTotal: waiting.length,
    waitingCounts: counts,
    waiting: waiting.slice(0, QUEUE_SNAPSHOT_WAITING_MAX).map(describe),
    history,
    progress: { ...progress },
  };
}

let notifyScheduled = false;
function notifyWatchers() {
  if (!watchers.size || notifyScheduled) return;
  notifyScheduled = true;  // coalesce bursts (e.g. 200 jobs added at once)
  setTimeout(() => {
    notifyScheduled = false;
    const snap = snapshot();
    for (const port of watchers) {
      try { port.postMessage(snap); } catch { watchers.delete(port); }
    }
  }, 50);
}

chrome.runtime.onConnect.addListener(port => {
  if (port.name !== 'nuf-queue-watch') return;
  watchers.add(port);
  port.onDisconnect.addListener(() => watchers.delete(port));
  paceLoaded.then(() => {
    try { port.postMessage(snapshot()); } catch { watchers.delete(port); }
  });
});

// ── Test hooks: the Playwright suite drives the queue through these ────────
// requestSlot adds a one-off job owned by a stand-in client and resolves when
// it is that job's turn; release reports its outcome.
globalThis.nufBroker = {
  async requestSlot(kind, owner = null, url = null, { visible = true } = {}) {
    await paceLoaded;
    return new Promise(resolve => {
      const client = {
        visible,
        post: msg => {
          if (msg.type === 'run') resolve({ slotId: msg.key });
          else if (msg.type === 'settled') resolve({ slotId: null });
        },
      };
      clients.add(client);
      addJob(client, { key: `test-${++queueSeq}`, kind, url, maxAttempts: 1 });
      pump();
    });
  },
  release(slotId, outcome, retryAfter = null, status = null) {
    const job = jobs.get(slotId);
    if (job && job === running) finishJob(job, outcome, status, retryAfter);
  },
  state: () => ({ ...pace, waiting: orderedWaiting().length, inFlight: !!running }),
  snapshot,
  reset() {
    Object.assign(pace, {
      paceDelay: 0, notBefore: 0, lastGrantAt: 0, successes: 0,
      consecutiveBlocks: 0, cooldownCycles: 0, backgroundHoldUntil: 0, backgroundStopped: false,
    });
    for (const job of jobs.values()) {
      for (const client of job.owners) postTo(client, { type: 'settled', key: job.key, kind: job.kind, result: 'cancelled' });
    }
    jobs.clear();
    running = null;
    history.length = 0;
    recentlyFinished.clear();
    progress.done = 0;
    progress.total = 0;
    clearTimeout(queueTimer);
    notifyWatchers();
    return chrome.storage.session.remove('nufQueuePace');
  },
};
