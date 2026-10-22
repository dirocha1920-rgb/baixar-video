/*
 * Ferramenta de download de vídeos (Instagram / TikTok).
 * Monta a interface dentro de qualquer elemento com o atributo data-vdl.
 *   <div data-vdl></div>                         -> API no mesmo domínio
 *   <div data-vdl data-api="https://api.x.com"></div> -> API em outro domínio
 */
(function () {
  "use strict";

  var VALID = /^(https?:\/\/)?([a-z0-9-]+\.)*(instagram\.com|instagr\.am|tiktok\.com)\//i;

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else if (attrs[k] !== false && attrs[k] != null) n.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    (children || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }

  function fmtSize(bytes) {
    if (!bytes) return "";
    var mb = bytes / (1024 * 1024);
    return mb < 1 ? "<1 MB" : (mb < 10 ? mb.toFixed(1).replace(".", ",") : Math.round(mb)) + " MB";
  }

  function fmtDuration(s) {
    if (!s) return "";
    s = Math.round(s);
    var m = Math.floor(s / 60), r = s % 60;
    return m + ":" + (r < 10 ? "0" : "") + r;
  }

  function mount(root) {
    var api = (root.getAttribute("data-api") || "").replace(/\/+$/, "");
    var state = { url: "", info: null, controller: null };

    // ---- Estrutura -------------------------------------------------------
    var input = el("input", {
      class: "vdl-input", type: "url", inputmode: "url", autocomplete: "off", spellcheck: "false",
      placeholder: "Cole o link do Reel ou do TikTok", "aria-label": "Link do vídeo", required: true
    });
    var pasteBtn = el("button", { class: "vdl-paste", type: "button", text: "Colar", hidden: true });
    var go = el("button", { class: "vdl-go", type: "submit", text: "Buscar vídeo" });
    var form = el("form", { class: "vdl-form", novalidate: true }, [
      el("div", { class: "vdl-field" }, [input, pasteBtn]), go
    ]);
    var hint = el("p", { class: "vdl-hint", text: "Funciona com Reels, posts e vídeos públicos do Instagram e do TikTok." });
    var msg = el("p", { class: "vdl-msg", role: "status", "aria-live": "polite", hidden: true });

    var skeleton = el("div", { class: "vdl-skeleton", hidden: true, "aria-hidden": "true" }, [
      el("span", { class: "s-thumb" }),
      el("div", { class: "s-lines" }, [
        el("span", { style: "width:45%" }), el("span", { style: "width:80%" }),
        el("span", { style: "width:65%" }), el("span", { style: "width:100%;height:54px;margin-top:28px" })
      ])
    ]);

    var thumb = el("img", { class: "vdl-thumb", alt: "", decoding: "async" });
    var who = el("p", { class: "vdl-who" });
    var where = el("p", { class: "vdl-where" });
    var caption = el("p", { class: "vdl-caption" });
    var qList = el("div", { class: "vdl-q-list", role: "radiogroup" });
    var qNote = el("p", { class: "vdl-q-note", hidden: true });
    var qSet = el("fieldset", { class: "vdl-q" }, [el("legend", { text: "Qualidade" }), qList, qNote]);
    var fill = el("span", { class: "vdl-fill", "aria-hidden": "true" });
    var dlLabel = el("span", { text: "Baixar vídeo" });
    var dlBtn = el("button", { class: "vdl-dl", type: "button" }, [fill, dlLabel]);
    var cancel = el("button", { class: "vdl-cancel", type: "button", text: "Cancelar", hidden: true });
    var result = el("div", { class: "vdl-result", hidden: true }, [
      thumb,
      el("div", { class: "vdl-body" }, [who, where, caption, qSet, dlBtn, cancel])
    ]);

    root.classList.add("vdl");
    root.appendChild(form);
    root.appendChild(hint);
    root.appendChild(msg);
    root.appendChild(skeleton);
    root.appendChild(result);

    // ---- Helpers de estado ----------------------------------------------
    function notify() {
      // Para o iframe de /embed avisar a altura ao site que o contém
      root.dispatchEvent(new CustomEvent("vdl:resize", { bubbles: true }));
    }
    function showMsg(text, kind) {
      msg.textContent = text || "";
      msg.className = "vdl-msg" + (kind ? " is-" + kind : "");
      msg.hidden = !text;
      notify();
    }
    function setDl(text, mode, pct) {
      dlLabel.textContent = text;
      dlBtn.classList.toggle("is-busy", mode === "preparing" || mode === "progress");
      dlBtn.classList.toggle("is-preparing", mode === "preparing");
      dlBtn.classList.toggle("is-done", mode === "done");
      fill.style.width = mode === "progress" ? Math.max(2, pct) + "%" : (mode === "preparing" ? "100%" : "0");
      dlBtn.setAttribute("aria-busy", mode === "preparing" || mode === "progress" ? "true" : "false");
    }
    async function readError(res) {
      try { var j = await res.json(); if (j && j.detail) return j.detail; } catch (_) { /* ignora */ }
      if (res.status === 429) return "Muitas tentativas seguidas. Espere um minuto e tente de novo.";
      if (res.status >= 500) return "O servidor teve um problema. Tente de novo em instantes.";
      return "Algo deu errado (código " + res.status + ").";
    }

    // ---- Colar da área de transferência ---------------------------------
    if (navigator.clipboard && navigator.clipboard.readText && window.isSecureContext) {
      pasteBtn.hidden = false;
      pasteBtn.addEventListener("click", async function () {
        try {
          var t = (await navigator.clipboard.readText()).trim();
          if (t) { input.value = t; if (VALID.test(t)) form.requestSubmit ? form.requestSubmit() : go.click(); }
        } catch (_) { input.focus(); }
      });
    }
    input.addEventListener("paste", function () {
      setTimeout(function () {
        if (VALID.test(input.value.trim()) && input.value.trim() !== state.url) {
          form.requestSubmit ? form.requestSubmit() : go.click();
        }
      }, 0);
    });

    // ---- Buscar informações ---------------------------------------------
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var url = input.value.trim();
      if (state.controller) state.controller.abort();
      result.hidden = true;
      if (!url) { showMsg("Cole um link primeiro.", "error"); input.focus(); return; }
      if (!VALID.test(url)) { showMsg("Esse link não é do Instagram nem do TikTok. Confira e cole de novo.", "error"); return; }

      go.disabled = true; go.textContent = "Buscando…";
      showMsg("");
      skeleton.hidden = false; notify();
      try {
        var res = await fetch(api + "/api/info", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url })
        });
        if (!res.ok) throw new Error(await readError(res));
        var data = await res.json();
        state.url = url; state.info = data;
        render(data);
      } catch (err) {
        showMsg(err.name === "TypeError" ? "Sem conexão com o servidor. Verifique sua internet." : err.message, "error");
      } finally {
        skeleton.hidden = true;
        go.disabled = false; go.textContent = "Buscar vídeo";
        notify();
      }
    });

    function render(d) {
      if (d.thumbnail) { thumb.src = d.thumbnail; thumb.alt = "Prévia do vídeo"; }
      else { thumb.removeAttribute("src"); thumb.alt = ""; }
      who.textContent = d.uploader ? "@" + String(d.uploader).replace(/^@/, "") : "Vídeo";
      var bits = [d.platform === "tiktok" ? "TikTok" : "Instagram"];
      if (d.duration) bits.push(fmtDuration(d.duration));
      where.textContent = bits.join(", ");
      caption.textContent = d.title || "";
      caption.hidden = !d.title;

      qList.textContent = "";
      d.options.forEach(function (o, i) {
        var id = "vdl-q-" + Math.random().toString(36).slice(2, 8);
        var extra = [];
        if (o.kind === "audio") extra.push("MP3");
        else if (o.codec) extra.push(o.codec);
        if (o.filesize) extra.push(fmtSize(o.filesize));
        var radio = el("input", { type: "radio", name: "vdl-q", id: id, value: o.key, checked: i === 0 });
        radio.dataset.codec = o.codec || "";
        radio.dataset.kind = o.kind;
        var label = el("label", { for: id }, [
          document.createTextNode(o.kind === "audio" ? "Só áudio" : (i === 0 ? o.label + " máx." : o.label)),
          extra.length ? el("small", { text: extra.join(", ") }) : null
        ]);
        qList.appendChild(el("div", { class: "vdl-q-opt" }, [radio, label]));
      });
      updateNote();
      setDl("Baixar vídeo");
      cancel.hidden = true;
      result.hidden = false;
      notify();
    }

    function selected() { return qList.querySelector("input:checked"); }

    function updateNote() {
      var s = selected();
      var codec = s ? s.dataset.codec : "";
      var hasH264 = Array.prototype.some.call(qList.querySelectorAll("input"), function (r) {
        return r.dataset.kind === "video" && !r.dataset.codec;
      });
      if (s && s.dataset.kind === "video" && codec && codec !== "MP3") {
        qNote.textContent = codec + " tem ótima qualidade, mas pode não abrir em alguns PCs com Windows" +
          (hasH264 ? ". Se acontecer, baixe uma opção sem essa marca." : ".");
        qNote.hidden = false;
      } else {
        qNote.hidden = true;
      }
      dlLabel.textContent = s && s.dataset.kind === "audio" ? "Baixar áudio" : "Baixar vídeo";
      notify();
    }
    qList.addEventListener("change", updateNote);

    // ---- Download com progresso -----------------------------------------
    dlBtn.addEventListener("click", async function () {
      if (dlBtn.classList.contains("is-busy")) return;
      var s = selected();
      var isAudio = s && s.dataset.kind === "audio";
      var q = s ? s.value : "";
      var controller = new AbortController();
      state.controller = controller;
      showMsg("");
      setDl(isAudio ? "Preparando o áudio…" : "Preparando o vídeo…", "preparing");
      cancel.hidden = false;
      notify();
      try {
        var res = await fetch(api + "/api/download?url=" + encodeURIComponent(state.url) + "&quality=" + encodeURIComponent(q),
          { signal: controller.signal });
        if (!res.ok) throw new Error(await readError(res));

        var total = parseInt(res.headers.get("Content-Length") || "0", 10);
        var blob;
        if (res.body && res.body.getReader && total) {
          var reader = res.body.getReader(), chunks = [], got = 0;
          for (;;) {
            var step = await reader.read();
            if (step.done) break;
            chunks.push(step.value); got += step.value.length;
            var pct = Math.min(100, Math.round(got * 100 / total));
            setDl("Baixando " + pct + "%", "progress", pct);
          }
          blob = new Blob(chunks, { type: res.headers.get("Content-Type") || "video/mp4" });
        } else {
          setDl("Baixando…", "preparing");
          blob = await res.blob();
        }

        var cd = res.headers.get("Content-Disposition") || "";
        var m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
        var name = m ? decodeURIComponent(m[1]) : (isAudio ? "audio.mp3" : "video.mp4");
        var href = URL.createObjectURL(blob);
        var a = el("a", { href: href, download: name, style: "display:none" });
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(href); }, 60000);
        setDl(isAudio ? "Áudio baixado" : "Vídeo baixado", "done");
        showMsg("Pronto. O arquivo " + name + " foi salvo (" + fmtSize(blob.size) + ").", "info");
      } catch (err) {
        if (err.name === "AbortError") { setDl(isAudio ? "Baixar áudio" : "Baixar vídeo"); showMsg("Download cancelado.", "info"); }
        else {
          setDl(isAudio ? "Baixar áudio" : "Baixar vídeo");
          showMsg(err.name === "TypeError" ? "A conexão caiu durante o download. Tente de novo." : err.message, "error");
        }
      } finally {
        cancel.hidden = true;
        state.controller = null;
        notify();
      }
    });
    cancel.addEventListener("click", function () { if (state.controller) state.controller.abort(); });

    // Link passado na URL da página: ?url=...
    var pre = new URLSearchParams(location.search).get("url");
    if (pre) { input.value = pre; form.requestSubmit ? form.requestSubmit() : go.click(); }
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-vdl]"), mount);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
