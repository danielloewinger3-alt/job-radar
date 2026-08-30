// Job Radar - Alfred deterministic action registry.
//
// Registers a dispatcher through the frozen window.JobRadar bridge. A recognised
// command is handled here (return true, so app.js's built-in handler does NOT
// also run); anything unrecognised returns false and the built-in handler takes
// over, including its own harmless "didn't quite catch that" no-op.
//
// Hard rules:
//  - Every state-changing command (stage change, pack generation) goes through
//    an on-screen confirmation button. A spoken "yes" never executes anything.
//  - Ambiguous application / business names show an on-screen choice list; no
//    silent first-fuzzy-match.
//  - Alfred never: opens/sends outreach mail, invokes /mailto, approves an
//    outreach draft, triggers autofill, uploads files, submits applications,
//    archives/deletes, or edits contacts.
//  - No natural-language interpretation. Unknown input performs no action.
(function () {
  "use strict";

  const bridge = window.JobRadar;
  const UI = window.JobRadarUI;
  if (!bridge || typeof bridge.registerAlfredDispatcher !== "function" || !UI) {
    console.error("alfred.js: JobRadar bridge or JobRadarUI missing");
    return;
  }
  const alfredRoot = document.getElementById("alfred-root");

  function norm(s) { return String(s == null ? "" : s).trim().toLowerCase().replace(/\s+/g, " "); }
  function speakSafe(ctx, msg) { try { ctx.speak(msg); } catch (e) {} }

  // ---- on-screen surface in #alfred-root (mirrors spoken output for a11y) ----

  function host() {
    if (!alfredRoot) return null;
    let h = alfredRoot.querySelector(".alfred-host");
    if (!h) { h = UI.el("div", { className: "alfred-host" }); alfredRoot.appendChild(h); }
    alfredRoot.hidden = false;
    return h;
  }
  function clearHost() {
    if (!alfredRoot) return;
    const h = alfredRoot.querySelector(".alfred-host");
    if (h) UI.clear(h);
    alfredRoot.hidden = true;
  }
  function showLine(text) {
    const h = host();
    if (!h) return;
    UI.clear(h);
    h.appendChild(UI.el("div", { className: "alfred-line", role: "status", "aria-live": "polite", text: text }));
    setTimeout(() => { if (h.querySelector(".alfred-line")) clearHost(); }, 6500);
  }
  function showChoices(title, items) {
    return new Promise((resolve) => {
      const h = host();
      if (!h) { resolve(null); return; }
      UI.clear(h);
      const card = UI.el("div", { className: "alfred-action-card", role: "group", "aria-label": title });
      card.appendChild(UI.el("h4", { text: title }));
      const ul = UI.el("ul", { className: "alfred-choice-list" });
      items.forEach((it) => {
        const b = UI.el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: it.label });
        b.addEventListener("click", () => { clearHost(); resolve(it.value); });
        ul.appendChild(UI.el("li", null, b));
      });
      const cancel = UI.el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Cancel" });
      cancel.addEventListener("click", () => { clearHost(); resolve(null); });
      card.append(ul, cancel);
      h.appendChild(card);
      const first = card.querySelector("button");
      if (first) { try { first.focus(); } catch (e) {} }
    });
  }

  // ---- entity resolution -------------------------------------------------

  async function resolveApp(rawName, ctx) {
    if (!UI.tracker) { speakSafe(ctx, "The tracker isn't ready, sir."); return null; }
    const r = await UI.tracker.listApps();
    if (r.error) {
      speakSafe(ctx, r.error.unavailable ? "The tracker isn't available yet, sir." : "I couldn't reach the tracker, sir.");
      showLine("Tracker not available.");
      return null;
    }
    const apps = r.apps || [];
    const q = norm(rawName);
    const exact = apps.filter((a) => norm((a.company || "") + " " + (a.role_title || "")) === q || norm(a.company || "") === q);
    const pool = exact.length ? exact : apps.filter((a) => norm((a.company || "") + " " + (a.role_title || "")).indexOf(q) !== -1);
    if (pool.length === 1) return pool[0];
    if (pool.length === 0) {
      speakSafe(ctx, "I couldn't find an application matching " + rawName + ", sir.");
      showLine('No application matches "' + rawName + '".');
      return null;
    }
    speakSafe(ctx, "I found a few, sir — please choose on screen.");
    return showChoices("Which application?", pool.map((a) => ({
      label: (a.company || "—") + (a.role_title ? " · " + a.role_title : ""), value: a,
    })));
  }

  function matchStage(raw) {
    if (!UI.tracker) return null;
    const q = norm(raw).replace(/[^a-z ]/g, "").trim();
    const keys = UI.tracker.stageKeys || [];
    for (let i = 0; i < keys.length; i++) {
      const k = keys[i];
      if (q === k || q === k.replace(/_/g, " ") || q === norm(UI.tracker.stageLabel(k))) return k;
    }
    return null;
  }

  // ---- action registry -------------------------------------------------

  const actions = [
    {
      id: "nav.section",
      risk: "low",
      patterns: [/^(?:open|show|go to) (?:the )?(tracker|files|outreach|integrations)\b/],
      run: (m, text, ctx) => {
        const name = m[1];
        const fn = UI.sections[name];
        if (fn) { fn(); speakSafe(ctx, "Opening " + name + ", sir."); }
        else speakSafe(ctx, "That section isn't available, sir.");
      },
    },
    {
      id: "nav.builtin",
      risk: "low",
      patterns: [/^(?:go to|show|open) (?:the )?(news|dossier|prospects)\b/],
      run: (m, text, ctx) => { ctx.builtin("open " + m[1]); },
    },
    {
      id: "jobs.filter",
      risk: "low",
      patterns: [/^search for (.+)/, /^filter (?:jobs?|roles?) (?:for )?(.+)/, /^show me (.+?) (?:jobs?|roles?)$/],
      run: (m, text, ctx) => {
        const q = m[1].trim();
        const search = document.getElementById("search");
        if (!search) { speakSafe(ctx, "I can't reach the filter, sir."); return; }
        search.value = q;
        search.dispatchEvent(new Event("input"));
        speakSafe(ctx, 'Filtering for "' + q + '", sir.');
        showLine('Job filter set to "' + q + '".');
      },
    },
    {
      id: "apps.list",
      risk: "low",
      patterns: [/^(?:list|show)(?: me)? (?:my )?applications?$/],
      run: async (m, text, ctx) => {
        if (!UI.tracker) return;
        UI.tracker.openBoard();
        const r = await UI.tracker.listApps();
        if (r.error) { speakSafe(ctx, "The tracker isn't available yet, sir."); return; }
        const n = (r.apps || []).length;
        speakSafe(ctx, "You have " + n + " tracked application" + (n === 1 ? "" : "s") + ", sir.");
      },
    },
    {
      id: "apps.open",
      risk: "low",
      patterns: [/^open application (.+)/],
      run: async (m, text, ctx) => {
        const app = await resolveApp(m[1], ctx);
        if (!app) return;
        UI.tracker.openDetailById(app.id);
        speakSafe(ctx, "Opening " + (app.company || "the application") + ", sir.");
      },
    },
    {
      id: "apps.next",
      risk: "low",
      patterns: [/what.?s next/, /^what should i do next/],
      run: async (m, text, ctx) => {
        if (!UI.tracker) { speakSafe(ctx, "The tracker isn't ready, sir."); return; }
        const r = await UI.tracker.calendarSummary();
        if (r.error) {
          speakSafe(ctx, r.error.unavailable ? "The tracker isn't available yet, sir." : "I couldn't reach your calendar, sir.");
          return;
        }
        UI.tracker.openCalendar();
        const items = r.items || [];
        if (!items.length) { speakSafe(ctx, "Nothing scheduled in the next thirty days, sir."); return; }
        const first = items[0];
        speakSafe(ctx, "Next up: " + (first.title || first.kind) + (first.company ? " for " + first.company : "") +
          ", sir. " + items.length + " item" + (items.length === 1 ? "" : "s") + " in all.");
      },
    },
    {
      id: "apps.prepare",
      risk: "confirm",
      patterns: [/^prepare (?:an? )?(?:application )?pack for (.+)/],
      run: async (m, text, ctx) => {
        const app = await resolveApp(m[1], ctx);
        if (!app) return;
        speakSafe(ctx, "I can prepare a pack for " + (app.company || "that application") + ". Confirm the inputs on screen, sir.");
        UI.tracker.openPackFormForId(app.id);
      },
    },
    {
      id: "apps.stage",
      risk: "confirm",
      patterns: [/^move (.+?) to (.+)/, /^mark (.+?) as (.+)/],
      run: async (m, text, ctx) => {
        const stageKey = matchStage(m[2]);
        if (!stageKey) { speakSafe(ctx, "That isn't one of the tracker stages, sir."); showLine("Unknown stage: " + m[2]); return; }
        const app = await resolveApp(m[1], ctx);
        if (!app) return;
        speakSafe(ctx, "Please confirm the stage change on screen, sir.");
        const r = await UI.confirm({
          title: "Move this application?",
          body: (app.company || "This application") + (app.role_title ? " · " + app.role_title : "") +
            " → " + UI.tracker.stageLabel(stageKey) + ". Voice can't confirm this — use the button.",
          confirmLabel: "Move to " + UI.tracker.stageLabel(stageKey),
          danger: stageKey === "rejected" || stageKey === "withdrawn",
          note: { label: "Note (optional)", placeholder: "Why is it moving?" },
        });
        if (!r.confirmed) return;
        const out = await UI.tracker.applyStage(app.id, stageKey, r.note);
        if (out.ok) speakSafe(ctx, "Done, sir.");
        else { speakSafe(ctx, "That didn't go through, sir."); showLine(out.message || "Stage change failed."); }
      },
    },
    {
      id: "outreach.open",
      risk: "low",
      patterns: [/^open outreach(?: for (.+))?$/, /^draft (?:a )?follow[- ]?up for (.+)/],
      run: async (m, text, ctx) => {
        if (!UI.outreach) { speakSafe(ctx, "Outreach isn't ready, sir."); return; }
        const who = m[1];
        if (!who) { UI.outreach.openPanel(); speakSafe(ctx, "Opening outreach, sir."); return; }
        const r = await UI.outreach.listThreads();
        if (r.error) { UI.outreach.openPanel(); speakSafe(ctx, "The outreach list isn't available yet, sir."); return; }
        const q = norm(who);
        const matches = (r.threads || []).filter((t) => norm(UI.outreach.businessName(t)).indexOf(q) !== -1);
        if (matches.length === 1) {
          UI.outreach.openThreadById(matches[0].id);
          speakSafe(ctx, "Opening the thread, sir. Drafting and sending are manual steps.");
          return;
        }
        if (matches.length === 0) { UI.outreach.openPanel(); speakSafe(ctx, "I couldn't find that business, sir."); return; }
        speakSafe(ctx, "A few matches, sir — choose on screen.");
        const pick = await showChoices("Which business?", matches.map((t) => ({ label: UI.outreach.businessName(t), value: t })));
        if (pick) UI.outreach.openThreadById(pick.id);
      },
    },
    {
      id: "nlu.unsupported",
      risk: "low",
      patterns: [/natural language|free[- ]?form|conversational mode|talk normally|understand anything/],
      run: (m, text, ctx) => {
        speakSafe(ctx, "I only handle specific commands for now, sir. Free-form interpretation isn't available.");
        showLine("Deterministic commands only — natural-language mode is not available.");
      },
    },
  ];

  // ---- dispatch --------------------------------------------------------

  let busy = false;

  function dispatch(heard, ctx) {
    const text = norm(heard);
    if (!text) return false;
    for (let i = 0; i < actions.length; i++) {
      const act = actions[i];
      for (let j = 0; j < act.patterns.length; j++) {
        const re = act.patterns[j];
        if (!re.test(text)) continue;
        if (busy) { speakSafe(ctx, "One moment, sir — please finish the prompt on screen."); return true; }
        const m = text.match(re);
        busy = true;
        Promise.resolve()
          .then(() => act.run(m, text, ctx))
          .catch((e) => { console.error("alfred action '" + act.id + "' threw", e); speakSafe(ctx, "Something went wrong, sir."); showLine("That command failed."); })
          .then(() => { busy = false; });
        return true; // recognised -> built-in handler must not also run
      }
    }
    return false; // unrecognised -> built-in handler (and its own no-op) takes over
  }

  bridge.registerAlfredDispatcher(dispatch);

  // Exposed for tests / introspection only.
  UI.alfred = { actions: actions.map((a) => ({ id: a.id, risk: a.risk })), dispatch: dispatch };
})();
