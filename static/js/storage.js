window.SSStorage = {
  BRIEF_KEY: "signal-scout-brief-v2",
  RESULTS_KEY: "signal-scout-results-v2",
  REVEALED_KEY: "signal-scout-revealed-phones",

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

  getRevealed() {
    try {
      return new Set(JSON.parse(localStorage.getItem(this.REVEALED_KEY) || "[]"));
    } catch (_) {
      return new Set();
    }
  },

  toggleRevealed(phone) {
    const set = this.getRevealed();
    if (set.has(phone)) set.delete(phone);
    else set.add(phone);
    localStorage.setItem(this.REVEALED_KEY, JSON.stringify([...set]));
    return set.has(phone);
  },
};
