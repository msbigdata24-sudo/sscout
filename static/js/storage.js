window.SSStorage = {
  BRIEF_KEY: "signal-scout-brief-v2",
  RESULTS_KEY: "signal-scout-results-v2",
  RESUME_KEY: "signal-scout-resume-run-v1",
  LAST_RUN_KEY: "signal-scout-last-run-v1",

  loadBrief(defaults) {
    try {
      const raw = localStorage.getItem(this.BRIEF_KEY);
      if (raw) return { ...defaults, ...JSON.parse(raw) };
    } catch (_) {}
    return { ...defaults };
  },

  saveBrief(data) {
    localStorage.setItem(this.BRIEF_KEY, JSON.stringify(data));
  },

  clearBrief() {
    localStorage.removeItem(this.BRIEF_KEY);
  },

  loadResults() {
    try {
      const raw = localStorage.getItem(this.RESULTS_KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) {}
    return [];
  },

  saveResults(data) {
    localStorage.setItem(this.RESULTS_KEY, JSON.stringify(data));
  },

  saveResumeRunId(id) {
    if (id) localStorage.setItem(this.RESUME_KEY, id);
    else localStorage.removeItem(this.RESUME_KEY);
  },

  loadResumeRunId() {
    try {
      return localStorage.getItem(this.RESUME_KEY) || "";
    } catch (_) {
      return "";
    }
  },

  /** Последний завершённый/открытый прогон — для экспорта после F5 и смены деплоя. */
  saveLastRunId(id) {
    if (id) localStorage.setItem(this.LAST_RUN_KEY, id);
    else localStorage.removeItem(this.LAST_RUN_KEY);
  },

  loadLastRunId() {
    try {
      return localStorage.getItem(this.LAST_RUN_KEY) || "";
    } catch (_) {
      return "";
    }
  },
};
