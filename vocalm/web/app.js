'use strict';
(() => {
  const BOOT = JSON.parse(document.getElementById('boot').textContent || '{}');
  const TOKEN = BOOT.token;
  let settings = BOOT.settings || {};
  let autostartInfo = BOOT.autostart || {};
  let live = null;
  let overview = null;
  const tableMode = {daily: false, rate: false};

  // ================= helpers =================
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k, d) { try { const v = localStorage.getItem('vocalm.' + k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('vocalm.' + k, v); } catch (e) { /* private window */ } },
  };

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
    return el;
  }
  const SVG = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined) continue;
      if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid) el.append(kid);
    return el;
  }

  async function api(path, body) {
    const opts = {headers: {'X-Vocalm-Token': TOKEN}};
    if (body !== undefined) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  const MINUS = '−';
  const fmtDb = (v) => (v === null || v === undefined) ? '–' : (v < 0 ? MINUS : '') + Math.abs(v).toFixed(1);
  const fmtNum = (v) => (v === null || v === undefined) ? '–' : Math.round(v).toLocaleString();
  function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const hrs = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), sc = sec % 60;
    if (hrs >= 24) return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
    if (hrs) return `${hrs}h ${m}m`;
    return m ? `${m}m ${sc}s` : `${sc}s`;
  }
  function durParts(sec) {
    sec = Math.max(0, sec || 0);
    if (sec >= 3600) return [(sec / 3600).toFixed(sec >= 36000 ? 0 : 1), 'h'];
    if (sec >= 60) return [String(Math.round(sec / 60)), 'min'];
    return [String(Math.round(sec)), 's'];
  }
  const fmtDurShort = (sec) => durParts(sec).join(' ');
  // "1h 12m" as big numbers with small units
  function durNodes(sec) {
    return fmtDur(sec).split(' ').flatMap((part) => {
      const m = part.match(/^(\d+)([a-z]+)$/);
      return m ? [m[1], h('span', {class: 'unit', text: m[2]})] : [part];
    });
  }
  const parseTs = (ts) => new Date(ts.length <= 10 ? ts + 'T00:00:00' : ts);
  const fmtTime = (ts) => parseTs(ts).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  const fmtDay = (ts) => parseTs(ts).toLocaleDateString([], {month: 'short', day: 'numeric'});
  const fmtDayLong = (ts) => parseTs(ts).toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, 2200);
  }
  function banner(msg, bad) {
    const b = $('banner');
    b.hidden = !msg;
    b.textContent = msg || '';
    b.classList.toggle('bad', !!bad);
  }

  // Nice round axis ticks: 0 / 5 / 10 / 15 rather than 0 / 3.7 / 7.4
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function axis(max, count, integer) {
    let step = niceStep(Math.max(max, integer ? 1 : 0.1) / count);
    if (integer) step = Math.max(1, Math.round(step));
    const top = Math.max(step, Math.ceil(max / step) * step);
    const ticks = [];
    for (let v = 0; v <= top + 1e-9; v += step) ticks.push(+v.toFixed(4));
    return {top, ticks};
  }

  // ---------- tooltip ----------
  const tip = $('tip');
  function showTip(x, y, title, rows) {
    tip.replaceChildren(h('div', {class: 't-title', text: title}),
      ...rows.map(([label, value, key]) => h('div', {class: 't-row'},
        key ? h('i', {class: 'key'}) : null, h('span', {text: label}), h('b', {text: value}))));
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 14;
    tip.style.left = Math.max(8, left) + 'px';
    tip.style.top = Math.max(8, top) + 'px';
  }
  const hideTip = () => { tip.hidden = true; };
  // Every hover target also works with the keyboard
  function hoverable(el, getTip, onEnter, onLeave) {
    el.setAttribute('tabindex', '0');
    const show = (e) => {
      const [title, rows] = getTip();
      let x = e.clientX, y = e.clientY;
      if (x === undefined) { const b = el.getBoundingClientRect(); x = b.right; y = b.top; }
      showTip(x, y, title, rows);
      if (onEnter) onEnter();
    };
    const hide = () => { hideTip(); if (onLeave) onLeave(); };
    el.addEventListener('pointermove', show);
    el.addEventListener('focus', show);
    el.addEventListener('pointerleave', hide);
    el.addEventListener('blur', hide);
  }

  // ---------- theme ----------
  let css = {};
  function readColors() {
    const st = getComputedStyle(document.documentElement);
    for (const k of ['accent', 'bad', 'warn', 'good', 'faint', 'muted', 'grid', 'text', 'panel', 'track', 'panel-3']) {
      css[k] = st.getPropertyValue('--' + k).trim();
    }
  }
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    store.set('theme', theme);
    readColors();
    if (overview && route() === 'insights') renderInsights();
  }
  setTheme(store.get('theme', matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
  $('theme').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  $('ver').textContent = 'v' + BOOT.version;
  document.title = 'Vocalm';

  // ================= routing =================
  function route() {
    const m = location.hash.match(/^#\/(\w+)/);
    return m && ['live', 'insights', 'settings'].includes(m[1]) ? m[1] : 'live';
  }
  function show() {
    const r = route();
    for (const v of ['live', 'insights', 'settings']) $('view-' + v).hidden = v !== r;
    document.querySelectorAll('.tabs a').forEach((a) => a.classList.toggle('on', a.dataset.tab === r));
    hideTip();
    if (r === 'live') loadToday();
    if (r === 'insights') loadOverview();
    if (r === 'settings') {
      fillSettings();
      loadDevices();
      if (location.hash === '#/settings/calibrate') $('calibrate').scrollIntoView({behavior: 'smooth'});
    }
  }
  addEventListener('hashchange', show);

  // ================= live stream =================
  const trace = [];          // {t, db} for the last 30 s
  let shown = -90;           // the meter's displayed level, eased toward the real one
  let todaySeq = -1;
  let source = null;

  function connect() {
    source = new EventSource('/api/live?t=' + encodeURIComponent(TOKEN));
    source.onopen = () => { setConn(true); banner(''); };
    source.onmessage = (e) => {
      live = JSON.parse(e.data);
      trace.push({t: Date.now() / 1000, db: live.db});
      while (trace.length && trace[0].t < Date.now() / 1000 - 32) trace.shift();
      renderLiveText();
      if (live.eventSeq !== todaySeq && route() === 'live') {
        todaySeq = live.eventSeq;
        loadToday();
      }
    };
    source.onerror = () => {
      setConn(false);
      // Either Vocalm stopped, or it restarted and this page's token is stale: tell which
      clearTimeout(connect.check);
      connect.check = setTimeout(async () => {
        try {
          const r = await fetch('/api/ping');
          if (r.ok) { location.reload(); return; }
        } catch (e) { /* not running */ }
        banner("Vocalm isn't running. Start it again and this page reconnects by itself.", true);
      }, 2500);
    };
  }
  function setConn(ok) {
    const c = $('conn');
    c.classList.toggle('ok', ok);
    c.classList.toggle('down', !ok);
    c.lastElementChild.textContent = ok ? 'Live' : 'Reconnecting';
  }

  // The meter's scale: from a bit under your normal voice to a bit over your line
  function domain() {
    // Same scale as the bar: from where talking starts to 15 dB past your line
    const thr = live ? live.threshold : settings.threshold_dbfs;
    const floor = live ? live.floor : Math.max(settings.gate_dbfs, settings.normal_dbfs - 18);
    return [floor, Math.min(0, thr + 15)];
  }
  const frac = (db) => { const [lo, hi] = domain(); return clamp((db - lo) / (hi - lo), 0, 1); };
  const zoneColor = (zone) => ({calm: css.good, close: css.warn, loud: css.bad}[zone] || css.faint);
  const ZONE_TEXT = {quiet: 'Not talking', calm: 'Calm', close: 'Getting close', loud: 'Too loud'};

  function renderLiveText() {
    if (!live) return;
    const st = $('status');
    let text = ZONE_TEXT[live.zone] || '';
    let zone = live.zone;
    let title = 'Listening';
    if (!live.mic.ok) { text = 'No microphone'; zone = 'quiet'; title = "Can't hear your microphone"; }
    else if (live.calib.phase) { text = 'Calibrating'; zone = 'quiet'; title = 'Calibrating'; }
    else if (live.paused) { text = 'Paused'; zone = 'quiet'; title = 'Paused'; }
    if ($('liveTitle').textContent !== title) $('liveTitle').textContent = title;
    if (st.dataset.zone !== zone) st.dataset.zone = zone;
    if (st.lastElementChild.textContent !== text) st.lastElementChild.textContent = text;
    $('levelNum').textContent = live.db <= live.floor ? '–' : fmtDb(live.db);  // below your talking floor: noise, not news
    const streakText = fmtDur(live.streak);
    if ($('streak').dataset.v !== streakText) {
      $('streak').dataset.v = streakText;
      $('streak').replaceChildren(...durNodes(live.streak));
    }
    renderZones();
    const peak = $('markPeak');
    // Same rule as the bar: only a peak that went red, and only until your timer runs out
    const showPeak = live.redPeakShowing && live.mic.ok && !live.paused && !live.calib.phase;
    peak.hidden = !showPeak;
    if (showPeak) {
      const ago = live.redPeakAgo < 60 ? 'just now' : `${Math.floor(live.redPeakAgo / 60)} min ago`;
      placeMark(peak, live.redPeak, `last red peak ${fmtDb(live.redPeak)} · ${ago}`);
    }
    if (route() === 'settings') renderLoudnessNote();
    $('nowApp').textContent = live.app || (settings.track_apps ? '–' : 'Not tracked');
    const sess = live.session;
    $('runInfo').textContent = `${fmtDurShort(sess.talk_s)} talking · ${sess.loud} loud`;
    $('micInfo').textContent = live.mic.ok ? live.mic.label.replace(/\s*\(.*\)$/, '') : 'Not found';
    $('micInfo').title = live.mic.ok ? live.mic.label : live.mic.error;
    setToggle($('btnPause'), live.paused, 'Resume', 'Pause');
    setToggle($('btnOverlay'), !live.overlay, 'Show bar', 'Hide bar');
    setToggle($('btnMove'), live.moveMode, 'Drop the bar here', 'Move bar');
    placeMark($('markLine'), live.threshold, `your line ${fmtDb(live.threshold)}`);
    placeMark($('markNormal'), live.normal, 'normal');
    $('meter').setAttribute('aria-valuenow', live.db);
    $('meter').setAttribute('aria-valuetext', `${fmtDb(live.db)} dBFS, ${text}`);
    if (!live.mic.ok && live.mic.error) banner("Vocalm can't hear your microphone. " + live.mic.error.split('\n')[0], true);
    if (route() === 'settings') renderCalibration();
  }
  // The meter's track shows the three zones faintly, so you can see where amber and red start before you get there
  function renderZones() {
    const key = [live.threshold, live.normal, live.gate].join();
    const el = $('meterZones');
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    const a = (frac(live.threshold - 6) * 100).toFixed(2), b = (frac(live.threshold) * 100).toFixed(2);
    const tint = (v, p) => `color-mix(in srgb, var(${v}) ${p}%, transparent)`;
    el.style.background = `linear-gradient(90deg, ${tint('--good', 9)} 0 ${a}%, ${tint('--warn', 14)} ${a}% ${b}%, ` +
      `${tint('--bad', 18)} ${b}% 100%)`;
  }
  function setToggle(btn, on, onText, offText) {
    btn.classList.toggle('on', on);
    const t = on ? onText : offText;
    if (btn.lastElementChild.textContent !== t) btn.lastElementChild.textContent = t;
  }
  function placeMark(el, db, label) {
    const f = frac(db);
    el.style.left = (f * 100).toFixed(2) + '%';
    const span = el.querySelector('span');
    if (!span) return;
    if (span.textContent !== label) span.textContent = label;
    // Near either end, hang the label inwards instead of off the edge of the meter
    span.style.transform = f < 0.12 ? 'translateX(-6px)' : f > 0.88 ? 'translateX(calc(-100% + 6px))' : 'translateX(-50%)';
  }

  // ---------- animation: the meter and the 30-second trace ----------
  let lastFrame = performance.now();
  function frame(now) {
    const dt = Math.min(0.1, (now - lastFrame) / 1000);
    lastFrame = now;
    if (live) {
      const target = live.mic.ok ? live.db : -120;
      shown += (target - shown) * Math.min(1, dt * 14);
      const zone = live.paused || live.calib.phase ? 'quiet' : live.zone;
      for (const el of document.querySelectorAll('.meter-fill')) {
        el.style.width = (frac(shown) * 100).toFixed(2) + '%';
        el.style.background = zoneColor(zone === 'quiet' && shown > live.floor ? 'calm' : zone);
      }
      for (const el of document.querySelectorAll('.mini-meter .meter-mark.line')) {
        const v = el.closest('#lineMeter') ? +$('sThreshold').value : live.threshold;
        el.style.left = (frac(v) * 100).toFixed(2) + '%';
      }
      for (const el of document.querySelectorAll('.mini-meter .meter-mark.normal')) {
        el.style.left = (frac(live.normal) * 100).toFixed(2) + '%';
      }
      if (route() === 'live') drawTrace();
    }
    requestAnimationFrame(frame);
  }

  function drawTrace() {
    const c = $('spark');
    const dpr = devicePixelRatio || 1;
    const w = c.clientWidth, ht = c.clientHeight;
    if (!w) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(ht * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(ht * dpr);
    }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, ht);
    const now = Date.now() / 1000, span = 30;
    const y = (db) => ht - 3 - frac(db) * (ht - 6);
    const x = (t) => (t - (now - span)) / span * w;
    const lineY = y(live.threshold);

    g.strokeStyle = css.grid;
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, ht - 0.5); g.lineTo(w, ht - 0.5); g.stroke();

    const pts = trace.filter((p) => p.t >= now - span - 1);
    if (pts.length > 1) {
      const path = new Path2D();
      pts.forEach((p, i) => (i ? path.lineTo(x(p.t), y(p.db)) : path.moveTo(x(p.t), y(p.db))));
      const area = new Path2D(path);
      area.lineTo(x(pts[pts.length - 1].t), ht); area.lineTo(x(pts[0].t), ht); area.closePath();
      g.globalAlpha = 0.1; g.fillStyle = css.accent; g.fill(area); g.globalAlpha = 1;
      g.lineWidth = 2; g.lineJoin = 'round'; g.lineCap = 'round';
      // Under the line in the accent; anything over it in red
      g.save(); g.beginPath(); g.rect(0, lineY, w, ht); g.clip(); g.strokeStyle = css.accent; g.stroke(path); g.restore();
      g.save(); g.beginPath(); g.rect(0, 0, w, lineY); g.clip(); g.strokeStyle = css.bad; g.stroke(path); g.restore();
    }
    g.setLineDash([4, 4]);
    g.strokeStyle = css.muted;
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, Math.round(lineY) + 0.5); g.lineTo(w, Math.round(lineY) + 0.5); g.stroke();
    g.setLineDash([]);
    g.fillStyle = css.muted;
    g.font = '600 11.5px Bahnschrift, "Segoe UI", sans-serif';
    g.fillText('your line', 4, Math.max(11, lineY - 5));
  }

  // ================= live page: today =================
  async function loadToday() {
    let t;
    try { t = await api('/api/today'); } catch (e) { return; }
    const tot = t.totals;
    const [talkNum, talkUnit] = durParts(tot.talk_s);
    renderKpis($('todayKpis'), [
      {label: 'Talking today', value: talkNum, unit: talkUnit},
      {label: 'Loud moments today', value: fmtNum(tot.loud)},
      {label: 'Of your talking was loud', value: tot.share_loud === null ? '–' : tot.share_loud.toFixed(1), unit: '%'},
      {label: 'Your typical level', value: tot.mean_db === null ? '–' : fmtDb(tot.mean_db), unit: 'dB'},
    ]);
    $('todayCount').textContent = tot.loud ? `${tot.loud} so far` : '';
    renderMoments($('todayList'), t.recent, false,
      'None yet today. Every time you stay over your line for more than a moment, it shows up here.');
    const apps = $('todayApps');
    if (!t.apps.length) {
      apps.replaceChildren(h('p', {class: 'empty', text: settings.track_apps
        ? 'Talk with a game or app in front and it shows up here.' : 'App tracking is off in Settings.'}));
    } else {
      const max = Math.max(...t.apps.map((a) => a.talk_s));
      apps.replaceChildren(h('div', {class: 'bars'}, t.apps.map((a) => h('div', {class: 'barrow'},
        h('span', {class: 'name', text: a.app, title: a.app}),
        h('span', {class: 'track'}, barFill(a.talk_s / max)),
        h('span', {class: 'val', text: `${fmtDurShort(a.talk_s)} · ${a.loud} loud`})))));
    }
  }
  function barFill(f) {
    const i = h('i');
    i.style.width = Math.max(1.5, f * 100).toFixed(1) + '%';
    return i;
  }
  function renderKpis(el, tiles) {
    el.replaceChildren(...tiles.map((k) => h('div', {class: 'kpi'},
      h('div', {class: 'label', text: k.label}),
      h('div', {class: 'value'}, k.value, k.unit && k.value !== '–' ? h('span', {class: 'unit', text: k.unit}) : null),
      k.delta ? h('div', {class: 'delta ' + (k.deltaClass || ''), text: k.delta}) : h('div', {class: 'delta', text: k.note || ' '}))));
  }
  function renderMoments(el, rows, withDate, emptyText) {
    if (!rows.length) { el.replaceChildren(h('li', {class: 'empty', text: emptyText})); return; }
    el.replaceChildren(...rows.map((r) => h('li', null,
      h('span', {class: 'when', text: withDate ? `${fmtDay(r.ts)}, ${fmtTime(r.ts)}` : fmtTime(r.ts)}),
      h('span', {class: 'what', text: r.app || '–', title: r.app || ''}),
      h('span', {class: 'peak', text: fmtDb(r.peak_dbfs) + ' dB'}),
      h('span', {class: 'len', text: (r.duration_s || 0).toFixed(1) + ' s'}))));
  }

  // ================= insights =================
  let days = +store.get('days', '30');
  document.querySelectorAll('#range button').forEach((b) => b.addEventListener('click', () => {
    days = +b.dataset.days;
    store.set('days', String(days));
    loadOverview();
  }));
  document.querySelectorAll('[data-table]').forEach((b) => b.addEventListener('click', () => {
    tableMode[b.dataset.table] = !tableMode[b.dataset.table];
    b.textContent = tableMode[b.dataset.table] ? 'Chart' : 'Table';
    renderInsights();
  }));

  async function loadOverview() {
    document.querySelectorAll('#range button').forEach((b) => b.classList.toggle('on', +b.dataset.days === days));
    document.querySelectorAll('.chart').forEach((c) => c.classList.add('loading'));  // keep the old frame while loading
    try {
      overview = await api('/api/overview?days=' + days);
    } catch (e) {
      banner("Couldn't load your history: " + e.message, true);
      return;
    } finally {
      document.querySelectorAll('.chart').forEach((c) => c.classList.remove('loading'));
    }
    renderInsights();
  }

  function pct(a, b) { return b ? Math.round((a - b) / b * 100) : null; }
  function deltaText(now, before, unit, lowerIsGood = true) {
    if (now === null || before === null || before === undefined || now === undefined) return [null, ''];
    const p = pct(now, before);
    if (p === null) return [null, ''];
    if (Math.abs(p) < 3) return [`about the same as ${unit}`, ''];
    const better = lowerIsGood ? p < 0 : p > 0;
    return [`${p < 0 ? '↓' : '↑'} ${Math.abs(p)}% vs ${unit}`, better ? 'good' : 'bad'];
  }

  function renderInsights() {
    const o = overview;
    if (!o) return;
    const r = o.range;
    $('rangeLabel').textContent = `${fmtDay(r.start)} – ${fmtDay(r.end)}` + (r.first ? ` · history since ${fmtDay(r.first)}` : '');
    renderVerdict(o);
    const t = o.totals, p = o.previous;
    // Counts either side of a moved line aren't like for like, so say so right on the number
    const moved = t.thresholds.length && p.thresholds.length &&
      (t.thresholds[0] !== p.thresholds[0] || t.thresholds[1] !== p.thresholds[1]);
    const unit = (days ? `previous ${days} days` : 'before') + (moved ? ' (your line moved)' : '');
    const [d1, c1] = days ? deltaText(t.loud, p.loud, unit) : [null, ''];
    const [d2, c2] = days ? deltaText(t.rate, p.rate, unit) : [null, ''];
    const [d3, c3] = days ? deltaText(t.share_loud, p.share_loud, unit) : [null, ''];
    renderKpis($('rangeKpis'), [
      {label: 'Loud moments', value: fmtNum(t.loud), delta: d1, deltaClass: c1},
      {label: 'Per hour of talking', value: t.rate === null ? '–' : t.rate.toFixed(1), delta: d2, deltaClass: c2,
       note: t.rate === null ? 'Needs talking time (v0.1 on)' : ''},
      {label: 'Of your talking was loud', value: t.share_loud === null ? '–' : t.share_loud.toFixed(1), unit: '%',
       delta: d3, deltaClass: c3},
      {label: 'Loudest moment', value: t.peak === null ? '–' : fmtDb(t.peak), unit: 'dB',
       note: t.peak === null ? '' : '0 dB is the loudest a mic can record'},
    ]);
    drawDaily();
    drawRate();
    drawHeat();
    renderApps();
    renderMoments($('loudest'), o.loudest, true, 'No loud moments in this range.');
    renderSessions();
  }

  function renderVerdict(o) {
    const w = o.week, lw = o.lastWeek;
    let icon = 'M4 12h16', cls = '', big, sub;
    const moved = w.thresholds.length && lw.thresholds.length &&
      (w.thresholds[0] !== lw.thresholds[0] || w.thresholds[1] !== lw.thresholds[1]);
    if (w.rate !== null && lw.rate !== null) {
      const p = pct(w.rate, lw.rate);
      if (p <= -15) { big = 'Calmer than the week before'; cls = 'good'; icon = 'M6 9l6 6 6-6'; }
      else if (p >= 15) { big = 'Louder than the week before'; cls = 'bad'; icon = 'M6 15l6-6 6 6'; }
      else big = 'About as calm as the week before';
      sub = `${lw.rate.toFixed(1)} → ${w.rate.toFixed(1)} loud moments per hour of talking (last 7 days vs the 7 before).`;
    } else if (w.loud || lw.loud) {
      const p = pct(w.loud, lw.loud);
      if (p !== null && p <= -15) { big = `Fewer loud moments than the week before`; cls = 'good'; icon = 'M6 9l6 6 6-6'; }
      else if (p !== null && p >= 15) { big = `More loud moments than the week before`; cls = 'bad'; icon = 'M6 15l6-6 6 6'; }
      else big = 'About the same as the week before';
      sub = `${fmtNum(lw.loud)} → ${fmtNum(w.loud)} loud moments. Vocalm only knows how long you talked from v0.1 on, ` +
            'so this counts moments rather than comparing per hour of talking. It gets fairer as that fills in.';
    } else {
      big = 'Not enough history yet';
      sub = 'Keep Vocalm running while you play; this compares each week with the one before.';
    }
    if (moved) sub += ' Your line moved between these weeks, so treat it as a rough comparison.';
    $('verdict').replaceChildren(
      h('div', {class: 'badge ' + cls}, svgIcon(icon)),
      h('div', null, h('div', {class: 'big', text: big}), h('p', {text: sub})));
  }
  function svgIcon(d) {
    return s('svg', {viewBox: '0 0 24 24', class: 'ic', 'aria-hidden': 'true'}, s('path', {d}));
  }

  // ---------- chart frame shared by the daily charts ----------
  const PAD = {l: 38, r: 16, t: 26, b: 26};
  function frameFor(el, height) {
    const W = Math.max(320, el.clientWidth || 800);
    return {W, H: height, pw: W - PAD.l - PAD.r, ph: height - PAD.t - PAD.b};
  }
  function yAxis(svg, f, ax, fmt) {
    const g = s('g', {class: 'grid'}), a = s('g', {class: 'axis'});
    for (const v of ax.ticks) {
      const y = PAD.t + f.ph - (v / ax.top) * f.ph;
      if (v > 0) g.append(s('line', {x1: PAD.l, x2: PAD.l + f.pw, y1: y, y2: y}));
      a.append(s('text', {x: PAD.l - 8, y: y + 4, 'text-anchor': 'end', text: fmt ? fmt(v) : String(v)}));
    }
    svg.append(g, a, s('line', {class: 'baseline', x1: PAD.l, x2: PAD.l + f.pw, y1: PAD.t + f.ph, y2: PAD.t + f.ph}));
  }
  function xLabels(svg, f, daily, cx) {
    const a = s('g', {class: 'axis'});
    const n = daily.length;
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(f.pw / 80))));
    daily.forEach((d, i) => {
      if ((n - 1 - i) % every === 0) a.append(s('text', {x: cx(i), y: f.H - 6, 'text-anchor': 'middle', text: fmtDay(d.date)}));
    });
    svg.append(a);
  }
  function changeMarks(svg, f, daily, cx) {
    for (const ch of overview.changes) {
      const i = daily.findIndex((d) => d.date === ch.date);
      if (i < 0) continue;
      const x = cx(i) - (f.pw / daily.length) / 2;
      svg.append(s('g', {class: 'change'},
        s('line', {x1: x, x2: x, y1: PAD.t - 8, y2: PAD.t + f.ph}),
        s('text', {x: x + 4, y: PAD.t - 12, text: `line moved ${fmtDb(ch.from)} → ${fmtDb(ch.to)}`})));
    }
  }
  function table(columns, rows) {
    return h('div', {class: 'table-wrap'}, h('table', null,
      h('thead', null, h('tr', null, columns.map((c) => h('th', {class: c.num ? 'num' : null, text: c.label})))),
      h('tbody', null, rows.map((r) => h('tr', null, columns.map((c) => h('td', {class: c.num ? 'num' : null, text: c.get(r)})))))));
  }

  function drawDaily() {
    const el = $('chartDaily');
    const daily = overview.daily;
    if (tableMode.daily) {
      el.replaceChildren(table([
        {label: 'Day', get: (d) => fmtDayLong(d.date)},
        {label: 'Loud moments', num: true, get: (d) => fmtNum(d.loud)},
        {label: 'Talking', num: true, get: (d) => d.talk_s ? fmtDurShort(d.talk_s) : '–'},
        {label: 'Per hour of talking', num: true, get: (d) => d.rate === null ? '–' : d.rate.toFixed(1)},
        {label: 'Your line', num: true, get: (d) => d.threshold === null ? '–' : fmtDb(d.threshold)},
      ], [...daily].reverse()));
      return;
    }
    const f = frameFor(el, 250);
    const ax = axis(Math.max(...daily.map((d) => d.loud), 1), 4, true);
    const svg = s('svg', {viewBox: `0 0 ${f.W} ${f.H}`, height: f.H, role: 'img',
      'aria-label': 'Loud moments per day; use the Table button for the values'});
    yAxis(svg, f, ax);
    const band = f.pw / daily.length;
    const bw = Math.max(1, Math.min(24, band - 2));      // 2px of air between touching bars, never wider than 24
    const cx = (i) => PAD.l + band * i + band / 2;
    const yv = (v) => PAD.t + f.ph - (v / ax.top) * f.ph;
    const bars = s('g');
    daily.forEach((d, i) => {
      if (d.loud > 0) {
        const x = cx(i) - bw / 2, y = yv(d.loud), hgt = PAD.t + f.ph - y;
        const rr = Math.min(4, bw / 2, hgt);              // rounded at the data end, square at the baseline
        bars.append(s('path', {class: 'bar', 'data-i': i,
          d: `M${x},${y + hgt}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + bw - rr}Q${x + bw},${y} ${x + bw},${y + rr}V${y + hgt}Z`}));
      }
    });
    svg.append(bars);
    changeMarks(svg, f, daily, cx);
    xLabels(svg, f, daily, cx);
    const hits = s('g');
    daily.forEach((d, i) => {
      const hit = s('rect', {class: 'hit', x: PAD.l + band * i, y: PAD.t, width: band, height: f.ph,
        'aria-label': `${fmtDayLong(d.date)}: ${d.loud} loud moments`});
      const bar = () => bars.querySelector(`[data-i="${i}"]`);
      hoverable(hit, () => [fmtDayLong(d.date), [
        ['Loud moments', fmtNum(d.loud), true],
        ...(d.talk_s ? [['Talking', fmtDurShort(d.talk_s)]] : []),
        ...(d.rate !== null ? [['Per hour of talking', d.rate.toFixed(1)]] : []),
        ...(d.threshold !== null ? [['Your line', fmtDb(d.threshold) + ' dB']] : []),
      ]], () => bar() && bar().classList.add('hover'), () => bar() && bar().classList.remove('hover'));
      hits.append(hit);
    });
    svg.append(hits);
    el.replaceChildren(svg);
  }

  function drawRate() {
    const el = $('chartRate');
    const daily = overview.daily;
    const pts = daily.map((d, i) => ({d, i})).filter((p) => p.d.rate !== null);
    if (tableMode.rate) {
      el.replaceChildren(pts.length ? table([
        {label: 'Day', get: (p) => fmtDayLong(p.d.date)},
        {label: 'Per hour of talking', num: true, get: (p) => p.d.rate.toFixed(1)},
        {label: 'Talking', num: true, get: (p) => fmtDurShort(p.d.talk_s)},
        {label: 'Of talking that was loud', num: true, get: (p) => p.d.talk_s ? (100 * p.d.over_s / p.d.talk_s).toFixed(1) + '%' : '–'},
      ], [...pts].reverse()) : h('p', {class: 'empty', text: 'No days with enough talking yet.'}));
      return;
    }
    if (!pts.length) {
      el.replaceChildren(h('p', {class: 'empty', text: overview.hasMinutes
        ? 'Needs a day with at least 5 minutes of talking in this range.'
        : 'This starts with Vocalm 0.1: the old script never recorded how long you talked, only the loud moments. ' +
          'It fills in from today, one point per day with at least 5 minutes of talking.'}));
      return;
    }
    const f = frameFor(el, 220);
    const ax = axis(Math.max(...pts.map((p) => p.d.rate), 1), 4, false);
    const svg = s('svg', {viewBox: `0 0 ${f.W} ${f.H}`, height: f.H, role: 'img',
      'aria-label': 'Loud moments per hour of talking, per day; use the Table button for the values'});
    yAxis(svg, f, ax, (v) => (ax.top < 5 ? v.toFixed(1) : String(v)));
    const band = f.pw / daily.length;
    const cx = (i) => PAD.l + band * i + band / 2;
    const yv = (v) => PAD.t + f.ph - (v / ax.top) * f.ph;
    // Break the line across days with no talking rather than drawing through them
    let dPath = '', prev = null;
    for (const p of pts) {
      dPath += (prev !== null && p.i === prev + 1 ? 'L' : 'M') + cx(p.i).toFixed(1) + ',' + yv(p.d.rate).toFixed(1);
      prev = p.i;
    }
    svg.append(s('path', {class: 'series', d: dPath}));
    for (const p of pts) svg.append(s('circle', {class: 'dot', cx: cx(p.i), cy: yv(p.d.rate), r: 4}));
    const last = pts[pts.length - 1];
    svg.append(s('text', {class: 'chart-text', x: Math.min(cx(last.i) + 8, f.W - 30), y: yv(last.d.rate) - 8,
      text: last.d.rate.toFixed(1)}));
    changeMarks(svg, f, daily, cx);
    xLabels(svg, f, daily, cx);
    // The crosshair finds the nearest day with a value
    const cross = s('line', {class: 'cross', y1: PAD.t, y2: PAD.t + f.ph, visibility: 'hidden'});
    const hit = s('rect', {class: 'hit', x: PAD.l, y: PAD.t, width: f.pw, height: f.ph,
      'aria-label': 'Per hour of talking, by day'});
    hit.addEventListener('pointermove', (e) => {
      const b = svg.getBoundingClientRect();
      const mx = (e.clientX - b.left) * (f.W / b.width);
      const near = pts.reduce((a, p) => (Math.abs(cx(p.i) - mx) < Math.abs(cx(a.i) - mx) ? p : a));
      cross.setAttribute('x1', cx(near.i)); cross.setAttribute('x2', cx(near.i));
      cross.setAttribute('visibility', 'visible');
      showTip(e.clientX, e.clientY, fmtDayLong(near.d.date), [
        ['Per hour of talking', near.d.rate.toFixed(1), true],
        ['Talking', fmtDurShort(near.d.talk_s)],
        ['Loud moments', fmtNum(near.d.loud)]]);
    });
    hit.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); hideTip(); });
    svg.append(cross, hit);
    el.replaceChildren(svg);
  }

  function drawHeat() {
    const el = $('chartHeat');
    const heat = overview.heatmap;
    const max = Math.max(...heat.flat());
    if (!max) { el.replaceChildren(h('p', {class: 'empty', text: 'No loud moments in this range.'})); return; }
    const W = Math.max(360, el.clientWidth || 800), left = 40, top = 6;
    const cw = (W - left - 4) / 24, ch = Math.min(28, Math.max(16, cw * 0.8));
    const H = top + ch * 7 + 24;
    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    // One hue, light to dark: the accent mixed into the surface in five steps
    const steps = [22, 40, 58, 78, 100];
    const color = (v) => (v ? `color-mix(in oklab, ${css.accent} ${steps[Math.min(4, Math.floor((v / max) * 4.999))]}%, ${css.panel})` : css['panel-3']);
    const svg = s('svg', {viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Loud moments by weekday and hour'});
    const axisG = s('g', {class: 'axis'});
    DAYS.forEach((d, r) => axisG.append(s('text', {x: left - 8, y: top + r * ch + ch / 2 + 4, 'text-anchor': 'end', text: d})));
    for (let hr = 0; hr < 24; hr += 3) {
      const label = hr === 0 ? '12 am' : hr < 12 ? `${hr} am` : hr === 12 ? '12 pm' : `${hr - 12} pm`;
      axisG.append(s('text', {x: left + hr * cw + 2, y: H - 6, text: label}));
    }
    svg.append(axisG);
    heat.forEach((row, r) => row.forEach((v, c) => {
      const cell = s('rect', {class: 'cell', x: left + c * cw, y: top + r * ch, width: cw, height: ch, rx: 3,
        style: `fill:${color(v)}`, 'aria-label': `${DAYS[r]} ${c}:00, ${v} loud moments`});
      hoverable(cell, () => [`${DAYS[r]}, ${String(c).padStart(2, '0')}:00–${String((c + 1) % 24).padStart(2, '0')}:00`,
        [['Loud moments', fmtNum(v), true]]], () => cell.classList.add('hover'), () => cell.classList.remove('hover'));
      svg.append(cell);
    }));
    const legend = h('div', {class: 'legend'}, h('span', {text: 'Fewer'}),
      h('span', {class: 'ramp'}, [0, ...steps].map((p, i) => {
        const sw = h('i');
        sw.style.background = i ? `color-mix(in oklab, ${css.accent} ${p}%, ${css.panel})` : css['panel-3'];
        return sw;
      })), h('span', {text: `More (busiest hour: ${fmtNum(max)})`}));
    el.replaceChildren(svg, legend);
  }

  function renderApps() {
    const el = $('apps');
    const apps = overview.apps;
    if (!apps.length) {
      el.replaceChildren(h('p', {class: 'empty', text: overview.hasMinutes
        ? 'No app with at least a minute of talking in this range.'
        : 'Starting with v0.1, Vocalm notes which app or game was in front while you talked (just its name). ' +
          'This fills in as you play, and shows where you get loudest.'}));
      return;
    }
    const metric = (a) => (a.rate !== null ? a.rate : null);
    const max = Math.max(...apps.map((a) => metric(a) || 0), 0.1);
    el.replaceChildren(
      h('div', {class: 'bars'}, apps.map((a) => {
        const row = h('div', {class: 'barrow'},
          h('span', {class: 'name', text: a.app, title: a.app}),
          h('span', {class: 'track'}, metric(a) !== null ? barFill(metric(a) / max) : null),
          h('span', {class: 'val', text: metric(a) !== null ? `${a.rate.toFixed(1)} / h` : 'too little talking'}));
        hoverable(row, () => [a.app, [
          ['Loud moments per hour of talking', a.rate === null ? '–' : a.rate.toFixed(1), true],
          ['Loud moments', fmtNum(a.loud)], ['Talking', fmtDurShort(a.talk_s)],
          ['Of talking that was loud', a.share_loud.toFixed(1) + '%'],
          ['Typical level', fmtDb(a.mean_db) + ' dB'], ['Loudest', fmtDb(a.peak) + ' dB']]]);
        return row;
      })),
      h('p', {class: 'hint', text: 'Bars show loud moments per hour of talking, so a game you play for hours isn\'t ' +
        'unfairly loud just because you played it more. Apps with under 5 minutes of talking have no bar yet.'}));
  }

  function renderSessions() {
    const rows = overview.sessions;
    $('sessions').replaceChildren(rows.length ? table([
      {label: 'Started', get: (r) => `${fmtDayLong(r.start)}, ${fmtTime(r.start)}`},
      {label: 'Ran for', num: true, get: (r) => fmtDur(r.duration_s)},
      {label: 'Talking', num: true, get: (r) => fmtDurShort(r.speaking_s)},
      {label: 'Loud moments', num: true, get: (r) => fmtNum(r.loud_events)},
      {label: 'Loudest', num: true, get: (r) => r.peak_dbfs === null ? '–' : fmtDb(r.peak_dbfs) + ' dB'},
      {label: '', get: (r) => (r.source === 'imported' ? 'from Loud Mic' : '')},
    ], rows) : h('p', {class: 'empty', text: 'No runs in this range.'}));
  }

  let resizeTimer;
  addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (overview && route() === 'insights') renderInsights(); }, 150);
  });

  // ================= settings =================
  const BIND = [
    // [input id, setting, label id, format, parse]
    ['sThreshold', 'threshold_dbfs', 'vThreshold', (v) => fmtDb(+v) + ' dBFS', Number],
    ['sSustain', 'sustain_seconds', 'vSustain', (v) => (+v).toFixed(1) + ' s', Number],
    ['sWidth', 'bar_width', 'vWidth', (v) => v + ' px', Number],
    ['sOpacity', 'opacity', 'vOpacity', (v) => Math.round(v * 100) + '%', Number],
    ['sCooldown', 'nudge_cooldown_s', 'vCooldown', (v) => fmtDur(+v), Number],
  ];
  const CHECKS = [['sOverlay', 'overlay_visible'], ['sNudge', 'nudge_enabled'], ['sTrack', 'track_apps'],
    ['sPeakMarker', 'bar_peak_marker']];
  const pending = {};
  let saveTimer;
  function queueSave(patch, now) {
    Object.assign(pending, patch);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, now ? 0 : 350);
  }
  async function flushSave() {
    const patch = {...pending};
    for (const k of Object.keys(pending)) delete pending[k];
    try {
      const r = await api('/api/settings', patch);
      settings = r.settings;
      toast('Saved');
    } catch (e) {
      toast("Couldn't save: " + e.message);
    }
  }

  function fillSettings() {
    for (const [id, key, label, fmt] of BIND) {
      const el = $(id);
      if (document.activeElement !== el) el.value = settings[key];
      $(label).textContent = fmt(el.value);
    }
    for (const [id, key] of CHECKS) $(id).checked = !!settings[key];
    $('sCorner').value = settings.corner;
    $('sBarLabel').value = settings.bar_label;
    fillTimeout();
    $('vNormal').textContent = fmtDb(settings.normal_dbfs) + ' dBFS';
    $('sAutostart').checked = !!autostartInfo.enabled;
    $('autostartRow').hidden = !autostartInfo.supported;
    $('dataDir').textContent = BOOT.dataDir;
    $('exportCsv').href = '/api/export.csv?t=' + encodeURIComponent(TOKEN);
    const lg = BOOT.legacy;
    $('legacyNote').textContent = lg && lg.events
      ? `Includes ${fmtNum(lg.events)} loud moments and ${fmtNum(lg.sessions)} runs brought over from the old Loud Mic script.` : '';
    const failed = (live && live.hotkeyFailures) || [];
    const keys = [['Ctrl+Alt+M', 'pause / resume'], ['Ctrl+Alt+B', 'move the bar'], ['Ctrl+Alt+Q', 'quit Vocalm']];
    $('hotkeys').replaceChildren(...keys.flatMap(([k, what]) => {
      const taken = failed.includes(k);
      return [h('span', null, ...k.split('+').flatMap((p, i) => (i ? ['+', h('kbd', {text: p})] : [h('kbd', {text: p})]))),
        h('span', {class: taken ? 'bad' : '', text: taken ? `${what} (taken by another program)` : what})];
    }));
    $('foot').textContent = `Vocalm ${BOOT.version} · dashboard at http://127.0.0.1:${BOOT.port} · runs only on this PC`;
    renderCalibration();
  }
  for (const [id, key, label, fmt, parse] of BIND) {
    $(id).addEventListener('input', (e) => {
      $(label).textContent = fmt(e.target.value);
      queueSave({[key]: parse(e.target.value)});
    });
  }
  for (const [id, key] of CHECKS) $(id).addEventListener('change', (e) => queueSave({[key]: e.target.checked}, true));
  $('sCorner').addEventListener('change', (e) => queueSave({corner: e.target.value, pos_x: null, pos_y: null}, true));
  $('sDevice').addEventListener('change', (e) => queueSave({device_query: e.target.value}, true));
  $('sBarLabel').addEventListener('change', (e) => queueSave({bar_label: e.target.value}, true));

  // ---------- how long a red peak stays on the bar ----------
  const PRESETS = ['60', '300', '600', '900', '0'];
  function fillTimeout() {
    const v = String(Math.round(settings.bar_info_timeout_s));
    const custom = !PRESETS.includes(v);
    if (document.activeElement !== $('sInfoCustom')) {
      $('sInfoTimeout').value = custom ? 'custom' : v;
      $('sInfoCustom').value = Math.max(1, Math.round(settings.bar_info_timeout_s / 60));
    }
    $('customTimeoutWrap').hidden = !custom && $('sInfoTimeout').value !== 'custom';
  }
  $('sInfoTimeout').addEventListener('change', (e) => {
    const custom = e.target.value === 'custom';
    $('customTimeoutWrap').hidden = !custom;
    if (custom) { $('sInfoCustom').focus(); queueSave({bar_info_timeout_s: (+$('sInfoCustom').value || 20) * 60}, true); }
    else queueSave({bar_info_timeout_s: +e.target.value}, true);
  });
  $('sInfoCustom').addEventListener('input', (e) => {
    const minutes = Math.round(+e.target.value);
    if (minutes >= 1 && minutes <= 1440) queueSave({bar_info_timeout_s: minutes * 60});
  });

  function renderLoudnessNote() {
    const l = live && live.loudness;
    if (!l) return;
    let text;
    if (l.mode === 'flat') {
      text = 'Switching to perceived loudness, so high-pitched shouts count as loud as they sound: ' +
        `${Math.round(l.progress * 100)}% (it needs about a minute of talking), then your line is moved to match.`;
    } else if (l.switched) {
      text = `Measured the way ears hear it (A-weighted). On switching over, your line moved from ` +
        `${fmtDb(l.switched.from)} to ${fmtDb(l.switched.to)} dB to keep your normal voice the same distance under it.`;
    } else {
      text = 'Measured the way ears hear it (A-weighted), so high-pitched shouts count as loud as they sound.';
    }
    if ($('loudnessNote').textContent !== text) $('loudnessNote').textContent = text;
  }
  $('sAutostart').addEventListener('change', async (e) => {
    try {
      const r = await api('/api/action', {action: e.target.checked ? 'autostart_on' : 'autostart_off'});
      autostartInfo = r.autostart;
      toast(autostartInfo.enabled ? 'Vocalm will start with Windows' : "Vocalm won't start with Windows");
    } catch (err) {
      e.target.checked = !e.target.checked;
      toast("Couldn't change that: " + err.message);
    }
  });

  async function loadDevices() {
    try {
      const r = await api('/api/devices');
      const sel = $('sDevice');
      const opts = r.devices.map((d) => h('option', {value: d, text: d}));
      if (!r.devices.some((d) => d === settings.device_query)) {
        opts.unshift(h('option', {value: settings.device_query, text: settings.device_query || "Windows' default microphone"}));
      }
      sel.replaceChildren(...opts);
      sel.value = settings.device_query;
      $('deviceHint').textContent = r.current ? `Listening to ${r.current}.` : "Couldn't open a microphone yet.";
    } catch (e) { /* keep what's there */ }
  }

  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-action]');
    if (!b) return;
    try {
      await api('/api/action', {action: b.dataset.action});
      const said = {move: 'The bar is following your mouse. Press Ctrl+Alt+B to drop it.', test_nudge: 'Look at your screen',
        reset_position: 'The bar is back in its corner', open_data: 'Opened your data folder'}[b.dataset.action];
      if (said) toast(said);
    } catch (err) { toast(err.message); }
  });

  // Live-page buttons
  $('btnPause').addEventListener('click', () => api('/api/action', {action: live && live.paused ? 'resume' : 'pause'}));
  $('btnOverlay').addEventListener('click', () => api('/api/action', {action: live && live.overlay ? 'overlay_off' : 'overlay_on'}));
  $('btnMove').addEventListener('click', () => api('/api/action', {action: 'move'}));
  $('btnNudge').addEventListener('click', () => api('/api/action', {action: 'test_nudge'}).then(() => toast('Look at your screen')));

  // ---------- calibration ----------
  function renderCalibration() {
    const stage = $('calibStage');
    const c = live ? live.calib : {phase: null, done: [], result: null, error: null};
    const done = c.done || [];
    let step = 1;
    if (c.phase === 'loud' || (done.includes('normal') && !c.result)) step = 2;
    if (c.result) step = 3;
    document.querySelectorAll('#calibSteps li').forEach((li) => {
      const n = +li.dataset.step;
      li.classList.toggle('on', n === step);
      li.classList.toggle('done', n < step);
    });
    const key = JSON.stringify([c.phase, done, !!c.result, c.error, c.phase ? Math.ceil(c.remaining) : 0]);
    if (stage.dataset.key === key) {
      const bar = stage.querySelector('.progress i');
      if (bar && c.phase) bar.style.width = (100 * (1 - c.remaining / c.seconds)).toFixed(1) + '%';
      return;
    }
    stage.dataset.key = key;
    const start = (phase, label) => h('button', {class: 'btn primary', type: 'button', text: label,
      onclick: () => api('/api/calibrate', {action: phase})});
    if (c.error) {
      stage.replaceChildren(h('p', {class: 'big', text: c.error}), start('normal', 'Try again'));
    } else if (c.phase) {
      stage.replaceChildren(
        h('p', {class: 'big', text: c.phase === 'normal' ? 'Talk normally…' : 'Now louder: the volume a friend would wince at…'}),
        h('p', {text: c.phase === 'normal' ? 'Chat like you would on Discord: tell it about your day, count, read something out.'
          : "Not a full scream: the level you'd want to be warned about."}),
        h('div', {class: 'countdown', text: Math.ceil(c.remaining) + ' s'}),
        h('div', {class: 'progress'}, h('i')));
    } else if (c.result) {
      const r = c.result;
      stage.replaceChildren(
        h('div', {class: 'result'},
          h('div', null, h('span', {class: 'label', text: 'Your normal voice'}), h('b', {text: fmtDb(r.normal)})),
          h('div', null, h('span', {class: 'label', text: 'Your too-loud voice'}), h('b', {text: fmtDb(r.loud)})),
          h('div', {class: 'new'}, h('span', {class: 'label', text: 'Your new line'}), h('b', {text: fmtDb(r.threshold)})),
          h('div', null, h('span', {class: 'label', text: 'Your line now'}), h('b', {text: fmtDb(settings.threshold_dbfs)}))),
        r.note ? h('p', {text: r.note}) : null,
        h('div', {class: 'row'},
          h('button', {class: 'btn primary', type: 'button', text: 'Use this line', onclick: async () => {
            await api('/api/settings', {threshold_dbfs: r.threshold, normal_dbfs: r.normal, setup_done: true});
            await api('/api/calibrate', {action: 'reset'});
            const st = await api('/api/state');
            settings = st.settings;
            fillSettings();
            $('welcome').hidden = true;
            toast(`Your line is now ${fmtDb(r.threshold)} dBFS`);
          }}),
          h('button', {class: 'btn', type: 'button', text: 'Try again', onclick: () => api('/api/calibrate', {action: 'normal'})}),
          h('button', {class: 'btn', type: 'button', text: 'Keep my current line', onclick: () => api('/api/calibrate', {action: 'reset'})})));
    } else if (done.includes('normal')) {
      stage.replaceChildren(
        h('p', {class: 'big', text: 'Got your normal voice. Now the loud one.'}),
        h('p', {text: "Next, 5 seconds at the volume a friend would wince at. Not your loudest scream: Vocalm puts your line " +
          'a bit under this, so a full scream would make it too forgiving.'}),
        start('loud', 'Start the loud pass'));
    } else {
      stage.replaceChildren(
        h('p', {class: 'big', text: `Your line is ${fmtDb(settings.threshold_dbfs)} dBFS`}),
        h('p', {text: 'First you talk normally for 10 seconds, then too loudly for 5. Vocalm puts your line between ' +
          "the two (at least 6 dB above your normal voice). Loud moments aren't counted while you calibrate."}),
        start('normal', 'Start'));
    }
  }

  // ================= startup =================
  if (BOOT.firstRun) {
    $('welcome').hidden = false;
    const lg = BOOT.legacy;
    if (lg && lg.events) {
      $('welcomeText').textContent = `Vocalm imported your ${fmtNum(lg.events)} loud moments and ${fmtNum(lg.sessions)} ` +
        `runs from the old Loud Mic script, and its settings: your line is still ${fmtDb(settings.threshold_dbfs)} dBFS. ` +
        'The bar on your screen works as before, with a tray icon now and this dashboard at 127.0.0.1:' + BOOT.port +
        '. Recalibrate if you like, or carry on.';
    }
  }
  $('welcomeDone').addEventListener('click', async () => {
    await api('/api/action', {action: 'finish_setup'});
    $('welcome').hidden = true;
  });

  async function checkClipTrove() {
    try {
      const r = await api('/api/cliptrove');
      $('cliptrove').hidden = !r.running;
      $('cliptrove').href = r.url;
    } catch (e) { /* ignore */ }
  }

  readColors();
  connect();
  show();
  checkClipTrove();
  setInterval(checkClipTrove, 60000);
  setInterval(() => { if (route() === 'live') loadToday(); }, 30000);
  requestAnimationFrame(frame);
})();
