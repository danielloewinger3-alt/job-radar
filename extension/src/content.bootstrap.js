// Content-script bootstrap (concatenated after lib/* by build.js into content.js).
//
// Injected into the TOP FRAME ONLY, once per explicit "Scan this page". It waits
// for a START message carrying a one-time nonce from the service worker, exchanges
// that nonce for the normalized pack (CONSUME_PACK), then scans + renders the
// review overlay. It never sends any page value or pack value back to the worker.

(function bootstrap() {
  if (window.__JOBRADAR_AUTOFILL_ACTIVE__) {
    // Re-injected into the same isolated world: the listener below is already
    // installed. A fresh START will arrive and supersede any current scan.
    return;
  }
  window.__JOBRADAR_AUTOFILL_ACTIVE__ = true;

  var runtimeId = chrome.runtime.id;
  var state = {
    pack: null,
    proposals: null,
    generation: 0, // bumped on every START/CLEANUP; stale async work bails
    handledNonces: new Set(),
  };

  function clearPack() {
    state.pack = null;
    state.proposals = null;
    removeOverlay(document);
  }

  function invalidate() {
    state.generation += 1;
    clearPack();
  }

  window.addEventListener('pagehide', invalidate, { once: true });
  window.addEventListener('beforeunload', invalidate, { once: true });

  chrome.runtime.onMessage.addListener(function (msg, sender) {
    if (!sender || sender.id !== runtimeId) return;
    if (!msg) return;
    if (msg.type === 'CLEANUP') {
      invalidate();
      return;
    }
    if (msg.type !== 'START') return;
    if (typeof msg.nonce !== 'string' || !/^[0-9a-f]{32}$/.test(msg.nonce)) return;
    if (state.handledNonces.has(msg.nonce)) return; // ignore duplicate delivery
    state.handledNonces.add(msg.nonce);
    handleStart(msg.nonce);
  });

  function handleStart(nonce) {
    var gen = ++state.generation; // supersede any in-flight scan
    clearPack();
    chrome.runtime.sendMessage({ type: 'CONSUME_PACK', nonce: nonce }, function (resp) {
      if (gen !== state.generation) return; // a newer START/CLEANUP won
      if (chrome.runtime.lastError || !resp || !resp.ok) {
        clearPack();
        var code = (resp && resp.error) || 'INTERNAL';
        renderError(
          code === 'PACK_UNAVAILABLE_RETRY'
            ? 'Pack no longer available. Reopen the Job Radar popup and press “Scan this page” again.'
            : 'Could not load the autofill pack for this page.',
        );
        return;
      }
      state.pack = resp.pack;
      try {
        runScan(gen);
      } catch (e) {
        clearPack();
        renderError('The autofill overlay failed to render on this page.');
      }
    });
  }

  function runScan(gen) {
    if (gen !== state.generation) return;
    var pack = state.pack;
    if (!pack) return;

    var scan = scanFields(document);
    var proposals = buildProposals(pack.fields || [], scan.fillable);
    state.proposals = proposals;

    var previewOnly = pack.reviewed !== true;

    renderOverlay({
      doc: document,
      pack: pack,
      proposals: proposals,
      pageFields: scan.fillable,
      excluded: scan.excluded,
      previewOnly: previewOnly,
      omittedCount: pack.omittedCount || 0,
      controller: {
        onAccept: function (proposal, pageIndex) {
          if (gen !== state.generation) return { ok: false, error: 'Scan is no longer active.' };
          if (previewOnly) return { ok: false, error: 'Preview only — pack not reviewed.' };
          var d = scan.fillable[pageIndex];
          if (!d || !d.el) return { ok: false, error: 'Target field not found.' };
          var valueToWrite = proposal.value;
          if (proposal.type === 'select') {
            var sel = proposal.selectResolution;
            if (!sel || sel.status !== 'ok') return { ok: false, error: 'No single matching option.' };
            valueToWrite = sel.option.value;
          }
          try {
            writeValue(d.el, valueToWrite);
            return { ok: true };
          } catch (err) {
            return {
              ok: false,
              error: err && err.name === 'RefusedWrite' ? 'Refused: ' + err.message : 'Write failed.',
            };
          }
        },
        onSkip: function () {},
        onPickTarget: function () {},
        onClose: invalidate,
      },
    });
  }

  function renderError(text) {
    renderOverlay({
      doc: document,
      pack: { heading: 'Job Radar Autofill' },
      proposals: [],
      pageFields: [],
      excluded: [],
      previewOnly: false,
      omittedCount: 0,
      controller: {
        onAccept: function () { return { ok: false, error: text }; },
        onSkip: function () {},
        onPickTarget: function () {},
        onClose: invalidate,
      },
    });
    try {
      var host = document.getElementById(HOST_ID);
      var root = host && host.shadowRoot;
      var note = root && root.querySelector('.note');
      if (note) note.textContent = text;
    } catch (e) {
      /* non-fatal */
    }
  }
})();
