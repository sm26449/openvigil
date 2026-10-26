/* ====================================================================
   Inregistrari: redare, timeline cu zoom, marcaje, export, analitica pe cadru (regiune, heatmap, time-lapse, masca)
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, DET_RO, GO2RTC, HD_MODE, JOB_STARE_RO, html, icon, lista, runJob, streamEl, t, toast } from './00-core.js';
import { pbHash, setHash } from './05-navigare.js';
import { loadCameras, post } from './10-live.js';
import { hdCam } from './20-hd.js';
import { AL_ICO, EV_TIP_RO, alNume, evCompact, openCases } from './40-evenimente.js';
import { hdBufferTarget, hdHeightParam } from './60-setari.js';
/* ---------- PLAYBACK ---------- */
function buildPbCams() {
  const sel = $('#pb-cam');
  sel.innerHTML = '';
  for (const c of S.cameras) {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = `${c.id}. ${c.name}` + (c.playback === false ? ' ' + t('(fără înregistrări)') : '');
    sel.appendChild(o);
  }
  $('#pb-date').value = localDay();
}

const pad = (n) => String(n).padStart(2, '0');
// ziua LOCALA ca YYYY-MM-DD. toISOString() dadea ziua UTC: intre 00:00 si 02:59 ora
// Romaniei interfata deschidea "ieri" (audit, Etapa 2).
function localDay(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
// "2026-06-01T10:20:30Z" -> secunde de la miezul noptii
function secOfDay(iso) {
  const m = iso.match(/T(\d{2}):(\d{2}):(\d{2})/);
  return m ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) : 0;
}
// data (YYYY-MM-DD) + secunde -> "20260601T102030Z"
function compact(date, sec) {
  sec = Math.max(0, Math.min(86399, Math.floor(sec)));
  const h = pad(Math.floor(sec / 3600)), mi = pad(Math.floor(sec % 3600 / 60)), s = pad(sec % 60);
  return date.replace(/-/g, '') + 'T' + h + mi + s + 'Z';
}

let curSegments = [];
async function searchRecordings() {
  const ch = +$('#pb-cam').value;
  const date = $('#pb-date').value;
  $('#pb-info').textContent = t('Caut...');
  const url = `/api/recordings?channel=${ch}&start=${date}T00:00:00Z&end=${date}T23:59:59Z`;
  const r = await fetch(url);
  const d = await r.json();
  if (d.error) { $('#pb-info').textContent = t('Eroare: {err}', { err: d.error }); return; }
  curSegments = d.segments || [];
  $('#pb-info').textContent = t('{n} segmente înregistrate', { n: d.count });
  renderTimeline(date);
  drawEventDots(ch, date);
  drawPbMarks(ch, date);
}

// marcaje pe timeline + lista laterala de evenimente (navigabila cu ⏮ ⏭)
let pbEvents = [];
async function drawEventDots(ch, date) {
  const tl = $('#timeline'), list = $('#pb-evlist');
  list.innerHTML = '';
  pbEvents = [];
  try {
    const items = (await (await fetch(`/api/alarms?date=${date}`)).json())
      .filter((i) => i.cam === ch);
    pbEvents = items.map((it) => {
      const [h, m, s] = it.time.split(':').map(Number);
      return { ...it, sec: h * 3600 + m * 60 + s };
    }).sort((a, b) => a.sec - b.sec);
    for (const ev of pbEvents) {
      const dot = document.createElement('div');
      // trei niveluri vizuale: miscare bruta, regula de camera (linie/zona), obiect detectat
      dot.className = 'evdot' + (DET_RO[ev.label] ? ' det'
        : ev.label === 'VMD' ? '' : ' regula');
      dot.dataset.sec = ev.sec;
      dot.title = t('{ora} — {ev} (click = redă)', { ora: ev.time, ev: t(alNume(ev.label)) });
      dot.onclick = (e) => { e.stopPropagation(); playFrom(date, ev.sec); };
      tl.appendChild(dot);
      const row = document.createElement('div');
      row.className = 'ev-row';
      row.innerHTML = html`<img loading="lazy" src="${ev.url}"><span>${ev.time}<br>` +
                      html`<b>${AL_ICO[ev.label] || '⚠'} ${t(alNume(ev.label))}</b></span>`;
      row.onclick = () => playFrom(date, ev.sec);
      ev.row = row;
      list.appendChild(row);
    }
    if (!pbEvents.length) list.innerHTML = html`<p class="hint">${t('Niciun eveniment in ziua asta.')}</p>`;
  } catch {}
  relayoutTimeline();
}

// navigare: eveniment anterior/urmator si sarituri fine fata de pozitia curenta
let pbPos = null, pbDate = null, pbStart = null, pbPaused = false;
const pbVideos = () => [...$('#player').querySelectorAll('video')];
function pbJump(sec) {
  if (pbDate === null) return;
  pbSeek(sec);
}
// Saritura: daca tinta e deja in buffer-ul playerului (MSE tine ultimele zeci de secunde),
// mutam doar currentTime — instant, fara flux nou. Altfel pornim redarea de la secunda ceruta.
function pbSeek(sec) {
  sec = Math.max(0, Math.min(86399, sec));
  const vs = pbVideos();
  if (vs.length && pbStart !== null) {
    const off = (sec - pbStart) / pbSpeed;
    const inBuf = (v) => { const b = v.buffered; return b.length && off >= b.start(0) && off <= b.end(b.length - 1) - 0.2; };
    if (vs.every(inBuf)) { for (const v of vs) v.currentTime = off; return; }
  }
  playFrom(pbDate, sec);
}
function pbTogglePause(force) {
  const vs = pbVideos();
  if (!vs.length) return;
  pbPaused = force ?? !pbPaused;
  for (const v of vs) {
    // pauza lipicioasa: elementul video-stream cheama play() singur la reconectare;
    // cat timp utilizatorul a vrut pauza, ramane pauza
    if (!v._pbHook) { v._pbHook = true; v.addEventListener('play', () => { if (pbPaused) v.pause(); }); }
    if (pbPaused) v.pause(); else v.play().catch(() => {});
  }
  $('#pb-play').textContent = pbPaused ? t('▶ Redă') : t('⏸ Pauză');
  $('#pb-play').classList.toggle('paused', pbPaused);
}
// cadru cu cadru: pauza + un pas de 1/25 s in buffer (camerele inregistreaza la 25 fps)
function pbFrame(dir) {
  const vs = pbVideos();
  if (!vs.length) return;
  pbTogglePause(true);
  for (const v of vs) {
    const t = v.currentTime + dir / 25, b = v.buffered;
    if (b.length && t >= b.start(0) && t <= b.end(b.length - 1)) v.currentTime = t;
  }
}
const PB_SPEEDS = [0.5, 1, 2, 4, 8];
function pbSetSpeed(sp) {
  pbSpeed = sp;
  for (const x of $('#pb-speed').querySelectorAll('button')) x.classList.toggle('active', +x.dataset.sp === sp);
  if (pbDate !== null) playFrom(pbDate, pbPos);   // reia din acelasi moment, cu noua viteza
}
$('#pb-play').onclick = () => pbTogglePause();
$('#pb-f-prev').onclick = () => pbFrame(-1);
$('#pb-f-next').onclick = () => pbFrame(1);
$('#pb-link').onclick = async () => {
  if (pbDate === null) { toast(t('Pornește întâi redarea.')); return; }
  const url = `${location.origin}/${pbHash()}`;
  try { await navigator.clipboard.writeText(url); toast(t('Legătura copiată: {url}', { url })); }
  catch { prompt(t('Copiază legătura către acest moment:'), url); }   // http in LAN: fara clipboard API
};
// tastatura, ca la un player: Space/K pauza, J/L si sageti ±10s (Shift ±1min), , . cadru,
// [ ] viteza, + − zoom pe bara, M marcaj. Doar in tabul Inregistrari, fara overlay-uri,
// si nu cand scrii intr-un camp.
document.addEventListener('keydown', (e) => {
  if ($('#pb').classList.contains('hidden') || !$('#hd-overlay').classList.contains('hidden')
      || !$('#clip-modal').classList.contains('hidden') || !$('#cases-modal').classList.contains('hidden')) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key;
  if (k === '+' || k === '=') { tlSetView(tlView.span / 2, pbPos ?? undefined); return; }
  if (k === '-' || k === '_') { tlSetView(tlView.span * 2, pbPos ?? undefined); return; }
  if (pbDate === null) return;
  if (k === ' ' || k === 'k' || k === 'K') { e.preventDefault(); pbTogglePause(); }
  else if (k === 'j' || k === 'J') pbSeek(pbPos - 10);
  else if (k === 'l' || k === 'L') pbSeek(pbPos + 10);
  else if (k === 'ArrowLeft') { e.preventDefault(); pbSeek(pbPos - (e.shiftKey ? 60 : 10)); }
  else if (k === 'ArrowRight') { e.preventDefault(); pbSeek(pbPos + (e.shiftKey ? 60 : 10)); }
  else if (k === ',') pbFrame(-1);
  else if (k === '.') pbFrame(1);
  else if (k === '[' || k === ']') {
    const i = PB_SPEEDS.indexOf(pbSpeed) + (k === '[' ? -1 : 1);
    if (i >= 0 && i < PB_SPEEDS.length) pbSetSpeed(PB_SPEEDS[i]);
  }
  else if (k === 'm' || k === 'M') $('#pb-mark').click();
});
$('#pb-ev-prev').onclick = () => {
  const prev = [...pbEvents].reverse().find((e) => e.sec < (pbPos ?? 86400) - 2);
  if (prev) pbJump(prev.sec); else $('#pb-info').textContent = t('Nu mai sunt evenimente înainte.');
};
$('#pb-ev-next').onclick = () => {
  const next = pbEvents.find((e) => e.sec > (pbPos ?? -1) + 2);
  if (next) pbJump(next.sec); else $('#pb-info').textContent = t('Nu mai sunt evenimente după.');
};
for (const b of document.querySelectorAll('#pb-nav button[data-seek]')) {
  b.onclick = () => { if (pbPos !== null) pbJump(pbPos + (+b.dataset.seek)); };
}

/* ---------- marcaje (bookmarks): momente cu eticheta, pe timeline ---------- */
// "2026-10-23 10:20:30" -> secunde de la miezul noptii, taiat la ziua `date` (un marcaj-
// interval care trece de miezul noptii se vede pana la capatul barei)
function tSec(t, date, capat = 0) {
  if (t.slice(0, 10) < date) return 0;
  if (t.slice(0, 10) > date) return 86399;
  const [h, m, s] = t.slice(11, 19).split(':').map(Number);
  return h * 3600 + m * 60 + s || capat;
}
async function loadMarks(cam, date, tl, list, onPlay) {
  let marks = [];
  try {
    marks = (await (await fetch(`/api/marcaje?cam=${cam}&day=${date}`)).json()).marcaje || [];
  } catch {}
  for (const el of tl.querySelectorAll('.marcaj')) el.remove();
  for (const m of marks) {
    const a = tSec(m.t, date), b = tSec(m.t_end, date);
    const el = document.createElement('div');
    el.className = 'marcaj' + (m.durata > 0 ? ' interval' : '');
    el.dataset.sec = a;
    el.style.left = (a / 86400 * 100) + '%';
    if (m.durata > 0) { el.dataset.end = b; el.style.width = Math.max(0.3, (b - a) / 86400 * 100) + '%'; }
    el.title = `🔖 ${m.t.slice(11, 19)}${m.durata > 0 ? ' – ' + m.t_end.slice(11, 19) : ''}`
      + (m.eticheta ? ' — ' + m.eticheta : '') + (m.nota ? '\n' + m.nota : '') + ' ' + t('(click = redă)');
    el.onclick = (e) => { e.stopPropagation(); onPlay(a); };
    el.onpointerdown = (e) => e.stopPropagation();   // sa nu porneasca selectia de export
    tl.appendChild(el);
  }
  if (!list) return;
  list.innerHTML = lista(marks, (m) => html`
    <div class="mk-row" data-id="${m.id}" title="${m.nota || ''}">
      <span>🔖 ${m.t.slice(11, 16)}${m.durata > 0 ? '–' + m.t_end.slice(11, 16) : ''}</span>
      <b>${m.eticheta || t('(fără etichetă)')}</b>
      <span class="mk-acts">
        <button class="mk-dosar link-btn" title="${t('Pune marcajul la un dosar de caz')}">${t('dosar')}</button>
        <button class="mk-del link-btn" title="${t('Șterge marcajul')}">✕</button>
      </span>
    </div>`, html`<p class="hint">${t('Niciun marcaj. Apasă „Marcaj” în timpul redării.')}</p>`);
  for (const row of list.querySelectorAll('.mk-row')) {
    const m = marks.find((x) => x.id === row.dataset.id);
    row.onclick = () => onPlay(tSec(m.t, date));
    row.querySelector('.mk-dosar').onclick = (e) => {
      e.stopPropagation();
      // un moment punctual devine o proba de ±15s in jurul lui
      S.pendingProba = { cam: m.cam, start: evCompact(m.t, m.durata > 0 ? 0 : -15),
                       end: evCompact(m.t_end, m.durata > 0 ? 0 : 15),
                       nota: m.eticheta || t('marcaj'), event_id: 'marcaj:' + m.id };
      openCases(true);
    };
    row.querySelector('.mk-del').onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(t('Ștergi marcajul?'))) return;
      await fetch(`/api/marcaje/${m.id}`, { method: 'DELETE' });
      loadMarks(cam, date, tl, list, onPlay);
    };
  }
}
async function addMark(cam, date, sec, reload) {
  const eticheta = prompt(t('Marcaj la {ora} — eticheta (ex. „mașină gri la poartă”):', { ora: hms(sec) }));
  if (eticheta === null) return;
  const r = await post('/api/marcaje', { cam, start: `${date} ${hms(sec)}`, eticheta });
  if (r && r.id) { toast(t('Marcaj pus la {ora}.', { ora: hms(sec) })); reload(); }
}
function drawPbMarks(ch, date) {
  loadMarks(ch, date, $('#timeline'), $('#pb-marks'), (sec) => playFrom(date, sec)).then(relayoutTimeline);
}
async function addMarkInterval(cam, date, a, b) {
  const eticheta = prompt(t('Marcaj {a} – {b} — eticheta:', { a: hms(a), b: hms(b) }));
  if (eticheta === null) return;
  const r = await post('/api/marcaje', { cam, start: `${date} ${hms(a)}`, end: `${date} ${hms(b)}`, eticheta });
  if (r && r.id) { toast(t('Marcaj pus pe interval.')); drawPbMarks(cam, date); }
}
$('#pb-mark').onclick = () => {
  if (pbPos === null || !pbDate) { toast(t('Pornește întâi redarea: alege un moment pe timeline.')); return; }
  const ch = +$('#pb-cam').value, date = pbDate;
  addMark(ch, date, Math.floor(pbPos), () => drawPbMarks(ch, date));
};

/* ---------- zoom digital (scroll / pinch / drag) pe orice player ---------- */
// Aplicat pe containerul playerului: transform pe elementul video, cu limitare la margini
// ca imaginea sa nu poata fi trasa in afara cadrului. Merge si pe live, si pe playback.
function attachDigitalZoom(container) {
  if (container._dzoom) return;
  container._dzoom = true;
  const st = { z: 1, x: 0, y: 0, drag: null, pinch: null };

  const target = () => container.querySelector('video');
  const apply = () => {
    const v = target();
    if (!v) return;
    const r = container.getBoundingClientRect();
    // limitele: la zoom z, imaginea poate fi mutata cu cel mult (z-1)/2 din dimensiune
    const mx = Math.max(0, (st.z - 1) * r.width / 2);
    const my = Math.max(0, (st.z - 1) * r.height / 2);
    st.x = Math.max(-mx, Math.min(mx, st.x));
    st.y = Math.max(-my, Math.min(my, st.y));
    v.style.transformOrigin = 'center center';
    v.style.transform = `translate(${st.x}px, ${st.y}px) scale(${st.z})`;
    v.style.cursor = st.z > 1 ? 'grab' : '';
    container.classList.toggle('zoomed', st.z > 1);
    let badge = container.querySelector('.zbadge');
    if (st.z > 1) {
      if (!badge) {
        badge = document.createElement('div');
        badge.className = 'zbadge';
        badge.title = t('Dublu-click sau Escape = revenire');
        container.appendChild(badge);
      }
      badge.innerHTML = html`${icon('search', 12)} ${st.z.toFixed(1)}×`;
    } else if (badge) badge.remove();
  };
  const reset = () => { st.z = 1; st.x = 0; st.y = 0; apply(); };
  container._dzoomReset = reset;

  // zoom cu rotita, centrat pe pozitia cursorului
  container.addEventListener('wheel', (e) => {
    if (!target()) return;
    e.preventDefault();
    const prev = st.z;
    st.z = Math.max(1, Math.min(6, st.z * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
    const r = container.getBoundingClientRect();
    const cx = e.clientX - r.left - r.width / 2;
    const cy = e.clientY - r.top - r.height / 2;
    const k = st.z / prev;
    st.x = (st.x - cx) * k + cx;
    st.y = (st.y - cy) * k + cy;
    if (st.z === 1) { st.x = 0; st.y = 0; }
    apply();
  }, { passive: false });

  // tragere cu mouse-ul cand e marit
  container.addEventListener('mousedown', (e) => {
    if (st.z <= 1 || !target()) return;
    e.preventDefault();
    st.drag = { x: e.clientX - st.x, y: e.clientY - st.y };
  });
  window.addEventListener('mousemove', (e) => {
    if (!st.drag) return;
    st.x = e.clientX - st.drag.x;
    st.y = e.clientY - st.drag.y;
    apply();
  });
  window.addEventListener('mouseup', () => { st.drag = null; });
  container.addEventListener('dblclick', (e) => { e.preventDefault(); reset(); });

  // pinch + drag pe touch
  const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  container.addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) st.pinch = { d: dist(e.touches), z: st.z };
    else if (e.touches.length === 1 && st.z > 1)
      st.drag = { x: e.touches[0].clientX - st.x, y: e.touches[0].clientY - st.y };
  }, { passive: true });
  container.addEventListener('touchmove', (e) => {
    if (st.pinch && e.touches.length === 2) {
      e.preventDefault();
      st.z = Math.max(1, Math.min(6, st.pinch.z * dist(e.touches) / st.pinch.d));
      if (st.z === 1) { st.x = 0; st.y = 0; }
      apply();
    } else if (st.drag && e.touches.length === 1) {
      e.preventDefault();
      st.x = e.touches[0].clientX - st.drag.x;
      st.y = e.touches[0].clientY - st.drag.y;
      apply();
    }
  }, { passive: false });
  container.addEventListener('touchend', () => { st.pinch = null; st.drag = null; });
}
// Escape iese din zoom (inainte de a inchide overlay-ul)
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  for (const el of [$('#hd-player'), $('#player')]) {
    if (el && el.classList.contains('zoomed')) { el._dzoomReset(); e.stopPropagation(); return; }
  }
}, true);

/* screenshot: cadrul curent din player (playback) sau cadrul nativ live (HD) */
function shotFromVideo(v, name) {
  if (!v || !v.videoWidth) { alert(t('Nu există imagine de salvat încă.')); return; }
  const cv = document.createElement('canvas');
  cv.width = v.videoWidth;
  cv.height = v.videoHeight;
  cv.getContext('2d').drawImage(v, 0, 0);
  cv.toBlob((b) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }, 'image/jpeg', 0.92);
}
const stamp = () => new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
$('#pb-shot').onclick = () => {
  const cam = $('#pb-cam').selectedOptions[0]?.textContent.split('.')[0] || 'cam';
  shotFromVideo($('#player').querySelector('video'), `cam${cam}_${stamp()}.jpg`);
};

/* ---------- timeline cu zoom: fereastra vizibila din zi ----------
   Bara arata [start, start+span) secunde din zi; 24h intreg sau 6h/1h/15min in jurul unui
   moment. Fiecare element de pe bara (segment, punct de eveniment, marcaj) isi tine
   secunda(ele) in data-sec/data-end, iar relayoutTimeline() le aseaza — asa zoom-ul nu
   trebuie sa stie cine a desenat ce. 1 px ≈ 220 s pe telefon la 24h; la 15 min ≈ 2 s. */
const tlView = { start: 0, span: 86400, manualLa: 0 };
const tlX = (sec) => ((sec - tlView.start) / tlView.span * 100) + '%';
function tlSetView(span, center, auto = false) {
  if (!auto) tlView.manualLa = Date.now();   // utilizatorul a ales fereastra: n-o mutam dupa cursor imediat
  span = Math.max(300, Math.min(86400, span));
  const mid = center ?? tlView.start + tlView.span / 2;
  tlView.span = span;
  tlView.start = Math.max(0, Math.min(86400 - span, mid - span / 2));
  for (const b of document.querySelectorAll('#tl-zoom button'))
    b.classList.toggle('active', +b.dataset.span === span);
  relayoutTimeline();
}
function relayoutTimeline() {
  const tl = $('#timeline');
  const { start, span } = tlView, end = start + span;
  for (const el of tl.children) {
    if (el.dataset.sec === undefined) continue;
    const a = +el.dataset.sec, b = el.dataset.end !== undefined ? +el.dataset.end : null;
    const vis = b === null ? (a >= start && a <= end) : (b >= start && a <= end);
    el.style.display = vis ? '' : 'none';
    if (!vis) continue;
    el.style.left = tlX(Math.max(a, start));
    if (b !== null) el.style.width = Math.max(0.15, (Math.min(b, end) - Math.max(a, start)) / span * 100) + '%';
  }
  if (tl._cursor && tl._cursorSec != null) placeCursor(tl._cursorSec);
  if (tl._sel && tl._selRange) placeSel(...tl._selRange);
  grupeazaPuncte(tl);
  // gradatii: pasul care da cel mult ~12 etichete
  const ticks = $('#ticks');
  ticks.innerHTML = '';
  // cel mai mare pas care da macar 6 etichete (24h -> 3h, 1h -> 10 min, 15 min -> 2 min)
  const step = [10800, 3600, 1800, 900, 600, 300, 120, 60, 30].find((x) => span / x >= 6) || 30;
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
    const sp = document.createElement('span');
    sp.style.left = tlX(t);
    sp.textContent = hms(t).slice(0, step < 60 ? 8 : 5);
    ticks.appendChild(sp);
  }
  const r = $('#tl-range');
  if (r) r.textContent = span >= 86400 ? '' : `${hms(start).slice(0, 5)} – ${hms(end).slice(0, 5)}`;
}
/* punctele mai apropiate de ~7 px se strang intr-o pastila cu numarul lor; clicul pe pastila
   apropie bara in jurul lor, in loc sa porneasca redarea „la nimereala”. */
function grupeazaPuncte(tl) {
  for (const g of tl.querySelectorAll('.evgrup')) g.remove();
  const w = tl.getBoundingClientRect().width || 1000;
  const pxSec = w / tlView.span;
  const dots = [...tl.querySelectorAll('.evdot')].filter((d) => d.style.display !== 'none')
    .map((d) => ({ el: d, sec: +d.dataset.sec })).sort((a, b) => a.sec - b.sec);
  let grup = [];
  const inchide = () => {
    if (grup.length < 2) { grup = []; return; }
    const a = grup[0].sec, b = grup[grup.length - 1].sec, mid = (a + b) / 2;
    for (const d of grup) d.el.style.display = 'none';
    const g = document.createElement('span');
    g.className = 'evgrup';
    g.textContent = grup.length;
    g.title = t('{n} evenimente {a}–{b} — clic pentru a apropia', { n: grup.length, a: hms(a).slice(0, 5), b: hms(b).slice(0, 5) });
    g.style.left = tlX(mid);
    g.onclick = (e) => { e.stopPropagation(); tlSetView(Math.max(300, Math.min(tlView.span / 4, Math.max(900, (b - a) * 2))), mid); };
    tl.appendChild(g);
    grup = [];
  };
  for (const d of dots) {
    // grup nou la o pauza de >7 px sau cand grupul ar depasi ~24 px (o zona densa devine un sir de pastile, nu o pastila de 3 ore)
    if (grup.length && ((d.sec - grup[grup.length - 1].sec) * pxSec > 7 || (d.sec - grup[0].sec) * pxSec > 24)) inchide();
    grup.push(d);
  }
  inchide();
}
function placeCursor(sec) {
  const tlA11y = $('#timeline'); if (tlA11y) { tlA11y.setAttribute('aria-valuenow', Math.round(sec)); tlA11y.setAttribute('aria-valuetext', hms(Math.round(sec))); }
  const tl = $('#timeline'), c = tl._cursor;
  if (!c) return;
  tl._cursorSec = sec;
  const vis = sec >= tlView.start && sec <= tlView.start + tlView.span;
  c.style.display = vis ? 'block' : 'none';
  c.style.left = tlX(sec);
}
function placeSel(a, b) {
  const tl = $('#timeline'), s = tl._sel;
  if (!s) return;
  tl._selRange = [a, b];
  const st = tlView.start, en = st + tlView.span;
  if (b < st || a > en) { s.style.display = 'none'; return; }
  s.style.display = 'block';
  s.style.left = tlX(Math.max(a, st));
  s.style.width = ((Math.min(b, en) - Math.max(a, st)) / tlView.span * 100) + '%';
}
$('#tl-zoom').onclick = (e) => {
  const b = e.target.closest('button[data-span]');
  if (b) tlSetView(+b.dataset.span, pbPos ?? $('#timeline')._cursorSec ?? undefined);
};

function renderTimeline(date) {
  const tl = $('#timeline');
  tl.innerHTML = '';
  tl._cursorSec = null; tl._selRange = null;
  for (const seg of curSegments) {
    const a = secOfDay(seg.start), b = secOfDay(seg.end);
    const bar = document.createElement('div');
    bar.className = 'seg' + (seg.type && seg.type !== 'motion' ? ' continuous' : '');
    bar.dataset.sec = a; bar.dataset.end = b;
    bar.title = `${seg.start.slice(11, 19)} – ${seg.end.slice(11, 19)} (${seg.type || 'rec'})`;
    tl.appendChild(bar);
  }
  const sel = document.createElement('div');
  sel.className = 'tl-sel'; sel.style.display = 'none';
  tl.appendChild(sel);
  tl._sel = sel;
  const cursor = document.createElement('div');
  cursor.className = 'cursor'; cursor.style.display = 'none';
  tl.appendChild(cursor);
  tl._cursor = cursor;
  relayoutTimeline();

  // Un clic porneste redarea; o tragere alege intervalul (export MP4 sau marcaj-interval).
  // Pana acum intervalul se tasta de mana, ora cu ora, ceea ce e ultimul lucru pe care
  // vrei sa-l faci cand tocmai ai gasit ce cautai.
  const secLa = (e) => {
    const r = tl.getBoundingClientRect();
    return Math.max(0, Math.min(86399, tlView.start + (e.clientX - r.left) / r.width * tlView.span));
  };
  let dragDe = null;
  tl.onpointerdown = (e) => {
    if (e.target.classList.contains('evdot') || e.target.classList.contains('evgrup')) return;   // clic pe un eveniment/grup, nu tragere
    dragDe = secLa(e);
    tl.setPointerCapture(e.pointerId);
  };
  tl.onpointermove = (e) => {
    if (dragDe === null) return;
    const s = secLa(e), a = Math.min(dragDe, s), b = Math.max(dragDe, s);
    if (b - a < Math.max(2, tlView.span / 400)) return;
    placeSel(a, b);
    $('#pb-info').textContent = t('Selectat {a} – {b} ({min} min)', { a: hms(a), b: hms(b), min: Math.round((b - a) / 60) });
  };
  tl.onpointerup = (e) => {
    if (dragDe === null) return;
    const s = secLa(e), a = Math.min(dragDe, s), b = Math.max(dragDe, s);
    dragDe = null;
    if (b - a < Math.max(2, tlView.span / 400)) {          // a fost un clic, nu o tragere
      sel.style.display = 'none'; tl._selRange = null;
      placeCursor(s);
      if (pbDate === date) pbSeek(s); else playFrom(date, s);
      return;
    }
    const b2 = Math.min(a + 1800, b);                      // exportul e plafonat la 30 min
    $('#exp-a').value = hms(a);
    $('#exp-b').value = hms(b2);
    $('#pb-info').innerHTML = html`${t('Selectat {a} – {b} ({min} min)', { a: hms(a), b: hms(b2), min: Math.round((b2 - a) / 60) })}`
      + (b - a > 1800 ? html` <span class="hint">${t('(tăiat la 30 min, cât permite exportul)')}</span>` : '')
      + html`: <button class="link-btn" id="sel-mp4">⬇ MP4</button> <button class="link-btn" id="sel-mark">${t('🔖 marcaj')}</button>`;
    $('#sel-mp4').onclick = () => $('#exp-btn').click();
    $('#sel-mark').onclick = () => addMarkInterval(+$('#pb-cam').value, date, Math.floor(a), Math.floor(b2));
  };
  // rotita: zoom in jurul pointerului; cu Shift deruleaza fereastra
  tl.onwheel = (e) => {
    e.preventDefault();
    if (e.shiftKey) { tlSetView(tlView.span, tlView.start + tlView.span / 2 + Math.sign(e.deltaY) * tlView.span * 0.2); return; }
    tlSetView(e.deltaY < 0 ? tlView.span / 2 : tlView.span * 2, secLa(e));
  };
}

const hms = (sec) => `${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec % 3600 / 60))}:${pad(Math.floor(sec % 60))}`;

let pbSpeed = 1;
// Camerele suplimentare redate in acelasi moment. Plafonul e masurat, nu ales din burta:
// 9 fluxuri concomitente merg la 2.6x fata de timpul real pe masina asta, deci 3x3 incape
// cu marja chiar si peste inregistrare si detectie.
const PB_MAX = 9;
const pbAspect = {};   // cam -> 'w / h', invatat de la primul cadru; cutia playerului nu sare intre fluxuri
S.pbExtra = [];

function renderMultiList() {
  const box = $('#pb-multi-list');
  const primar = +$('#pb-cam').value;
  box.innerHTML = html`<b>${t('Redate în același moment')}</b>${S.cameras.map((c) => {
    if (c.id === primar) return html`<span class="mc primar">${c.name} <i>${t('principala')}</i></span>`;
    const on = S.pbExtra.includes(c.id);
    return html`<button class="mc${on ? ' on' : ''}" data-cam="${c.id}">${c.name}</button>`;
  })}<span class="mc-info">${t('{n} din {max}', { n: S.pbExtra.length + 1, max: PB_MAX })}</span>`;
  for (const b of box.querySelectorAll('button[data-cam]')) {
    b.onclick = () => {
      const id = +b.dataset.cam;
      if (S.pbExtra.includes(id)) S.pbExtra = S.pbExtra.filter((x) => x !== id);
      else if (S.pbExtra.length + 1 >= PB_MAX) { toast(t('Maximum {n} camere odată', { n: PB_MAX })); return; }
      else S.pbExtra.push(id);
      renderMultiList();
      if (pbDate) playFrom(pbDate, pbPos);
    };
  }
}
$('#pb-multi').onclick = () => {
  const box = $('#pb-multi-list');
  box.classList.toggle('hidden');
  $('#pb-multi').classList.toggle('active', !box.classList.contains('hidden'));
  if (!box.classList.contains('hidden')) renderMultiList();
};

async function playFrom(date, sec) {
  const primar = +$('#pb-cam').value;
  // clic dupa ultimul material al zilei: spunem imediat, fara sa pornim fluxuri goale
  const ultim = curSegments.length ? secOfDay(curSegments[curSegments.length - 1].end) : 0;
  if (curSegments.length && sec > ultim + 60) {
    $('#pb-info').textContent = t('Nu există înregistrare la {ora} — ultima e la {ultima}.',
                                    { ora: hms(sec).slice(0, 5), ultima: hms(ultim).slice(0, 5) });
    return;
  }
  const toate = [primar, ...S.pbExtra.filter((x) => x !== primar)];
  const start = compact(date, sec);
  const end = date.replace(/-/g, '') + 'T235959Z';
  // pre-completeaza intervalul de export: de la momentul redat, 2 minute
  $('#exp-a').value = hms(sec);
  $('#exp-b').value = hms(Math.min(86399, sec + 120));
  const ora = `${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec % 3600 / 60))}`;
  $('#pb-info').textContent = t('Redau de la {ora}', { ora })
    + (toate.length > 1 ? ' ' + t('pe {n} camere', { n: toate.length }) : '')
    + (pbSpeed !== 1 ? ' ' + t('cu viteza {viteza}x (fără sunet)', { viteza: pbSpeed }) : '') + '...';
  pbPaused = false;
  $('#pb-play').textContent = t('⏸ Pauză');
  $('#pb-play').classList.remove('paused');

  const wrap = $('#player');
  // nu golim: ce era ramane „vechi” pana cand noul flux da primul cadru (sau 4 s)
  for (const c of wrap.children) c.classList.add('vechi');
  const curataVechi = () => { for (const c of [...wrap.querySelectorAll('.vechi')]) c.remove(); };
  if (toate.length > 1 || wrap.classList.contains('grid')) curataVechi();   // grila: fara suprapunere
  wrap.classList.toggle('grid', toate.length > 1);
  if (toate.length === 1 && pbAspect[primar]) wrap.style.setProperty('--pb-ar', pbAspect[primar]);
  setTimeout(curataVechi, 4000);
  if (toate.length > 1) {
    // patrat cat mai apropiat: 2 camere -> 2 coloane, 5 -> 3, 9 -> 3
    wrap.style.setProperty('--col', Math.ceil(Math.sqrt(toate.length)));
  }

  const nume = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
  const cereri = toate.map((ch) =>
    fetch(`/api/play?channel=${ch}&start=${start}&end=${end}&speed=${pbSpeed}${hdHeightParam()}`)
      .then((r) => r.json()).then((d) => ({ ch, d })).catch(() => ({ ch, d: { error: t('rețea') } })));
  const raspunsuri = await Promise.all(cereri);

  const rele = raspunsuri.filter((x) => x.d.error);
  if (rele.length === raspunsuri.length) {
    $('#pb-info').textContent = t('Eroare: {err}', { err: raspunsuri[0].d.error });
    return;
  }
  for (const { ch, d } of raspunsuri) {
    const dala = document.createElement('div');
    dala.className = 'pb-dala';
    if (d.error) {
      dala.innerHTML = html`<div class="pb-gol">${nume[ch] || t('cam {id}', { id: ch })}<small>${d.error}</small></div>`;
      wrap.appendChild(dala);
      continue;
    }
    if (toate.length > 1) {
      const et = document.createElement('span');
      et.className = 'pb-eticheta';
      et.textContent = nume[ch] || t('cam {id}', { id: ch });
      dala.appendChild(et);
    }
    // primul cadru al noului flux: retinem raportul camerei (cutia nu mai sare data viitoare)
    // si abia atunci scoatem videoul vechi
    dala.addEventListener('loadeddata', (ev) => {
      const v = ev.target;
      if (v.videoWidth && v.videoHeight) {
        pbAspect[ch] = `${v.videoWidth} / ${v.videoHeight}`;
        if (toate.length === 1) wrap.style.setProperty('--pb-ar', pbAspect[ch]);
      }
      curataVechi();
    }, true);
    // mic delay ca go2rtc sa porneasca ffmpeg-ul; MSE (native 6MP, WebRTC ar refuza)
    setTimeout(() => dala.appendChild(streamEl(d.stream, HD_MODE, false, hdBufferTarget())), 400);
    wrap.appendChild(dala);
  }
  if (rele.length) {
    $('#pb-info').textContent += ' ' + t('({n} fără material la ora asta)', { n: rele.length });
  }
  if (toate.length === 1) {
    attachDigitalZoom(wrap);
    if (wrap._dzoomReset) wrap._dzoomReset();  // porneste de la 1x pe fiecare redare noua
  }
  startCursor(date, sec);
}

/* cursorul avanseaza in timpul redarii, urmarind timpul din player */
let curTimer = null;
let curUltimVizibil = true;
function startCursor(date, sec0) {
  pbDate = date;
  pbPos = sec0;
  pbStart = sec0;
  setHash(pbHash());
  clearInterval(curTimer);
  const t0 = Date.now();
  let tic = 0;
  curTimer = setInterval(() => {
    const v = $('#player').querySelector('video');
    // timpul redat: preferam currentTime al playerului (sta pe loc in pauza); altfel ceasul
    const played = v && v.currentTime > 0 ? v.currentTime : (Date.now() - t0) / 1000;
    // la viteza marita, o secunda redata inseamna pbSpeed secunde de material
    pbPos = sec0 + played * pbSpeed;
    placeCursor(pbPos);
    // bara marita urmareste cursorul: cand IESE din fereastra (era inauntru la tic-ul trecut),
    // fereastra se muta dupa el — dar nu in primele secunde dupa ce utilizatorul a ales singur
    // alta fereastra (zoom pe un grup de evenimente, derulare), ca sa nu-i fuga bara de sub ochi
    const vizibil = pbPos >= tlView.start && pbPos <= tlView.start + tlView.span * 0.98;
    if (tlView.span < 86400 && !vizibil && curUltimVizibil && Date.now() - tlView.manualLa > 8000)
      tlSetView(tlView.span, pbPos + tlView.span * 0.3, true);
    curUltimVizibil = vizibil;
    $('#pb-pos').textContent = hms(pbPos);
    // adresa reflecta momentul (link partajabil) — doar cat timp tabul Inregistrari e pe ecran
    if (++tic % 10 === 0 && !$('#pb').classList.contains('hidden') && !hdCam) setHash(pbHash());
    // evidentiaza in lista evenimentul din dreptul cursorului
    for (const ev of pbEvents) {
      if (ev.row) ev.row.classList.toggle('now', Math.abs(ev.sec - pbPos) < 8);
    }
  }, 500);
}

/* ---------- export MP4 ---------- */
$('#exp-btn').onclick = () => {
  const ch = +$('#pb-cam').value;
  const date = $('#pb-date').value;
  const a = $('#exp-a').value, b = $('#exp-b').value;
  if (!date || !a || !b) { alert(t('Alege data si intervalul (de la / pana la).')); return; }
  const c = (t) => date.replace(/-/g, '') + 'T' + t.replace(/:/g, '').padEnd(6, '0') + 'Z';
  const secs = (t) => t.split(':').reduce((x, y) => x * 60 + +y, 0);
  const dur = secs(b) - secs(a);
  if (dur <= 0 || dur > 1800) { alert(t('Interval invalid (max 30 minute).')); return; }
  $('#pb-info').textContent = t('Export în coada... (la camerele NVR durează ~cât clipul)');
  const wm = $('#exp-wm') && $('#exp-wm').checked ? '&watermark=1' : '';
  runJob(`/api/export?channel=${ch}&start=${c(a)}&end=${c(b)}${wm}`, {}, {
    onProgress: (j) => {
      $('#pb-info').textContent = j.stare === 'gata' ? t('Export gata — se descarcă.')
        : j.stare === 'eroare' ? t('Export eșuat: {mesaj}', { mesaj: j.mesaj })
        : t('Export: {stare}', { stare: j.mesaj || t(JOB_STARE_RO[j.stare] || j.stare) }) + (j.progres ? ' ' + j.progres + '%' : '');
    },
  });
};

/* ---------- Etapa 5: analitica retroactiva pe cadru ----------
   Un canvas cu cadrul camerei (video-ul redat, altfel o poza live) si un dreptunghi tras cu
   mouse-ul. Regiune: /api/regiune intoarce detectiile zilei ale caror chenare ating
   dreptunghiul. Harta de caldura: /api/heatmap, grila traseelor de oameni desenata peste
   cadru. Masca: dreptunghiurile innegrite, salvate pe camera (/api/cam/<id>/masca). */
function ovFrame(ch, cv, dupa) {
  // cadrul de fundal: video-ul din player (daca reda camera) sau poza live din go2rtc
  const v = pbVideos().find((x) => x.videoWidth);
  if (v && +$('#pb-cam').value === ch && !$('#pb').classList.contains('hidden')) { dupa(v, v.videoWidth, v.videoHeight); return; }
  const im = new Image();
  im.onload = () => dupa(im, im.width, im.height);
  im.onerror = () => dupa(null, 16, 9);
  im.src = `${GO2RTC}/api/frame.jpeg?src=cam${ch}&_=${Date.now()}`;
}
function ovFit(cv, w, h) {
  const body = cv.parentElement, side = body.querySelector('.ov-side');
  const maxW = body.clientWidth - (side ? side.offsetWidth + 16 : 0) - 16, maxH = body.clientHeight - 16;
  const k = Math.min(maxW / w, maxH / h);
  cv.width = Math.max(200, Math.round(w * k)); cv.height = Math.max(120, Math.round(h * k));
}
function ovDrag(cv, onRect, onMove) {
  let a = null;
  const pos = (e) => { const r = cv.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
  cv.onpointerdown = (e) => { e.preventDefault(); cv.setPointerCapture?.(e.pointerId); a = pos(e); };
  cv.onpointermove = (e) => { if (!a) return; const b = pos(e); onMove && onMove(ovRect(a, b)); };
  cv.onpointerup = (e) => { if (!a) return; const r = ovRect(a, pos(e)); a = null; if (r[2] > 0.01 && r[3] > 0.01) onRect(r); else onMove && onMove(null); };
  cv.onpointercancel = () => { a = null; };
}
const ovRect = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])];
function ovDrawRect(g, cv, r, color, fill) {
  g.strokeStyle = color; g.lineWidth = 2;
  if (fill) { g.fillStyle = fill; g.fillRect(r[0] * cv.width, r[1] * cv.height, r[2] * cv.width, r[3] * cv.height); }
  g.strokeRect(r[0] * cv.width, r[1] * cv.height, r[2] * cv.width, r[3] * cv.height);
}

// --- cautare pe regiune ---
let regSrc = null, regRect = null, regHits = [], regCh = 0, regDay = '';
function openRegiune() {
  regCh = +$('#pb-cam').value; regDay = $('#pb-date').value || localDay();
  regRect = null; regHits = [];
  const c = S.cameras.find((x) => x.id === regCh);
  $('#reg-title').textContent = t('Caută pe regiune — {cam}, {zi}', { cam: c ? c.name : t('cam {id}', { id: regCh }), zi: regDay });
  $('#reg-info').textContent = t('Desenează o regiune.'); $('#reg-list').innerHTML = '';
  $('#reg-overlay').classList.remove('hidden');
  ovFrame(regCh, $('#reg-canvas'), (src, w, h) => { regSrc = { src, w, h }; drawRegiune(); });
}
function drawRegiune(temp) {
  const cv = $('#reg-canvas'); if (!regSrc) return;
  ovFit(cv, regSrc.w, regSrc.h);
  const g = cv.getContext('2d');
  g.fillStyle = '#000'; g.fillRect(0, 0, cv.width, cv.height);
  if (regSrc.src) try { g.drawImage(regSrc.src, 0, 0, cv.width, cv.height); } catch {}
  for (const e of regHits) ovDrawRect(g, cv, e.chenar, 'rgba(227,179,65,.9)');
  const r = temp || regRect;
  if (r) ovDrawRect(g, cv, r, '#58a6ff', 'rgba(88,166,255,.18)');
}
ovDrag($('#reg-canvas'), async (r) => {
  regRect = r; drawRegiune();
  $('#reg-info').textContent = t('Caut...');
  try {
    const d = await (await fetch(`/api/regiune?channel=${regCh}&day=${regDay}&x=${r[0].toFixed(4)}&y=${r[1].toFixed(4)}&w=${r[2].toFixed(4)}&h=${r[3].toFixed(4)}`)).json();
    if (d.error) { $('#reg-info').textContent = t('Eroare: {err}', { err: d.error }); return; }
    regHits = d.evenimente || [];
    $('#reg-info').textContent = regHits.length ? t('{n} detecții au atins regiunea', { n: regHits.length }) : t('Nicio detecție în regiune în ziua asta (chenarele există pentru evenimentele de după 2026-10-23).');
    $('#reg-list').innerHTML = lista(regHits, (e) => html`<div class="ev-row" data-sec="${e.sec}"><b>${e.t.slice(11)}</b>${t(EV_TIP_RO[e.tip] || e.tip)}: ${t(DET_RO[e.eticheta] || e.eticheta || '')}</div>`, '');
    for (const row of $('#reg-list').querySelectorAll('.ev-row')) row.onclick = () => { $('#reg-overlay').classList.add('hidden'); playFrom(regDay, +row.dataset.sec); };
    drawRegiune();
    // punctele gasite apar si pe timeline, galbene
    for (const d0 of document.querySelectorAll('#timeline .evdot.reg')) d0.remove();
    for (const e of regHits) { const dot = document.createElement('div'); dot.className = 'evdot reg'; dot.dataset.sec = e.sec; dot.title = t('{ora} — {ce} (regiune)', { ora: e.t.slice(11), ce: e.eticheta || e.tip }); dot.onclick = (ev) => { ev.stopPropagation(); playFrom(regDay, e.sec); }; $('#timeline').appendChild(dot); }
    relayoutTimeline();
  } catch (e) { $('#reg-info').textContent = t('Eroare: {err}', { err: e }); }
}, (r) => drawRegiune(r));
$('#pb-regiune').onclick = openRegiune;
$('#reg-close').onclick = () => $('#reg-overlay').classList.add('hidden');

// --- harta de caldura ---
let heatSrc = null, heatZile = 1, heatCh = 0, heatData = null;
function openHeat() {
  heatCh = +$('#pb-cam').value;
  const c = S.cameras.find((x) => x.id === heatCh);
  $('#heat-title').textContent = t('Harta de căldură — {cam}', { cam: c ? c.name : t('cam {id}', { id: heatCh }) });
  $('#heat-overlay').classList.remove('hidden');
  ovFrame(heatCh, $('#heat-canvas'), (src, w, h) => { heatSrc = { src, w, h }; loadHeat(); });
}
async function loadHeat() {
  $('#heat-info').textContent = t('Calculez...');
  try {
    heatData = await (await fetch(`/api/heatmap?channel=${heatCh}&day=${$('#pb-date').value || localDay()}&zile=${heatZile}`)).json();
    const perioada = heatZile === 1 ? t('ziua aleasă') : t('ultimele {n} zile', { n: heatZile });
    $('#heat-info').textContent = heatData.error ? t('Eroare: {err}', { err: heatData.error })
      : t('{n} episoade de prezență umană în {perioada}', { n: heatData.episoade, perioada }) + (heatData.max ? '' : ' ' + t('— nimic de desenat'));
  } catch (e) { $('#heat-info').textContent = t('Eroare: {err}', { err: e }); }
  drawHeat();
}
function drawHeat() {
  const cv = $('#heat-canvas'); if (!heatSrc) return;
  ovFit(cv, heatSrc.w, heatSrc.h);
  const g = cv.getContext('2d');
  g.fillStyle = '#000'; g.fillRect(0, 0, cv.width, cv.height);
  if (heatSrc.src) try { g.drawImage(heatSrc.src, 0, 0, cv.width, cv.height); } catch {}
  if (!heatData || !heatData.max) return;
  const cw = cv.width / heatData.w, chh = cv.height / heatData.h;
  for (let y = 0; y < heatData.h; y++) for (let x = 0; x < heatData.w; x++) {
    const v = heatData.grid[y][x]; if (!v) continue;
    const f = v / heatData.max, a = 0.15 + 0.75 * Math.sqrt(f);      // radacina: si traseele rare se vad
    g.fillStyle = `rgba(255, ${Math.round(200 - 200 * f)}, 0, ${a.toFixed(2)})`;
    g.fillRect(x * cw, y * chh, cw + 0.5, chh + 0.5);
  }
}
$('#pb-heat').onclick = openHeat;
$('#heat-close').onclick = () => $('#heat-overlay').classList.add('hidden');
$('#heat-zile').onclick = (e) => { const b = e.target.closest('button[data-zile]'); if (!b) return; heatZile = +b.dataset.zile; for (const x of $('#heat-zile').querySelectorAll('button')) x.classList.toggle('on', x === b); loadHeat(); };

// --- time-lapse: ziua intreaga, un cadru pe minut, ca job ---
$('#pb-timelapse').onclick = async () => {
  const ch = +$('#pb-cam').value, zi = $('#pb-date').value || localDay();
  const j = await runJob(`/api/timelapse?channel=${ch}&day=${zi}&pas=60`, {}, { download: true });
  if (j && j.stare === 'gata') toast(t('Time-lapse gata — se descarcă'));
};

// --- masca de confidentialitate (admin, din HD) ---
let maskCam = null, maskRects = [], maskSrc = null;
function openMaskEditor(c) {
  maskCam = c; maskRects = (c.mask || []).map((r) => r.slice());
  $('#mask-title').textContent = t('Masca de confidențialitate — {cam}', { cam: c.name });
  $('#mask-overlay').classList.remove('hidden');
  ovFrame(c.id, $('#mask-canvas'), (src, w, h) => { maskSrc = { src, w, h }; drawMask(); });
}
function drawMask(temp) {
  const cv = $('#mask-canvas'); if (!maskSrc) return;
  ovFit(cv, maskSrc.w, maskSrc.h);
  const g = cv.getContext('2d');
  g.fillStyle = '#000'; g.fillRect(0, 0, cv.width, cv.height);
  if (maskSrc.src) try { g.drawImage(maskSrc.src, 0, 0, cv.width, cv.height); } catch {}
  for (const r of maskRects) ovDrawRect(g, cv, r, '#f85149', 'rgba(0,0,0,.85)');
  if (temp) ovDrawRect(g, cv, temp, '#f85149', 'rgba(248,81,73,.25)');
}
ovDrag($('#mask-canvas'), (r) => { maskRects.push(r.map((v) => +v.toFixed(4))); drawMask(); }, (r) => drawMask(r));
$('#mask-undo').onclick = () => { maskRects.pop(); drawMask(); };
$('#mask-save').onclick = async () => {
  const d = await post(`/api/cam/${maskCam.id}/masca`, { rects: maskRects });
  if (d && !d.error) { maskCam.mask = d.mask; toast(maskRects.length ? t('Masca salvată — fluxurile se refac în câteva secunde') : t('Masca scoasă')); $('#mask-overlay').classList.add('hidden'); loadCameras(); }
};
$('#mask-clear').onclick = async () => {
  const d = await post(`/api/cam/${maskCam.id}/masca`, { rects: null });
  if (d && !d.error) { maskCam.mask = null; maskRects = []; toast(t('Masca scoasă')); $('#mask-overlay').classList.add('hidden'); loadCameras(); }
};
$('#mask-close').onclick = () => $('#mask-overlay').classList.add('hidden');
window.addEventListener('resize', () => { drawRegiune(); drawHeat(); drawMask(); });

export { addMark, attachDigitalZoom, buildPbCams, compact, curSegments, hms, loadMarks, localDay, openMaskEditor, pad, pbDate, pbPos, pbSetSpeed, playFrom, renderMultiList, searchRecordings, secOfDay, shotFromVideo, stamp };
