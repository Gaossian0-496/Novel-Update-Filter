'use strict';

// Client for the browser-wide request queue in background.js. A page hands
// every request it wants to the queue; the queue decides order, pacing,
// retries and cooldowns, and tells the page when to run each job. Shared by
// the content scripts (listed in manifest.json) and the manager page (loaded
// with a <script> tag), so it must not depend on content-script state.
//
//   requestQueue.handle(kind, run)    run(job) → { outcome, status?, retryAfter? }
//   requestQueue.add([{ key, kind, url }])   background jobs, deduplicated by key
//   requestQueue.request(kind, url, run, onWait) → Promise<value>
//                                     one-off job (scroll, Start reading); run returns
//                                     { outcome, status?, retryAfter?, value }
//   requestQueue.onSettled(cb)        cb({ key, kind, result: 'ok'|'gone'|'failed'|'cancelled' })
//   requestQueue.cancelKinds(kinds) · requestQueue.trackVisibility()
//
// outcome: 'ok' | 'skip' (nothing needed fetching) | 'gone' (retrying will not
// help) | 'blocked' (403/429/503 or a security check) | 'error' (retried).

const requestQueue = (() => {
  const runners = {};                 // kind → run(job)
  const oneOff = new Map();           // key → { run, onWait, resolve }
  const pending = new Map();          // key → spec, re-sent after a reconnect
  const settledListeners = [];
  let port = null;
  let gone = false;                   // extension reloaded under this page
  let trackingVisibility = false;
  let oneOffSeq = 0;

  function noteError(err) {
    if (/context invalidated/i.test(String(err?.message || err))) {
      gone = true;
      if (typeof noteChromeError === 'function') noteChromeError(err);
      runStranded();
    } else {
      console.log('[nuf] request queue port error', err);
    }
  }

  // No queue any more (extension reloaded): run the one-off requests still
  // waiting for a turn, so their callers are not left hanging
  function runStranded() {
    for (const [key, single] of oneOff) {
      if (single.running) continue;
      oneOff.delete(key);
      pending.delete(key);
      single.run({ key, kind: single.kind, url: single.url })
        .then(r => single.resolve(r.value), () => single.resolve(undefined));
    }
  }

  function send(msg) {
    if (!port) return false;
    try {
      port.postMessage(msg);
      return true;
    } catch (err) {
      noteError(err);
      return false;
    }
  }

  async function onMessage(msg) {
    if (msg.type === 'run') {
      const single = oneOff.get(msg.key);
      let result;
      if (single) single.running = true;
      // A one-off running now is no longer handed over on reconnect: a restarted
      // queue would run it a second time. Background jobs stay, because the queue
      // may retry them later and a restarted queue must still know about them.
      if (single) pending.delete(msg.key);
      try {
        result = single ? await single.run(msg) : runners[msg.kind] ? await runners[msg.kind](msg) : { outcome: 'skip' };
      } catch (err) {
        console.log('[nuf] queued job failed', msg.key, err);
        result = { outcome: 'error' };
      }
      send({ type: 'done', key: msg.key, outcome: result.outcome, status: result.status ?? null, retryAfter: result.retryAfter ?? null });
      if (single) {
        oneOff.delete(msg.key);
        pending.delete(msg.key);
        single.resolve(result.value);
      }
    } else if (msg.type === 'waiting') {
      oneOff.get(msg.key)?.onWait?.(msg);
    } else if (msg.type === 'settled') {
      pending.delete(msg.key);
      const single = oneOff.get(msg.key);
      // A job still running (the queue gave up waiting on it) finishes on its own
      if (single && !single.running) {
        // Cancelled before its turn (queue reset): run it now rather than never
        oneOff.delete(msg.key);
        single.run({ key: msg.key, kind: single.kind, url: single.url })
          .then(r => single.resolve(r.value), () => single.resolve(undefined));
      }
      for (const cb of settledListeners) cb(msg);
    }
  }

  function connect() {
    if (gone) return;
    try {
      port = chrome.runtime.connect({ name: 'nuf-jobs' });
    } catch (err) {
      port = null;
      noteError(err);
      return;
    }
    port.onMessage.addListener(onMessage);
    port.onDisconnect.addListener(() => {
      port = null;
      // The service worker restarted: reconnect and hand the work over again
      setTimeout(connect, 1000);
    });
    if (trackingVisibility) send({ type: 'visible', visible: !document.hidden });
    if (pending.size) send({ type: 'add', jobs: [...pending.values()] });
  }

  connect();

  return {
    handle(kind, run) {
      runners[kind] = run;
    },
    onSettled(cb) {
      settledListeners.push(cb);
    },
    add(specs) {
      const fresh = specs.filter(spec => !pending.has(spec.key));
      if (!fresh.length) return;
      for (const spec of fresh) pending.set(spec.key, spec);
      send({ type: 'add', jobs: fresh });
    },
    cancelKinds(kinds) {
      for (const [key, spec] of pending) if (kinds.includes(spec.kind)) pending.delete(key);
      send({ type: 'cancel-kinds', kinds });
    },
    has(key) {
      return pending.has(key);
    },
    request(kind, url, run, onWait = null) {
      const key = `${kind}-${Date.now()}-${++oneOffSeq}`;
      return new Promise(resolve => {
        const spec = { key, kind, url };
        // No queue to ask (extension reloaded): run straight away
        if (gone) {
          run({ key, kind, url }).then(r => resolve(r.value), () => resolve(undefined));
          return;
        }
        oneOff.set(key, {
          run: async job => {
            onWait?.(null);  // its turn: the fetch starts now
            return run(job);
          },
          kind,
          url,
          onWait,
          resolve,
        });
        // Without a port (service worker restarting) it waits in pending and
        // is sent on reconnect
        pending.set(key, spec);
        send({ type: 'add', jobs: [spec] });
      });
    },
    // Background jobs only run in a visible tab
    trackVisibility() {
      trackingVisibility = true;
      send({ type: 'visible', visible: !document.hidden });
      document.addEventListener('visibilitychange', () => send({ type: 'visible', visible: !document.hidden }));
    },
  };
})();
