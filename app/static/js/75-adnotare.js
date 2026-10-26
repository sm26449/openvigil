/* ====================================================================
   Adnotare: etichetele datasetului de antrenament (data/finetune), cadru cu cadru
   Lotul 1: lista, cadrul cu chenarele teacher-ului, legenda claselor, navigare din taste,
   „Confirma” (salveaza etichetele asa cum sunt si marcheaza cadrul verificat de om).
   Lotul 2: editare pe canvas — click = selecteaza, tragere din interior = muta, din colt =
   redimensioneaza, tragere pe gol = chenar nou (clasa curenta), Delete = sterge, 1-9/0 =
   clasa (a selectiei sau cea curenta), Ctrl+Z = anuleaza, Esc = deselecteaza, Enter = salveaza.
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js.
   ==================================================================== */
import { $, esc, html, lista, t } from './00-core.js';
import { post } from './10-live.js';

const CULORI = ['#3fb950', '#58a6ff', '#f0883e', '#d2a8ff', '#ffa657', '#79c0ff', '#ff7b72', '#a5d6ff', '#e3b341', '#7ee787', '#f778ba'];
let adCadre = [], adIdx = -1, adCur = null, adImg = null, adClase = [], adTotal = 0;
let adSel = -1, adClasa = 0, adDirty = false, adUndo = [], adDrag = null, adTemp = null;
const MANER = 7;   // px: zona de prindere a coltului
const clasaCuloare = (c) => CULORI[c % CULORI.length];

function adParams() {
  const p = new URLSearchParams();
  if ($('#ad-split').value) p.set('split', $('#ad-split').value);
  if ($('#ad-nev').checked) p.set('neverificate', '1');
  if ($('#ad-cam').value) p.set('cam', $('#ad-cam').value);
  p.set('limit', '200');
  return p.toString();
}

async function loadAdnotare(pastreaza = false) {
  let d;
  try { d = await (await fetch('/api/adnotare?' + adParams())).json(); } catch { return; }
  if (!d || d.error) return;
  adCadre = d.cadre || []; adClase = d.clase || []; adTotal = d.total || 0;
  $('#ad-count').textContent = ($('#ad-split').value === 'nesigure'
    ? t('{n} cadre nesigure în coadă (cele mai nesigure primele)', { n: adTotal })
    : t('{n} cadre ({v} verificate de om, {u} în coada de nesigure)', { n: adTotal, v: d.verificate, u: d.nesigure || 0 })) + (adTotal > adCadre.length ? ', ' + t('primele {n}', { n: adCadre.length }) : '');
  $('#ad-ignore').classList.toggle('hidden', $('#ad-split').value !== 'nesigure');
  $('#ad-list').innerHTML = lista(adCadre, (c, i) => html`
    <div class="ad-row" data-i="${i}" title="${c.split}/${c.fn}">
      <span>${c.fn.replace(/\.jpg$/, '').replace(/^cam(\d+)-(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)$/, 'cam$1 $4.$3 $5:$6:$7')}</span>
      ${c.verificat ? html`<span class="v" title="${t('verificat de om')}">✓</span>` : ''}
      <span class="n">${c.n < 0 ? '?' : c.n}</span>
    </div>`, `<p class="hint">${esc(t('Niciun cadru. Construiește datasetul din Setări → Unelte.'))}</p>`);
  for (const row of document.querySelectorAll('#ad-list .ad-row')) {
    row.onclick = () => adDeschide(+row.dataset.i);
  }
  if (!adCadre.length) { adCur = null; adDeseneaza(); return; }
  if (!(pastreaza && adIdx >= 0 && adIdx < adCadre.length)) adIdx = 0;
  adDeschide(adIdx);
}

async function adDeschide(i) {
  if (i < 0 || i >= adCadre.length) return;
  if (adDirty && !confirm(t('Ai modificări nesalvate pe cadrul curent. Le pierzi?'))) return;
  adDirty = false; adUndo = []; adSel = -1; adDrag = null; adTemp = null;
  adIdx = i;
  const c = adCadre[i];
  for (const r of document.querySelectorAll('#ad-list .ad-row')) r.classList.toggle('on', +r.dataset.i === i);
  const on = document.querySelector('#ad-list .ad-row.on'); if (on) on.scrollIntoView({ block: 'nearest' });
  let d;
  try { d = await (await fetch(`/api/adnotare/cadru/${c.split}/${encodeURIComponent(c.fn)}`)).json(); } catch { return; }
  if (!d || d.error) return;
  adCur = d;
  adImg = new Image();
  adImg.onload = adDeseneaza;
  adImg.src = `/api/adnotare/img/${c.split}/${encodeURIComponent(c.fn)}`;
  $('#ad-info').textContent = `${c.split}/${c.fn} · ${d.w}×${d.h} · ${t('{n} chenare', { n: d.boxes.length })}${d.verificat ? ' · ' + t('verificat {cand}', { cand: d.verificat.t }) + (d.verificat.cine ? ' ' + t('de {cine}', { cine: d.verificat.cine }) : '') : ''}`;
  adLegenda();
}

function adLegenda() {
  const nr = {};
  for (const b of (adCur ? adCur.boxes : [])) nr[b.cls] = (nr[b.cls] || 0) + 1;
  $('#ad-legend').innerHTML = html`${adClase.map((n, i) => html`<span class="${i === adClasa ? 'on' : ''}" style="--c:${clasaCuloare(i)}" data-cls="${i}" title="${t('tasta {k}: clasa pentru chenarele noi sau pentru cel selectat', { k: (i + 1) % 10 })}">${n}${nr[i] ? ' ×' + nr[i] : ''}</span>`)}`;
  for (const el of document.querySelectorAll('#ad-legend span')) el.onclick = () => adSetClasa(+el.dataset.cls);
  adStare();
}
function adStare() {
  const b = $('#ad-confirm'); if (!b) return;
  b.textContent = adDirty ? t('Salvează') : t('Confirmă');
  b.title = adDirty ? t('Scrie etichetele modificate și marchează cadrul verificat (Enter)') : t('Etichetele sunt corecte: salvează și marchează verificat (Enter)');
  const m = $('#ad-msg'); if (adDirty) m.textContent = t('modificat, nesalvat');
}
function adSetClasa(c) {
  if (!(c >= 0 && c < adClase.length)) return;
  adClasa = c;
  if (adSel >= 0 && adCur && adCur.boxes[adSel] && adCur.boxes[adSel].cls !== c) { adPush(); adCur.boxes[adSel].cls = c; adDirty = true; }
  adLegenda(); adDeseneaza();
}
function adPush() { adUndo.push(JSON.stringify(adCur.boxes)); if (adUndo.length > 50) adUndo.shift(); }

function adDeseneaza() {
  const cv = $('#ad-canvas'); if (!cv) return;
  const g = cv.getContext('2d');
  if (!adCur || !adImg || !adImg.complete || !adImg.naturalWidth) {
    cv.width = 640; cv.height = 360; g.fillStyle = '#000'; g.fillRect(0, 0, cv.width, cv.height);
    g.fillStyle = '#888'; g.font = '14px sans-serif'; g.fillText(adCadre.length ? t('se încarcă…') : t('niciun cadru'), 20, 30);
    return;
  }
  const w = adImg.naturalWidth, h = adImg.naturalHeight;
  const maxW = cv.parentElement.clientWidth || 800, k = Math.min(maxW / w, (window.innerHeight * 0.62) / h);
  cv.width = Math.round(w * k); cv.height = Math.round(h * k);
  g.drawImage(adImg, 0, 0, cv.width, cv.height);
  adCur.boxes.forEach((b, i) => {
    const x = (b.cx - b.w / 2) * cv.width, y = (b.cy - b.h / 2) * cv.height, bw = b.w * cv.width, bh = b.h * cv.height;
    const sel = i === adSel;
    if (b.conf !== undefined && b.conf < 0.55) g.setLineDash([5, 4]);
    g.lineWidth = sel ? 3 : 2; g.strokeStyle = clasaCuloare(b.cls); g.strokeRect(x, y, bw, bh);
    g.setLineDash([]);
    if (sel) {
      g.fillStyle = '#fff';
      for (const [hx, hy] of [[x, y], [x + bw, y], [x, y + bh], [x + bw, y + bh]]) g.fillRect(hx - 4, hy - 4, 8, 8);
    }
    const t = (adClase[b.cls] || ('cls ' + b.cls)) + (b.conf !== undefined ? ` ${Math.round(b.conf * 100)}%` : '');
    g.font = '12px sans-serif'; const tw = g.measureText(t).width + 8;
    g.fillStyle = clasaCuloare(b.cls); g.fillRect(x, Math.max(0, y - 16), tw, 16);
    g.fillStyle = '#000'; g.fillText(t, x + 4, Math.max(12, y - 4));
  });
  if (adTemp) { g.setLineDash([6, 4]); g.lineWidth = 2; g.strokeStyle = clasaCuloare(adClasa); g.strokeRect(adTemp[0] * cv.width, adTemp[1] * cv.height, adTemp[2] * cv.width, adTemp[3] * cv.height); g.setLineDash([]); }
}

/* ---- editarea pe canvas ---- */
const clamp01 = (v) => Math.min(1, Math.max(0, v));
function adPos(e) {
  const cv = $('#ad-canvas'), r = cv.getBoundingClientRect();
  return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
}
// ce e sub cursor: {i, colt} (colt = 'nw'|'ne'|'sw'|'se' sau null pentru interior), altfel null
function adLoveste(p) {
  const cv = $('#ad-canvas');
  if (!adCur) return null;
  const px = p[0] * cv.width, py = p[1] * cv.height;
  const ordine = adSel >= 0 ? [adSel, ...adCur.boxes.map((_, i) => i).filter((i) => i !== adSel)] : adCur.boxes.map((_, i) => i);
  for (const i of ordine) {
    const b = adCur.boxes[i];
    const x = (b.cx - b.w / 2) * cv.width, y = (b.cy - b.h / 2) * cv.height, x2 = x + b.w * cv.width, y2 = y + b.h * cv.height;
    const colturi = { nw: [x, y], ne: [x2, y], sw: [x, y2], se: [x2, y2] };
    for (const k in colturi) if (Math.abs(px - colturi[k][0]) <= MANER && Math.abs(py - colturi[k][1]) <= MANER) return { i, colt: k };
    if (px >= x && px <= x2 && py >= y && py <= y2) return { i, colt: null };
  }
  return null;
}
function adAplicaDrag(p) {
  const d = adDrag, b = adCur.boxes[d.i];
  if (!b) return;
  const dx = p[0] - d.start[0], dy = p[1] - d.start[1];
  const o = d.orig;
  if (!d.colt) {
    b.cx = clamp01(o.cx + dx); b.cy = clamp01(o.cy + dy);
    b.cx = Math.min(1 - b.w / 2, Math.max(b.w / 2, b.cx)); b.cy = Math.min(1 - b.h / 2, Math.max(b.h / 2, b.cy));
    return;
  }
  let x1 = o.cx - o.w / 2, y1 = o.cy - o.h / 2, x2 = o.cx + o.w / 2, y2 = o.cy + o.h / 2;
  if (d.colt.includes('w')) x1 = clamp01(x1 + dx); else x2 = clamp01(x2 + dx);
  if (d.colt.includes('n')) y1 = clamp01(y1 + dy); else y2 = clamp01(y2 + dy);
  const nx1 = Math.min(x1, x2), nx2 = Math.max(x1, x2), ny1 = Math.min(y1, y2), ny2 = Math.max(y1, y2);
  b.cx = (nx1 + nx2) / 2; b.cy = (ny1 + ny2) / 2; b.w = Math.max(0.005, nx2 - nx1); b.h = Math.max(0.005, ny2 - ny1);
}
function adWireCanvas() {
  const cv = $('#ad-canvas'); if (!cv) return;
  cv.onpointerdown = (e) => {
    if (!adCur) return;
    e.preventDefault(); cv.setPointerCapture?.(e.pointerId);
    const p = adPos(e), hit = adLoveste(p);
    if (hit) {
      adSel = hit.i; adClasa = adCur.boxes[hit.i].cls;
      adDrag = { i: hit.i, colt: hit.colt, start: p, orig: { ...adCur.boxes[hit.i] }, mutat: false };
    } else { adSel = -1; adDrag = { nou: true, start: p }; adTemp = null; }
    adLegenda(); adDeseneaza();
  };
  cv.onpointermove = (e) => {
    if (!adDrag) { const hit = adCur ? adLoveste(adPos(e)) : null; cv.style.cursor = hit ? (hit.colt ? (hit.colt === 'nw' || hit.colt === 'se' ? 'nwse-resize' : 'nesw-resize') : 'move') : 'crosshair'; return; }
    const p = adPos(e);
    if (adDrag.nou) { const a = adDrag.start; adTemp = [Math.min(a[0], p[0]), Math.min(a[1], p[1]), Math.abs(p[0] - a[0]), Math.abs(p[1] - a[1])]; }
    else { if (!adDrag.mutat) { adPush(); adDrag.mutat = true; } adAplicaDrag(p); adDirty = true; adStare(); }
    adDeseneaza();
  };
  cv.onpointerup = (e) => {
    if (!adDrag) return;
    const p = adPos(e);
    if (adDrag.nou) {
      const a = adDrag.start, w = Math.abs(p[0] - a[0]), h = Math.abs(p[1] - a[1]);
      if (w > 0.01 && h > 0.01) {
        adPush();
        adCur.boxes.push({ cls: adClasa, cx: (a[0] + p[0]) / 2, cy: (a[1] + p[1]) / 2, w, h });
        adSel = adCur.boxes.length - 1; adDirty = true;
      }
      adTemp = null;
    }
    adDrag = null; adLegenda(); adDeseneaza();
  };
  cv.onpointercancel = () => { adDrag = null; adTemp = null; adDeseneaza(); };
}
function adSterge() {
  if (!adCur || adSel < 0) return;
  adPush(); adCur.boxes.splice(adSel, 1); adSel = -1; adDirty = true; adLegenda(); adDeseneaza();
}
function adAnuleaza() {
  if (!adUndo.length) return;
  adCur.boxes = JSON.parse(adUndo.pop()); adSel = -1; adDirty = adUndo.length > 0; adStare(); if (!adDirty) $('#ad-msg').textContent = ''; adLegenda(); adDeseneaza();
}

function adScoateCurent(msg) {
  adCadre.splice(adIdx, 1); adTotal = Math.max(0, adTotal - 1);
  $('#ad-msg').textContent = msg;
  const row = document.querySelector(`#ad-list .ad-row[data-i="${adIdx}"]`); if (row) row.remove();
  for (const r of document.querySelectorAll('#ad-list .ad-row')) { const i = +r.dataset.i; if (i > adIdx) r.dataset.i = i - 1; }
  adCur = null; adSel = -1;
  if (adCadre.length) adDeschide(Math.min(adIdx, adCadre.length - 1)); else { adDeseneaza(); $('#ad-info').textContent = t('coada e goală'); }
}
async function adIgnora() {
  if (!adCur || adCur.split !== 'nesigure') return;
  const d = await post(`/api/adnotare/cadru/nesigure/${encodeURIComponent(adCur.fn)}/ignora`, {});
  if (d && d.ok) { adDirty = false; adUndo = []; adScoateCurent(t('„{fn}” ignorat', { fn: adCur.fn })); }
}
let adScanTimer = null;
async function adScanStare() {
  let d;
  try { d = await (await fetch('/api/adnotare/nesigure/stare')).json(); } catch { return; }
  const st = $('#ad-scan-st'), s = d.scan;
  if (!s) { st.textContent = t('nvr-ai nu rulează: scanarea are nevoie de modelul lui.'); return; }
  if (s.activ) {
    st.textContent = t('scanez {p}/{total} cadre, {g} nesigure…', { p: s.progres, total: s.total, g: s.gasite });
    clearTimeout(adScanTimer); adScanTimer = setTimeout(adScanStare, 2000);
  } else if (s.t) {
    st.textContent = s.eroare ? t('scanarea a eșuat: {eroare}', { eroare: s.eroare }) : t('ultima scanare: {total} cadre, {g} nesigure · în coadă: {q}', { total: s.total, g: s.gasite, q: d.in_coada });
    if (adScanTimer) { clearTimeout(adScanTimer); adScanTimer = null; if ($('#ad-split').value === 'nesigure') loadAdnotare(); }
  } else st.textContent = d.in_coada ? t('în coadă: {q}', { q: d.in_coada }) : '';
}
async function adConfirma() {
  if (!adCur) return;
  const d = await post(`/api/adnotare/cadru/${adCur.split}/${encodeURIComponent(adCur.fn)}`, { boxes: adCur.boxes });
  if (d && d.ok) {
    adDirty = false; adUndo = [];
    if (adCur.split === 'nesigure') { adScoateCurent(t('„{fn}” a intrat în dataset: {n} chenare, verificat ✓', { fn: adCur.fn, n: d.n })); return; }
    adCadre[adIdx].verificat = true;
    const row = document.querySelector(`#ad-list .ad-row[data-i="${adIdx}"]`);
    if (row && !row.querySelector('.v')) row.insertAdjacentHTML('beforeend', `<span class="v" title="${esc(t('verificat de om'))}">✓</span>`);
    $('#ad-msg').textContent = t('„{fn}” salvat: {n} chenare, verificat ✓', { fn: adCur.fn, n: d.n });
    adStare();
    if (adIdx + 1 < adCadre.length) adDeschide(adIdx + 1);
  }
}

$('#ad-prev') && ($('#ad-prev').onclick = () => adDeschide(adIdx - 1));
$('#ad-next') && ($('#ad-next').onclick = () => adDeschide(adIdx + 1));
$('#ad-confirm') && ($('#ad-confirm').onclick = adConfirma);
$('#ad-ignore') && ($('#ad-ignore').onclick = adIgnora);
$('#ad-scan') && ($('#ad-scan').onclick = async () => {
  const n = +$('#ad-scan-n').value || 500;
  const d = await post('/api/adnotare/nesigure/scaneaza', { n });
  if (d && !d.error) { $('#ad-scan-st').textContent = d.ok ? t('scanarea a pornit…') : t('o scanare rulează deja'); adScanTimer = setTimeout(adScanStare, 1500); }
});
for (const id of ['ad-split', 'ad-nev', 'ad-cam']) { const el = $('#' + id); if (el) el.onchange = () => loadAdnotare(); }
window.addEventListener('resize', () => { if (!$('#aipane-adnot').classList.contains('hidden')) adDeseneaza(); });
document.addEventListener('keydown', (e) => {
  if ($('#aipane-adnot').classList.contains('hidden') || $('#ai').classList.contains('hidden')) return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
  if (e.key === 'ArrowRight') { adDeschide(adIdx + 1); e.preventDefault(); }
  else if (e.key === 'ArrowLeft') { adDeschide(adIdx - 1); e.preventDefault(); }
  else if (e.key === 'Enter') { adConfirma(); e.preventDefault(); }
  else if (e.key === 'Delete' || e.key === 'Backspace') { adSterge(); e.preventDefault(); }
  else if (e.key === 'Escape' && adSel >= 0) { adSel = -1; adDeseneaza(); e.preventDefault(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { adAnuleaza(); e.preventDefault(); }
  else if (/^[0-9]$/.test(e.key) && !e.ctrlKey) { adSetClasa(e.key === '0' ? 9 : +e.key - 1); e.preventDefault(); }
});
adWireCanvas();
// incarcarea: cand se deschide sub-tabul Adnotare (butonul din nav-ul AI) sau daca e deja ales
const adBtn = document.querySelector('.ainav-item[data-pane="adnot"]');
if (adBtn) adBtn.addEventListener('click', () => { loadAdnotare(true); adScanStare(); });
if (adBtn && localStorage.getItem('aiPane') === 'adnot') setTimeout(() => loadAdnotare(), 300);

export { loadAdnotare };
