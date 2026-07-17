/* Сигнал-Скаут — UI (fetch → Python API) */
(function () {
  const API_BASE = window.location.protocol === "file:"
    ? "http://127.0.0.1:8765"
    : window.location.origin;

  const PILOT = {
    operatorName: "",
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

  const EMPTY_BRIEF = {
    operatorName: "",
    clientName: "",
    clientSite: "",
    niche: "",
    regionMode: "include",
    regions: "",
    queries: "",
    excludeDomains: "",
    phoneFilter: "business",
    sources: ["serp", "site", "catalog"],
    checkAlive: true,
    maxSites: 50,
    crawlDepth: 2,
    requestDelayMs: 500,
    useProxy: false,
    xmlRiverUser: "",
    apiKey: "",
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
  const DEPLOY_VERSION_KEY = "signal-scout-deploy-version";
  const EXPECTED_BUILD_VERSION = "2026-07-17-zenrows";
  const ADMIN_TOKEN_KEY = "signal-scout-admin-token-v1";
  let adminConfigured = false;
  let isAdminSession = false;

  function getAdminToken() {
    try {
      return sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function setAdminToken(token) {
    try {
      if (token) sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
      else sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    } catch (_) {}
    isAdminSession = Boolean(token);
    updateHistoryAdminUi();
  }

  function currentOperatorName() {
    return ($("#operator-name")?.value || brief?.operatorName || "").trim();
  }

  function apiHeaders(extra = {}) {
    const headers = { ...extra };
    const token = getAdminToken();
    if (token) headers["X-Admin-Token"] = token;
    return headers;
  }

  function historyQueryParams(limit = 200) {
    const params = new URLSearchParams({ limit: String(limit) });
    const op = currentOperatorName();
    if (op) params.set("operator", op);
    return params.toString();
  }

  async function fetchHistory(limit = 200) {
    return fetch(`${API_BASE}/api/history?${historyQueryParams(limit)}`, {
      headers: apiHeaders(),
    });
  }

  async function fetchRunResults(runId) {
    const params = new URLSearchParams();
    const op = currentOperatorName();
    if (op) params.set("operator", op);
    const qs = params.toString();
    return fetch(`${API_BASE}/api/results/${runId}${qs ? `?${qs}` : ""}`, {
      headers: apiHeaders(),
    });
  }

  function updateHistoryAdminUi() {
    const bar = $("#history-admin-bar");
    const loginBtn = $("#btn-admin-login");
    const logoutBtn = $("#btn-admin-logout");
    const pwd = $("#admin-password");
    const scope = $("#history-scope-label");
    if (!bar) return;
    bar.hidden = !adminConfigured;
    const admin = Boolean(getAdminToken());
    isAdminSession = admin;
    if (loginBtn) loginBtn.hidden = admin;
    if (logoutBtn) logoutBtn.hidden = !admin;
    if (pwd) pwd.hidden = admin;
    if (scope) {
      scope.textContent = admin
        ? "Режим админа: все прогоны на сервере"
        : "Ваши прогоны (по имени в брифе)";
      scope.classList.toggle("is-admin", admin);
    }
  }

  async function loginAsAdmin() {
    const password = ($("#admin-password")?.value || "").trim();
    if (!password) return toast("Введите пароль админа");
    try {
      const res = await fetch(`${API_BASE}/api/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const { ok, data } = await parseApiResponse(res);
      if (!ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : "Неверный пароль");
      }
      setAdminToken(data.token || "");
      if ($("#admin-password")) $("#admin-password").value = "";
      toast("Вход как админ");
      if ($("#page-history")?.classList.contains("active")) loadHistory();
    } catch (e) {
      toast(e.message || "Не удалось войти");
    }
  }

  function logoutAdmin() {
    setAdminToken("");
    toast("Режим админа выключен");
    if ($("#page-history")?.classList.contains("active")) loadHistory();
  }

  function normalizeClientSite(raw) {
    let s = (raw || "").trim();
    if (!s) return "";
    const urlMatch = s.match(/https?:\/\/[^\s<>"'·|]+/i);
    if (urlMatch) return urlMatch[0].replace(/[.,;)]+$/, "");
    if (!/^https?:\/\//i.test(s) && !s.includes(" ") && /[\w.-]+\.(?:ru|com|рф|org|net|biz)/i.test(s)) {
      return `https://${s.replace(/^\/+/, "").replace(/[.,;)]+$/, "")}`;
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

  const SHORT_REGION_LABELS = {
    "Москва": "г. Москва",
    "Санкт-Петербург": "г. Санкт-Петербург",
    "Севастополь": "г. Севастополь",
    "Белгородская область": "Белгородская обл.",
    "Брянская область": "Брянская обл.",
    "Владимирская область": "Владимирская обл.",
    "Воронежская область": "Воронежская обл.",
    "Ивановская область": "Ивановская обл.",
    "Калужская область": "Калужская обл.",
    "Костромская область": "Костромская обл.",
    "Курская область": "Курская обл.",
    "Липецкая область": "Липецкая обл.",
    "Московская область": "Московская обл.",
    "Орловская область": "Орловская обл.",
    "Рязанская область": "Рязанская обл.",
    "Смоленская область": "Смоленская обл.",
    "Тамбовская область": "Тамбовская обл.",
    "Тверская область": "Тверская обл.",
    "Тульская область": "Тульская обл.",
    "Ярославская область": "Ярославская обл.",
    "Архангельская область": "Архангельская обл.",
    "Вологодская область": "Вологодская обл.",
    "Калининградская область": "Калининградская обл.",
    "Ленинградская область": "Ленинградская обл.",
    "Мурманская область": "Мурманская обл.",
    "Новгородская область": "Новгородская обл.",
    "Псковская область": "Псковская обл.",
    "Астраханская область": "Астраханская обл.",
    "Волгоградская область": "Волгоградская обл.",
    "Запорожская область": "Запорожская обл.",
    "Ростовская область": "Ростовская обл.",
    "Херсонская область": "Херсонская обл.",
    "Кировская область": "Кировская обл.",
    "Нижегородская область": "Нижегородская обл.",
    "Оренбургская область": "Оренбургская обл.",
    "Пензенская область": "Пензенская обл.",
    "Самарская область": "Самарская обл.",
    "Саратовская область": "Саратовская обл.",
    "Ульяновская область": "Ульяновская обл.",
    "Курганская область": "Курганская обл.",
    "Свердловская область": "Свердловская обл.",
    "Тюменская область": "Тюменская обл.",
    "Челябинская область": "Челябинская обл.",
    "Иркутская область": "Иркутская обл.",
    "Новосибирская область": "Новосибирская обл.",
    "Омская область": "Омская обл.",
    "Томская область": "Томская обл.",
    "Амурская область": "Амурская обл.",
    "Магаданская область": "Магаданская обл.",
    "Сахалинская область": "Сахалинская обл.",
    "Кемеровская область — Кузбасс": "Кемеровская обл. — Кузбасс",
    "Ненецкий автономный округ": "Ненецкий АО",
    "Ханты-Мансийский автономный округ — Югра": "ХМАО — Югра",
    "Ямало-Ненецкий автономный округ": "Ямало-Ненецкий АО",
    "Чукотский автономный округ": "Чукотский АО",
    "Еврейская автономная область": "Еврейская АО",
    "Донецкая Народная Республика": "ДНР",
    "Луганская Народная Республика": "ЛНР",
  };

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function regionShortLabel(name) {
    return SHORT_REGION_LABELS[name] || name;
  }

  function isRegionActive(name) {
    const low = (name || "").toLowerCase();
    return activeRegions.some((x) => x.toLowerCase() === low);
  }

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
      syncRegionTreeChecks();
      return;
    }
    if (empty) empty.hidden = true;
    box.innerHTML = activeRegions.map((r) => {
      const safe = escapeHtml(r);
      return `<button type="button" class="region-chip" data-region="${safe}" title="Убрать: ${safe}">
        <span>${safe}</span><span class="region-chip-x" aria-hidden="true">×</span>
      </button>`;
    }).join("");
    syncRegionTreeChecks();
  }

  function syncRegionFilter() {
    fillRegionFilter(collectRegions());
  }

  function syncRegionTreeChecks() {
    const tree = $("#region-tree");
    if (!tree) return;
    tree.querySelectorAll(".region-subject-check").forEach((cb) => {
      cb.checked = isRegionActive(cb.value);
    });
    tree.querySelectorAll(".region-district").forEach((dist) => {
      const cbs = [...dist.querySelectorAll(".region-subject-check")];
      const checked = cbs.filter((c) => c.checked).length;
      const districtCb = dist.querySelector(".region-district-check");
      if (!districtCb) return;
      districtCb.checked = checked > 0 && checked === cbs.length;
      districtCb.indeterminate = checked > 0 && checked < cbs.length;
      const countEl = dist.querySelector(".region-district-count");
      if (countEl) {
        countEl.textContent = checked ? `${checked}/${cbs.length}` : `${cbs.length}`;
      }
    });
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
    } else {
      syncRegionTreeChecks();
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

  function removeRegions(names) {
    const drop = new Set((names || []).map((n) => (n || "").toLowerCase()));
    const before = activeRegions.length;
    activeRegions = activeRegions.filter((r) => !drop.has(r.toLowerCase()));
    if (activeRegions.length !== before) {
      renderRegionChips();
      syncRegionFilter();
    } else {
      syncRegionTreeChecks();
    }
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

  function buildRegionTree() {
    const tree = $("#region-tree");
    const districts = window.SS_FEDERAL_DISTRICTS || [];
    if (!tree || !districts.length) return;

    tree.innerHTML = districts.map((d) => {
      const id = escapeHtml(d.id);
      const name = escapeHtml(d.name);
      const short = escapeHtml(d.short || "");
      const subjects = (d.regions || []).map((r) => {
        const full = escapeHtml(r);
        const label = escapeHtml(regionShortLabel(r));
        return `<label class="region-subject-row" data-region-name="${full}" data-region-label="${label}">
          <input type="checkbox" class="region-tree-check region-subject-check" value="${full}">
          <span>${label}</span>
        </label>`;
      }).join("");
      return `<div class="region-district" data-district-id="${id}" data-district-name="${name}" data-district-short="${short}">
        <div class="region-district-row">
          <button type="button" class="region-district-toggle" aria-label="Раскрыть ${name}" title="Раскрыть">
            <span class="chevron" aria-hidden="true">▶</span>
          </button>
          <label class="region-district-pick">
            <input type="checkbox" class="region-tree-check region-district-check" data-district="${id}">
            <span class="region-district-label">${name}${short ? ` (${short})` : ""}</span>
            <span class="region-district-count">${(d.regions || []).length}</span>
          </label>
        </div>
        <div class="region-subjects">${subjects}</div>
      </div>`;
    }).join("");

    syncRegionTreeChecks();
  }

  function filterRegionTree(query) {
    const tree = $("#region-tree");
    if (!tree) return;
    const q = (query || "").trim().toLowerCase();
    tree.querySelectorAll(".region-district").forEach((dist) => {
      const dName = (dist.dataset.districtName || "").toLowerCase();
      const dShort = (dist.dataset.districtShort || "").toLowerCase();
      let anyVisible = false;
      dist.querySelectorAll(".region-subject-row").forEach((row) => {
        const hay = `${row.dataset.regionName || ""} ${row.dataset.regionLabel || ""}`.toLowerCase();
        const match = !q || hay.includes(q) || dName.includes(q) || dShort.includes(q);
        row.classList.toggle("hidden-by-filter", !match);
        if (match) anyVisible = true;
      });
      const districtMatch = !q || dName.includes(q) || dShort.includes(q) || anyVisible;
      dist.classList.toggle("hidden-by-filter", !districtMatch);
      if (q && anyVisible) dist.classList.add("open");
    });
  }

  function initRegionPresetSelect() {
    buildRegionTree();
  }

  let pendingResumeRunId = null;

  /** Только домен — для сравнения прогонов в истории, не для поля в брифе. */
  function clientSiteHostKey(url) {
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
    const siteKey = clientSiteHostKey($("#client-site")?.value || brief?.clientSite || "");
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
      const res = await fetchHistory(15);
      const data = await res.json();
      for (const it of data.items || []) {
        if (siteKey && it.client_site && clientSiteHostKey(it.client_site) !== siteKey) continue;
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
      : "Сервер: офлайн · Ctrl+F5 или подождите 1–2 мин (деплой на Render)";
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
    const sourcesEl = $("#sources");
    const sources = sourcesEl
      ? [...sourcesEl.selectedOptions].map((o) => o.value)
      : ["serp", "site", "catalog"];
    const clientSite = normalizeClientSite($("#client-site")?.value || "");
    return ensureBriefQueries({
      operatorName: ($("#operator-name")?.value || "").trim(),
      clientName: ($("#client-name")?.value || "").trim(),
      clientSite,
      niche: ($("#niche")?.value || "").trim(),
      regionMode: document.querySelector('input[name="regionMode"]:checked')?.value || "include",
      regions: collectRegions(),
      queries: ($("#queries")?.value || "").trim(),
      excludeDomains: ($("#exclude-domains")?.value || "").trim(),
      phoneFilter: $("#phone-filter")?.value || "business",
      sources,
      checkAlive: $("#check-alive")?.checked ?? true,
      maxSites: parseInt($("#max-sites")?.value, 10) || 50,
      crawlDepth: parseInt($("#crawl-depth")?.value, 10) || 2,
      requestDelayMs: parseInt($("#request-delay")?.value, 10) || 500,
      useProxy: $("#use-proxy")?.checked ?? false,
      xmlRiverUser: ($("#xml-river-user")?.value || "").trim(),
      apiKey: ($("#api-key")?.value || "").trim(),
    });
  }

  function fillForm(data) {
    $("#operator-name").value = data.operatorName || "";
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

  function briefDownloadBasename(data) {
    const raw = (data.clientName || data.clientSite || "brief").trim();
    const host = (() => {
      try {
        return new URL(data.clientSite || "").hostname.replace(/^www\./, "");
      } catch (_) {
        return "";
      }
    })();
    const base = (raw || host || "brief")
      .replace(/[<>:"/\\|?*\n\r\t]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 60) || "brief";
    const date = new Date().toISOString().slice(0, 10);
    return `signal-scout-brief ${base} ${date}.txt`;
  }

  const BRIEF_TXT_LABELS = {
    operatorName: "Кто запускает",
    clientName: "Клиент",
    clientSite: "Сайт клиента",
    niche: "Ниша",
    regionMode: "Режим регионов",
    regions: "Регионы",
    queries: "Поисковые запросы",
    excludeDomains: "Исключить домены",
    phoneFilter: "Фильтр телефонов",
    sources: "Источники",
    checkAlive: "Проверка живого сайта",
    maxSites: "Макс. сайтов",
    crawlDepth: "Глубина обхода",
    requestDelayMs: "Пауза между запросами (мс)",
    useProxy: "Прокси",
    xmlRiverUser: "XMLRiver ID",
    apiKey: "XMLRiver ключ",
  };

  const BRIEF_TXT_KEYS = Object.keys(BRIEF_TXT_LABELS);

  function briefToText(data) {
    const lines = [
      "Сигнал-Скаут — бриф",
      `Экспорт: ${new Date().toLocaleString("ru-RU")}`,
      "Формат: signal-scout-brief-txt v1",
      "",
    ];
    for (const key of BRIEF_TXT_KEYS) {
      const label = BRIEF_TXT_LABELS[key];
      let value = data[key];
      if (key === "sources" && Array.isArray(value)) value = value.join(", ");
      if (typeof value === "boolean") value = value ? "да" : "нет";
      if (value == null) value = "";
      value = String(value);
      if (value.includes("\n")) {
        lines.push(`${label}:`);
        lines.push("---");
        lines.push(value.replace(/\r\n/g, "\n").replace(/\r/g, "\n"));
        lines.push("---");
      } else {
        lines.push(`${label}: ${value}`);
      }
    }
    return lines.join("\n") + "\n";
  }

  function parseBriefBool(raw) {
    const s = String(raw || "").trim().toLowerCase();
    if (["да", "yes", "true", "1", "on"].includes(s)) return true;
    if (["нет", "no", "false", "0", "off"].includes(s)) return false;
    return null;
  }

  function parseBriefText(raw) {
    const labelToKey = {};
    for (const [key, label] of Object.entries(BRIEF_TXT_LABELS)) {
      labelToKey[label.toLowerCase()] = key;
      labelToKey[key.toLowerCase()] = key;
    }
    const data = {};
    const lines = String(raw || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("Сигнал-Скаут")
        || trimmed.startsWith("Экспорт:") || trimmed.startsWith("Формат:")) {
        i += 1;
        continue;
      }
      const colon = line.indexOf(":");
      if (colon < 0) {
        i += 1;
        continue;
      }
      const label = line.slice(0, colon).trim();
      const key = labelToKey[label.toLowerCase()];
      if (!key) {
        i += 1;
        continue;
      }
      let rest = line.slice(colon + 1).trim();
      if (!rest && lines[i + 1]?.trim() === "---") {
        i += 2;
        const block = [];
        while (i < lines.length && lines[i].trim() !== "---") {
          block.push(lines[i]);
          i += 1;
        }
        if (lines[i]?.trim() === "---") i += 1;
        data[key] = block.join("\n").trim();
        continue;
      }
      data[key] = rest;
      i += 1;
    }
    if (data.sources != null && typeof data.sources === "string") {
      data.sources = data.sources.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
    }
    if (data.checkAlive != null) {
      const b = parseBriefBool(data.checkAlive);
      if (b !== null) data.checkAlive = b;
    }
    if (data.useProxy != null) {
      const b = parseBriefBool(data.useProxy);
      if (b !== null) data.useProxy = b;
    }
    ["maxSites", "crawlDepth", "requestDelayMs"].forEach((k) => {
      if (data[k] != null && data[k] !== "") data[k] = Number(data[k]) || data[k];
    });
    return data;
  }

  function downloadBriefFile() {
    const data = readForm();
    if (!(data.operatorName || "").trim()) {
      toast("Укажите ваше имя в поле «Кто запускает»");
      $("#operator-name")?.focus();
      return;
    }
    if (!isValidClientSite(data.clientSite)) {
      toast("Сначала укажите корректный URL сайта клиента");
      $("#client-site")?.focus();
      return;
    }
    $("#client-site").value = data.clientSite;
    window.SSStorage.saveBrief(data);
    brief = data;
    updateRunUI();

    const text = briefToText(data);
    const blob = new Blob(["\uFEFF" + text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = briefDownloadBasename(data);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast("Бриф скачан — текстовый файл в «Загрузках»");
  }

  function applyBriefFromFile(raw) {
    const trimmed = String(raw || "").trim();
    if (!trimmed) throw new Error("Файл пустой");

    let data = null;
    if (trimmed.startsWith("{")) {
      let parsed;
      try {
        parsed = JSON.parse(trimmed);
      } catch (_) {
        throw new Error("Не удалось прочитать файл брифа");
      }
      data = parsed && typeof parsed === "object" && parsed.brief
        ? parsed.brief
        : parsed;
    } else {
      data = parseBriefText(trimmed);
    }

    if (!data || typeof data !== "object") {
      throw new Error("В файле нет данных брифа");
    }
    const merged = ensureBriefQueries({
      ...EMPTY_BRIEF,
      ...data,
      clientSite: normalizeClientSite(data.clientSite || ""),
    });
    if (!isValidClientSite(merged.clientSite)) {
      throw new Error("В файле нет корректного сайта клиента");
    }
    fillForm(merged);
    $("#client-site").value = merged.clientSite;
    brief = readForm();
    window.SSStorage.saveBrief(brief);
    fillRegionFilter(brief.regions);
    updateRunUI();
  }

  async function uploadBriefFile(file) {
    if (!file) return;
    try {
      const text = await file.text();
      applyBriefFromFile(text);
      toast("Бриф загружен из файла и сохранён в браузере");
    } catch (e) {
      toast(e.message || "Не удалось прочитать файл");
    }
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
      $("#live-banner").hidden = true;
      $("#pagination").hidden = true;
      $("#pager").hidden = true;
      return;
    }

    empty.hidden = true;
    wrap.hidden = false;
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

  let healthRetryTimer = null;

  function scheduleHealthRetry() {
    clearInterval(healthRetryTimer);
    healthRetryTimer = setInterval(async () => {
      if (apiOnline) return;
      const data = await checkApi();
      if (data) {
        refreshStartButtonLabel();
        updateRunUI();
      }
    }, 8000);
  }

  async function fetchHealth() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 25000);
    try {
      const res = await fetch(`${API_BASE}/api/health`, { signal: ctrl.signal });
      if (!res.ok) return null;
      return await res.json();
    } catch (_) {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async function checkApi() {
    const el = $("#api-status");
    const data = await fetchHealth();
    if (!data) {
      apiOnline = false;
      if (el) {
        el.textContent = serverOfflineHint();
        el.style.color = "var(--danger)";
      }
      return null;
    }

    apiOnline = true;
    const parts = ["Сервер: онлайн"];
    try {
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
    } catch (_) {
      if (data.xmlriver_configured) parts.push("XMLRiver (ключ в Render)");
    }
    if (data.zenrows_configured) parts.push("ZenRows ✓");
    else if (data.scrapingbee_configured) parts.push("ScrapingBee ✓");
    else if (data.scrapingfish_configured) parts.push("ScrapingFish ✓");
    else if (data.scraping_configured) parts.push("Scraping ✓");
    adminConfigured = Boolean(data.admin_configured);
    if (getAdminToken()) {
      try {
        const st = await fetch(`${API_BASE}/api/admin/status`, { headers: apiHeaders() });
        const status = await st.json();
        if (!status.admin) setAdminToken("");
      } catch (_) {
        setAdminToken("");
      }
    }
    updateHistoryAdminUi();
    rememberDeployVersion(data.version);
    if (data.version) parts.push(`вер. ${data.version}`);
    if (el) {
      el.textContent = parts.join(" · ");
      const bad = parts.some((p) => p.includes("✗") || p.includes("нужен"));
      el.style.color = bad ? "var(--warn)" : "var(--success)";
      el.style.cursor = "pointer";
      el.title = "Нажмите, чтобы проверить связь с сервером ещё раз";
    }
    if (data.version && data.version !== EXPECTED_BUILD_VERSION && !sessionStorage.getItem("ss-old-build-toast")) {
      sessionStorage.setItem("ss-old-build-toast", "1");
      toast("Обновите деплой на Render (main) или подождите автодеплой");
    }
    return data;
  }

  async function checkApiWithRetry() {
    for (let attempt = 0; attempt < 4; attempt++) {
      const data = await checkApi();
      if (data) return data;
      if (attempt < 3) await new Promise((r) => setTimeout(r, 2500));
    }
    return null;
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
      toast("Нажмите «Продолжить сбор» или запустите сбор заново");
      return;
    }
    if (res.status === 502 || res.status === 503) {
      return;
    }
    if (!res.ok) throw new Error("Статус недоступен");
    const data = await res.json();
    let stepLabel = data.current_step ? stepTitle(data.current_step) : "";
    const fp = data.pipeline?.filter_progress;
    if (data.current_step === "filter" && fp && fp.total) {
      stepLabel = `Фильтры: ${fp.checked || 0}/${fp.total} · живых ${fp.alive || 0}`;
    }
    applyPipeline(data.pipeline || {}, data.progress || 0, {
      status: data.status,
      current_step_label: stepLabel ? `Сейчас: ${stepLabel}…` : "",
    });

    if (data.status === "done") {
      clearInterval(pollTimer);
      stopRunTimer();
      saveResumeRunId("");
      setProgressUI(100, "", "done");
      const rr = await fetchRunResults(runId);
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
          const rr = await fetchRunResults(runId);
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
    let stepLabel = data.current_step ? stepTitle(data.current_step) : "";
    const fp = data.pipeline?.filter_progress;
    if (data.current_step === "filter" && fp && fp.total) {
      stepLabel = `Фильтры: ${fp.checked || 0}/${fp.total} · живых ${fp.alive || 0}`;
    }
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
      const health = await checkApiWithRetry();
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
    if (!(brief.operatorName || "").trim()) {
      toast("Укажите ваше имя в поле «Кто запускает»");
      $("#operator-name")?.focus();
      showPage("brief");
      return;
    }
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

    const health = await checkApiWithRetry();
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

  function formatHistoryQueries(item) {
    const queries = item.queries || [];
    if (!queries.length) return "<span class='hint'>—</span>";
    const preview = queries.slice(0, 2).map((q) => escapeHtml(q)).join("<br>");
    if (queries.length <= 2) return `<div class="history-queries">${preview}</div>`;
    const rest = queries.slice(2).map((q) => `<li>${escapeHtml(q)}</li>`).join("");
    return `<details class="history-queries"><summary>${preview}<br>ещё ${queries.length - 2}</summary><ul>${rest}</ul></details>`;
  }

  function formatHistoryRegions(item) {
    const count = item.regions_count || 0;
    if (!count) return "все РФ";
    const mode = item.region_mode === "exclude" ? "кроме" : "";
    if (count <= 2) return `${mode} ${(item.regions || []).join(", ")}`.trim();
    return `${mode} ${count} регионов`.trim();
  }

  function openInstructions() {
    const modal = $("#instructions-modal");
    if (!modal) return;
    modal.hidden = false;
    modal.classList.add("open");
    $("#btn-close-instructions")?.focus();
  }

  function closeInstructions() {
    const modal = $("#instructions-modal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.hidden = true;
  }

  async function loadHistory() {
    const box = $("#history-list");
    const meta = $("#history-meta");
    if (!box) return;
    if (!apiOnline) {
      box.innerHTML = "<p class='hint'>Сервер офлайн</p>";
      if (meta) meta.textContent = "";
      return;
    }
    const res = await fetchHistory(200);
    if (res.status === 400) {
      box.innerHTML = "<p class='hint'>Укажите ваше имя в брифе — тогда здесь будут ваши прогоны. Или войдите как админ.</p>";
      if (meta) meta.textContent = "";
      updateHistoryAdminUi();
      return;
    }
    if (!res.ok) {
      box.innerHTML = "<p class='hint'>Не удалось загрузить историю</p>";
      if (meta) meta.textContent = "";
      return;
    }
    const data = await res.json();
    const items = data.items || [];
    const total = data.total ?? items.length;
    if (meta) {
      if (data.admin) {
        meta.textContent = total
          ? `Админ: всего прогонов на сервере ${total}. Показано: ${items.length}.`
          : "Админ: пока нет прогонов на сервере.";
      } else {
        meta.textContent = total
          ? `Ваши прогоны (${data.operator || currentOperatorName()}): ${total}. Показано: ${items.length}.`
          : `Пока нет ваших прогонов (${data.operator || currentOperatorName() || "укажите имя в брифе"}).`;
      }
    }
    updateHistoryAdminUi();
    if (!items.length) {
      box.innerHTML = "<p class='hint'>Пока нет прогонов</p>";
      return;
    }
    box.innerHTML = `
      <table class="history-table">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Кто</th>
            <th>Клиент</th>
            <th>Запросы</th>
            <th>Регионы</th>
            <th>Сайтов</th>
            <th>Тел.</th>
            <th>Статус</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${items.map((it) => `
            <tr>
              <td>${new Date(it.created_at).toLocaleString("ru-RU")}</td>
              <td>${escapeHtml(it.operator_name || "не указан")}</td>
              <td>
                <div>${escapeHtml(it.client_name || "—")}</div>
                <div class="history-site">${escapeHtml(it.client_site || "")}</div>
              </td>
              <td>${formatHistoryQueries(it)}</td>
              <td>${escapeHtml(formatHistoryRegions(it))}</td>
              <td>${it.sites_count}</td>
              <td>${it.phones_count}</td>
              <td><span class="status-pill ${escapeHtml(it.status || "")}">${escapeHtml(it.status_label || it.status || "—")}</span></td>
              <td><span class="status-pill ${escapeHtml(it.status || "")}">${escapeHtml(it.status_label || it.status || "—")}</span></td>
              <td class="history-actions">
                <button type="button" class="btn btn-ghost btn-sm" data-open-run="${escapeHtml(it.id)}">${
                  it.status === "running" || it.status === "pending"
                    ? "К сбору"
                    : it.status === "stopped" || it.status === "error"
                      ? "Продолжить"
                      : "Открыть"
                }</button>
                ${it.status === "running" || it.status === "pending"
                  ? `<button type="button" class="btn btn-ghost btn-sm" data-stop-run="${escapeHtml(it.id)}">Остановить</button>`
                  : ""}
              </td>
            </tr>`).join("")}
        </tbody>
      </table>`;

    box.querySelectorAll("[data-open-run]").forEach((btn) => {
      btn.addEventListener("click", () => openHistoryRun(btn.dataset.openRun));
    });
    box.querySelectorAll("[data-stop-run]").forEach((btn) => {
      btn.addEventListener("click", () => stopHistoryRun(btn.dataset.stopRun));
    });
  }

  async function openHistoryRun(id) {
    if (!id) return;
    try {
      const st = await fetch(`${API_BASE}/api/run/${id}`, { headers: apiHeaders() });
      if (!st.ok) throw new Error("Не удалось открыть прогон");
      const info = await st.json();

      const rr = await fetchRunResults(id);
      if (!rr.ok) {
        const errBody = await rr.json().catch(() => ({}));
        throw new Error(typeof errBody.detail === "string" ? errBody.detail : "Нет доступа к прогону");
      }
      const payload = await rr.json();
      const runBrief = payload.brief || {};
      if (runBrief.clientSite || runBrief.operatorName) {
        fillForm({ ...EMPTY_BRIEF, ...runBrief });
        brief = readForm();
        window.SSStorage.saveBrief(brief);
        fillRegionFilter(brief.regions);
        updateRunUI();
      }

      results = payload.results || [];
      currentRunId = id;
      isLiveRun = !payload.is_demo;
      window.SSStorage.saveResults(results);

      if (info.status === "running" || info.status === "pending") {
        saveResumeRunId(id);
        showPage("run");
        setRunButtons(true);
        startRunTimer();
        await showRunState(id, info.status);
        await startPolling(id);
        toast("Открыт идущий сбор — можно остановить или дождаться конца");
        return;
      }

      if (info.status === "stopped" || info.status === "error") {
        saveResumeRunId(id);
        pendingResumeRunId = id;
        showPage("run");
        setRunButtons(false);
        await showRunState(id, info.status);
        await refreshStartButtonLabel();
        toast(info.status === "error"
          ? "Прогон с ошибкой — нажмите «Продолжить сбор»"
          : "Прогон остановлен — нажмите «Продолжить сбор»");
        return;
      }

      enableResultsUI();
      $("#results-subtitle").textContent = `${runBrief.clientName || brief?.clientName || "Клиент"} · ${results.length} строк`;
      showPage("results");
      toast(results.length ? "Результаты открыты" : "Прогон готов, строк пока нет");
    } catch (e) {
      toast(e.message || "Не удалось открыть прогон");
    }
  }

  async function stopHistoryRun(id) {
    if (!id) return;
    try {
      const res = await fetch(`${API_BASE}/api/run/${id}/stop`, { method: "POST", headers: apiHeaders() });
      const { ok, data } = await parseApiResponse(res);
      if (!ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось остановить");
      }
      toast("Сбор остановлен — можно открыть и продолжить");
      await loadHistory();
      if (currentRunId === id) {
        saveResumeRunId(id);
        pendingResumeRunId = id;
        await showRunState(id, "stopped");
        setRunButtons(false);
        await refreshStartButtonLabel();
      }
    } catch (e) {
      toast(e.message || "Ошибка остановки");
    }
  }

  async function compareSessions() {
    const a = $("#compare-a").value;
    const b = $("#compare-b").value;
    if (!a || !b) return toast("Выберите две сессии");
    const params = new URLSearchParams({ a, b });
    const op = currentOperatorName();
    if (op) params.set("operator", op);
    const res = await fetch(`${API_BASE}/api/history/compare?${params}`, {
      headers: apiHeaders(),
    });
    const data = await res.json();
    $("#compare-result").innerHTML = `
      <p><strong>Новые:</strong> ${data.new_sites.length ? data.new_sites.join(", ") : "—"}</p>
      <p><strong>Исчезли:</strong> ${data.removed_sites.length ? data.removed_sites.join(", ") : "—"}</p>
      <p><strong>Общие:</strong> ${data.common.length}</p>`;
  }

  let lastSuggestedSiteUrl = "";
  let suggestBriefInFlight = false;
  let suggestBriefTimer = null;
  let suggestBlurTimer = null;

  function setSuggestUi(active, message) {
    const btn = $("#btn-suggest-brief");
    const status = $("#suggest-status");
    const card = $("#client-card");
    if (btn) {
      btn.disabled = active;
      btn.classList.toggle("is-loading", active);
      btn.setAttribute("aria-busy", active ? "true" : "false");
    }
    if (card) card.classList.toggle("suggest-active", active);
    if (status) {
      status.hidden = !active;
      if (message) status.textContent = message;
    }
    const hint = $("#niche-hint");
    if (hint && active && message) hint.textContent = message;
  }

  async function suggestBriefFromSite(force) {
    const url = normalizeClientSite($("#client-site")?.value || "");
    if (!url || !isValidClientSite(url)) {
      toast("Укажите корректный URL сайта");
      return;
    }
    if (suggestBriefInFlight) return;
    if (!force && url === lastSuggestedSiteUrl) return;

    if (!apiOnline) {
      const health = await checkApi();
      if (!health) {
        toast("Сервер офлайн — подождите «Сервер: онлайн» или Ctrl+F5");
        return;
      }
    }

    suggestBriefInFlight = true;
    const started = Date.now();
    setSuggestUi(true, "Опрос сайта: главная и разделы услуг…");

    suggestBriefTimer = setInterval(() => {
      const sec = Math.floor((Date.now() - started) / 1000);
      setSuggestUi(true, `Опрос сайта: главная и разделы… ${sec} сек`);
    }, 500);

    const hint = $("#niche-hint");
    try {
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 90000);
      const res = await fetch(
        `${API_BASE}/api/brief/suggest?url=${encodeURIComponent(url)}`,
        { signal: ctrl.signal },
      );
      clearTimeout(timeout);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = typeof data.detail === "string" ? data.detail : "Не удалось разобрать сайт";
        if (hint) hint.textContent = err;
        toast(err);
        return;
      }
      if (!data.niche && !data.queries) {
        if (hint) hint.textContent = "Сайт открыт, но не удалось определить нишу — заполните вручную";
        toast("Мало данных на главной странице — допишите нишу вручную");
        return;
      }
      lastSuggestedSiteUrl = url;
      if (data.clientName) $("#client-name").value = data.clientName;
      if (data.niche) $("#niche").value = data.niche;
      if (data.queries) $("#queries").value = data.queries;
      if (data.excludeDomains) $("#exclude-domains").value = data.excludeDomains;
      brief = readForm();
      window.SSStorage.saveBrief(brief);
      updateRunUI();
      const doneMsg = data.survey
        ? `Опрос ${data.survey.pages} стр. · ${data.survey.businessModel || "ниша"} · ${data.title || "готово"}`
        : data.title
          ? `Готово · ${data.title}`
          : "Ниша, запросы и исключения подставлены · регионы укажите вручную";
      if (hint) hint.textContent = doneMsg;
      toast("Бриф заполнен по сайту · регионы — вручную");
    } catch (e) {
      const msg = e?.name === "AbortError"
        ? "Сайт долго не отвечает — попробуйте ещё раз"
        : "Ошибка связи с сервером";
      if (hint) hint.textContent = msg;
      toast(msg);
    } finally {
      clearInterval(suggestBriefTimer);
      suggestBriefTimer = null;
      suggestBriefInFlight = false;
      setSuggestUi(false);
    }
  }

  function exportUrl(ext) {
    const params = new URLSearchParams();
    const op = currentOperatorName();
    if (op) params.set("operator", op);
    const token = getAdminToken();
    if (token) params.set("admin_token", token);
    const qs = params.toString();
    return `${API_BASE}/api/export/${currentRunId}.${ext}${qs ? `?${qs}` : ""}`;
  }

  function exportCsv() {
    if (currentRunId && apiOnline) window.open(exportUrl("csv"), "_blank");
    else toast("Нет активного прогона");
  }

  function exportXls() {
    if (currentRunId && apiOnline) window.open(exportUrl("xlsx"), "_blank");
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
    if (results.length) {
      enableResultsUI();
      $("#results-subtitle").textContent = `${brief.clientName} · ${results.length} строк`;
    }

    $$(".nav button").forEach((btn) => btn.addEventListener("click", () => showPage(btn.dataset.page)));

    $("#brief-form").addEventListener("submit", (e) => {
      e.preventDefault();
      brief = readForm();
      if (!(brief.operatorName || "").trim()) {
        toast("Укажите ваше имя в поле «Кто запускает»");
        $("#operator-name")?.focus();
        return;
      }
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

    $("#region-tree")?.addEventListener("click", (e) => {
      const toggle = e.target.closest(".region-district-toggle");
      if (toggle) {
        e.preventDefault();
        const dist = toggle.closest(".region-district");
        if (dist) dist.classList.toggle("open");
        return;
      }
    });

    $("#region-tree")?.addEventListener("change", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLInputElement) || t.type !== "checkbox") return;

      if (t.classList.contains("region-district-check")) {
        const dist = t.closest(".region-district");
        const names = [...(dist?.querySelectorAll(".region-subject-check") || [])]
          .map((cb) => cb.value)
          .filter(Boolean);
        if (t.checked) {
          const added = addRegions(names);
          toast(added
            ? (added === 1 ? "Добавлен 1 регион" : `Добавлено регионов: ${added}`)
            : "Все регионы округа уже выбраны");
        } else {
          removeRegions(names);
        }
        return;
      }

      if (t.classList.contains("region-subject-check")) {
        if (t.checked) addRegions([t.value]);
        else removeRegion(t.value);
      }
    });

    $("#region-tree-filter")?.addEventListener("input", (e) => {
      filterRegionTree(e.target.value || "");
    });

    $("#btn-region-clear")?.addEventListener("click", () => {
      clearRegions();
      toast("Регионы очищены");
    });

    $("#btn-instructions")?.addEventListener("click", openInstructions);
    $("#btn-close-instructions")?.addEventListener("click", closeInstructions);
    $("#instructions-modal")?.addEventListener("click", (e) => {
      if (e.target?.id === "instructions-modal") closeInstructions();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeInstructions();
    });

    $("#btn-reset-pilot").addEventListener("click", () => {
      fillForm(PILOT);
      brief = readForm();
      window.SSStorage.saveBrief(brief);
      updateRunUI();
      toast("Пример брифа загружен");
    });

    $("#btn-clear-brief")?.addEventListener("click", () => {
      fillForm(EMPTY_BRIEF);
      clearRegions();
      lastSuggestedSiteUrl = "";
      brief = readForm();
      window.SSStorage.clearBrief();
      window.SSStorage.saveBrief(brief);
      const hint = $("#niche-hint");
      if (hint) hint.textContent = "";
      updateRunUI();
      toast("Бриф очищен — можно заполнить заново");
    });

    $("#btn-download-brief")?.addEventListener("click", downloadBriefFile);
    $("#btn-upload-brief")?.addEventListener("click", () => {
      $("#brief-file-input")?.click();
    });
    $("#brief-file-input")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      uploadBriefFile(file);
    });

    $("#client-site").addEventListener("blur", () => {
      clearTimeout(suggestBlurTimer);
      suggestBlurTimer = setTimeout(() => {
        suggestBriefFromSite(false);
        refreshStartButtonLabel();
      }, 350);
    });
    $("#btn-suggest-brief")?.addEventListener("mousedown", (e) => e.preventDefault());
    $("#btn-suggest-brief")?.addEventListener("click", () => {
      clearTimeout(suggestBlurTimer);
      lastSuggestedSiteUrl = "";
      suggestBriefFromSite(true);
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
    $("#btn-admin-login")?.addEventListener("click", loginAsAdmin);
    $("#btn-admin-logout")?.addEventListener("click", logoutAdmin);
    $("#admin-password")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") loginAsAdmin();
    });
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

    checkApiWithRetry().then(() => {
      scheduleHealthRetry();
      purgeStaleRuns();
      refreshStartButtonLabel();
    });
    $("#api-status")?.addEventListener("click", () => {
      const el = $("#api-status");
      if (el) el.textContent = "Сервер: проверка…";
      checkApiWithRetry().then((data) => {
        if (data) {
          refreshStartButtonLabel();
          updateRunUI();
        }
      });
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") checkApi().then(() => refreshStartButtonLabel());
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
