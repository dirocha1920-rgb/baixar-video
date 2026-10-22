/*
 * Download em lote: vários links e/ou perfis (@) do TikTok e do Instagram.
 * Monta a interface dentro de qualquer elemento com data-vdl-batch.
 */
(function () {
  "use strict";

  var TERMINAL = { done: 1, cancelled: 1, failed: 1 };
  var STATUS_TEXT = {
    pending: "Na fila", downloading: "Baixando…", done: "Pronto", skipped: "Pulado", error: "Erro"
  };

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else if (attrs[k] !== false && attrs[k] != null) n.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function fmtSize(b) {
    if (!b) return "";
    var mb = b / 1048576;
    if (mb >= 1024) return (mb / 1024).toFixed(1).replace(".", ",") + " GB";
    return mb < 1 ? "<1 MB" : (mb < 10 ? mb.toFixed(1).replace(".", ",") : Math.round(mb)) + " MB";
  }
  function plat(p) { return p === "tiktok" ? "TikTok" : "Instagram"; }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (_) { return null; } }

  function mount(root) {
    var api = (root.getAttribute("data-api") || "").replace(/\/+$/, "");
    var cfg = { max_per_profile: 50, max_sources: 20, ttl_minutes: 60, instagram_profiles: true };
    var st = { jobId: null, timer: null, rows: {}, handle: store("vdl-handle") || "", checkSeq: 0 };

    // ---------- Formulário ----------
    var text = el("textarea", {
      class: "vdb-text", rows: "6", spellcheck: "false", autocomplete: "off",
      "aria-label": "Links e perfis, um por linha",
      placeholder: "Um por linha:\nhttps://www.tiktok.com/@perfil\nhttps://www.instagram.com/perfil/\n@perfil\nhttps://www.instagram.com/reel/…"
    });
    var recognized = el("div", { class: "vdb-recognized", "aria-live": "polite" });

    var handleBox = el("fieldset", { class: "vdb-handle", hidden: true }, [
      el("legend", { text: "Os @ sem link são de qual rede?" })
    ]);
    var handleList = el("div", { class: "vdb-pills", role: "radiogroup" });
    ["tiktok", "instagram"].forEach(function (p) {
      var id = "vdb-h-" + p + "-" + Math.random().toString(36).slice(2, 6);
      var r = el("input", { type: "radio", name: "vdb-handle", id: id, value: p, checked: st.handle === p });
      r.addEventListener("change", function () { st.handle = p; store("vdl-handle", p); check(); });
      handleList.appendChild(el("div", { class: "vdl-q-opt" }, [r, el("label", { for: id, text: plat(p) })]));
    });
    handleBox.appendChild(handleList);

    var perProfile = el("select", { class: "vdb-select", id: "vdb-pp-" + Math.random().toString(36).slice(2, 6) });
    var quality = el("select", { class: "vdb-select", id: "vdb-q-" + Math.random().toString(36).slice(2, 6) }, [
      el("option", { value: "max", text: "Máxima disponível" }),
      el("option", { value: "compat", text: "Compatível com tudo (H.264)" })
    ]);
    var opts = el("div", { class: "vdb-opts" }, [
      el("div", { class: "vdb-opt" }, [el("label", { for: perProfile.id, text: "Vídeos por perfil" }), perProfile]),
      el("div", { class: "vdb-opt" }, [el("label", { for: quality.id, text: "Qualidade" }), quality])
    ]);
    var igNote = el("p", { class: "vdb-note", hidden: true });
    var start = el("button", { class: "vdl-dl vdb-start", type: "submit" }, [el("span", { text: "Baixar tudo" })]);
    var formMsg = el("p", { class: "vdl-msg", role: "status", hidden: true });
    var form = el("form", { class: "vdb-form", novalidate: true },
      [text, recognized, handleBox, opts, igNote, start, formMsg]);

    // ---------- Acompanhamento ----------
    var jobTitle = el("p", { class: "vdb-job-title" });
    var jobSub = el("p", { class: "vdb-job-sub" });
    var barFill = el("span", { class: "vdb-bar-fill" });
    var bar = el("div", { class: "vdb-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, [barFill]);
    var sourcesList = el("ul", { class: "vdb-sources" });
    var itemsList = el("ol", { class: "vdb-items" });
    var zipBtn = el("a", { class: "vdl-dl vdb-zip", hidden: true, href: "#" });
    var cancelBtn = el("button", { class: "vdl-cancel", type: "button", text: "Cancelar lote" });
    var newBtn = el("button", { class: "vdb-new", type: "button", text: "Novo lote", hidden: true });
    var jobNote = el("p", { class: "vdb-note" });
    var jobView = el("section", { class: "vdb-job", hidden: true, "aria-live": "polite" }, [
      jobTitle, jobSub, bar, sourcesList, zipBtn, el("div", { class: "vdb-actions" }, [cancelBtn, newBtn]),
      itemsList, jobNote
    ]);

    root.classList.add("vdl", "vdb");
    root.appendChild(form);
    root.appendChild(jobView);

    function notify() { root.dispatchEvent(new CustomEvent("vdl:resize", { bubbles: true })); }
    function showMsg(t, kind) {
      formMsg.textContent = t || ""; formMsg.className = "vdl-msg" + (kind ? " is-" + kind : ""); formMsg.hidden = !t; notify();
    }
    async function readError(res) {
      try { var j = await res.json(); if (j && j.detail) return j.detail; } catch (_) { /* ignora */ }
      return res.status === 429 ? "Muitas tentativas. Espere um pouco." : "Algo deu errado (código " + res.status + ").";
    }

    function fillPerProfile() {
      perProfile.textContent = "";
      [5, 10, 25, 50, 100].filter(function (n) { return n <= cfg.max_per_profile; }).forEach(function (n) {
        perProfile.appendChild(el("option", { value: n, text: "Até " + n, selected: n === 10 }));
      });
      if (!perProfile.options.length) perProfile.appendChild(el("option", { value: cfg.max_per_profile, text: "Até " + cfg.max_per_profile }));
    }

    // ---------- Reconhecimento ao vivo ----------
    var debounce;
    text.addEventListener("input", function () { clearTimeout(debounce); debounce = setTimeout(check, 350); });

    async function check() {
      var seq = ++st.checkSeq;
      var value = text.value.trim();
      if (!value) { recognized.textContent = ""; handleBox.hidden = true; igNote.hidden = true; notify(); return; }
      try {
        var res = await fetch(api + "/api/batch/check", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: value, handle_platform: st.handle || null })
        });
        if (seq !== st.checkSeq || !res.ok) return;
        renderCheck(await res.json());
      } catch (_) { /* sem conexão: valida ao enviar */ }
    }

    function renderCheck(d) {
      recognized.textContent = "";
      // Mostra a escolha de rede sempre que houver um @ ou nome solto (sem link)
      var hasHandle = text.value.split(/[\s,;]+/).some(function (t) {
        return t && !/[\/:]/.test(t) && !/(instagram\.com|tiktok\.com|instagr\.am)/i.test(t);
      });
      handleBox.hidden = !(d.needs_platform || hasHandle);
      if (d.sources.length) {
        var profilesN = d.sources.filter(function (s) { return s.kind === "profile"; }).length;
        var linksN = d.sources.length - profilesN;
        var parts = [];
        if (profilesN) parts.push(profilesN + (profilesN > 1 ? " perfis" : " perfil"));
        if (linksN) parts.push(linksN + (linksN > 1 ? " links" : " link"));
        recognized.appendChild(el("p", { class: "vdb-count", text: "Reconhecido: " + parts.join(" e ") }));
        var ul = el("ul", { class: "vdb-chips" });
        d.sources.slice(0, 30).forEach(function (s) {
          var what = s.kind === "profile" ? (s.platform === "instagram" ? "Reels de " + s.label : "vídeos de " + s.label) : s.label;
          ul.appendChild(el("li", { class: "vdb-chip is-" + s.platform }, [el("b", { text: plat(s.platform) }), " " + what]));
        });
        recognized.appendChild(ul);
      }
      d.problems.filter(function (p) { return !/escolha se os @/.test(p); }).forEach(function (p) {
        recognized.appendChild(el("p", { class: "vdb-problem", text: p }));
      });
      var igProfile = d.sources.some(function (s) { return s.kind === "profile" && s.platform === "instagram"; });
      igNote.hidden = !igProfile;
      igNote.textContent = cfg.instagram_profiles
        ? "Do Instagram, só os Reels do perfil são baixados (posts de foto e vídeos do feed ficam de fora)."
        : "Este servidor ainda não está configurado para listar perfis do Instagram (precisa de login). Links de Reels avulsos funcionam normalmente.";
      igNote.classList.toggle("is-warn", !cfg.instagram_profiles);
      notify();
    }

    // ---------- Enviar ----------
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!text.value.trim()) { showMsg("Cole pelo menos um link ou perfil.", "error"); text.focus(); return; }
      start.setAttribute("aria-busy", "true"); start.classList.add("is-busy", "is-preparing");
      showMsg("");
      try {
        var res = await fetch(api + "/api/batch", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text.value, handle_platform: st.handle || null,
            per_profile: parseInt(perProfile.value, 10), quality: quality.value })
        });
        if (!res.ok) throw new Error(await readError(res));
        var d = await res.json();
        openJob(d.id);
      } catch (err) {
        showMsg(err.name === "TypeError" ? "Sem conexão com o servidor." : err.message, "error");
      } finally {
        start.removeAttribute("aria-busy"); start.classList.remove("is-busy", "is-preparing");
      }
    });

    // ---------- Acompanhar lote ----------
    function openJob(id) {
      st.jobId = id; st.rows = {};
      itemsList.textContent = ""; sourcesList.textContent = "";
      try { history.replaceState(null, "", "#lote=" + id); } catch (_) { /* iframe sandbox */ }
      form.hidden = true; jobView.hidden = false;
      zipBtn.hidden = true; newBtn.hidden = true; cancelBtn.hidden = false;
      jobTitle.textContent = "Preparando o lote…"; jobSub.textContent = "";
      barFill.style.width = "0"; bar.classList.add("is-indeterminate");
      notify();
      poll();
    }

    async function poll() {
      clearTimeout(st.timer);
      if (!st.jobId) return;
      try {
        var res = await fetch(api + "/api/batch/" + encodeURIComponent(st.jobId), { cache: "no-store" });
        if (res.status === 404) { resetToForm((await readError(res))); return; }
        if (!res.ok) throw new Error(await readError(res));
        var d = await res.json();
        renderJob(d);
        if (!TERMINAL[d.status]) st.timer = setTimeout(poll, 1500);
      } catch (_) {
        jobSub.textContent = "Conexão instável, tentando de novo…";
        st.timer = setTimeout(poll, 4000);
      }
    }

    function renderJob(d) {
      var c = d.counts, finished = c.done + c.skipped + c.error;
      var pct = c.total ? Math.round(finished * 100 / c.total) : 0;
      var running = !TERMINAL[d.status];

      if (d.status === "queued") {
        jobTitle.textContent = "Na fila";
        jobSub.textContent = d.queue_position ? "Seu lote é o " + d.queue_position + "º da fila." : "";
      } else if (d.status === "listing") {
        jobTitle.textContent = "Procurando os vídeos…";
        jobSub.textContent = c.total ? c.total + " encontrados até agora" : "Lendo os perfis.";
      } else if (d.status === "downloading") {
        jobTitle.textContent = "Baixando " + Math.min(finished + 1, c.total) + " de " + c.total;
        jobSub.textContent = c.done + " prontos" + (d.total_size ? ", " + fmtSize(d.total_size) : "") +
          ". Os vídeos são baixados um de cada vez, com pausas, para a plataforma não bloquear.";
      } else if (d.status === "done") {
        jobTitle.textContent = c.done + (c.done === 1 ? " vídeo pronto" : " vídeos prontos");
        var extra = [];
        if (c.skipped) extra.push(c.skipped + " pulado" + (c.skipped > 1 ? "s" : ""));
        if (c.error) extra.push(c.error + " com erro");
        jobSub.textContent = fmtSize(d.total_size) + (extra.length ? ". " + extra.join(", ") + " (veja a lista)." : ".");
      } else {
        jobTitle.textContent = d.status === "cancelled" ? "Lote cancelado" : "O lote não foi concluído";
        jobSub.textContent = d.message || "";
      }

      bar.classList.toggle("is-indeterminate", running && (d.status !== "downloading" || !c.total));
      barFill.style.width = (d.status === "done" ? 100 : pct) + "%";
      bar.setAttribute("aria-valuenow", String(d.status === "done" ? 100 : pct));

      // Perfis/links e o que foi encontrado
      sourcesList.textContent = "";
      d.sources.forEach(function (s) {
        var txt;
        if (s.status === "error") txt = s.message;
        else if (s.status === "listing") txt = "procurando…";
        else if (s.status === "pending") txt = "aguardando";
        else txt = s.kind === "profile" ? s.found + (s.platform === "instagram" ? (s.found === 1 ? " Reel" : " Reels") : (s.found === 1 ? " vídeo" : " vídeos")) : "1 vídeo";
        sourcesList.appendChild(el("li", { class: "vdb-src is-" + s.status }, [
          el("b", { text: plat(s.platform) + " " + s.label }), el("span", { text: txt })
        ]));
      });

      // Vídeos
      d.items.forEach(function (it) {
        var row = st.rows[it.index];
        if (!row) {
          row = {
            li: el("li", { class: "vdb-item" }),
            name: el("span", { class: "vdb-item-name" }),
            state: el("span", { class: "vdb-item-state" }),
            link: el("a", { class: "vdb-item-dl", text: "Baixar", hidden: true })
          };
          row.li.appendChild(row.name); row.li.appendChild(row.state); row.li.appendChild(row.link);
          itemsList.appendChild(row.li);
          st.rows[it.index] = row;
        }
        var label = it.title || it.url.replace(/^https?:\/\/(www\.)?/, "");
        row.name.textContent = label;
        row.name.title = it.url;
        var s = STATUS_TEXT[it.status] || it.status;
        if (it.status === "done") s += ", " + fmtSize(it.size);
        if ((it.status === "skipped" || it.status === "error") && it.message) s = it.message;
        row.state.textContent = s;
        row.li.className = "vdb-item is-" + it.status;
        if (it.status === "done" && row.link.hidden) {
          row.link.href = api + "/api/batch/" + encodeURIComponent(d.id) + "/file/" + it.index;
          row.link.setAttribute("download", it.filename);
          row.link.setAttribute("aria-label", "Baixar " + it.filename);
          row.link.hidden = false;
        }
      });

      // Ações
      cancelBtn.hidden = !running;
      newBtn.hidden = running;
      if (c.done > 0 && (!running)) {
        zipBtn.hidden = false;
        zipBtn.href = api + "/api/batch/" + encodeURIComponent(d.id) + "/zip";
        zipBtn.setAttribute("download", "");
        zipBtn.textContent = "Baixar tudo em .zip (" + fmtSize(d.total_size) + ")";
      } else {
        zipBtn.hidden = true;
      }
      jobNote.textContent = TERMINAL[d.status] && c.done
        ? "Os arquivos ficam disponíveis por " + cfg.ttl_minutes + " minutos. Você também pode baixar um por um na lista."
        : (running ? "Pode deixar esta página aberta; o progresso continua mesmo se você recarregar." : "");
      notify();
    }

    cancelBtn.addEventListener("click", async function () {
      if (!st.jobId) return;
      cancelBtn.disabled = true;
      try { await fetch(api + "/api/batch/" + encodeURIComponent(st.jobId) + "/cancel", { method: "POST" }); }
      catch (_) { /* o próximo poll mostra o estado */ }
      cancelBtn.disabled = false;
      poll();
    });

    function resetToForm(message) {
      clearTimeout(st.timer);
      st.jobId = null;
      try { history.replaceState(null, "", location.pathname + location.search); } catch (_) { /* ignora */ }
      jobView.hidden = true; form.hidden = false;
      showMsg(message || "", message ? "info" : "");
      notify();
    }
    newBtn.addEventListener("click", function () { resetToForm(""); text.focus(); });

    // ---------- Início ----------
    fillPerProfile();
    fetch(api + "/api/config").then(function (r) { return r.ok ? r.json() : null; }).then(function (c) {
      if (c && c.batch) { cfg = c.batch; fillPerProfile(); if (text.value.trim()) check(); }
    }).catch(function () { /* usa padrões */ });

    var m = /lote=([\w-]+)/.exec(location.hash);
    if (m) openJob(m[1]);
  }

  function init() { Array.prototype.forEach.call(document.querySelectorAll("[data-vdl-batch]"), mount); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
