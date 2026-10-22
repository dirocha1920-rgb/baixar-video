/* Abas "Um vídeo" / "Vários vídeos". Abre direto no lote com ?modo=lote ou #lote=ID. */
(function () {
  "use strict";
  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-vdl-tabs]"), function (bar) {
      var tabs = Array.prototype.slice.call(bar.querySelectorAll('[role="tab"]'));
      function select(tab, focus) {
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
          var panel = document.getElementById(t.getAttribute("aria-controls"));
          if (panel) panel.hidden = !on;
        });
        if (focus) tab.focus();
        document.dispatchEvent(new CustomEvent("vdl:resize"));
      }
      tabs.forEach(function (t, i) {
        t.addEventListener("click", function () { select(t); });
        t.addEventListener("keydown", function (e) {
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            select(tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length], true);
          }
        });
      });
      var wantBatch = /(^|[?&])modo=lote\b/.test(location.search) || /lote=/.test(location.hash);
      select(wantBatch ? tabs[1] : tabs[0]);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
