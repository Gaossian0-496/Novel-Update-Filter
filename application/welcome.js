'use strict';

// MV3 extension pages have a default CSP of `script-src 'self'` which blocks
// inline <script> blocks and onclick="" attributes. This handler lives in an
// external file so it actually runs.
//
// Closing the tab: prefer chrome.tabs.getCurrent + chrome.tabs.remove. A bare
// window.close() is unreliable on chrome.tabs.create-opened tabs because the
// browser may treat them as user-opened (no window.opener) and refuse.

document.getElementById('got-it-btn').addEventListener('click', () => {
  if (typeof chrome !== 'undefined' && chrome.tabs && chrome.tabs.getCurrent) {
    chrome.tabs.getCurrent((tab) => {
      if (tab && tab.id != null) {
        chrome.tabs.remove(tab.id);
      } else {
        window.close();
      }
    });
  } else {
    window.close();
  }
});
