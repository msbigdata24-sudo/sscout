/* Сигнал-Скаут — UI (fetch → Python API) */
(function () {
  const API_BASE = window.location.protocol === "file:"
    ? "http://127.0.0.1:8765"
    : window.location.origin;

  const PILOT = {
    clientName: "ООО «Опалубка-Домстрой»",
    clientSite: "https://opalubka-domstroy.ru",
    niche: "Аренда и продажа опалубки (крупнощитовая, мелкощитовая, перекрытия, колонны). Склад Щелково, МО.",
    regionMode: "include",
    regions: "Московская область, Ярославская область, Владимирская область, Нижегородская область",
    queries: [
      "аренда опалубки",
      "аренда крупнощитовой опалубки",
      "аренда мелкощитовой опалубки",
      "аренда опалубки Москва",
      "аренда опалубки Щелково",
      "аренда опалубки Ярославль",
      "аренда опалубки Владимир",
      "аренда опалубки Нижний Новгород",
    ].join("\n"),
    excludeDomains: "opalubka.ru, peri.ru, snab-str.ru, opalubka-market.ru, avito.ru, 2gis.ru, yell.ru, pulscen.ru",
    phoneFilter: "business",
    sources: ["serp", "site", "catalog"],
    checkAlive: true,
    maxSites: 50,
    crawlDepth: 2,
    requestDelayMs: 500,
    useProxy: false,
  };

  const PIPELINE_STEPS = [
    { id: "analyze", title: "Разбор сайта клиента", desc: "Ниша, ключевые слова, гео из контента." },
    { id: "serp", title: "Поиск в Яндекс", desc: "XMLRiver: живой поиск, 4 страницы выдачи." },
    { id: "filter", title: "Фильтры", desc: "Регион, живой сайт, исключения, лимит сайтов." },
    { id: "crawl", title: "Обход сайтов конкурентов", desc: "Главная, контакты, footer, JSON-LD." },
    { id: "catalog", title: "Каталоги и площадки", desc: "KudaGid, 2GIS — если на сайте пусто." },
    { id: "dedup", title: "Нормализация и удаление дубликатов", desc: "79001234567 · мобильные и городские." },
  ];

  let brief = null;
  let results = [];
  let currentRunId = null;
  let isLiveRun = false;
  let pollTimer = null;
  let elapsedTimer = null;
  let runStartedAt = 0;
  let runActive = false;
  let apiOnline = false;
  let tablePage = 1;
  let sortCol = "";
  let sortDir = 1;
  const PAGE_SIZE = 50;
  const RESUME_KEY = "signal-scout-resume-run";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    setTimeout(() => el.classList.remove("show"), 2800);
  }

  function formatPhone(raw) {
    if (!raw) return "";
    let d = String(raw).replace(/\D/g, "");
    if (d.length === 10) d = "7" + d;
    if (d.length === 11 && d.startsWith("8")) d = "7" + d.slice(1);
    return d.length === 11 && d.startsWith("7") ? d : raw;
  }

  function phoneTypeLabel(t) {
    return t === "mobile" ? "мобильный" : t === "city" ? "городской" : "";
  }

  function statusTag(status) {
    const map = {
      найден: "tag-ok",
      "без телефона": "tag-warn",
      исключён: "tag-danger",
      агрегатор: "tag-muted",
    };
    return `<span class="tag ${map[status] || "tag-muted"}">${status}</span>`;
  }

  function renderPhoneCell(phone, type, valid) {
    if (!phone) return '<span class="phone phone-empty">—</span>';
    const cls = type === "mobile" ? "phone-mobile" : type === "city" ? "phone-city" : "";
    const invalid = valid === false ? " phone-invalid" : "";
    const shown = formatPhone(phone);
    const tip = phoneTypeLabel(type) || "номер";
    return `<span class="phone-wrap">
      <span class="phone ${cls}${invalid}" title="${tip}">${shown}</span>
      <button type="button" class="btn-mini" data-copy="${shown}" title="Копировать">⧉</button>
    </span>`;
  }

  function readForm() {
    const sources = [...$("#sources").selectedOptions].map((o) => o.value);
    return {
      clientName: $("#client-name").value.trim(),
      clientSite: $("#client-site").value.trim(),
      niche: $("#niche").value.trim(),
      regionMode: document.querySelector('input[name="regionMode"]:checked')?.value || "include",
      regions: $("#regions").value.trim(),
      queries: $("#queries").value.trim(),
      excludeDomains: $("#exclude-domains").value.trim(),
      phoneFilter: $("#phone-filter").value,
      sources,
      checkAlive: $("#check-alive").checked,
      maxSites: parseInt($("#max-sites").value, 10) || 50,
      crawlDepth: parseInt($("#crawl-depth").value, 10) || 2,
      requestDelayMs: parseInt($("#request-delay").value, 10) || 500,
      useProxy: $("#use-proxy").checked,
      xmlRiverUser: $("#xml-river-user").value.trim(),
      apiKey: $("#api-key").value.trim(),
    };
  }

  function fillForm(data) {
    $("#client-name").value = data.clientName || "";
    $("#client-site").value = data.clientSite || "";
    $("#niche").value = data.niche || "";
    $$('input[name="regionMode"]').forEach((r) => {
      r.checked = r.value === (data.regionMode || "include");
    });
    $("#regions").value = data.regions || "";
    $("#queries").value = data.queries || "";
    $("#exclude-domains").value = data.excludeDomains || "";
    $("#phone-filter").value = data.phoneFilter || "business";
    $("#max-sites").value = data.maxSites ?? 50;
    $("#crawl-depth").value = data.crawlDepth ?? 2;
    $("#request-delay").value = data.requestDelayMs ?? 500;
    $("#use-proxy").checked = !!data.useProxy;
    $("#check-alive").checked = data.checkAlive !== false;
    $("#xml-river-user").value = data.xmlRiverUser || "";
    $("#api-key").value = data.apiKey || "";
    const src = data.sources || ["serp", "site"];
    $$("#sources option").forEach((o) => {
      o.selected = src.includes(o.value);
    });
    fillRegionFilter(data.regions);
  }

  function fillRegionFilter(regionsStr) {
    const sel = $("#region-filter");
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">Все регионы</option>';
    (regionsStr || "").split(",").map((r) => r.trim()).filter(Boolean).forEach((r) => {
      const o = document.createElement("option");
      o.value = r;
      o.textContent = r;
      sel.appendChild(o);
    });
    if (cur) sel.value = cur;
  }

  function renderPipeline(state) {
    $("#pipeline").innerHTML = PIPELINE_STEPS.map((step, i) => {
      const st = state[step.id] || "pending";
      const icon = st === "done" ? "✓" : st === "running" ? "…" : i + 1;
      const log = state[step.id + "Log"] ? `<div class="pipe-log">${state[step.id + "Log"]}</div>` : "";
      return `<div class="pipe-step ${st}"><div class="pipe-dot">${icon}</div><div class="pipe-body"><h3>${step.title}</h3><p>${step.desc}</p>${log}</div></div>`;
    }).join("");
  }

  function formatElapsed(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function stepTitle(stepId) {
    const step = PIPELINE_STEPS.find((s) => s.id === stepId);
    return step ? step.title : "";
  }

  function tickElapsed() {
    if (!runActive || !runStartedAt) return 0;
    const sec = Math.floor((Date.now() - runStartedAt) / 1000);
    const tick = $("#run-elapsed");
    if (tick) tick.textContent = `${formatElapsed(sec)}`;
    return sec;
  }

  function startRunTimer() {
    runStartedAt = Date.now();
    runActive = true;
    $("#run-card")?.classList.add("run-active");
    const el = $("#run-elapsed");
    if (el) el.hidden = false;
    tickElapsed();
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(tickElapsed, 250);
  }

  function stopRunTimer() {
    runActive = false;
    clearInterval(elapsedTimer);
    $("#run-card")?.classList.remove("run-active");
  }

  function setProgressUI(progress, label, status) {
    const pct = Math.max(0, Math.min(100, Math.round(progress || 0)));
    const fill = $("#progress-fill");
    const bar = $("#progress-bar");
    const labelEl = $("#progress-label");
    const pctEl = $("#progress-percent");
    if (fill) fill.style.width = `${pct}%`;
    if (bar) bar.setAttribute("aria-valuenow", String(pct));
    if (pctEl) {
      pctEl.textContent = `${pct}%`;
      pctEl.style.color = status === "done" ? "var(--success)" : "var(--accent)";
    }
    if (labelEl) {
      if (status === "done") labelEl.innerHTML = "<strong>Готово!</strong> Переход к результатам…";
      else if (status === "error") labelEl.innerHTML = "<strong>Ошибка</strong> — см. лог ниже";
      else if (status === "stopped") labelEl.textContent = "Остановлено";
      else if (status === "running" || status === "pending") labelEl.textContent = label || "Сбор выполняется…";
      else if (label) labelEl.textContent = label;
    }
  }

  function renderLiveLogs(logs, waiting) {
    const box = $("#live-logs");
    if (!box) return;
    box.classList.toggle("waiting", !!waiting);
    if (!logs?.length) {
      box.innerHTML = waiting
        ? '<div class="log-line">Подключение к серверу, ожидайте…</div>'
        : '<div class="log-line log-muted">Здесь появятся сообщения по ходу сбора</div>';
      return;
    }
    box.innerHTML = logs.slice(-80).map((l) => {
      const cls = l.status === "error" ? "log-err" : l.status === "success" ? "log-ok" : l.status === "skip" ? "log-muted" : "";
      return `<div class="log-line ${cls}"><span class="log-ts">${l.ts}</span> ${l.msg}</div>`;
    }).join("");
    box.scrollTop = box.scrollHeight;
  }

  function filterRows(rows) {
    const search = ($("#table-search").value || "").toLowerCase();
    const statusF = $("#status-filter").value;
    const typeF = $("#type-filter")?.value || "";
    const regionF = $("#region-filter")?.value || "";
    const dateF = $("#date-filter")?.value || "";

    return rows.filter((r) => {
      if (statusF && r.status !== statusF) return false;
      if (typeF && r.p1_type !== typeF && r.p2_type !== typeF) return false;
      if (regionF && (r.region || "") !== regionF) return false;
      if (search) {
        const hay = [r.site, r.name, r.offer, r.p1, r.p2, r.source, r.region].join(" ").toLowerCase();
        if (!hay.includes(search)) return false;
      }
      if (dateF && currentRunId) {
        /* фильтр по дате сессии — на уровне истории */
      }
      return true;
    });
  }

  function sortRows(rows) {
    if (!sortCol) return rows;
    return [...rows].sort((a, b) => {
      const av = (a[sortCol] || "").toString().toLowerCase();
      const bv = (b[sortCol] || "").toString().toLowerCase();
      if (av < bv) return -sortDir;
      if (av > bv) return sortDir;
      return 0;
    });
  }

  function groupByRegion(rows) {
    const groups = {};
    rows.forEach((r) => {
      const g = r.region || "Без региона";
      if (!groups[g]) groups[g] = [];
      groups[g].push(r);
    });
    return groups;
  }

  function renderTable(rows) {
    const groupMode = $("#group-regions")?.checked;
    let filtered = sortRows(filterRows(rows));

    const empty = $("#results-empty");
    const wrap = $("#table-wrap");
    const body = $("#results-body");

    if (!rows.length) {
      empty.hidden = false;
      wrap.hidden = true;
      $("#stats").hidden = true;
      $("#demo-banner").hidden = true;
      $("#live-banner").hidden = true;
      $("#pagination").hidden = true;
      $("#pager").hidden = true;
      return;
    }

    empty.hidden = true;
    wrap.hidden = false;
    $("#demo-banner").hidden = true;
    $("#live-banner").hidden = !isLiveRun;
    renderStats(rows);

    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    if (tablePage > totalPages) tablePage = totalPages;
    const pageRows = filtered.slice((tablePage - 1) * PAGE_SIZE, tablePage * PAGE_SIZE);

    const rowHtml = (r) => {
      const url = r.site.startsWith("http") ? r.site : `https://${r.site}`;
      return `<tr data-site="${r.site}">
        <td><a class="site-link" href="${url}" target="_blank" rel="noopener">${r.site}</a></td>
        <td>${r.name}</td>
        <td>${r.region || "—"}</td>
        <td>${r.offer}</td>
        <td>${renderPhoneCell(r.p1, r.p1_type, r.p1_valid)}</td>
        <td>${renderPhoneCell(r.p2, r.p2_type, r.p2_valid)}</td>
        <td>${r.source}</td>
        <td class="status-cell" data-site="${r.site}">${statusTag(r.status)}</td>
      </tr>`;
    };

    if (groupMode) {
      const groups = groupByRegion(pageRows);
      body.innerHTML = Object.entries(groups).map(([reg, items]) => `
        <tr class="group-row"><td colspan="8"><details open><summary>${reg} (${items.length})</summary>
        <table class="inner-table">${items.map(rowHtml).join("")}</table></details></td></tr>`).join("");
    } else {
      body.innerHTML = pageRows.map(rowHtml).join("");
    }

    const pag = $("#pager");
    const pagText = $("#pagination-text");
    if (pag && pagText) {
      pag.hidden = filtered.length <= PAGE_SIZE;
      pagText.textContent = `Страница ${tablePage} из ${totalPages} · строк ${filtered.length}`;
    }

    body.querySelectorAll("[data-copy]").forEach((btn) => {
      btn.addEventListener("click", () => {
        navigator.clipboard.writeText(btn.dataset.copy);
        toast("Скопировано");
      });
    });
    body.querySelectorAll(".status-cell").forEach((cell) => {
      cell.addEventListener("click", () => cycleStatus(cell.dataset.site));
    });
  }

  const STATUS_CYCLE = ["найден", "без телефона", "исключён", "агрегатор"];

  function cycleStatus(site) {
    const row = results.find((r) => r.site === site);
    if (!row) return;
    const i = STATUS_CYCLE.indexOf(row.status);
    row.status = STATUS_CYCLE[(i + 1) % STATUS_CYCLE.length];
    window.SSStorage.saveResults(results);
    renderTable(results);
  }

  function renderStats(rows) {
    const total = rows.length;
    const withPhone = rows.filter((r) => r.p1 || r.p2).length;
    const excluded = rows.filter((r) => r.status === "исключён" || r.status === "агрегатор").length;
    $("#stats").hidden = false;
    $("#stats").innerHTML = `
      <div class="stat"><div class="stat-value">${total}</div><div class="stat-label">Всего</div></div>
      <div class="stat"><div class="stat-value">${withPhone}</div><div class="stat-label">С телефоном</div></div>
      <div class="stat"><div class="stat-value">${total - withPhone - excluded}</div><div class="stat-label">Без номера</div></div>
      <div class="stat"><div class="stat-value">${excluded}</div><div class="stat-label">Исключено</div></div>`;
  }

  function isSearchConfigured(health, b) {
    if ((b.xmlRiverUser || "").trim() && (b.apiKey || "").trim()) return true;
    if (health?.xmlriver_configured) return true;
    if (health?.yandex_xml_fallback) return true;
    return false;
  }

  async function checkApi() {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (!res.ok) throw new Error("offline");
      const data = await res.json();
      apiOnline = true;
      const el = $("#api-status");
      const parts = ["Сервер: онлайн"];
      const b = readForm();
      const hasKeys = (b.xmlRiverUser || "").trim() && (b.apiKey || "").trim();
      if (data.xmlriver_configured || hasKeys) {
        const q = new URLSearchParams();
        if (b.xmlRiverUser) q.set("xmlRiverUser", b.xmlRiverUser);
        if (b.apiKey) q.set("apiKey", b.apiKey);
        try {
          const probe = await fetch(`${API_BASE}/api/xmlriver/check?${q}`);
          const probeData = await probe.json();
          if (probeData.ok) {
            parts.push(`XMLRiver ✓ (${probeData.hits} в тесте)`);
          } else {
            parts.push(`XMLRiver ✗ ${probeData.error || "ошибка"}`);
          }
        } catch (_) {
          parts.push(data.xmlriver_configured ? "XMLRiver (ключ в Render)" : "XMLRiver ?");
        }
      } else if (data.yandex_xml_fallback) {
        parts.push("Яндекс XML (резерв) ✓");
      } else {
        parts.push("нужен ключ XMLRiver");
      }
      if (data.scraping_configured) parts.push("Scraping ✓");
      el.textContent = parts.join(" · ");
      const bad = parts.some((p) => p.includes("✗") || p.includes("нужен"));
      el.style.color = bad ? "var(--warn)" : "var(--success)";
      return data;
    } catch (_) {
      apiOnline = false;
      const el = $("#api-status");
      el.textContent = "Сервер: офлайн · run.ps1";
      el.style.color = "var(--danger)";
      return null;
    }
  }

  function applyPipeline(pipeline, progress, meta = {}) {
    const state = {};
    PIPELINE_STEPS.forEach((s) => {
      state[s.id] = pipeline[s.id] || "pending";
      if (pipeline[s.id + "Log"]) state[s.id + "Log"] = pipeline[s.id + "Log"];
    });
    renderPipeline(state);

    tickElapsed();

    let label = meta.current_step_label || "";
    if (!label) {
      const running = PIPELINE_STEPS.find((s) => state[s.id] === "running");
      if (running) label = `Сейчас: ${running.title}…`;
      else {
        const done = PIPELINE_STEPS.filter((s) => state[s.id] === "done").length;
        if (done > 0 && done < PIPELINE_STEPS.length) label = `Выполнено шагов: ${done} из ${PIPELINE_STEPS.length}`;
      }
    }
    if (meta.status === "done") label = "";
    setProgressUI(progress, label, meta.status);
    renderLiveLogs(pipeline.logs, meta.waiting);
  }

  function notifyDone(count) {
    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") {
      new Notification("Сигнал-Скаут", { body: `Сбор завершён! Найдено ${count} телефонов` });
    }
  }

  async function pollRun(runId) {
    const res = await fetch(`${API_BASE}/api/run/${runId}`);
    if (!res.ok) throw new Error("Статус недоступен");
    const data = await res.json();
    const stepLabel = data.current_step ? stepTitle(data.current_step) : "";
    applyPipeline(data.pipeline || {}, data.progress || 0, {
      status: data.status,
      current_step_label: stepLabel ? `Сейчас: ${stepLabel}…` : "",
    });

    if (data.status === "done") {
      clearInterval(pollTimer);
      stopRunTimer();
      sessionStorage.removeItem(RESUME_KEY);
      setProgressUI(100, "", "done");
      const rr = await fetch(`${API_BASE}/api/results/${runId}`);
      const payload = await rr.json();
      results = payload.results || [];
      isLiveRun = true;
      currentRunId = runId;
      window.SSStorage.saveResults(results);
      enableResultsUI();
      $("#results-subtitle").textContent = `${brief.clientName} · живой сбор · ${results.length} строк`;
      const phones = results.filter((r) => r.p1).length;
      notifyDone(phones);
      toast(`Сбор завершён за ${formatElapsed(Math.floor((Date.now() - runStartedAt) / 1000))}`);
      setTimeout(() => showPage("results"), 400);
      setRunButtons(false);
      return;
    }
    if (data.status === "error") {
      clearInterval(pollTimer);
      stopRunTimer();
      applyPipeline(data.pipeline || {}, data.progress || 0, { status: "error" });
      toast(data.error || "Ошибка");
      setRunButtons(false);
      return;
    }
    if (data.status === "stopped") {
      clearInterval(pollTimer);
      stopRunTimer();
      applyPipeline(data.pipeline || {}, data.progress || 0, { status: "stopped" });
      if (data.can_resume && runId) {
        sessionStorage.setItem(RESUME_KEY, runId);
        currentRunId = runId;
        try {
          const rr = await fetch(`${API_BASE}/api/results/${runId}`);
          const payload = await rr.json();
          if ((payload.results || []).length) {
            results = payload.results;
            window.SSStorage.saveResults(results);
          }
        } catch (_) {}
        toast(`Остановлено · обойдено ${data.results_count || 0} сайтов · «Запустить сбор» продолжит`);
      } else {
        toast("Сбор остановлен");
      }
      setRunButtons(false);
      return;
    }
    if (data.status === "running" || data.status === "pending") {
      setRunButtons(true);
    }
  }

  async function parseApiResponse(res) {
    const text = await res.text();
    if (!text) return { ok: res.ok, data: {} };
    try {
      return { ok: res.ok, data: JSON.parse(text) };
    } catch (_) {
      const snippet = text.replace(/\s+/g, " ").slice(0, 120);
      throw new Error(
        res.ok
          ? "Сервер вернул неверный ответ"
          : `Ошибка сервера (${res.status}): ${snippet || "нет данных"}`
      );
    }
  }

  function setRunButtons(running) {
    const hasBrief = Boolean(brief?.clientSite);
    const startBtn = $("#btn-start");
    if (startBtn) {
      startBtn.disabled = running || !hasBrief;
      startBtn.setAttribute("aria-busy", running ? "true" : "false");
    }
    const stopBtn = $("#btn-stop");
    if (stopBtn) stopBtn.disabled = !running;
  }

  function resetRunUi(message) {
    clearInterval(pollTimer);
    pollTimer = null;
    stopRunTimer();
    currentRunId = null;
    setRunButtons(false);
    if (message) {
      setProgressUI(0, message, "error");
      renderLiveLogs([{ ts: "--:--:--", msg: message, status: "error" }], false);
    }
  }

  function enableResultsUI() {
    $("#table-search").disabled = false;
    $("#status-filter").disabled = false;
    $("#type-filter").disabled = false;
    $("#region-filter").disabled = false;
    $("#btn-export").disabled = false;
    $("#btn-export-xls").disabled = false;
    renderTable(results);
  }

  async function startPolling(runId) {
    currentRunId = runId;
    isLiveRun = true;
    pollTimer = setInterval(() => pollRun(runId).catch((e) => {
      clearInterval(pollTimer);
      stopRunTimer();
      toast(e.message);
      setRunButtons(false);
    }), 1500);
    await pollRun(runId);
  }

  async function runPipeline() {
    brief = readForm();
    window.SSStorage.saveBrief(brief);

    const resumeId = sessionStorage.getItem(RESUME_KEY) || currentRunId;
    if (resumeId) {
      try {
        const st = await fetch(`${API_BASE}/api/run/${resumeId}`);
        if (st.ok) {
          const info = await st.json();
          if (info.can_resume && (info.status === "stopped" || info.status === "error")) {
            return resumePipeline(resumeId);
          }
        }
      } catch (_) {}
      sessionStorage.removeItem(RESUME_KEY);
    }

    setRunButtons(true);
    startRunTimer();
    setProgressUI(1, "Проверка сервера…", "running");
    renderLiveLogs(null, true);

    const health = await checkApi();
    if (!apiOnline) {
      resetRunUi("Сервер офлайн");
      return toast("Запустите run.ps1");
    }
    if (!isSearchConfigured(health, brief)) {
      resetRunUi("Нужен ключ XMLRiver");
      return toast("Укажите ID и ключ XMLRiver в брифе или в .env");
    }

    if (Notification.permission === "default") Notification.requestPermission();

    setProgressUI(2, "Отправка брифа на сервер…", "running");
    applyPipeline({}, 2, { waiting: true, current_step_label: "Запуск…" });

    const waitUi = setInterval(() => {
      if (!runActive) {
        clearInterval(waitUi);
        return;
      }
      const sec = tickElapsed();
      if (!currentRunId) {
        setProgressUI(2, `Ожидание сервера… ${sec} сек`, "running");
      }
    }, 500);

    try {
      const res = await fetch(`${API_BASE}/api/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(brief),
      });
      clearInterval(waitUi);
      const { ok, data: payload } = await parseApiResponse(res);
      if (!ok) {
        const err = payload.detail;
        throw new Error(typeof err === "string" ? err : JSON.stringify(err) || "Ошибка запуска");
      }
      currentRunId = payload.run_id;
      sessionStorage.removeItem(RESUME_KEY);
      await startPolling(currentRunId);
    } catch (e) {
      clearInterval(waitUi);
      resetRunUi(e.message || "Не удалось запустить сбор");
      toast(e.message || "Ошибка запуска");
    }
  }

  async function resumePipeline(runId) {
    brief = readForm();
    window.SSStorage.saveBrief(brief);
    setRunButtons(true);
    startRunTimer();
    setProgressUI(5, "Продолжение сбора…", "running");
    renderLiveLogs(null, true);
    try {
      const res = await fetch(`${API_BASE}/api/run/${runId}/resume`, { method: "POST" });
      const { ok, data: payload } = await parseApiResponse(res);
      if (!ok) {
        const err = payload.detail;
        throw new Error(typeof err === "string" ? err : JSON.stringify(err) || "Ошибка продолжения");
      }
      toast("Продолжаем с места остановки…");
      await startPolling(runId);
    } catch (e) {
      resetRunUi(e.message || "Не удалось продолжить сбор");
      toast(e.message || "Ошибка продолжения");
    }
  }

  async function loadHistory() {
    const box = $("#history-list");
    if (!box) return;
    if (!apiOnline) {
      box.innerHTML = "<p class='hint'>Сервер офлайн</p>";
      return;
    }
    const res = await fetch(`${API_BASE}/api/history`);
    const data = await res.json();
    box.innerHTML = (data.items || []).map((it) => `
      <div class="history-item">
        <div><strong>${it.client_name || "—"}</strong> · ${new Date(it.created_at).toLocaleString("ru-RU")}</div>
        <div class="hint">${it.sites_count} сайтов · ${it.phones_count} тел. · ${it.status}</div>
        <div class="actions" style="margin-top:8px">
          <button type="button" class="btn btn-ghost btn-sm" data-load-run="${it.id}">Открыть</button>
        </div>
      </div>`).join("") || "<p class='hint'>Пока нет прогонов</p>";

    box.querySelectorAll("[data-load-run]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.loadRun;
        const r = await fetch(`${API_BASE}/api/results/${id}`);
        const p = await r.json();
        results = p.results || [];
        currentRunId = id;
        isLiveRun = !p.is_demo;
        window.SSStorage.saveResults(results);
        enableResultsUI();
        showPage("results");
      });
    });
  }

  async function compareSessions() {
    const a = $("#compare-a").value;
    const b = $("#compare-b").value;
    if (!a || !b) return toast("Выберите две сессии");
    const res = await fetch(`${API_BASE}/api/history/compare?a=${a}&b=${b}`);
    const data = await res.json();
    $("#compare-result").innerHTML = `
      <p><strong>Новые:</strong> ${data.new_sites.length ? data.new_sites.join(", ") : "—"}</p>
      <p><strong>Исчезли:</strong> ${data.removed_sites.length ? data.removed_sites.join(", ") : "—"}</p>
      <p><strong>Общие:</strong> ${data.common.length}</p>`;
  }

  async function previewNiche() {
    const url = $("#client-site").value.trim();
    if (!url || !apiOnline) return;
    try {
      const res = await fetch(`${API_BASE}/api/preview?url=${encodeURIComponent(url)}`);
      const data = await res.json();
      if (data.niche_hint && !$("#niche").value) $("#niche").value = data.niche_hint;
      $("#niche-hint").textContent = data.title ? `Подсказка: ${data.title}` : "";
    } catch (_) {}
  }

  function exportCsv() {
    if (currentRunId && apiOnline) window.open(`${API_BASE}/api/export/${currentRunId}.csv`, "_blank");
    else toast("Нет активного прогона");
  }

  function exportXls() {
    if (currentRunId && apiOnline) window.open(`${API_BASE}/api/export/${currentRunId}.xls`, "_blank");
    else toast("Нет активного прогона");
  }

  function showPage(id) {
    $$(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === id));
    $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${id}`));
    if (id === "history") loadHistory();
  }

  function updateRunUI() {
    setRunButtons(runActive);
    const hasBrief = Boolean(brief?.clientSite);
    $("#run-subtitle").textContent = hasBrief
      ? `Клиент: ${brief.clientName} · ${brief.clientSite}`
      : "Сначала сохраните бриф.";
  }

  function init() {
    brief = window.SSStorage.loadBrief(PILOT);
    results = window.SSStorage.loadResults();
    fillForm(brief);
    renderPipeline({});
    setProgressUI(0, "Нажмите «Запустить сбор»");
    renderLiveLogs(null, false);
    updateRunUI();
    if (results.length) {
      enableResultsUI();
      $("#results-subtitle").textContent = `${brief.clientName} · ${results.length} строк`;
    }

    $$(".nav button").forEach((btn) => btn.addEventListener("click", () => showPage(btn.dataset.page)));

    $("#brief-form").addEventListener("submit", (e) => {
      e.preventDefault();
      brief = readForm();
      window.SSStorage.saveBrief(brief);
      fillRegionFilter(brief.regions);
      updateRunUI();
      toast("Бриф сохранён");
    });

    $("#btn-reset-pilot").addEventListener("click", () => {
      fillForm(PILOT);
      brief = readForm();
      window.SSStorage.saveBrief(brief);
      updateRunUI();
      toast("Пилот загружен");
    });

    $("#client-site").addEventListener("blur", previewNiche);
    $("#btn-start").addEventListener("click", runPipeline);
    $("#btn-stop").addEventListener("click", async () => {
      if (currentRunId) {
        try {
          await fetch(`${API_BASE}/api/run/${currentRunId}/stop`, { method: "POST" });
        } catch (_) {}
      }
      resetRunUi("Остановлено");
      toast("Остановлено");
    });

    $("#table-search").addEventListener("input", () => { tablePage = 1; renderTable(results); });
    $("#status-filter").addEventListener("change", () => renderTable(results));
    $("#type-filter")?.addEventListener("change", () => renderTable(results));
    $("#region-filter")?.addEventListener("change", () => renderTable(results));
    $("#group-regions")?.addEventListener("change", () => renderTable(results));
    $("#btn-export").addEventListener("click", exportCsv);
    $("#btn-export-xls").addEventListener("click", exportXls);
    $("#btn-compare")?.addEventListener("click", compareSessions);
    $("#btn-prev-page")?.addEventListener("click", () => { if (tablePage > 1) { tablePage--; renderTable(results); } });
    $("#btn-next-page")?.addEventListener("click", () => { tablePage++; renderTable(results); });

    $$("th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const col = th.dataset.sort;
        if (sortCol === col) sortDir *= -1;
        else { sortCol = col; sortDir = 1; }
        renderTable(results);
      });
    });

    checkApi();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
