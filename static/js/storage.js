window.SSStorage = {
  BRIEF_KEY: "signal-scout-brief-v2",
  RESULTS_KEY: "signal-scout-results-v2",

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
};
