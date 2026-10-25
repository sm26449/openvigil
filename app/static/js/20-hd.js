/* ====================================================================
   HD: overlay-ul pe o camera (live, time-shift, PTZ, zoom digital, chenare)
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, CAN_ACT, DET_RO, HD_MODE, gridFrame, html, icon, streamEl, talkStop, toast } from './00-core.js';
import { show } from './05-navigare.js';
import { ZOOM_MAX_MS, buildCtrls, post } from './10-live.js';
import { addMark, attachDigitalZoom, compact, hms, loadMarks, localDay, pad, searchRecordings, secOfDay } from './30-redare.js';
import { AL_RO } from './40-evenimente.js';
import { hdBufferTarget, hdHeightParam } from './60-setari.js';
/* ---------- HD on-demand (click pe o camera) ---------- */
let hdCam = null;
let hdPos = null;      // secunda din zi redata in HD (null = live)
/* Meniul suprapus peste imaginea camerei. Arata doar ce stie camera aceea sa faca:
   rotire numai daca s-a dovedit ca se roteste (proba din panoul camerei), zoom numai la
   lentila motorizata, pozitii memorate numai unde camera chiar le pastreaza. Asa nu apar
   butoane care nu fac nimic — capcana in care intrasem deja o data. */
function ptzOverlay(c) {
  if (!CAN_ACT) return null;   // rotirea/zoomul/pozitiile: admin si operator
  const f = c.features || {};
  if (!(f.ptz || f.zoom || f.presets || f.focus)) return null;
  const box = document.createElement('div');
  box.className = 'ptz-ov';

  const trimite = (date) => post(`/api/cam/${c.id}/ptz`, date);
  const tine = (el, date) => {
    let t = null;
    const stop = () => {
      if (t === null) return;
      clearTimeout(t); t = null;
      el.classList.remove('activ');
      trimite({ pan: 0, tilt: 0, zoom: 0 });
    };
    el.onpointerdown = (e) => {
      e.preventDefault(); e.stopPropagation();
      el.setPointerCapture?.(e.pointerId);
      el.classList.add('activ');
      t = setTimeout(stop, ZOOM_MAX_MS);   // acelasi opritor de siguranta ca la zoom
      trimite(date);
    };
    el.onpointerup = stop; el.onpointercancel = stop; el.onlostpointercapture = stop;
    window.addEventListener('blur', stop);
  };

  if (f.ptz) {
    const pad = document.createElement('div');
    pad.className = 'ptz-pad';
    const dir = [['↖', -60, 60], ['↑', 0, 60], ['↗', 60, 60],
                 ['←', -60, 0], ['', 0, 0], ['→', 60, 0],
                 ['↙', -60, -60], ['↓', 0, -60], ['↘', 60, -60]];
    for (const [sg, p, t] of dir) {
      const b = document.createElement('button');
      if (!sg) { b.className = 'gol'; b.disabled = true; } else {
        b.textContent = sg;
        b.title = 'Ține apăsat ca să se rotească';
        tine(b, { pan: p, tilt: t });
      }
      pad.appendChild(b);
    }
    box.appendChild(pad);
  }

  if (f.zoom) {
    const z = document.createElement('div');
    z.className = 'ptz-rand';
    for (const [ic, v, ttl] of [['zoomin', 60, 'Apropie'], ['zoomout', -60, 'Depărtează']]) {
      const b = document.createElement('button');
      b.innerHTML = icon(ic); b.title = ttl + ' (ține apăsat)';
      tine(b, { zoom: v });
      z.appendChild(b);
    }
    box.appendChild(z);
  }

  if (f.presets) {
    const p = document.createElement('div');
    p.className = 'ptz-pre';
    const selPoz = document.createElement('select');
    selPoz.title = 'Poziții memorate';
    const incarca = async () => {
      const ps = await (await fetch(`/api/cam/${c.id}/presets`)).json();
      selPoz.innerHTML = html`<option value="">Poziții memorate…</option>${
        (Array.isArray(ps) ? ps : []).map((x) => html`<option value="${x.id}">${x.nume}</option>`)}`;
    };
    selPoz.onchange = async () => {
      if (!selPoz.value) return;
      await post(`/api/cam/${c.id}/presets`, { id: +selPoz.value });
      toast('Camera se duce pe poziție');
    };
    const add = document.createElement('button');
    add.innerHTML = icon('plus'); add.title = 'Memorează poziția de acum';
    add.onclick = async (e) => {
      e.stopPropagation();
      const nume = prompt('Cum numim poziția asta?', 'Poziție nouă');
      if (!nume) return;
      const r = await post(`/api/cam/${c.id}/presets`, { nume });
      if (r && r.error) { toast('Nu a mers: ' + r.error); return; }
      await incarca();
      toast('Poziție memorată');
    };
    const del = document.createElement('button');
    del.innerHTML = icon('trash'); del.title = 'Șterge poziția aleasă';
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!selPoz.value) { toast('Alege întâi o poziție'); return; }
      await fetch(`/api/cam/${c.id}/presets?id=${selPoz.value}`, { method: 'DELETE' });
      await incarca();
      toast('Poziție ștearsă');
    };
    if (ADMIN) p.append(selPoz, add, del);   // operatorul recheama/salveaza, nu sterge
    else p.append(selPoz, add);
    box.appendChild(p);
    incarca();
  }

  if (f.focus) {
    const fr = document.createElement('div');
    fr.className = 'ptz-focus';
    const sel = document.createElement('select');
    sel.title = 'Cum focalizează camera';
    sel.innerHTML = '<option value="AUTO">Focalizare automata</option>'
      + '<option value="SEMIAUTO">Focalizeaza dupa miscare</option>'
      + '<option value="MANUAL">Focalizare manuala</option>';
    fetch(`/api/cam/${c.id}/focus`).then((r) => r.json()).then((d) => {
      if (d.mod) sel.value = d.mod;
    }).catch(() => {});
    sel.onchange = async () => {
      const r = await post(`/api/cam/${c.id}/focus`, { mod: sel.value });
      toast(r && r.error ? 'Nu a mers: ' + r.error : 'Focalizare schimbată');
    };
    fr.appendChild(sel);
    box.appendChild(fr);
  }
  return box;
}

async function openHD(c, fromRoute = false) {
  const ov = $('#hd-overlay'), wrap = $('#hd-player');
  hdCam = c;
  // intrare in istoric: Inapoi (telefon) inchide overlay-ul, nu paraseste aplicatia
  if (fromRoute) history.replaceState(null, '', '#live');
  if (history.state && history.state.hd) history.replaceState({ hd: c.id }, '', `#hd/${c.id}`);
  else history.pushState({ hd: c.id }, '', `#hd/${c.id}`);
  $('#hd-title').textContent = `${c.id}. ${c.name} — HD`;
  const ctrls = $('#hd-ctrls');
  ctrls.innerHTML = '';
  ctrls.appendChild(buildCtrls(c, wrap, true));
  ov.classList.remove('hidden');
  gridPause(true);          // sub overlay nu se vede grila: 17 fluxuri degeaba
  await hdGoLive();
  loadHdTimeline();
}
// Fluxurile din grila se opresc cat timp HD-ul le acopera si repornesc la inchidere
// (elementele raman in DOM, doar conexiunea se taie: video-rtc.js stie sa se reconecteze).
function gridPause(pauza) {
  for (const v of document.querySelectorAll('#grid video-stream')) {
    try {
      if (pauza && !v._pauzat) { v.disconnectedCallback(); v._pauzat = true; }
      else if (!pauza && v._pauzat) { v.connectedCallback(); v._pauzat = false; }
    } catch {}
  }
}

// live HD (mainstream transcodat). Substream-first: aratam instant substream-ul (deja
// ruleaza in grila, fara ecran verde) si urcam la HD cand acesta are primul cadru —
// mainstream-ul HEVC asteapta un keyframe (~2.5s+, GOP mare), pana atunci ar fi verde.
async function hdGoLive() {
  const wrap = $('#hd-player');
  if (wrap._hdMon) { clearInterval(wrap._hdMon); wrap._hdMon = null; }
  wrap.innerHTML = '';
  wrap.style.position = 'relative';
  // poster instant: ultimul cadru cunoscut (same-origin, cache 20s) — umple ecranul cat
  // timp substream-ul isi negociaza primul cadru, ca sa nu mai fie zona goala/"loading".
  const poster = document.createElement('img');
  poster.src = gridFrame(hdCam.id) || `/api/thumb/${hdCam.id}`;
  poster.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;pointer-events:none';
  wrap.appendChild(poster);
  $('#hd-when').textContent = '';
  $('#hd-live').classList.add('on');
  hdPos = null;
  const d = await (await fetch(`/api/play?channel=${hdCam.id}${hdHeightParam()}`)).json();
  if (d.error) { wrap.innerHTML = html`Eroare: ${d.error}`; return; }
  const sub = streamEl(`cam${hdCam.id}`, 'mse,webrtc');   // substream live: plasa de siguranta
  sub.style.position = 'absolute'; sub.style.inset = '0';
  const hd = streamEl(d.stream, HD_MODE, false, hdBufferTarget());  // HD (mainstream) deasupra
  hd.style.position = 'absolute'; hd.style.inset = '0';
  hd.style.opacity = '0'; hd.style.transition = 'opacity .25s';
  wrap.appendChild(sub);   // substream-ul RAMANE mereu dedesubt (nu-l mai scot)
  wrap.appendChild(hd);
  // Dezvaluim HD-ul O SINGURA DATA, cand a rulat CONTINUU ~1 s. NU mai comutam inapoi la
  // substream daca HD mai are un hopa — oscilatia low<->high era mai deranjanta decat un
  // mic sacadaj. Substream-ul ramane dedesubt ca fundal, dar odata aratat, HD-ul ramane
  // aratat. Gardianul era 3 s pe vremea cand ffmpeg scotea cadre verzi la pornire
  // (nobuffer pierdea IDR-ul — reparat in gen_go2rtc.py); acum primul cadru HD e curat.
  let last = -1, good = 0, ticks = 0;
  wrap._hdMon = setInterval(() => {
    const v = hd.video || hd.querySelector('video');
    if (!v) return;
    ticks++;
    if (v.videoWidth > 0 && v.currentTime > last + 0.02) {
      last = v.currentTime;
      good++;
    } else {
      good = 0;                                   // resetam seria daca se blocheaza
    }
    if (good >= 2) {                              // ~1s de redare CONTINUA -> aratam si gata
      hd.style.opacity = '1';
      clearInterval(wrap._hdMon); wrap._hdMon = null;
    } else if (ticks > 40) {                      // 20s si tot nu s-a stabilizat -> renuntam,
      clearInterval(wrap._hdMon); wrap._hdMon = null;   // ramane substream-ul (fara oscilatie)
    }
  }, 500);
  attachDigitalZoom(wrap);
  const ov2 = ptzOverlay(hdCam);
  if (ov2) wrap.appendChild(ov2);
  if ($('#hd-boxes').classList.contains('on')) startHdBoxes();
  hdMark(null);
}

// time-shift: redare din inregistrari de la un moment (Date) al zilei de azi
async function hdPlayAt(when) {
  const wrap = $('#hd-player');
  if (wrap._hdMon) { clearInterval(wrap._hdMon); wrap._hdMon = null; }   // opresc monitorul HD-live
  stopHdBoxes();   // chenarele sunt LIVE (din bucla curenta) — pe inregistrare ar minti
  const date = localDay();
  const sec = when.getHours() * 3600 + when.getMinutes() * 60 + when.getSeconds();
  const start = compact(date, sec);
  wrap.innerHTML = 'Caut înregistrarea...';
  $('#hd-live').classList.remove('on');
  $('#hd-when').textContent = `⏱ ${hms(sec)} (înregistrare)`;
  const d = await (await fetch(`/api/play?channel=${hdCam.id}&start=${start}${hdHeightParam()}`)).json();
  if (d.error) { wrap.innerHTML = html`<span class="hd-err">${d.error}</span>`; return; }
  wrap.innerHTML = '';
  setTimeout(() => wrap.appendChild(streamEl(d.stream, HD_MODE, false, hdBufferTarget())), 400);
  attachDigitalZoom(wrap);
  hdMark(sec);
  hdPos = sec;
}
/* --- overlay de chenare pe HD live (faza 6): ce vede modelul + de ce a filtrat ---
   Verde = detectie raportata; gri punctat = vazuta dar inghitita de un filtru
   (marime/margine/zona/neconfirmat) — raspunsul vizual la "de ce n-a raportat?".
   Chenarele vin din bucla nvr-ai (substream), video-ul HD are ~1-2s latenta ->
   o mica nepotrivire pe obiecte rapide e normala. */
let hdBoxTimer = null;
const FILTRU_RO = { marime: 'marime', margine: 'margine', zona: 'în afara zonei',
                    neconfirmat: 'neconfirmat încă', stationar: 'parcat (suprimat)' };

function stopHdBoxes() {
  if (hdBoxTimer) { clearInterval(hdBoxTimer); hdBoxTimer = null; }
  const c = $('#hd-player .boxes-canvas');
  if (c) c.remove();
}

function startHdBoxes() {
  stopHdBoxes();
  const wrap = $('#hd-player');
  const cv = document.createElement('canvas');
  cv.className = 'boxes-canvas';
  cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:5';
  wrap.appendChild(cv);
  hdBoxTimer = setInterval(async () => {
    try {
      const d = await (await fetch('/api/events?full=1')).json();
      const st = d.detect[hdCam.id] || d.detect[String(hdCam.id)] || {};
      st.stationare = (d.stationare || {})[hdCam.id]
        || (d.stationare || {})[String(hdCam.id)] || [];
      drawHdBoxes(cv, st);
    } catch { /* tranzitoriu (restart server) — urmatoarea iteratie reincearca */ }
  }, 1200);
}

function drawHdBoxes(cv, st) {
  const wrap = cv.parentElement;
  if (!wrap) return;
  const v = [...wrap.querySelectorAll('video')].reverse().find((x) => x.videoWidth > 0);
  const r = wrap.getBoundingClientRect();
  cv.width = r.width; cv.height = r.height;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!v || !st.boxes) return;
  // urmareste zoom-ul digital (transformul e doar pe <video>)
  cv.style.transform = v.style.transform || '';
  cv.style.transformOrigin = 'center center';
  // object-fit: contain -> zona reala a imaginii in interiorul wrap-ului
  const s = Math.min(r.width / v.videoWidth, r.height / v.videoHeight);
  const iw = v.videoWidth * s, ih = v.videoHeight * s;
  const ox = (r.width - iw) / 2, oy = (r.height - ih) / 2;
  // date vechi = motion-gate tine camera pe loc (scena statica) -> estompam
  cv.style.opacity = (st.boxes_age || 0) > 4 ? '.45' : '1';
  ctx.font = '13px system-ui';
  // parcarile (episoade E1): albastru, cu durata — starea, nu un eveniment
  for (const p of (st.stationare || [])) {
    const x = ox + p.box[0] * iw, y = oy + p.box[1] * ih;
    const w = p.box[2] * iw, h = p.box[3] * ih;
    ctx.strokeStyle = '#58a6ff';
    ctx.setLineDash([2, 3]);
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
    const txt = `parcat ${p.minute >= 60 ? Math.round(p.minute / 60) + 'h' : p.minute + 'min'}`;
    ctx.fillStyle = 'rgba(31,111,235,.85)';
    ctx.fillRect(x - 1, y + h + 1, ctx.measureText(txt).width + 8, 19);
    ctx.fillStyle = '#fff';
    ctx.fillText(txt, x + 3, y + h + 15);
  }
  for (const b of st.boxes) {
    const x = ox + b.x * iw, y = oy + b.y * ih, w = b.w * iw, h = b.h * ih;
    const ok = !b.filtru;
    ctx.strokeStyle = ok ? '#3fb950' : '#8b949e';
    ctx.setLineDash(ok ? [] : [6, 4]);
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
    const txt = `${b.lbl} ${Math.round(b.conf * 100)}%`
      + (b.filtru ? ` · ${FILTRU_RO[b.filtru] || b.filtru}` : '');
    const tw = ctx.measureText(txt).width + 8;
    ctx.fillStyle = ok ? 'rgba(35,134,54,.85)' : 'rgba(90,98,108,.85)';
    ctx.fillRect(x - 1, Math.max(0, y - 19), tw, 19);
    ctx.fillStyle = '#fff';
    ctx.fillText(txt, x + 3, Math.max(13, y - 5));
  }
}

$('#hd-boxes').onclick = () => {
  const on = !$('#hd-boxes').classList.contains('on');
  $('#hd-boxes').classList.toggle('on', on);
  try { localStorage.setItem('hd_boxes', on ? '1' : ''); } catch {}
  if (on && $('#hd-live').classList.contains('on')) startHdBoxes();
  else stopHdBoxes();
};
try { $('#hd-boxes').classList.toggle('on', !!localStorage.getItem('hd_boxes')); } catch {}

$('#hd-live').onclick = hdGoLive;
for (const b of document.querySelectorAll('#hd-shift button[data-back]')) {
  b.onclick = () => hdPlayAt(new Date(Date.now() - b.dataset.back * 1000));
}
$('#hd-topb').onclick = () => {
  // duce camera + momentul curent in tabul Inregistrari (context complet)
  const c = hdCam;
  closeHD().then(() => {
    show('pb');
    $('#pb-cam').value = c.id;
    $('#pb-date').value = localDay();
    searchRecordings();
  });
};

// bara zilei sub player: segmente inregistrate + evenimente, click = time-shift
let hdSegs = [];
async function loadHdTimeline() {
  const date = localDay();
  const tl = $('#hd-timeline');
  tl.innerHTML = '';
  try {
    const [rec, al] = await Promise.all([
      fetch(`/api/recordings?channel=${hdCam.id}&start=${date}T00:00:00Z&end=${date}T23:59:59Z`).then((r) => r.json()),
      fetch(`/api/alarms?date=${date}`).then((r) => r.json()),
    ]);
    if (!hdCam) return;               // inchis cat timp asteptam raspunsurile
    hdSegs = rec.segments || [];
    for (const seg of hdSegs) {
      const a = secOfDay(seg.start), b = secOfDay(seg.end);
      const bar = document.createElement('div');
      bar.className = 'seg';
      bar.style.left = (a / 86400 * 100) + '%';
      bar.style.width = Math.max(0.15, (b - a) / 86400 * 100) + '%';
      tl.appendChild(bar);
    }
    for (const it of al.filter((i) => i.cam === hdCam.id)) {
      const [h, m, s] = it.time.split(':').map(Number);
      const dot = document.createElement('div');
      dot.className = 'evdot' + (DET_RO[it.label] ? ' det' : '');
      dot.style.left = ((h * 3600 + m * 60 + s) / 86400 * 100) + '%';
      dot.title = `${it.time} — ${AL_RO[it.label] || it.label}`;
      dot.onclick = (e) => { e.stopPropagation(); hdPlayAt(new Date(`${date}T${it.time}`)); };
      tl.appendChild(dot);
    }
  } catch {}
  drawHdMarks();
  const cur = document.createElement('div');
  cur.className = 'cursor'; cur.style.display = 'none';
  tl.appendChild(cur); tl._cursor = cur;
  // marcaj "acum"
  const now = new Date();
  const nowEl = document.createElement('div');
  nowEl.className = 'nowmark';
  nowEl.style.left = ((now.getHours() * 3600 + now.getMinutes() * 60) / 86400 * 100) + '%';
  tl.appendChild(nowEl);
  const ticks = $('#hd-ticks');
  ticks.innerHTML = '';
  for (let h = 0; h <= 24; h += 3) {
    const s = document.createElement('span');
    s.style.left = (h / 24 * 100) + '%';
    s.textContent = pad(h) + ':00';
    ticks.appendChild(s);
  }
  tl.onclick = (e) => {
    const r = tl.getBoundingClientRect();
    const sec = (e.clientX - r.left) / r.width * 86400;
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    hdPlayAt(new Date(d.getTime() + sec * 1000));
  };
}
function hdMark(sec) {
  const cur = $('#hd-timeline')._cursor;
  if (!cur) return;
  if (sec === null) { cur.style.display = 'none'; return; }
  cur.style.display = 'block';
  cur.style.left = (sec / 86400 * 100) + '%';
}

function closeHD(fromPop = false) {
  talkStop();
  stopHdBoxes();
  const wrap = $('#hd-player');
  if (wrap._hdMon) { clearInterval(wrap._hdMon); wrap._hdMon = null; }
  if (wrap._dzoomReset) wrap._dzoomReset();
  $('#hd-overlay').classList.add('hidden');
  $('#hd-ctrls').innerHTML = '';
  wrap.innerHTML = '';   // opreste stream-ul HD
  gridPause(false);
  $('#hd-timeline').innerHTML = '';
  hdPos = null;
  hdCam = null;
  // Inchis din buton: scoatem intrarea #hd din istoric. popstate-ul care urmeaza e al
  // nostru (nu o navigare a utilizatorului) si nu trebuie sa schimbe ecranul; promisiunea
  // se rezolva dupa el, ca apelantul sa poata trece in alt tab fara sa fie suprascris.
  if (!fromPop && history.state && history.state.hd) {
    return new Promise((r) => { S.popIgnora = r; history.back(); });
  }
  return Promise.resolve();
}
S.popIgnora = null;   // rezolvatorul promisiunii din closeHD, cat timp asteptam popstate-ul
function drawHdMarks() {
  if (!hdCam) return;
  const date = localDay();
  loadMarks(hdCam.id, date, $('#hd-timeline'), null, (sec) => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    hdPlayAt(new Date(d.getTime() + sec * 1000));
  });
}
$('#hd-mark').onclick = () => {
  if (!hdCam) return;
  const n = new Date();
  const sec = hdPos ?? (n.getHours() * 3600 + n.getMinutes() * 60 + n.getSeconds());
  addMark(hdCam.id, localDay(n), sec, drawHdMarks);
};

export { closeHD, hdCam, hdGoLive, openHD };
