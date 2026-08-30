// Bookmarklet bootstrap (concatenated after lib/* by build.js into bookmarklet.js).
//
// LIMITED FALLBACK -- not the production path. This runs in the page's own
// JavaScript environment and is far less isolated than the extension. Prefer the
// extension whenever it is available.
//
// It contacts no backend. On invocation it opens a small overlay with a textarea;
// the user pastes autofill JSON they exported for this session. The JSON is parsed
// with JSON.parse (never eval) and validated against the same schema_version and
// field contract as the extension. The parsed pack is kept only in this closure,
// the textarea is cleared immediately after a successful parse, and "Clear and
// close" drops every reference. No storage of any kind is used.

(function bookmarklet() {
  var pack = null; // closure-only

  function reset() {
    pack = null;
    removeOverlay(document);
  }

  // Drop the pasted pack if the page navigates/unloads while the overlay is open.
  window.addEventListener('pagehide', reset, { once: true });

  function openPasteUI() {
    removeOverlay(document);
    var host = document.createElement('div');
    host.id = HOST_ID;
    var shadow = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;

    var style = document.createElement('style');
    style.textContent =
      ':host{all:initial}.b{position:fixed;top:12px;right:12px;width:340px;z-index:2147483647;' +
      'font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1a1a1a;' +
      'background:#fff;border:1px solid #c9c9c9;border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.22);padding:12px}' +
      '.w{background:#fff4e5;border:1px solid #f0c27b;border-radius:6px;padding:8px;margin-bottom:8px;font-weight:600}' +
      'textarea{width:100%;height:120px;font:12px monospace;box-sizing:border-box}' +
      '.e{color:#b42318;margin-top:6px}button{font:inherit;padding:4px 10px;margin-top:8px;margin-right:6px;' +
      'border-radius:6px;border:1px solid #b9b9b9;background:#f6f6f6;cursor:pointer}';
    shadow.appendChild(style);

    var box = document.createElement('div');
    box.className = 'b';

    var warn = document.createElement('div');
    warn.className = 'w';
    warn.textContent =
      'Bookmarklet fallback: runs in this page’s JavaScript context and is less isolated than the extension. It never submits and never contacts a server.';
    box.appendChild(warn);

    var ta = document.createElement('textarea');
    ta.setAttribute('placeholder', 'Paste exported autofill JSON here');
    box.appendChild(ta);

    var err = document.createElement('div');
    err.className = 'e';
    err.hidden = true;
    box.appendChild(err);

    var loadBtn = document.createElement('button');
    loadBtn.textContent = 'Load JSON';
    var closeBtn = document.createElement('button');
    closeBtn.textContent = 'Clear and close';

    loadBtn.addEventListener('click', function () {
      var raw = ta.value;
      var parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (e) {
        err.hidden = false;
        err.textContent = 'That is not valid JSON.';
        return;
      }
      ta.value = ''; // clear immediately after parsing
      try {
        var af = normalizeAutofill(parsed); // schema_version + field contract
        var gated = applyReviewedGate(af.fields, af.reviewed);
        pack = {
          heading: 'Pasted pack' + (af.reviewed ? ' (reviewed)' : ' (unreviewed — preview only)'),
          reviewed: af.reviewed,
          fields: gated,
          omittedCount: af.omittedCount,
        };
        review();
      } catch (e2) {
        pack = null;
        err.hidden = false;
        err.textContent = 'Rejected: ' + (e2 && e2.message ? e2.message : 'unsupported pack format') + '.';
      }
    });
    closeBtn.addEventListener('click', reset);

    box.appendChild(loadBtn);
    box.appendChild(closeBtn);
    shadow.appendChild(box);
    (document.body || document.documentElement).appendChild(host);
  }

  function review() {
    if (!pack) return;
    var scan = scanFields(document);
    var proposals = buildProposals(pack.fields || [], scan.fillable);
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
          if (previewOnly) return { ok: false, error: 'Preview only — pack not reviewed.' };
          var d = scan.fillable[pageIndex];
          if (!d || !d.el) return { ok: false, error: 'Target field not found.' };
          var v = proposal.value;
          if (proposal.type === 'select') {
            var sel = proposal.selectResolution;
            if (!sel || sel.status !== 'ok') return { ok: false, error: 'No single matching option.' };
            v = sel.option.value;
          }
          try {
            writeValue(d.el, v);
            return { ok: true };
          } catch (e) {
            return { ok: false, error: e && e.name === 'RefusedWrite' ? 'Refused: ' + e.message : 'Write failed.' };
          }
        },
        onSkip: function () {},
        onPickTarget: function () {},
        onClose: reset,
      },
    });
  }

  openPasteUI();
})();
