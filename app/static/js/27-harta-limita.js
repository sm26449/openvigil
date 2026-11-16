/* ====================================================================
   Harta: limita proprietatii (scara din laturile cunoscute, unghiuri, suprafata)
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { $, LIMBA, html, t, toast } from './00-core.js';
import { post } from './10-live.js';
/* Adminul traseaza conturul proprietatii (clic pe colturi, in ordine) si scrie lungimile
   laturilor pe care le stie. Serverul (app/plan_metrica.py) calculeaza din ele masura planului
   (p.metrica): scara — unica sau corectata pe directii pentru o schita deformata —, lungimea
   fiecarei laturi pe plan si abaterea fata de cea introdusa, unghiurile colturilor, perimetrul
   si suprafata. Modulul nu depinde de harta (25-harta.js): primeste prin limitaInit planul
   curent, redesenarea si actualizarea planului dupa salvare. */
const NS = 'http://www.w3.org/2000/svg';
let ctx = null;          // {plan(), edit(), reda(), actualizeaza(plan), alteModuri()}
let desen = null;        // colturile trasate pana acum (fractii) cand trasezi; altfel null
let trag = null;         // {i, puncte} cat tragi un colt
let ultim = null;        // ultimul context de desen {svg, W, H, k}, pentru redesenarea in timpul tragerii
let panouCheie = '';

const fmt = (x, zec = 1) => Number(x).toLocaleString(LIMBA === 'en' ? 'en-GB' : 'ro-RO', { maximumFractionDigits: zec });
const litera = (i) => String.fromCharCode(65 + (i % 26)) + (i >= 26 ? Math.floor(i / 26) : '');
const latura = (i, n) => `${litera(i)}–${litera((i + 1) % n)}`;

function limitaInit(c) { ctx = c; }
const limitaTraseaza = () => !!desen;
function limitaOpreste() { if (desen) { desen = null; ctx.reda(); } }

function frac(e) {
  const r = $('#harta-svg').getBoundingClientRect();
  return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
}
function el(parinte, tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const [a, v] of Object.entries(attrs)) e.setAttribute(a, v);
  if (text != null) e.textContent = text;
  parinte.appendChild(e); return e;
}

/* Desenul de pe plan. In vizualizare: doar conturul, punctat. In editare: colturile (de
   tras), literele lor, lungimea fiecarei laturi si unghiul fiecarui colt. Cat trasezi:
   linia frânta de pana acum + elasticul spre cursor. */
function limitaDesen(svg, p, W, H, k) {
  ultim = { svg, W, H, k };
  svg.querySelector('#harta-limita-g')?.remove();
  const edit = ctx.edit();
  const pts = desen || (trag && trag.puncte) || (p.limita && p.limita.puncte);
  if (!pts || !pts.length) return;
  const g = document.createElementNS(NS, 'g');
  g.id = 'harta-limita-g';
  g.setAttribute('class', 'hl' + (edit ? ' edit' : '') + (desen ? ' traseaza' : ''));
  // DEASUPRA camerelor: altfel conul unei camere acoperea un colt, iar tragerea muta camera.
  // Linia si textele nu primesc clicuri (pointer-events: none), deci camerele raman apasabile.
  svg.appendChild(g);
  const P = pts.map(([x, y]) => [x * W, y * H]);
  const d = 'M' + P.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L') + (desen ? '' : ' Z');
  el(g, 'path', { class: 'hl-linie', d, 'stroke-width': (edit ? 2.2 : 1.6) * k, 'stroke-dasharray': `${8 * k} ${5 * k}` });
  if (desen) {
    el(g, 'line', { id: 'hl-elastic', class: 'hl-linie', x1: P.at(-1)[0], y1: P.at(-1)[1], x2: P.at(-1)[0], y2: P.at(-1)[1], 'stroke-width': 1.4 * k, 'stroke-dasharray': `${4 * k} ${4 * k}` });
  }
  if (!edit) return;
  const met = !desen && !trag && p.metrica && p.metrica.laturi && p.metrica.laturi.length === P.length ? p.metrica : null;
  const cx = P.reduce((s, q) => s + q[0], 0) / P.length, cy = P.reduce((s, q) => s + q[1], 0) / P.length;
  const txt = (x, y, s, cls = 'hl-txt') => el(g, 'text', { class: cls, x, y, 'font-size': 12 * k, 'stroke-width': 3 * k, 'text-anchor': 'middle', 'dominant-baseline': 'central' }, s);
  P.forEach(([x, y], i) => {
    // spre exterior pentru litera, spre interior pentru unghi (fata de centrul poligonului)
    const dx = x - cx, dy = y - cy, n = Math.hypot(dx, dy) || 1;
    txt(x + dx / n * 16 * k, y + dy / n * 16 * k, litera(i), 'hl-litera');
    if (met) txt(x - dx / n * 26 * k, y - dy / n * 26 * k, `${fmt(met.unghiuri[i], 0)}°`, 'hl-unghi');
    if (met && i < P.length) {
      const [x2, y2] = P[(i + 1) % P.length], l = met.laturi[i];
      const rau = l.abatere != null && Math.abs(l.abatere) > 0.1;
      txt((x + x2) / 2, (y + y2) / 2, `${fmt(l.m_plan)} m`, 'hl-lung' + (l.m_introdus ? ' stiuta' : '') + (rau ? ' rau' : ''));
    }
  });
  if (!desen) P.forEach(([x, y], i) => {
    const c = el(g, 'circle', { class: 'hl-pct', cx: x, cy: y, r: 6 * k, 'stroke-width': 1.5 * k });
    c.dataset.i = i;
  });
  else el(g, 'circle', { class: 'hl-pct inceput', cx: P[0][0], cy: P[0][1], r: 8 * k, 'stroke-width': 1.5 * k });
}
const redesen = () => { const p = ctx.plan(); if (ultim && p) limitaDesen(ultim.svg, p, ultim.W, ultim.H, ultim.k); };

function limitaInfo() {
  if (!desen) return null;
  return desen.length < 3 ? t('Limita: clic pe colțurile proprietății, în ordine ({n} până acum).', { n: desen.length })
    : t('Limita: continuă cu colțurile sau închide pe primul colț (Enter); Backspace șterge ultimul, Esc renunță.');
}

async function salveaza(limita) {
  const p = ctx.plan();
  const d = await post('/api/harta', { id: p.id, limita });
  if (d && !d.error) { ctx.actualizeaza(d.plan); return true; }
  return false;
}
async function inchide() {
  if (!desen || desen.length < 3) { toast(t('Limita are nevoie de cel puțin 3 colțuri'), true); return; }
  const puncte = desen;
  desen = null;
  if (await salveaza({ puncte, laturi: {} })) toast(t('Limita a fost salvată — scrie acum lungimile laturilor pe care le știi.'));
  else ctx.reda();
}
function porneste() {
  const p = ctx.plan();
  if (!p || !p.imagine) { toast(t('Încarcă întâi imaginea planului'), true); return; }
  if (p.limita && !confirm(t('Redesenezi limita? Colțurile și lungimile de acum se înlocuiesc.'))) return;
  ctx.alteModuri();
  desen = [];
  ctx.reda();
}

// trasarea si tragerea colturilor, prinse in faza de captura: nu ajung la marcajele camerelor
$('#harta-svg').addEventListener('pointerdown', (e) => {
  if (!ctx || !ctx.plan()) return;
  if (desen) {
    e.preventDefault(); e.stopPropagation();
    const f = frac(e), r = $('#harta-svg').getBoundingClientRect();
    const lang = desen.length && Math.hypot((f[0] - desen[0][0]) * r.width, (f[1] - desen[0][1]) * r.height) < 14;
    if (lang && desen.length >= 3) { inchide(); return; }
    desen.push(f); ctx.reda(); return;
  }
  const h = e.target.closest && e.target.closest('.hl-pct');
  if (!h || h.dataset.i == null || !ctx.edit()) return;
  e.preventDefault(); e.stopPropagation();
  $('#harta-svg').setPointerCapture?.(e.pointerId);
  trag = { i: +h.dataset.i, puncte: ctx.plan().limita.puncte.map((q) => [...q]), mutat: false };
}, true);
$('#harta-svg').addEventListener('pointermove', (e) => {
  if (trag) { trag.puncte[trag.i] = frac(e); trag.mutat = true; redesen(); return; }
  if (desen && desen.length && ultim) {
    const l = $('#hl-elastic'), f = frac(e);
    if (l) { l.setAttribute('x2', f[0] * ultim.W); l.setAttribute('y2', f[1] * ultim.H); }
  }
});
const gataTrag = async () => {
  if (!trag) return;
  const { puncte, mutat } = trag;
  trag = null;
  if (!mutat) { redesen(); return; }
  if (!(await salveaza({ puncte, laturi: ctx.plan().limita.laturi || {} }))) ctx.reda();   // refuzat (ex. se intersecteaza): revine
};
$('#harta-svg').addEventListener('pointerup', gataTrag);
$('#harta-svg').addEventListener('pointercancel', () => { trag = null; redesen(); });
document.addEventListener('keydown', (e) => {
  if (!desen) return;
  if (e.key === 'Enter') { e.preventDefault(); inchide(); }
  else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); desen = null; ctx.reda(); }
  else if (e.key === 'Backspace' && !e.target.closest('input, textarea')) { e.preventDefault(); desen.pop(); ctx.reda(); }
}, true);

/* Panoul din dreapta: laturile cu lungimile tale si ce iese pe plan, apoi rezumatul.
   Structura se reface doar cand se schimba numarul de colturi; altfel se actualizeaza doar
   textele, ca sa nu pierzi campul in care scrii. */
function limitaPanou(p) {
  const box = $('#harta-limita'), met = p.metrica;
  const lim = p.limita, n = lim ? lim.puncte.length : 0;
  const cheie = desen ? 'desen' : `${n}`;
  if (cheie !== panouCheie) {
    panouCheie = cheie;
    box.querySelector('.hl-corp').innerHTML = desen
      ? html`<p class="hint">${t('Clic pe colțurile proprietății, în ordine. Închide pe primul colț sau cu Enter; Backspace șterge ultimul colț, Esc renunță.')}</p>
          <div class="hl-btns"><button type="button" id="hl-inchide">${t('Închide limita')}</button><button type="button" id="hl-renunta">${t('Renunță')}</button></div>`
      : !n ? html`<p class="hint">${t('Trasează conturul proprietății și scrie lungimile laturilor pe care le știi: din ele se calculează scara planului (corectată și pentru o schiță deformată), unghiurile colțurilor și suprafața.')}</p>
          <div class="hl-btns"><button type="button" id="hl-start">${t('Trasează limita')}</button></div>`
      : html`<table class="hl-laturi"><thead><tr><th>${t('Latura')}</th><th>${t('Lungimea reală')}</th><th>${t('Pe plan')}</th></tr></thead><tbody>
          ${lim.puncte.map((_, i) => html`<tr><th scope="row">${latura(i, n)}</th>
            <td><input class="hl-m" data-i="${i}" inputmode="decimal" maxlength="8" placeholder="?" title="${t('Gol = lungime necunoscută')}" aria-label="${t('Lungimea reală a laturii {l}, în metri', { l: latura(i, n) })}"> m</td>
            <td class="hl-plan" data-i="${i}"></td></tr>`)}</tbody></table>
          <p class="hl-rezumat hint"></p><ul class="hl-avert"></ul>
          <div class="hl-btns"><button type="button" id="hl-start">${t('Redesenează')}</button><button type="button" id="hl-sterge">${t('Șterge limita')}</button></div>`;
    for (const b of box.querySelectorAll('#hl-start')) b.onclick = porneste;
    const bi = box.querySelector('#hl-inchide'); if (bi) bi.onclick = inchide;
    const br = box.querySelector('#hl-renunta'); if (br) br.onclick = () => { desen = null; ctx.reda(); };
    const bs = box.querySelector('#hl-sterge'); if (bs) bs.onclick = async () => {
      if (confirm(t('Ștergi limita proprietății? Scara revine la cea din două clicuri, dacă există.'))) await salveaza(null);
    };
    for (const inp of box.querySelectorAll('.hl-m')) inp.onchange = () => schimbaLatura(inp);
  }
  if (desen || !n) return;
  for (const inp of box.querySelectorAll('.hl-m')) {
    const v = (lim.laturi || {})[inp.dataset.i];
    if (inp !== document.activeElement) inp.value = v != null ? fmt(v, 2) : '';
  }
  const laturi = met && met.laturi && met.laturi.length === n ? met.laturi : null;
  for (const td of box.querySelectorAll('.hl-plan')) {
    const l = laturi && laturi[+td.dataset.i];
    // abaterea cu o zecimala; sub 0,05% e „0%”, fara semn (nu „−0%”)
    const ab = l && l.abatere != null ? Math.round(l.abatere * 1000) / 10 : null;
    td.textContent = !l ? '—' : ab == null ? `${fmt(l.m_plan)} m`
      : `${fmt(l.m_plan)} m · ${ab > 0 ? '+' : ab < 0 ? '−' : ''}${fmt(Math.abs(ab))}%`;
    td.classList.toggle('rau', !!(l && l.abatere != null && Math.abs(l.abatere) > 0.1));
  }
  const stiute = Object.keys(lim.laturi || {}).length;
  const rez = [];
  if (!stiute) rez.push(t('Scrie lungimea măcar a unei laturi: scara se va calcula din limită.'));
  else if (met && met.sursa === 'limita') {
    rez.push(met.mod === 'anizotrop'
      ? t('Planul e întins cu {p}% pe o direcție; scara e corectată pe direcții.', { p: fmt((met.deformare.raport - 1) * 100, 0) })
      : stiute === 1 ? t('Scara vine dintr-o singură latură; mai scrie una ca să poată fi verificată.')
      : t('Scara vine din {n} laturi; planul pare desenat la scară.', { n: stiute }));
  }
  if (laturi) rez.push(t('Perimetru {p} m · suprafață {s} m² ({ha} ha).', { p: fmt(met.perimetru, 0), s: fmt(met.suprafata, 0), ha: fmt(met.suprafata / 10000, 2) }));
  box.querySelector('.hl-rezumat').textContent = rez.join(' ');
  box.querySelector('.hl-avert').innerHTML = html`${(met && met.sursa === 'limita' ? met.avertismente : []).map((a) => html`<li>${a}</li>`)}`;
}
async function schimbaLatura(inp) {
  const p = ctx.plan(), lim = p.limita;
  const txt = inp.value.trim().replace(/\s/g, '').replace(',', '.');
  const laturi = { ...(lim.laturi || {}) };
  if (txt === '') delete laturi[inp.dataset.i];
  else {
    const m = Number(txt);
    if (!(m >= 0.1 && m <= 100000)) { toast(t('Lungimea unei laturi: un număr de metri, între 0,1 și 100000'), true); inp.value = ''; return; }
    laturi[inp.dataset.i] = m;
  }
  await salveaza({ puncte: lim.puncte, laturi });
}
$('#harta-limita-btn').onclick = porneste;

export { limitaDesen, limitaInfo, limitaInit, limitaOpreste, limitaPanou, limitaTraseaza };
