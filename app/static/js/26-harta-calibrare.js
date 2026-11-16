/* ====================================================================
   Harta: calibrarea unei camere pe plan
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { $, t, toast } from './00-core.js';
import { post } from './10-live.js';
import { amprentaMontaj, eroriCalibrare, proiect } from './24-harta-metrica.js';
/* Perechi de puncte cadru <-> plan (fractii 0..1). Clic in cadru, apoi pe acelasi loc pe plan;
   punctele se pot trage dupa aceea. De la 4 perechi, serverul calculeaza omografia (proba,
   fara salvare: POST /api/harta/omografie) si planul arata grila solului din cadru proiectata,
   plus eroarea fiecarei perechi. Salvarea: POST /api/harta {id, cam, calibrare: {perechi}}. */
const NS = 'http://www.w3.org/2000/svg';
const MAX = 12;
let cal = null;   // {plan, cam, perechi: [[cx,cy,px,py]], astept: [cx,cy] | null, h, eroare, erori, eroriM, met, onSalvat}

// onSalvat(plan): planul intreg intors de server (cu masura), pe care harta il preia
function openCalibrare(plan, cam, onSalvat) {
  const poz = (plan.camere || {})[cam.id] || {};
  cal = { plan, cam, perechi: ((poz.calibrare || {}).perechi || []).map((p) => [...p]), astept: null,
          h: null, eroare: null, erori: [], eroriM: [], met: plan.metrica, poz, onSalvat };
  $('#harta-cal-title').textContent = t('Calibrează {nume} pe „{plan}”', { nume: `${cam.id}. ${cam.name}`, plan: plan.nume });
  const ci = $('#harta-cal-cam-img'), pi = $('#harta-cal-plan-img');
  ci.onload = pi.onload = desen;
  ci.src = `/api/thumb/${cam.id}?t=${Date.now()}`;
  pi.src = `/api/harta/${plan.id}/imagine?v=${plan._v || 0}`;
  $('#harta-cal').classList.remove('hidden');
  proba();
}

// coordonate: fractii pe imagine <-> viewBox-ul SVG (pixelii naturali ai imaginii)
const dim = (img) => [img.naturalWidth || 1, img.naturalHeight || 1];
function frac(svg, e) {
  const r = svg.getBoundingClientRect();
  return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
}
function el(svg, tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const [a, v] of Object.entries(attrs)) e.setAttribute(a, v);
  if (text != null) e.textContent = text;
  svg.appendChild(e); return e;
}

function desen() {
  if (!cal) return;
  for (const [svgSel, imgSel, off] of [['#harta-cal-cam-svg', '#harta-cal-cam-img', 0], ['#harta-cal-plan-svg', '#harta-cal-plan-img', 2]]) {
    const svg = $(svgSel), img = $(imgSel), [W, H] = dim(img);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = '';
    const k = W / Math.max(1, svg.clientWidth || W);
    if (off === 2) reperMontaj(svg, W, H, k);
    if (off === 2 && cal.h) grila(svg, W, H, k);
    cal.perechi.forEach((p, i) => {
      const x = p[off] * W, y = p[off + 1] * H, rau = cal.erori[i] > 0.02;
      if (off === 2 && cal.h && cal.erori[i] != null) {
        const q = proiect(cal.h, p[0], p[1]);
        if (q) el(svg, 'line', { class: 'hc-err', x1: x, y1: y, x2: q[0] * W, y2: q[1] * H, 'stroke-width': 2 * k });
      }
      const c = el(svg, 'circle', { class: 'hc-pct' + (rau ? ' rau' : ''), cx: x, cy: y, r: 9 * k, 'stroke-width': 1.5 * k });
      c.dataset.i = i; c.dataset.off = off;
      el(svg, 'text', { class: 'hc-nr', x, y, 'font-size': 11 * k }, String(i + 1));
    });
    if (off === 0 && cal.astept) {
      el(svg, 'circle', { class: 'hc-pct astept', cx: cal.astept[0] * W, cy: cal.astept[1] * H, r: 9 * k, 'stroke-width': 1.5 * k });
      el(svg, 'text', { class: 'hc-nr', x: cal.astept[0] * W, y: cal.astept[1] * H, 'font-size': 11 * k }, String(cal.perechi.length + 1));
    }
  }
  stare();
}

/* Pe plan, ca reper cand alegi punctele: unde e camera si zona pe care ar vedea-o dupa montaj
   (inaltime, inclinare, deschidere, directie). Montajul e estimat din ochi, deci e doar un
   ajutor: un punct pus mult in afara zonei merita privit de doua ori; calibrarea ramane cea care
   conteaza (dupa salvare, harta deseneaza zona din ea). */
function reperMontaj(svg, W, H, k) {
  const poz = cal.poz;
  if (poz.x == null) return;
  const cx = poz.x * W, cy = poz.y * H;
  const z = amprentaMontaj(poz, cal.met, cx, cy);
  if (z) el(svg, 'path', { class: 'hc-montaj', d: 'M' + z.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L') + ' Z',
                           'stroke-width': 1.5 * k, 'stroke-dasharray': `${6 * k} ${4 * k}` });
  el(svg, 'circle', { class: 'hc-cam', cx, cy, r: 7 * k, 'stroke-width': 2 * k });
}
// grila solului din cadru (linii la fiecare 10%), proiectata pe plan; segmentele care trec
// de orizont sau ies mult din plan se rup
function grila(svg, W, H, k) {
  const linie = (pts) => {
    let d = '', sus = true;
    for (const [x, y] of pts) {
      const q = proiect(cal.h, x, y);
      if (!q || Math.abs(q[0] - 0.5) > 1.5 || Math.abs(q[1] - 0.5) > 1.5) { sus = true; continue; }
      d += `${sus ? 'M' : 'L'}${(q[0] * W).toFixed(1)},${(q[1] * H).toFixed(1)} `;
      sus = false;
    }
    if (d) el(svg, 'path', { class: 'hc-grila', d, 'stroke-width': 1.2 * k });
  };
  const N = 40, pas = (i) => i / N;
  for (let g = 0; g <= 10; g++) {
    linie(Array.from({ length: N + 1 }, (_, i) => [g / 10, pas(i)]));   // verticale din cadru
    linie(Array.from({ length: N + 1 }, (_, i) => [pas(i), g / 10]));   // orizontale din cadru
  }
}

function stare() {
  const n = cal.perechi.length;
  // cu masura planului, eroarea in metri pe teren (corecta si pe o schita deformata)
  const err = cal.eroare == null ? '' : cal.met
    ? t('eroare maximă ~{m} m', { m: Math.max(...cal.eroriM).toFixed(1).replace('.', ',') })
    : t('eroare maximă {p}% din plan', { p: (cal.eroare * 100).toFixed(1).replace('.', ',') });
  $('#harta-cal-pas').textContent = cal.astept ? t('Punctul {n}: acum clic pe același loc pe plan.', { n: n + 1 })
    : n >= MAX ? t('{n} perechi (maximul). {err}', { n, err })
    : n < 4 ? t('Punctul {n}: clic în cadrul camerei (mai trebuie {r}).', { n: n + 1, r: 4 - n })
    : t('{n} perechi — {err}. Poți adăuga altele sau salva.', { n, err });
  $('#harta-cal-save').disabled = n < 4 || !cal.h || !!cal.astept;
  $('#harta-cal-undo').disabled = !n && !cal.astept;
}

let probaNr = 0;
async function proba() {
  cal.h = null; cal.eroare = null; cal.erori = []; cal.eroriM = [];
  if (cal.perechi.length < 4) { desen(); return; }
  const nr = ++probaNr;
  try {
    const r = await fetch('/api/harta/omografie', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                   body: JSON.stringify({ perechi: cal.perechi }) });
    const d = await r.json();
    if (nr !== probaNr || !cal) return;
    if (d.error) { toast(d.error, true); desen(); return; }
    cal.h = d.h; cal.eroare = d.eroare;
    cal.erori = eroriCalibrare(cal.perechi, d.h, null);          // fractii din plan (perechea rosie)
    cal.eroriM = cal.met ? eroriCalibrare(cal.perechi, d.h, cal.met) : [];
  } catch { if (nr === probaNr) toast(t('Proba calibrării nu a răspuns'), true); }
  desen();
}

// clic = punct nou (cadru, apoi plan); apasare pe un punct existent = il tragi
function legaSvg(svgSel, off) {
  const svg = $(svgSel);
  svg.onpointerdown = (e) => {
    if (!cal) return;
    e.preventDefault();
    const tinta = e.target.closest('.hc-pct');
    if (tinta && tinta.dataset.i != null) {
      const i = +tinta.dataset.i;
      svg.setPointerCapture?.(e.pointerId);
      svg.onpointermove = (ev) => {
        const [x, y] = frac(svg, ev);
        cal.perechi[i][off] = x; cal.perechi[i][off + 1] = y;
        tinta.setAttribute('cx', x * svg.viewBox.baseVal.width); tinta.setAttribute('cy', y * svg.viewBox.baseVal.height);
        tinta.nextSibling?.setAttribute('x', x * svg.viewBox.baseVal.width); tinta.nextSibling?.setAttribute('y', y * svg.viewBox.baseVal.height);
      };
      svg.onpointerup = svg.onpointercancel = () => { svg.onpointermove = svg.onpointerup = svg.onpointercancel = null; proba(); };
      return;
    }
    const f = frac(svg, e);
    if (off === 0) {
      if (cal.astept) { cal.astept = f; desen(); return; }   // alt loc pentru punctul curent
      if (cal.perechi.length >= MAX) { toast(t('Maximum {n} perechi', { n: MAX }), true); return; }
      cal.astept = f; desen();
    } else {
      if (!cal.astept) { toast(t('Întâi alege punctul în cadrul camerei'), true); return; }
      cal.perechi.push([...cal.astept, ...f]); cal.astept = null;
      proba();
    }
  };
}
legaSvg('#harta-cal-cam-svg', 0);
legaSvg('#harta-cal-plan-svg', 2);
$('#harta-cal-undo').onclick = () => {
  if (!cal) return;
  if (cal.astept) cal.astept = null; else cal.perechi.pop();
  proba();
};
$('#harta-cal-cadru').onclick = () => { if (cal) $('#harta-cal-cam-img').src = `/api/thumb/${cal.cam.id}?t=${Date.now()}`; };
$('#harta-cal-save').onclick = async () => {
  if (!cal || cal.perechi.length < 4) return;
  const d = await post('/api/harta', { id: cal.plan.id, cam: cal.cam.id, calibrare: { perechi: cal.perechi } });
  if (d && !d.error) {
    const f = cal.onSalvat;
    toast(t('Calibrarea a fost salvată.'));
    $('#harta-cal-close').click();
    f && f(d.plan);
  }
};
$('#harta-cal-close').onclick = () => { $('#harta-cal').classList.add('hidden'); cal = null; };
window.addEventListener('resize', () => { if (cal) desen(); });

export { openCalibrare };
