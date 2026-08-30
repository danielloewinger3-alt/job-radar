// Shared test helpers: a minimal fake `chrome`, and a jsdom loader.

import { JSDOM } from 'jsdom';

export function makeDom(html = '<!DOCTYPE html><html><body></body></html>') {
  const dom = new JSDOM(html, { url: 'https://jobs.example.com/apply' });
  return dom.window;
}

// Installs a jsdom window's DOM globals for code that reaches for `document` /
// `Event` directly. Returns a restore function.
export function withDomGlobals(win) {
  const keys = ['window', 'document', 'Event', 'CustomEvent', 'Node', 'HTMLElement', 'CSS', 'getComputedStyle'];
  const saved = {};
  for (const k of keys) {
    saved[k] = globalThis[k];
    if (win[k] !== undefined) globalThis[k] = win[k];
  }
  globalThis.document = win.document;
  return () => {
    for (const k of keys) {
      if (saved[k] === undefined) delete globalThis[k];
      else globalThis[k] = saved[k];
    }
  };
}

export function makeFakeChrome(opts = {}) {
  const listeners = { message: [], tabRemoved: [] };
  const calls = { executeScript: [], tabsSendMessage: [], storageSet: [], storageGet: [] };
  const storage = { local: { ...(opts.localStore || {}) } };

  const chrome = {
    runtime: {
      id: opts.runtimeId || 'abcdefghijklmnopabcdefghijklmnop',
      lastError: null,
      getURL: (p) => 'chrome-extension://' + (opts.runtimeId || 'x') + '/' + p,
      onMessage: {
        addListener: (fn) => listeners.message.push(fn),
      },
      sendMessage: (msg) => Promise.resolve(opts.onRuntimeSendMessage ? opts.onRuntimeSendMessage(msg) : undefined),
    },
    tabs: {
      onRemoved: { addListener: (fn) => listeners.tabRemoved.push(fn) },
      query: async () => opts.tabs || [{ id: 101 }],
      sendMessage: async (tabId, msg, sendOpts) => {
        calls.tabsSendMessage.push({ tabId, msg, sendOpts });
        if (opts.tabsSendMessageThrows) throw new Error('no receiver');
      },
    },
    scripting: {
      executeScript: async (args) => {
        calls.executeScript.push(args);
        if (opts.executeScriptThrows) throw new Error('cannot inject');
        return [{ result: null }];
      },
    },
    storage: {
      local: {
        get: async (key) => {
          calls.storageGet.push(key);
          if (typeof key === 'string') return { [key]: storage.local[key] };
          return { ...storage.local };
        },
        set: async (obj) => {
          calls.storageSet.push(obj);
          Object.assign(storage.local, obj);
        },
      },
    },
  };

  return {
    chrome,
    listeners,
    calls,
    storage,
    fireMessage(msg, sender) {
      const results = [];
      for (const fn of listeners.message) {
        let responded;
        const ret = fn(msg, sender, (r) => {
          responded = r;
        });
        results.push({ ret, responded });
      }
      return results;
    },
    fireTabRemoved(tabId) {
      for (const fn of listeners.tabRemoved) fn(tabId);
    },
  };
}

// Await a fake-chrome message dispatch that responds asynchronously.
export function sendAndWait(harness, msg, sender) {
  return new Promise((resolve, reject) => {
    let settled = false;
    for (const fn of harness.listeners.message) {
      const ret = fn(msg, sender, (r) => {
        settled = true;
        resolve(r);
      });
      if (ret !== true && !settled) {
        // synchronous handler that did not keep the channel open
      }
    }
    setTimeout(() => {
      if (!settled) reject(new Error('no response within 1s'));
    }, 1000);
  });
}
