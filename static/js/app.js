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
  let startingRun = false;
  const PAGE_SIZE = 50;

  function normalizeClientSite(raw) {
    let s = (raw || "").trim();
    if (!s) return "";
    const urlMatch = s.match(/https?:\/\/[^\s<>"'·|]+/i);
    if (urlMatch) return urlMatch[0].replace(/[.,;)]+$/, "");
    const domainMatch = s.match(/([\w.-]+\.(?:ru|com|рф|org|net|biz))/i);
    if (domainMatch && !s.includes(" ")) {
      const host = domainMatch[1];
      return /^https?:\/\//i.test(s) ? s : `https://${host}`;
    }
    return s;
  }

  function isValidClientSite(raw) {
    const normalized = normalizeClientSite(raw);
    if (!normalized) return false;
    try {
      const u = new URL(normalized.startsWith("http") ? normalized : `https://${normalized}`);
      return Boolean(u.hostname && u.hostname.includes("."));
    } catch (_) {
      return false;
    }
  }

  let activeRegions = [];

  function parseRegionsStr(regionsStr) {
    return [...new Set(
      (regionsStr || "").split(",").map((r) => r.trim()).filter(Boolean),
    )];
  }

  function collectRegions() {
    return activeRegions.join(", ");
  }

  function renderRegionChips() {
    const box = $("#region-chips");
    const empty = $("#region-chips-empty");
    if (!box) return;
    if (!activeRegions.length) {
      box.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    box.innerHTML = activeRegions.map((r) => {
      const safe = r.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
      return `<button type="button" class="region-chip" data-region="${safe}" title="Убрать: ${safe}">
        <span>${safe}</span><span class="region-chip-x" aria-hidden="true">×</span>
      </button>`;
    }).join("");
  }

  function syncRegionFilter() {
    fillRegionFilter(collectRegions());
  }

  function addRegions(names) {
    let added = 0;
    for (const name of names) {
      const r = (name || "").trim();
      if (!r) continue;
      if (activeRegions.some((x) => x.toLowerCase() === r.toLowerCase())) continue;
      activeRegions.push(r);
      added += 1;
    }
    if (added) {
      renderRegionChips();
      syncRegionFilter();
    }
    return added;
  }

  function addRegion(name) {
    const added = addRegions([name]);
    if (!added) toast("Регион уже в списке");
    return added > 0;
  }

  function removeRegion(name) {
    activeRegions = activeRegions.filter((r) => r !== name);
    renderRegionChips();
    syncRegionFilter();
  }

  function clearRegions() {
    activeRegions = [];
    renderRegionChips();
    syncRegionFilter();
  }

  function fillRegionPresets(regionsStr) {
    activeRegions = parseRegionsStr(regionsStr);
    renderRegionChips();
  }

  function initRegionPresetSelect() {
    const sel = $("#region-preset-add");
    const list = window.SS_REGIONS_RU || [];
    if (!sel || !list.length) return;
    sel.innerHTML = list.map((name) => {
      const safe = name.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
      return `<option value="${safe}">${safe}</option>`;
    }).join("");
  }

  let pendingResumeRunId = null;

  function normalizeClientSite(url) {
    const raw = (url || "").trim();
    if (!raw) return "";
    try {
      const u = new URL(raw.includes("://") ? raw : `https://${raw}`);
      return u.hostname.replace(/^www\./i, "").toLowerCase();
    } catch (_) {
      return raw.replace(/^https?:\/\//i, "").replace(/^www\./i, "").split("/")[0].toLowerCase();
    }
  }

  function clearStaleRun(message) {
    saveResumeRunId("");
    sessionStorage.removeItem(window.SSStorage.RESUME_KEY);
    clearInterval(pollTimer);
    pollTimer = null;
    currentRunId = null;
    isLiveRun = false;
    stopRunTimer();
    setRunButtons(false);
    setProgressUI(0, message || "Запустите сбор заново", "stopped");
    renderLiveLogs([{
      ts: "--:--:--",
      msg: message || "Старый прогон не найден на сервере (после обновления Render база пустая). Нажмите «Быстрая проверка».",
      status: "error",
    }], false);
    refreshStartButtonLabel();
  }

  async function purgeStaleRuns() {
    const ids = [
      window.SSStorage.loadResumeRunId(),
      sessionStorage.getItem(window.SSStorage.RESUME_KEY),
      currentRunId,
    ].filter((v, i, a) => v && a.indexOf(v) === i);
    for (const id of ids) {
      try {
        const res = await fetch(`${API_BASE}/api/run/${id}`);
        if (res.status === 404) clearStaleRun();
      } catch (_) {}
    }
  }

  function rememberDeployVersion(version) {
    if (!version) return;
    const prev = localStorage.getItem(DEPLOY_VERSION_KEY);
    if (prev && prev !== version) {
      saveResumeRunId("");
      sessionStorage.removeItem(window.SSStorage.RESUME_KEY);
      currentRunId = null;
    }
    localStorage.setItem(DEPLOY_VERSION_KEY, version);
  }

  function saveResumeRunId(id) {
    window.SSStorage.saveResumeRunId(id || "");
    if (id) sessionStorage.setItem(window.SSStorage.RESUME_KEY, id);
    else sessionStorage.removeItem(window.SSStorage.RESUME_KEY);
  }

  function isResumableRunInfo(info) {
    if (!info) return false;
    if (info.can_resume) return true;
    if (info.status === "running") {
      return (info.results_count || 0) > 0 || (info.progress || 0) > 0;
    }
    if (info.status !== "stopped" && info.status !== "error") return false;
    const p = info.pipeline || {};
    if (p.analyze === "done") return true;
    return PIPELINE_STEPS.some((s) => p[s.id] === "done");
  }

  async function findResumableRunId() {
    const siteKey = normalizeClientSite($("#client-site")?.value || brief?.clientSite || "");
    const ids = [
      window.SSStorage.loadResumeRunId(),
      sessionStorage.getItem(window.SSStorage.RESUME_KEY),
      currentRunId,
    ].filter((v, i, a) => v && a.indexOf(v) === i);

    for (const id of ids) {
      try {
        const st = await fetch(`${API_BASE}/api/run/${id}`);
        if (!st.ok) continue;
        const info = await st.json();
        if (isResumableRunInfo(info)) return id;
      } catch (_) {}
    }

    if (!apiOnline) return null;
    try {
      const res = await fetch(`${API_BASE}/api/history?limit=15`);
      const data = await res.json();
      for (const it of data.items || []) {
        if (siteKey && it.client_site && normalizeClientSite(it.client_site) !== siteKey) continue;
        if (!["stopped", "error", "running"].includes(it.status)) continue;
        const st = await fetch(`${API_BASE}/api/run/${it.id}`);
        if (!st.ok) continue;
        const info = await st.json();
        if (isResumableRunInfo(info)) return it.id;
      }
    } catch (_) {}
    return null;
  }

  async function refreshStartButtonLabel() {
    const startBtn = $("#btn-start");
    const resumeBtn = $("#btn-resume");
    if (runActive) return;
    const resumeId = await findResumableRunId();
    pendingResumeRunId = resumeId || null;
    if (resumeBtn) {
      resumeBtn.hidden = !resumeId;
      resumeBtn.disabled = !resumeId || startingRun;
    }
    if (startBtn && !runActive) {
      startBtn.textContent = "Запустить сбор";
    }
  }

  function isLocalDev() {
    const h = window.location.hostname;
    return h === "127.0.0.1" || h === "localhost" || window.location.protocol === "file:";
  }

  function serverOfflineHint() {
    return isLocalDev()
      ? "Сервер: офлайн · запустите run.ps1"
      : "Сервер: офлайн · подождите 1–2 мин или обновите деплой на Render";
  }

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

  function renderPhoneCell(phone) {
    if (!phone) return '<span class="phone phone-empty">—</span>';
    const shown = formatPhone(phone);
    return `<span class="phone-wrap">
      <span class="phone">${shown}</span>
      <button type="button" class="btn-mini" data-copy="${shown}" title="Копировать">⧉</button>
    </span>`;
  }

  function rowPhones(r) {
    if (Array.isArray(r.phones) && r.phones.length) return r.phones.filter(Boolean);
    return [r.p1, r.p2].filter(Boolean);
  }

  function renderContactsCell(r) {
    const nums = rowPhones(r);
    if (!nums.length) return "—";
    return nums.map((p) => renderPhoneCell(p)).join("<span class='contacts-sep'>, </span>");
  }

  function normalizeQueriesField(value) {
    if (Array.isArray(value)) return value.filter(Boolean).join("\n");
    return String(value || "").trim();
  }

  function ensureBriefQueries(data) {
    const queries = normalizeQueriesField(data?.queries);
    if (queries) return { ...data, queries };
    return { ...data, queries: PILOT.queries };
  }

  function readForm() {
    const sources = [...$("#sources").selectedOptions].map((o) => o.value);
    const clientSite = normalizeClientSite($("#client-site").value);
    return ensureBriefQueries({
      clientName: $("#client-name").value.trim(),
      clientSite,
      niche: $("#niche").value.trim(),
      regionMode: document.querySelector('input[name="regionMode"]:checked')?.value || "include",
      regions: collectRegions(),
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
    });
  }

  function fillForm(data) {
    $("#client-name").value = data.clientName || "";
    $("#client-site").value = data.clientSite || "";
    $("#niche").value = data.niche || "";
    $$('input[name="regionMode"]').forEach((r) => {
      r.checked = r.value === (data.regionMode || "include");
    });
    fillRegionPresets(data.regions || "");
    $("#queries").value = normalizeQueriesField(data.queries) || PILOT.queries;
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
        <td class="contacts-cell">${renderContactsCell(r)}</td>
        <td>${r.source}</td>
        <td class="status-cell" data-site="${r.site}">${statusTag(r.status)}</td>
      </tr>`;
    };

    if (groupMode) {
      const groups = groupByRegion(pageRows);
      body.innerHTML = Object.entries(groups).map(([reg, items]) => `
        <tr class="group-row"><td colspan="7"><details open><summary>${reg} (${items.length})</summary>
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
      const EXPECTED_VERSION = "2026-07-05-resume-button";
      rememberDeployVersion(data.version);
      if (data.version) parts.push(`вер. ${data.version}`);
      el.textContent = parts.join(" · ");
      const bad = parts.some((p) => p.includes("✗") || p.includes("нужен"));
      const oldBuild = !data.version || data.version !== EXPECTED_VERSION;
      el.style.color = bad || oldBuild ? "var(--warn)" : "var(--success)";
      if (oldBuild) {
        toast("Сервер устарел — в Render: Manual Deploy → latest commit (main)");
      }
      return data;
    } catch (_) {
      apiOnline = false;
      const el = $("#api-status");
      el.textContent = serverOfflineHint();
      el.style.color = "var(--danger)";
      return null;
    }
  }

  function pipelineStepLabel(state) {
    const done = PIPELINE_STEPS.filter((s) => state[s.id] === "done").length;
    const running = PIPELINE_STEPS.find((s) => state[s.id] === "running");
    if (running) return `Шаг ${done + 1}/${PIPELINE_STEPS.length} · ${running.title}…`;
    if (done > 0 && done < PIPELINE_STEPS.length) {
      return `Шаг ${done}/${PIPELINE_STEPS.length} · подготовка следующего этапа…`;
    }
    return "";
  }

  function applyPipeline(pipeline, progress, meta = {}) {
    const state = {};
    PIPELINE_STEPS.forEach((s) => {
      state[s.id] = pipeline[s.id] || "pending";
      if (pipeline[s.id + "Log"]) state[s.id + "Log"] = pipeline[s.id + "Log"];
    });
    renderPipeline(state);

    tickElapsed();

    let label = meta.current_step_label || pipelineStepLabel(state) || "";
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
    if (res.status === 404) {
      clearInterval(pollTimer);
      pollTimer = null;
      clearStaleRun("Прогон не найден — после обновления сервера запустите сбор заново");
      toast("Нажмите «Быстрая проверка (12 сайтов)»");
      return;
    }
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
      saveResumeRunId("");
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
      saveResumeRunId(runId);
      currentRunId = runId;
      const logs = [...(data.pipeline?.logs || [])];
      if (data.error && !logs.some((l) => l.msg === data.error)) {
        logs.push({ ts: "--:--:--", msg: data.error, status: "error" });
      }
      applyPipeline({ ...(data.pipeline || {}), logs }, data.progress || 0, { status: "error" });
      toast(data.error || "Ошибка");
      setRunButtons(false);
      refreshStartButtonLabel();
      return;
    }
    if (data.status === "stopped") {
      clearInterval(pollTimer);
      stopRunTimer();
      applyPipeline(data.pipeline || {}, data.progress || 0, { status: "stopped" });
      if (isResumableRunInfo(data) && runId) {
        saveResumeRunId(runId);
        currentRunId = runId;
        try {
          const rr = await fetch(`${API_BASE}/api/results/${runId}`);
          const payload = await rr.json();
          if ((payload.results || []).length) {
            results = payload.results;
            window.SSStorage.saveResults(results);
          }
        } catch (_) {}
        toast(`Остановлено · обойдено ${data.results_count || 0} сайтов · «Продолжить сбор» продолжит`);
      } else {
        toast("Сбор остановлен");
      }
      setRunButtons(false);
      refreshStartButtonLabel();
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
    const hasBrief = Boolean(brief?.clientSite) && isValidClientSite(brief?.clientSite);
    const startBtn = $("#btn-start");
    const resumeBtn = $("#btn-resume");
    const quickBtn = $("#btn-quick");
    if (startBtn) {
      startBtn.disabled = running || !hasBrief || startingRun;
      startBtn.setAttribute("aria-busy", running ? "true" : "false");
      startBtn.classList.toggle("is-loading", running);
      if (running) startBtn.textContent = "Сбор идёт…";
    }
    if (resumeBtn) {
      resumeBtn.disabled = running || !pendingResumeRunId || startingRun;
      resumeBtn.hidden = running || !pendingResumeRunId;
    }
    if (quickBtn) quickBtn.disabled = running || !hasBrief || startingRun;
    const stopBtn = $("#btn-stop");
    if (stopBtn) stopBtn.disabled = !running;
    if (!running && startBtn) refreshStartButtonLabel();
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
    saveResumeRunId(runId);
    pollTimer = setInterval(() => pollRun(runId).catch(() => {
      clearInterval(pollTimer);
      pollTimer = null;
      stopRunTimer();
      saveResumeRunId(runId);
      currentRunId = runId;
      toast("Связь с сервером прервана · нажмите «Продолжить сбор»");
      setRunButtons(false);
      refreshStartButtonLabel();
    }), 1500);
    await pollRun(runId);
  }

  async function showRunState(runId, statusHint) {
    const res = await fetch(`${API_BASE}/api/run/${runId}`);
    if (!res.ok) return null;
    const data = await res.json();
    const stepLabel = data.current_step ? stepTitle(data.current_step) : "";
    applyPipeline(data.pipeline || {}, data.progress || 0, {
      status: statusHint || data.status,
      current_step_label: stepLabel ? `Сейчас: ${stepLabel}…` : "",
    });
    return data;
  }

  async function runQuickPipeline() {
    if (startingRun || runActive) return;
    saveResumeRunId("");
    brief = readForm();
    if (!isValidClientSite(brief.clientSite)) {
      toast("Сначала укажите корректный URL сайта в брифе");
      return;
    }
    window.SSStorage.saveBrief(brief);
    startingRun = true;
    saveResumeRunId("");
    setRunButtons(true);
    startRunTimer();
    setProgressUI(5, "Быстрый обход 12 конкурентов…", "running");
    renderLiveLogs(null, true);
    try {
      const health = await checkApi();
      if (!apiOnline) throw new Error("Сервер офлайн");
      const res = await fetch(`${API_BASE}/api/run/quick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(brief),
      });
      const { ok, data: payload } = await parseApiResponse(res);
      if (!ok) {
        const err = payload.detail;
        throw new Error(typeof err === "string" ? err : JSON.stringify(err) || "Ошибка запуска");
      }
      currentRunId = payload.run_id;
      toast("Быстрый обход запущен — ~3–5 минут");
      await startPolling(currentRunId);
    } catch (e) {
      resetRunUi(e.message || "Не удалось запустить быстрый обход");
      toast(e.message || "Ошибка");
    } finally {
      startingRun = false;
      if (!runActive) setRunButtons(false);
    }
  }

  async function runPipeline() {
    if (startingRun || runActive) return;
    brief = readForm();
    if (!isValidClientSite(brief.clientSite)) {
      toast("Укажите корректный URL сайта, например https://opalubka-domstroy.ru");
      return;
    }
    window.SSStorage.saveBrief(brief);
    $("#client-site").value = brief.clientSite;

    const resumeId = await findResumableRunId();
    if (resumeId) {
      return resumePipeline(resumeId);
    }

    startingRun = true;
    saveResumeRunId("");
    setRunButtons(true);
    startRunTimer();
    setProgressUI(1, "Проверка сервера…", "running");
    renderLiveLogs(null, true);

    const health = await checkApi();
    if (!apiOnline) {
      resetRunUi("Сервер офлайн");
      startingRun = false;
      setRunButtons(false);
      return toast(isLocalDev() ? "Запустите run.ps1" : "Сервер на Render не отвечает — подождите или обновите страницу (Ctrl+F5)");
    }
    if (!isSearchConfigured(health, brief)) {
      resetRunUi("Нужен ключ XMLRiver");
      startingRun = false;
      setRunButtons(false);
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
      if (payload.resumed || payload.reconnected) {
        toast(payload.reconnected ? "Подключаемся к сбору…" : "Продолжаем с места остановки…");
        await showRunState(currentRunId, "running");
      } else {
        saveResumeRunId("");
      }
      await startPolling(currentRunId);
    } catch (e) {
      clearInterval(waitUi);
      resetRunUi(e.message || "Не удалось запустить сбор");
      toast(e.message || "Ошибка запуска");
    } finally {
      startingRun = false;
      if (!runActive) setRunButtons(false);
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
      const state = await showRunState(runId, "running");
      if (state?.status === "running") {
        toast("Подключаемся к идущему сбору…");
        currentRunId = runId;
        saveResumeRunId(runId);
        await startPolling(runId);
        return;
      }
      const res = await fetch(`${API_BASE}/api/run/${runId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(brief),
      });
      const { ok, data: payload } = await parseApiResponse(res);
      if (!ok) {
        const err = payload.detail;
        throw new Error(typeof err === "string" ? err : JSON.stringify(err) || "Ошибка продолжения");
      }
      toast("Продолжаем с места остановки…");
      currentRunId = runId;
      saveResumeRunId(runId);
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
    const items = data.items || [];
    if (!items.length) {
      box.innerHTML = "<p class='hint'>Пока нет прогонов</p>";
      return;
    }
    box.innerHTML = `
      <table class="history-table">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Клиент</th>
            <th>Сайтов</th>
            <th>Телефонов</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${items.map((it) => `
            <tr>
              <td>${new Date(it.created_at).toLocaleString("ru-RU")}</td>
              <td>${it.client_name || "—"}</td>
              <td>${it.sites_count}</td>
              <td>${it.phones_count}</td>
              <td>${it.status}</td>
              <td><button type="button" class="btn btn-ghost btn-sm" data-load-run="${it.id}">Открыть</button></td>
            </tr>`).join("")}
        </tbody>
      </table>`;

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
        $("#results-subtitle").textContent = `${p.brief?.clientName || brief.clientName} · ${results.length} строк`;
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
    const hasBrief = Boolean(brief?.clientSite) && isValidClientSite(brief?.clientSite);
    $("#run-subtitle").textContent = hasBrief
      ? `Клиент: ${brief.clientName} · ${brief.clientSite}`
      : "Сначала сохраните бриф с корректным URL сайта.";
    refreshStartButtonLabel();
  }

  function init() {
    initRegionPresetSelect();
    brief = ensureBriefQueries(window.SSStorage.loadBrief(PILOT));
    results = window.SSStorage.loadResults();
    fillForm(brief);
    brief = readForm();
    window.SSStorage.saveBrief(brief);
    renderPipeline({});
    setProgressUI(0, "Нажмите «Запустить сбор»");
    renderLiveLogs(null, false);
    updateRunUI();
    checkApi().then(() => {
      purgeStaleRuns();
      refreshStartButtonLabel();
    });
    if (results.length) {
      enableResultsUI();
      $("#results-subtitle").textContent = `${brief.clientName} · ${results.length} строк`;
    }

    $$(".nav button").forEach((btn) => btn.addEventListener("click", () => showPage(btn.dataset.page)));

    $("#brief-form").addEventListener("submit", (e) => {
      e.preventDefault();
      brief = readForm();
      if (!isValidClientSite(brief.clientSite)) {
        toast("В поле «Сайт клиента» нужен адрес вида https://example.ru");
        return;
      }
      $("#client-site").value = brief.clientSite;
      window.SSStorage.saveBrief(brief);
      fillRegionFilter(brief.regions);
      updateRunUI();
      toast("Бриф сохранён");
    });

    $("#region-chips")?.addEventListener("click", (e) => {
      const chip = e.target.closest(".region-chip");
      if (!chip?.dataset.region) return;
      removeRegion(chip.dataset.region);
    });

    $("#btn-region-add-preset")?.addEventListener("click", () => {
      const sel = $("#region-preset-add");
      const picked = [...(sel?.selectedOptions || [])].map((o) => o.value).filter(Boolean);
      if (!picked.length) {
        toast("Выберите регионы в списке (Ctrl+клик для нескольких)");
        return;
      }
      const added = addRegions(picked);
      if (sel) $$("#region-preset-add option").forEach((o) => { o.selected = false; });
      if (added) {
        toast(added === 1 ? "Добавлен 1 регион" : `Добавлено регионов: ${added}`);
      } else {
        toast("Все выбранные регионы уже в списке");
      }
    });

    $("#btn-region-clear")?.addEventListener("click", () => {
      clearRegions();
      toast("Регионы очищены");
    });

    $("#btn-reset-pilot").addEventListener("click", () => {
      fillForm(PILOT);
      brief = readForm();
      window.SSStorage.saveBrief(brief);
      updateRunUI();
      toast("Пилот загружен");
    });

    $("#client-site").addEventListener("blur", () => {
      previewNiche();
      refreshStartButtonLabel();
    });
    $("#btn-start").addEventListener("click", runPipeline);
    $("#btn-resume")?.addEventListener("click", () => {
      if (pendingResumeRunId) resumePipeline(pendingResumeRunId);
    });
    $("#btn-quick")?.addEventListener("click", runQuickPipeline);
    $("#btn-stop").addEventListener("click", async () => {
      const runId = currentRunId;
      if (!runId) return;
      saveResumeRunId(runId);
      clearInterval(pollTimer);
      pollTimer = null;
      stopRunTimer();
      setRunButtons(false);
      try {
        await fetch(`${API_BASE}/api/run/${runId}/stop`, { method: "POST" });
        await pollRun(runId);
      } catch (_) {
        setProgressUI(0, "Остановлено", "stopped");
      }
      currentRunId = runId;
      refreshStartButtonLabel();
      toast("Остановлено · нажмите «Продолжить сбор»");
    });

    $("#table-search").addEventListener("input", () => { tablePage = 1; renderTable(results); });
    $("#status-filter").addEventListener("change", () => renderTable(results));
    $("#type-filter")?.addEventListener("change", () => renderTable(results));
    $("#region-filter")?.addEventListener("change", () => renderTable(results));
    $("#group-regions")?.addEventListener("change", () => renderTable(results));
    $("#btn-export").addEventListener("click", exportCsv);
    $("#btn-export-xls").addEventListener("click", exportXls);
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
