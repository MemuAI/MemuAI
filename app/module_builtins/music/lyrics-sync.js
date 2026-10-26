

const LyricTiming = (() => {
  const GAP = 4.5;
  const RATE = 3.6;
  const MIN_RATE = 1.8, MAX_RATE = 7;
  const VOWELS = /[aeiouyæøœåäöüáéíóúàèìòùâêîôûãõąęıаеёиоуыэюяіїєў]+/gi;
  const DENSE = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]/g;

  function syllables(text) {
    const s = String(text || "");
    let n = (s.match(DENSE) || []).length;
    for (const word of s.replace(DENSE, " ").split(/[^\p{L}\p{N}']+/u)) {
      if (!word) continue;
      if (/^\d+$/.test(word)) { n += Math.min(6, word.length * 1.5); continue; }
      let v = (word.match(VOWELS) || []).length;
      if (v > 1 && /[^aeiouyl]e$/i.test(word)) v--;
      n += Math.max(1, v);
    }
    return n;
  }
  const median = (xs) => { const a = xs.slice().sort((x, y) => x - y); return a.length ? (a.length % 2 ? a[a.length >> 1] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2) : 0; };
  const isText = (row) => !!String(row[1] || "").trim();

  function rate(rows) {
    const samples = [];
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i], next = rows[i + 1];
      if (!isText(row)) continue;
      const words = Array.isArray(row[2]) && row[2].length > 1 ? row[2] : null;
      if (words) {
        const span = (words[words.length - 1][2] || words[words.length - 1][0]) - words[0][0];
        if (span > .4) samples.push(syllables(row[1]) * (words[words.length - 1][2] ? 1 : (words.length - 1) / words.length) / span);
        continue;
      }
      if (!next || !isText(next)) continue;
      const gap = next[0] - row[0], syl = syllables(row[1]);
      if (gap >= .6 && gap <= 10 && syl >= 2) samples.push(syl / gap);
    }
    return samples.length >= 3 ? Math.max(MIN_RATE, Math.min(MAX_RATE, median(samples))) : RATE;
  }

  const estimate = (syl, r) => syl / r * 1.08 + .35;

  function build(rows, duration) {
    rows = Array.isArray(rows) ? rows : [];
    const r = rate(rows), lines = [], gaps = [];
    const total = Number(duration) > 0 ? Number(duration) : 0;
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (!isText(row)) continue;
      const start = Number(row[0]) || 0;
      const k = i + 1;
      const next = rows[k] ? Number(rows[k][0]) : (total > start ? total : start + 8);
      const limit = Math.max(start + .3, next);
      let end = 0;
      const words = Array.isArray(row[2]) && row[2].length ? row[2] : null;
      let timed = null;
      if (words) {
        timed = words.map((w, j) => {
          const s = Number(w[0]) || start, after = words[j + 1] ? Number(words[j + 1][0]) : 0;
          const own = estimate(syllables(w[1]), r);
          let e = Number(w[2]) > s ? Number(w[2]) : after ? Math.min(after, s + Math.max(1.6, own * 2.5)) : s + own;
          return { start: s, end: Math.min(e, limit) };
        });
      }
      if (Number(row[3]) > start) end = Number(row[3]);
      else if (timed) end = timed[timed.length - 1].end;
      else if (rows[k] && !isText(rows[k])) end = next;
      else {
        const est = estimate(syllables(row[1]), r);
        end = next - start <= est + Math.max(1.5, est * .35) ? next : start + est;
      }
      end = Math.max(start + .3, Math.min(end, limit));
      lines.push({ start, end, row: i, words: timed });
    }
    for (let j = 0; j < lines.length; j++) {
      const from = lines[j].end, to = lines[j + 1] ? lines[j + 1].start : 0;
      if (to && to - from >= GAP) gaps.push({ after: j, from, to });
    }
    if (lines.length && lines[0].start >= GAP + .5) gaps.unshift({ after: -1, from: .5, to: lines[0].start });
    return { lines, gaps, rate: r };
  }

  function at(timing, now) {
    const list = timing.lines;
    let lo = 0, hi = list.length - 1, idx = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (list[mid].start <= now) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    const gap = timing.gaps.find((g) => g.after === idx && now >= g.from && now < g.to) || null;
    const line = list[idx];
    return { line: idx, progress: line ? Math.max(0, Math.min(1, (now - line.start) / Math.max(.05, line.end - line.start))) : 0,
      gap, gapProgress: gap ? Math.max(0, Math.min(1, (now - gap.from) / (gap.to - gap.from))) : 0, gapLeft: gap ? gap.to - now : 0 };
  }
  return { build, at, syllables, rate, GAP };
})();
