// Job Radar - assist / Alfred workstream front-end module (placeholder).
//
// Loaded last as an ordered classic <script> after app.js, so window.JobRadar is
// already defined when this runs. When the assist workstream lands it will call
// window.JobRadar.registerAlfredDispatcher(fn) here to take over voice-command
// parsing; until then this is an empty IIFE shell and the built-in Alfred
// handler in app.js stays fully in charge. Do not add interface logic here in
// the prelude.
(function () {
  "use strict";
})();
