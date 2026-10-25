/* ====================================================================
   Fluxul de evenimente de langa Live: ultimele cadre salvate azi (detectii, fete, numere,
   alarme), cu miniaturi si filtre pe tip; click = redarea momentului (#pb/cam/zi/sec).
   Se reimprospateaza la 10 s doar cand grila Live e vizibila, incremental (?dupa=).
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js.
   ==================================================================== */
import { $, DET_RO, html } from './00-core.js';
import { AL_RO } from './40-evenimente.js';
import { openFisa } from './45-fisa.js';

const CAT_RO = { om: 'Om', vehicul: 'Mașină', animal: 'Animal', fata: 'Față', numar: 'Număr', miscare: 'Mișcare', alarma: 'Alarmă', sabotaj: 'Sabotaj' };
let fluxItems = [], fluxCat = '', fluxUltim = '', fluxTimer = null, fluxOn = false;

function fxEticheta(it) {
  if (it.cat === 'fata' || it.cat === 'numar') return it.label;
  return DET_RO[it.label] || AL_RO[it.label] || it.label;
}
function fxSec(t) { const [h, m, s] = t.split(':').map(Number); return h * 3600 + m * 60 + s; }

function fluxRender(noi = new Set()) {
  const list = $('#flux-list'); if (!list) return;
  // „Toate” = tot in afara de miscarea simpla (VMD), care are chip-ul ei
  const items = fluxItems.filter((it) => fluxCat ? it.cat === fluxCat : it.cat !== 'miscare').slice(0, 60);
  list.innerHTML = html`${items.map((it) => html`
    <button class="flux-item ${noi.has(it.url) ? 'nou' : ''}" data-cat="${it.cat}" data-cam="${it.cam}" data-zi="${it.zi}" data-sec="${fxSec(it.time)}"
      title="${fxEticheta(it)} · ${it.cam_nume} · ${it.time} — click: redă momentul">
      <img src="${it.thumb}" alt="" loading="lazy">
      <span class="fx-main"><span class="fx-what">${fxEticheta(it)}</span><span>${it.cam_nume}</span><span class="fx-when">${it.time}</span></span>
    </button>`)}`;
  if (!items.length) list.innerHTML = '<p class="hint">Nimic azi' + (fluxCat ? ' la ' + CAT_RO[fluxCat].toLowerCase() : ' în afară de mișcare simplă') + '.</p>';
  for (const b of list.querySelectorAll('.flux-item')) {
    b.onclick = () => {
      const it = items.find((x) => String(x.cam) === b.dataset.cam && fxSec(x.time) === +b.dataset.sec) || items[0];
      openFisa({ cam: it.cam, t: `${it.zi} ${it.time}`, tip: it.cat === 'alarma' || it.cat === 'miscare' ? 'alarma_camera' : 'detectie', det: it.label, poza: it.url, cat: it.cat });
    };
  }
  const nm = fluxItems.filter((it) => it.cat === 'miscare').length;
  $('#flux-info').textContent = fluxCat ? `${items.length} ${CAT_RO[fluxCat].toLowerCase()} din ${fluxItems.length} cadre azi` : `${items.length} evenimente azi (+${nm} mișcare simplă)`;
}

async function fluxLoad(incremental = true) {
  if (!fluxOn) return;
  const q = new URLSearchParams({ limit: '60' });
  if (incremental && fluxUltim) q.set('dupa', fluxUltim);
  let d;
  try { d = await (await fetch('/api/flux?' + q)).json(); } catch { return; }
  if (!d || d.error) return;
  if (d.zi !== fluxZi) { fluxItems = []; fluxUltim = ''; fluxZi = d.zi; }   // a trecut miezul noptii
  const noi = new Set();
  if (incremental && fluxUltim) {
    for (const it of d.cadre) noi.add(it.url);
    fluxItems = [...d.cadre, ...fluxItems].slice(0, 200);
  } else fluxItems = d.cadre;
  if (fluxItems.length) fluxUltim = fluxItems[0].time;
  fluxRender(noi);
}
let fluxZi = '';

function fluxSet(on) {
  fluxOn = on;
  $('#flux').classList.toggle('hidden', !on);
  $('#flux-toggle').classList.toggle('on', on);
  try { localStorage.setItem('fluxOn', on ? '1' : '0'); } catch {}
  if (on) fluxLoad(false);
}
$('#flux-toggle') && ($('#flux-toggle').onclick = () => fluxSet(!fluxOn));
for (const b of document.querySelectorAll('#flux-chips button')) {
  b.onclick = () => {
    for (const x of document.querySelectorAll('#flux-chips button')) x.classList.toggle('on', x === b);
    fluxCat = b.dataset.cat; fluxRender();
  };
}
// implicit: pornit pe ecrane late, oprit pe telefon; alegerea ramane in localStorage
let implicit = window.innerWidth > 1000;
try { const v = localStorage.getItem('fluxOn'); if (v !== null) implicit = v === '1'; } catch {}
if ($('#flux')) fluxSet(implicit);
fluxTimer = setInterval(() => {
  if (document.hidden || !fluxOn) return;
  if (!$('#live').classList.contains('hidden')) fluxLoad(true);
}, 10000);

export { fluxLoad, fluxSet };
