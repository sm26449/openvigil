/* ====================================================================
   Harta proprietatii
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, html, lista, t, toast } from './00-core.js';
import { post } from './10-live.js';
import { openHD } from './20-hd.js';
import { amprentaCalibrare, amprentaMontaj, eroriCalibrare, lungime, metriPePxX, proiect } from './24-harta-metrica.js';
import { openCalibrare } from './26-harta-calibrare.js';
import { limitaDesen, limitaInfo, limitaInit, limitaOpreste, limitaPanou, limitaTraseaza } from './27-harta-limita.js';
/* ---------- Etapa 4: harta proprietatii ----------
   Un plan (imagine) cu camerele asezate si conurile lor de vedere. Pozitiile sunt fractii
   din imagine, deci SVG-ul de deasupra foloseste viewBox-ul imaginii si scaleaza singur.
   Starea vine din /api/events (alarma, detectie) si /api/pulse (inregistreaza?); un
   incident critic face marcajul sa pulseze. Click = HD. Adminul editeaza: trage camerele,
   roteste directia si deschiderea conului din panoul lateral. */
let hartaPlanuri = [], hartaPid = localStorage.getItem('hartaPlan') || '', hartaEdit = false, hartaSel = null, hartaEroare = false;
let hartaMasoara = null;   // masurarea scarii: null | [] | [{x,y}] (primul punct ales)
const hartaMk = {};
const hartaPlan = () => hartaPlanuri.find((p) => p.id === hartaPid) || hartaPlanuri[0];
// raspunsul oricarei salvari aduce planul intreg (cu masura recalculata): il punem peste cel
// tinut aici, pastrand _v (versiunea imaginii) si referintele celorlalte module
function hartaActualizeaza(plan) {
  const p = hartaPlanuri.find((x) => x.id === plan.id);
  if (p) Object.assign(p, plan); else hartaPlanuri.push(plan);
  renderHarta();
}
async function loadHarta() {
  try {
    const d = await (await fetch('/api/harta')).json();
    hartaPlanuri = d.planuri || [];
    hartaEroare = false;
  } catch { hartaEroare = true; }   // NU stergem planurile — pastram ultima harta valida (audit H2)
  const sel = $('#harta-plan');
  sel.innerHTML = html`${hartaPlanuri.map((p) => html`<option value="${p.id}">${p.nume}</option>`)}`;
  sel.classList.toggle('hidden', hartaPlanuri.length < 2 && !hartaEdit);
  const p = hartaPlan();
  hartaPid = p ? p.id : '';
  if (p) sel.value = p.id;
  $('#harta-edit').classList.toggle('hidden', !ADMIN);
  for (const x of $('#harta-heat-ctl').querySelectorAll('button')) x.classList.toggle('on', +x.dataset.zile === hartaHeatZile);
  renderHarta();
  if (hartaHeatZile && !hartaEdit) hartaHeatLoad();
}
function renderHarta() {
  const p = hartaPlan();
  const img = $('#harta-img'), svg = $('#harta-svg'), wrap = $('#harta-wrap'), empty = $('#harta-empty');
  for (const k in hartaMk) delete hartaMk[k];
  svg.innerHTML = '';
  hartaCardInchide();
  wrap.classList.toggle('edit', hartaEdit);
  $('#harta-tools').classList.toggle('hidden', !hartaEdit);
  if (!hartaEdit) { hartaMasoara = null; limitaOpreste(); }
  const calibrate = !!(p && Object.values(p.camere || {}).some((x) => x.calibrare));
  $('#harta-heat-ctl').classList.toggle('hidden', hartaEdit || !calibrate);
  if (hartaEdit || !calibrate) hartaHeatData = null;
  wrap.classList.toggle('masoara', !!hartaMasoara || limitaTraseaza());
  $('#harta-scara').textContent = hartaMasoara ? t('Anulează scara') : t('Scara');
  $('#harta-scara-del').classList.toggle('hidden', !hartaEdit || !(p && p.scara));
  $('#harta-side').classList.toggle('hidden', !hartaEdit || !p);
  if (!p) {
    img.removeAttribute('src'); wrap.classList.add('gol');
    if (hartaEroare) {
      // eroare de incarcare != harta neconfigurata: nu lasam adminul sa creada ca trebuie refacuta (audit H2)
      empty.innerHTML = html`${t('Harta nu s-a putut încărca.')} <button id="harta-retry" class="link-btn">${t('Reîncearcă')}</button>`;
      const rb = $('#harta-retry'); if (rb) rb.onclick = loadHarta;
    } else {
      empty.textContent = ADMIN ? t('Niciun plan încă. Apasă „Editează” → „Plan nou”, apoi încarcă o imagine (schiță curții, o captură din satelit) și așază camerele.')
                                : t('Administratorul nu a configurat încă harta proprietății.');
    }
    empty.classList.remove('hidden');
    $('#harta-info').textContent = '';
    return;
  }
  if (!p.imagine) {
    img.removeAttribute('src'); wrap.classList.add('gol');
    empty.textContent = ADMIN ? t('Planul nu are imagine: apasă „Încarcă imaginea” (PNG/JPEG/WebP, până la 8 MB).') : t('Planul nu are încă o imagine.');
    empty.classList.remove('hidden');
  } else {
    empty.classList.add('hidden'); wrap.classList.remove('gol');
    const src = `/api/harta/${p.id}/imagine?v=${p._v || 0}`;
    if (img.getAttribute('src') !== src) { img.onload = () => drawHarta(); img.src = src; }
    else drawHarta();
  }
  const n = Object.keys(p.camere || {}).length;
  $('#harta-info').textContent = limitaInfo() || (hartaMasoara
    ? (hartaMasoara.length ? t('Acum clic pe al doilea punct.') : t('Clic pe primul punct al unei distanțe pe care o știi (gard, latura casei).'))
    : hartaEdit ? t('{n} camere pe plan — trage camerele; click pe una o selectează', { n })
    : hartaHeatData ? hartaHeatText()
    : t('{n} camere pe plan', { n }));
  if (hartaEdit) renderHartaSide();
}
function drawHarta() {
  const p = hartaPlan(), img = $('#harta-img'), svg = $('#harta-svg');
  if (!p || !img.naturalWidth) return;
  const W = img.naturalWidth, H = img.naturalHeight;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = '';
  const R = Math.min(W, H) * 0.12;
  // marcajele au marime de ECRAN (px), nu de imagine: un plan de 4000 px sau unul de 400 px
  // arata aceleasi puncte si litere; k = cati pixeli de imagine incap intr-un pixel de ecran
  const k = W / Math.max(1, svg.clientWidth || img.clientWidth || W);
  const names = Object.fromEntries(S.cameras.map((c) => [c.id, c]));
  const met = p.metrica;   // masura planului (server: plan_metrica.py), din limita sau din scara
  for (const [cid, pos] of Object.entries(p.camere || {})) {
    const c = names[+cid];
    if (!c) continue;
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'hm' + (hartaSel === +cid ? ' sel' : ''));
    g.dataset.id = cid;
    const cx = pos.x * W, cy = pos.y * H;
    const a1 = (pos.dir - pos.fov / 2) * Math.PI / 180, a2 = (pos.dir + pos.fov / 2) * Math.PI / 180;
    const pt = (a) => `${(cx + R * Math.sin(a)).toFixed(1)},${(cy - R * Math.cos(a)).toFixed(1)}`;
    // zona de sol vazuta: din CALIBRARE cand camera e calibrata (masuratoarea omului), altfel
    // din montaj (scara + inaltime + inclinare, cu pata oarba de sub camera); fara ele, conul
    // simbolic de raza fixa, ca pana acum
    const sol = pos.calibrare ? amprentaCalibrare(pos.calibrare.h, met, [pos.x, pos.y], W, H)
      : met ? amprentaMontaj(pos, met, cx, cy) : null;
    const dCon = sol ? 'M' + sol.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L') + ' Z'
      : `M${cx.toFixed(1)},${cy.toFixed(1)} L${pt(a1)} A${R},${R} 0 ${pos.fov > 180 ? 1 : 0} 1 ${pt(a2)} Z`;
    g.innerHTML = `<path class="con${sol ? ' sol' : ''}" style="stroke-width:${k}" d="${dCon}"/>`
      + `<circle class="pct" style="stroke-width:${(2.5 * k).toFixed(2)}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${(9 * k).toFixed(2)}"/>`
      + `<text class="lbl" style="stroke-width:${(3 * k).toFixed(2)}" x="${(cx + 13 * k).toFixed(1)}" y="${(cy + 4 * k).toFixed(1)}"></text>`;
    g.querySelector('text').textContent = `${c.id}. ${c.name}`;
    g.querySelector('text').setAttribute('font-size', (12 * k).toFixed(2));
    if (!hartaEdit) {
      g.onclick = () => hartaCard(c);
      // focusabil + nume accesibil + Enter/Space, ca marcajele sa poata fi deschise si de la
      // tastatura, nu doar cu mouse-ul (audit H3)
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      g.setAttribute('aria-label', t('{nume} — arată imaginea camerei', { nume: c.name }));
      g.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); hartaCard(c); } };
      const ttl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      ttl.textContent = t('{nume} — clic pentru imaginea camerei', { nume: c.name }); g.appendChild(ttl);
    } else hartaDrag(g, c, pos);
    svg.appendChild(g);
    hartaMk[c.id] = g;
  }
  hartaScaraDesen(p, W, H, k, met);
  limitaDesen(svg, p, W, H, k);   // dupa camere: colturile limitei raman apucabile
  hartaHeatDesen();
  hartaLiveDesen();
  hartaStare();
}
/* ---------- harta de caldura pe plan ----------
   Picioarele oamenilor din episoadele camerelor calibrate, proiectate pe plan de server
   (analiza.heatmap_plan, grila de fractii). Aici: pete estompate, colorate pe o rampa
   albastru -> galben -> rosu; radacina patrata, ca si drumurile rare sa se vada. */
let hartaHeatZile = 0, hartaHeatData = null, hartaHeatNr = 0;
try { hartaHeatZile = +(localStorage.getItem('hartaCaldura') || 0); } catch {}
const RAMPA = [[0, [40, 90, 255]], [0.35, [0, 220, 255]], [0.6, [255, 230, 0]], [1, [255, 40, 0]]];
function rampa(v) {
  for (let i = 1; i < RAMPA.length; i++) {
    const [a, ca] = RAMPA[i - 1], [b, cb] = RAMPA[i];
    if (v <= b) { const f = (v - a) / (b - a); return ca.map((c, j) => Math.round(c + (cb[j] - c) * f)); }
  }
  return RAMPA.at(-1)[1];
}
function hartaHeatText() {
  const d = hartaHeatData;
  if (d.error) return t('Eroare: {err}', { err: d.error });
  const perioada = d.zile === 1 ? t('azi') : t('ultimele {n} zile', { n: d.zile });
  let s = t('Căldură {perioada}: {n} episoade, {p} puncte pe plan', { perioada, n: d.episoade, p: d.puncte });
  if (d.fara_sol) s += ' · ' + t('{n} puncte vechi fără punct de sol, neincluse', { n: d.fara_sol });
  if (d.partial) s += ' · ' + t('rezultat parțial: prea multe episoade, cele mai vechi lipsesc — alege o perioadă mai scurtă');
  return s;
}
async function hartaHeatLoad() {
  const p = hartaPlan(), nr = ++hartaHeatNr;
  if (!hartaHeatZile || !p || hartaEdit || !Object.values(p.camere || {}).some((x) => x.calibrare)) {
    if (hartaHeatData) { hartaHeatData = null; renderHarta(); }
    return;
  }
  $('#harta-info').textContent = t('Calculez...');
  let d;
  try { d = await (await fetch(`/api/harta/${p.id}/heatmap?zile=${hartaHeatZile}`)).json(); }
  catch (e) { d = { error: String(e) }; }
  if (nr !== hartaHeatNr) return;
  hartaHeatData = d;
  renderHarta();
}
function hartaHeatDesen() {
  const cv = $('#harta-heat'), d = hartaHeatData, wrap = $('#harta-wrap'), img = $('#harta-img');
  const on = !!(d && !d.error && d.max && !hartaEdit && img.naturalWidth);
  cv.classList.toggle('hidden', !on);
  if (!on) return;
  const dpr = window.devicePixelRatio || 1, W = Math.round(img.clientWidth * dpr), H = Math.round(img.clientHeight * dpr);
  cv.width = W; cv.height = H;
  cv.style.height = `${img.clientHeight}px`;
  const g = cv.getContext('2d');
  // 1. intensitatea, ca alfa: pete radiale adunate
  const cw = W / d.grid, ch = H / d.grid, r = Math.max(cw, ch, 6 * dpr) * 2.2;
  for (const [x, y, n] of d.celule) {
    const cx = (x + 0.5) * cw, cy = (y + 0.5) * ch, a = 0.2 + 0.8 * Math.sqrt(n / d.max);
    const gr = g.createRadialGradient(cx, cy, 0, cx, cy, r);
    gr.addColorStop(0, `rgba(0,0,0,${a.toFixed(3)})`); gr.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = gr; g.fillRect(cx - r, cy - r, 2 * r, 2 * r);
  }
  // 2. alfa -> culoare pe rampa
  const im = g.getImageData(0, 0, W, H), px = im.data;
  for (let i = 3; i < px.length; i += 4) {
    const a = px[i] / 255;
    if (!a) continue;
    const [cr, cg, cb] = rampa(Math.min(1, a));
    px[i - 3] = cr; px[i - 2] = cg; px[i - 1] = cb; px[i] = Math.round(Math.min(1, a * 1.4) * 190);
  }
  g.putImageData(im, 0, 0);
  wrap.dataset.caldura = String(d.puncte);
}
$('#harta-heat-ctl').onclick = (e) => {
  const b = e.target.closest('button[data-zile]'); if (!b) return;
  hartaHeatZile = +b.dataset.zile;
  try { localStorage.setItem('hartaCaldura', String(hartaHeatZile)); } catch {}
  for (const x of $('#harta-heat-ctl').querySelectorAll('button')) x.classList.toggle('on', x === b);
  hartaHeatLoad();
};
let hartaRedim = 0;
window.addEventListener('resize', () => { clearTimeout(hartaRedim); hartaRedim = setTimeout(() => { if (hartaHeatData) hartaHeatDesen(); }, 150); });

/* ---------- detectiile live ca puncte pe plan ----------
   Doar camerele calibrate; serverul proiecteaza (omografie.py) punctul de pe sol al fiecarui
   chenar. Cerem la 2 s, doar cat se vede harta (nu in editare). */
let hartaLivePts = [];
const VEHICULE = new Set(['car', 'truck', 'bus', 'motorcycle', 'bicycle']);
function hartaLiveDesen() {
  const svg = $('#harta-svg'), img = $('#harta-img');
  svg.querySelector('#harta-live')?.remove();
  if (hartaEdit || !hartaLivePts.length || !img.naturalWidth) return;
  const W = img.naturalWidth, H = img.naturalHeight, k = W / Math.max(1, svg.clientWidth || W);
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.id = 'harta-live';
  for (const pt of hartaLivePts) {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    const fel = pt.lbl === 'person' ? 'om' : VEHICULE.has(pt.lbl) ? 'vehicul' : 'altceva';
    c.setAttribute('class', `hlive ${fel}${(pt.varsta || 0) > 4 ? ' vechi' : ''}`);
    c.setAttribute('cx', (pt.x * W).toFixed(1)); c.setAttribute('cy', (pt.y * H).toFixed(1));
    c.setAttribute('r', (6 * k).toFixed(2)); c.setAttribute('stroke-width', (1.5 * k).toFixed(2));
    g.appendChild(c);
  }
  svg.appendChild(g);
}
async function hartaLive() {
  const p = hartaPlan();
  if (document.hidden || hartaEdit || !p || $('#harta').classList.contains('hidden')
      || !Object.values(p.camere || {}).some((x) => x.calibrare)) {
    if (hartaLivePts.length) { hartaLivePts = []; hartaLiveDesen(); }
    return;
  }
  try {
    const d = await (await fetch(`/api/harta/${p.id}/live`)).json();
    hartaLivePts = d.puncte || [];
  } catch { hartaLivePts = []; }
  hartaLiveDesen();
}
setInterval(hartaLive, 2000);
/* ---------- scara planului ----------
   p.scara = doua puncte (fractii) + distanta reala in metri. Masura folosita la desen vine
   insa din p.metrica (limita proprietatii, daca are laturi cunoscute; altfel scara). */
// bara de scara (stanga jos) pe orice plan cu scara; in editare si linia masurata
function hartaScaraDesen(p, W, H, k, met) {
  const svg = $('#harta-svg'), NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    for (const [a, v] of Object.entries(attrs)) e.setAttribute(a, v);
    if (text != null) e.textContent = text;
    svg.appendChild(e); return e;
  };
  const pct = (x, y) => el('circle', { class: 'hs-pct', cx: x, cy: y, r: 4 * k, 'stroke-width': k });
  if (hartaEdit && p.scara && !hartaMasoara && (!met || met.sursa === 'scara')) {
    const s = p.scara, x1 = s.x1 * W, y1 = s.y1 * H, x2 = s.x2 * W, y2 = s.y2 * H;
    el('line', { class: 'hs-linie', x1, y1, x2, y2, 'stroke-width': 2 * k, 'stroke-dasharray': `${6 * k} ${4 * k}` });
    pct(x1, y1); pct(x2, y2);
    el('text', { class: 'hs-txt', x: (x1 + x2) / 2 + 6 * k, y: (y1 + y2) / 2 - 6 * k, 'font-size': 12 * k, 'stroke-width': 3 * k },
      `${String(s.m).replace('.', ',')} m`);
  }
  if (hartaMasoara && hartaMasoara.length) pct(hartaMasoara[0].x * W, hartaMasoara[0].y * H);
  if (!met) return;
  const mpp = metriPePxX(met);   // bara e orizontala: metri pe pixel pe orizontala
  const tinta = 120 * k * mpp;   // ~120 px de ecran, in metri
  const m = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000].filter((x) => x <= tinta).pop() || 1;
  const L = m / mpp, x0 = 16 * k, y0 = H - 16 * k, tic = 6 * k;
  const d = `M${x0},${y0 - tic} V${y0} H${x0 + L} V${y0 - tic}`;
  el('path', { class: 'hbara-umbra', d, 'stroke-width': 4 * k });
  el('path', { class: 'hbara', d, 'stroke-width': 2 * k });
  el('text', { class: 'hbara-txt', x: x0 + L + 6 * k, y: y0, 'font-size': 12 * k, 'stroke-width': 3 * k }, `${m} m`);
}
// masurarea: doua clicuri pe plan, apoi distanta. Prinsa in faza de captura pe SVG, ca sa nu
// ajunga la marcaje (tragere / card) cat timp masori.
$('#harta-svg').addEventListener('pointerdown', async (e) => {
  if (!hartaMasoara) return;
  e.preventDefault(); e.stopPropagation();
  const r = $('#harta-svg').getBoundingClientRect();
  const f = { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  if (!hartaMasoara.length) { hartaMasoara = [f]; renderHarta(); return; }
  const a = hartaMasoara[0];
  hartaMasoara = null;
  const txt = prompt(t('Câți metri sunt, în realitate, între cele două puncte?'), '');
  const m = txt == null ? NaN : Number(txt.trim().replace(',', '.'));
  if (!(m > 0)) { if (txt != null) toast(t('Distanța trebuie să fie un număr de metri.'), true); renderHarta(); return; }
  const p = hartaPlan();
  const d = await post('/api/harta', { id: p.id, scara: { x1: a.x, y1: a.y, x2: f.x, y2: f.y, m } });
  if (d && !d.error) { toast(t('Scara a fost salvată.')); hartaActualizeaza(d.plan); } else renderHarta();
}, true);
$('#harta-scara').onclick = () => {
  if (!hartaPlan() || !hartaPlan().imagine) { toast(t('Încarcă întâi imaginea planului'), true); return; }
  limitaOpreste();
  hartaMasoara = hartaMasoara ? null : [];
  renderHarta();
};
$('#harta-scara-del').onclick = async () => {
  const p = hartaPlan(); if (!p || !p.scara) return;
  if (!confirm(t('Ștergi scara planului? Camerele revin la conul simplu.'))) return;
  const d = await post('/api/harta', { id: p.id, scara: null });
  if (d && !d.error) hartaActualizeaza(d.plan);
};
function hartaDrag(g, c, pos) {
  const svg = $('#harta-svg'), img = $('#harta-img');
  const toFrac = (e) => {
    const r = svg.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  let moved = false, ox = pos.x, oy = pos.y;
  // Cheia (audit H1): in timpul tragerii NU reconstruim SVG-ul — drawHarta() inlocuia <g>-ul care
  // tinea pointer capture + handlerii, deci tragerea se rupea si pointerup nu mai salva. Mutam
  // ACELASI element cu un `transform` si redesenam + salvam o SINGURA data, la final.
  const incheie = async (salveaza) => {
    g.onpointermove = null; g.classList.remove('drag'); g.removeAttribute('transform');
    if (!moved) return;
    if (!salveaza) { pos.x = ox; pos.y = oy; }   // anulat: revenim la pozitia dinainte
    drawHarta();
    if (salveaza) await hartaSave({ [c.id]: pos });
  };
  g.onpointerdown = (e) => {
    e.preventDefault(); g.setPointerCapture?.(e.pointerId); g.classList.add('drag'); moved = false;
    ox = pos.x; oy = pos.y;
    hartaSel = c.id; renderHartaSide();
    for (const m of Object.values(hartaMk)) m.classList.toggle('sel', m === g);
    g.onpointermove = (ev) => {
      const f = toFrac(ev); pos.x = f.x; pos.y = f.y; moved = true;
      const W = img.naturalWidth || 1, H = img.naturalHeight || 1;
      g.setAttribute('transform', `translate(${((pos.x - ox) * W).toFixed(1)},${((pos.y - oy) * H).toFixed(1)})`);
    };
  };
  g.onpointerup = () => incheie(true);
  g.onlostpointercapture = () => incheie(true);   // pierderea capturii = tot eliberare -> salvam
  g.onpointercancel = () => incheie(false);
}
async function hartaSave(camere) {
  const p = hartaPlan();
  if (!p) return;
  const d = await post('/api/harta', { id: p.id, camere });
  if (d && !d.error) { hartaActualizeaza(d.plan); $('#harta-info').textContent = t('{n} camere pe plan — salvat ✓', { n: Object.keys(p.camere).length }); }
}
function renderHartaSide() {
  const p = hartaPlan();
  if (!p) return;
  limitaPanou(p);
  const placed = new Set(Object.keys(p.camere || {}).map(Number));
  const libere = S.cameras.filter((c) => !placed.has(c.id));
  $('#harta-unplaced').innerHTML = lista(libere, (c) => html`<div class="u-row" data-id="${c.id}"><b>${c.id}.</b> ${c.name}</div>`,
    html`<p class="hint">${t('Toate camerele sunt pe un plan.')}</p>`);
  for (const row of document.querySelectorAll('#harta-unplaced .u-row')) {
    const pune = () => { const id = +row.dataset.id; hartaSel = id; hartaSave({ [id]: { x: 0.5, y: 0.5, dir: 0, fov: 70 } }); };
    row.tabIndex = 0;
    row.onclick = pune;
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pune(); } };   // audit H3
  }
  const selBox = $('#harta-sel');
  const pos = hartaSel !== null ? (p.camere || {})[hartaSel] : null;
  selBox.classList.toggle('hidden', !pos);
  if (!pos) return;
  const c = S.cameras.find((x) => x.id === hartaSel);
  $('#harta-sel-name').textContent = c ? `${c.id}. ${c.name}` : t('cam {id}', { id: hartaSel });
  // raspunsul unei salvari redeseneaza panoul: nu suprascriem campul in care scrii chiar acum
  const pune = (id, v) => { const el = $(id); if (el !== document.activeElement) el.value = v; };
  pune('#harta-dir', pos.dir); pune('#harta-fov', pos.fov);
  pune('#harta-h', pos.inaltime ?? ''); pune('#harta-tilt', pos.inclinare ?? '');
  const calib = pos.calibrare;
  $('#harta-cal-del').classList.toggle('hidden', !calib);
  $('#harta-cal-open').textContent = calib ? t('Refă calibrarea') : t('Calibrează pe plan');
  $('#harta-cal-stare').textContent = calib
    ? [t('Calibrată: {n} puncte, {err}.', { n: calib.perechi.length, err: hartaEroareText(calib, p.metrica) }),
       t('Zona de pe hartă vine din calibrare.'), hartaMontajDinCalibrare(pos, p.metrica)].filter(Boolean).join(' ')
    : t('Necalibrată: detecțiile ei nu apar ca puncte pe hartă.');
  const th = $('#harta-thumb');
  if (th.dataset.cam !== String(hartaSel)) { th.dataset.cam = hartaSel; th.src = `/api/thumb/${hartaSel}?t=${Date.now()}`; }
  hartaValori(pos);
}
/* Valorile afisate langa glisoare + cat sol acopera camera, din inaltime, inclinare si
   deschidere. Unghiul vertical se estimeaza din cel orizontal pentru un cadru 16:9;
   e o aproximare ca sa verifici montajul, nu o masuratoare. */
function hartaValori(pos) {
  $('#harta-dir-v').textContent = `${Math.round(pos.dir)}°`;
  $('#harta-fov-v').textContent = `${Math.round(pos.fov)}°`;
  $('#harta-sol').textContent = hartaSol(pos);
}
function hartaSol(pos) {
  if (pos.inaltime == null || pos.inclinare == null || !pos.inaltime) return '';
  const rad = Math.PI / 180;
  const v = 2 * Math.atan(Math.tan(pos.fov / 2 * rad) * 9 / 16) / rad;
  const sus = pos.inclinare - v / 2, jos = pos.inclinare + v / 2;
  const m = (x) => x < 10 ? x.toFixed(1) : String(Math.round(x));
  const aproape = jos >= 90 ? 0 : pos.inaltime / Math.tan(jos * rad);
  return sus <= 0
    ? t('Vede solul de la ~{a} m până la orizont (unghi vertical ~{v}°).', { a: m(aproape), v: Math.round(v) })
    : t('Vede solul de la ~{a} m până la ~{b} m (unghi vertical ~{v}°).', { a: m(aproape), b: m(pos.inaltime / Math.tan(sus * rad)), v: Math.round(v) });
}
/* Ce spune calibrarea despre montaj: centrul imaginii cade pe plan la o anumita distanta de
   camera; cu inaltimea introdusa, asta inseamna o anumita inclinare. Daca difera mult de cea
   introdusa, una dintre ele (de obicei inaltimea estimata din ochi) nu e buna. */
function hartaMontajDinCalibrare(pos, met) {
  const q = met && pos.calibrare && proiect(pos.calibrare.h, 0.5, 0.5);
  if (!q) return '';
  const d = lungime(met, (q[0] - pos.x) * met.w, (q[1] - pos.y) * met.h);
  if (!(d > 0.5)) return '';
  const fmt1 = (x) => x.toFixed(x < 10 ? 1 : 0).replace('.', ',');
  if (!(pos.inaltime > 0)) return t('Centrul imaginii cade pe plan la ~{d} m de cameră.', { d: fmt1(d) });
  return t('Centrul imaginii cade pe plan la ~{d} m; cu înălțimea de {h} m, înclinarea ar fi ~{u}° (introdusă: {i}°).',
    { d: fmt1(d), h: fmt1(pos.inaltime), u: Math.round(Math.atan2(pos.inaltime, d) * 180 / Math.PI), i: pos.inclinare ?? '—' });
}
// cea mai mare abatere a perechilor calibrarii: in metri cu masura planului, altfel % din plan
function hartaEroareText(calib, met) {
  const e = Math.max(...eroriCalibrare(calib.perechi, calib.h, met));
  return met ? t('eroare maximă ~{m} m', { m: e.toFixed(1).replace('.', ',') })
             : t('eroare maximă {p}% din plan', { p: (e * 100).toFixed(1).replace('.', ',') });
}
$('#harta-cal-open').onclick = () => {
  const p = hartaPlan(), c = S.cameras.find((x) => x.id === hartaSel);
  if (!p || !c) return;
  if (!p.imagine) { toast(t('Încarcă întâi imaginea planului'), true); return; }
  openCalibrare(p, c, hartaActualizeaza);
};
$('#harta-cal-del').onclick = async () => {
  const p = hartaPlan(); if (!p || hartaSel === null) return;
  if (!confirm(t('Ștergi calibrarea camerei? Detecțiile ei nu vor mai apărea pe hartă.'))) return;
  const d = await post('/api/harta', { id: p.id, cam: hartaSel, calibrare: null });
  if (d && !d.error) hartaActualizeaza(d.plan);
};
const hartaPos = () => { const p = hartaPlan(); return p && hartaSel !== null ? (p.camere || {})[hartaSel] : null; };
$('#harta-dir').oninput = () => { const pos = hartaPos(); if (pos) { pos.dir = +$('#harta-dir').value; hartaValori(pos); drawHarta(); } };
$('#harta-fov').oninput = () => { const pos = hartaPos(); if (pos) { pos.fov = +$('#harta-fov').value; hartaValori(pos); drawHarta(); } };
$('#harta-dir').onchange = $('#harta-fov').onchange = () => { const pos = hartaPos(); if (pos) hartaSave({ [hartaSel]: pos }); };
// inaltimea si inclinarea: gol = necunoscuta (cheia dispare); valorile invalide le refuza serverul
for (const [id, cheie, max] of [['#harta-h', 'inaltime', 50], ['#harta-tilt', 'inclinare', 90]]) {
  $(id).onchange = () => {
    const pos = hartaPos(); if (!pos) return;
    const el = $(id), txt = el.value.trim().replace(',', '.');
    if (txt === '') delete pos[cheie];
    else {
      const val = Number(txt);
      if (!Number.isFinite(val) || val < 0 || val > max) { toast(t('Valoare între 0 și {max}', { max }), true); el.value = pos[cheie] ?? ''; return; }
      pos[cheie] = val;
    }
    hartaValori(pos);
    hartaSave({ [hartaSel]: pos });
  };
}
// miniatura camerei selectate se improspateaza cat timp editezi harta (un cadru, nu un flux)
setInterval(() => {
  if (document.hidden || hartaSel === null || $('#harta').classList.contains('hidden')) return;
  const card = !$('#harta-card').classList.contains('hidden');
  if (!hartaEdit && !card) return;
  const th = $(hartaEdit ? '#harta-thumb' : '#harta-card-img'), id = hartaSel, nou = new Image();
  nou.onload = () => { if (hartaSel === id) th.src = nou.src; };
  nou.src = `/api/thumb/${id}?t=${Date.now()}`;
}, 5000);
$('#harta-unplace').onclick = () => { if (hartaSel !== null) { const id = hartaSel; hartaSel = null; hartaSave({ [id]: null }); } };
$('#harta-edit').onclick = () => { hartaEdit = !hartaEdit; $('#harta-edit').textContent = hartaEdit ? t('Gata') : t('Editează'); hartaSel = null; loadHarta(); };
$('#harta-plan').onchange = () => { hartaPid = $('#harta-plan').value; localStorage.setItem('hartaPlan', hartaPid); hartaSel = null; hartaHeatData = null; renderHarta(); hartaHeatLoad(); };
$('#harta-new').onclick = async () => {
  const nume = prompt(t('Numele planului (ex. Curtea, Parter, Etaj):'), t('Curtea'));
  if (!nume) return;
  const d = await post('/api/harta', { plan_nou: nume });
  if (d && !d.error) { hartaPid = d.plan.id; localStorage.setItem('hartaPlan', hartaPid); loadHarta(); }
};
$('#harta-rename').onclick = async () => {
  const p = hartaPlan(); if (!p) return;
  const nume = prompt(t('Numele nou al planului:'), p.nume);
  if (!nume) return;
  const d = await post('/api/harta', { id: p.id, nume });
  if (d && !d.error) loadHarta();
};
$('#harta-del').onclick = async () => {
  const p = hartaPlan(); if (!p) return;
  if (!confirm(t('Ștergi planul „{nume}” cu imaginea și pozițiile lui?', { nume: p.nume }))) return;
  const r = await fetch(`/api/harta/${p.id}`, { method: 'DELETE' });
  const d = await r.json();
  if (d.error) { alert(t('Eroare: {err}', { err: d.error })); return; }
  hartaPid = ''; loadHarta();
};
$('#harta-upload').onclick = () => { if (hartaPlan()) $('#harta-file').click(); else toast(t('Fă întâi un plan'), true); };
$('#harta-file').onchange = async () => {
  const f = $('#harta-file').files[0], p = hartaPlan();
  if (!f || !p) return;
  const r = await fetch(`/api/harta/${p.id}/imagine`, { method: 'POST', headers: { 'Content-Type': f.type }, body: f });
  const d = await r.json();
  $('#harta-file').value = '';
  if (d.error) { alert(t('Eroare: {err}', { err: d.error })); return; }
  p._v = Date.now();
  loadHarta().then(() => { hartaPlan()._v = p._v; renderHarta(); });
};
/* Cardul din modul vizualizare: clic pe o camera -> imaginea ei langa marcaj, ca sa vezi
   daca ce filmeaza se potriveste cu locul si conul de pe plan. HD / Inregistrari din card. */
let hartaCardCam = null;
function hartaCard(c) {
  const p = hartaPlan(), pos = p && (p.camere || {})[c.id];
  if (!pos) return;
  hartaSel = c.id; hartaCardCam = c;
  for (const [id, m] of Object.entries(hartaMk)) m.classList.toggle('sel', +id === c.id);
  const card = $('#harta-card'), img = $('#harta-card-img');
  $('#harta-card-name').textContent = `${c.id}. ${c.name}`;
  if (img.dataset.cam !== String(c.id)) { img.dataset.cam = c.id; img.removeAttribute('src'); }
  img.src = `/api/thumb/${c.id}?t=${Date.now()}`;
  const mont = [
    pos.inaltime != null ? t('înălțime {h} m', { h: pos.inaltime }) : '',
    pos.inclinare != null ? t('înclinare {u}°', { u: pos.inclinare }) : '',
    t('direcție {d}°, con {f}°', { d: Math.round(pos.dir), f: Math.round(pos.fov) }),
    pos.calibrare ? t('calibrată pe plan') : '',
  ].filter(Boolean).join(' · ');
  $('#harta-card-info').textContent = [mont, hartaSol(pos)].filter(Boolean).join('. ');
  card.classList.remove('hidden');
  // Asezarea: cardul nu trebuie sa acopere conul (exact ce compari cu imaginea). Pe ecran
  // ingust (telefon) sta sub harta; altfel langa marcaj, in partea opusa conului — pe
  // orizontala dupa sin(dir), pe verticala dupa cos(dir) — si tinut in zona hartii.
  const body = $('.harta-body').getBoundingClientRect(), wr = $('#harta-wrap').getBoundingClientRect();
  const w = card.offsetWidth, h = card.offsetHeight, gap = 18;
  let x, y;
  if (body.width < 600) {
    x = (body.width - w) / 2; y = wr.bottom - body.top + 8;
  } else {
    const mx = wr.left - body.left + pos.x * wr.width, my = wr.top - body.top + pos.y * wr.height;
    // ce e desenat pentru camera (con sau zona de sol + eticheta), taiat la zona hartii
    const gb = hartaMk[c.id].getBoundingClientRect();
    const U = { l: Math.max(gb.left, wr.left) - body.left, r: Math.min(gb.right, wr.right) - body.left,
                t: Math.max(gb.top, wr.top) - body.top, b: Math.min(gb.bottom, wr.bottom) - body.top };
    const bh = Math.max(body.height, wr.bottom - body.top);
    const cy0 = Math.max(8, Math.min(my - h / 2, bh - h - 8)), cx0 = Math.max(8, Math.min(mx - w / 2, body.width - w - 8));
    const variante = [[U.r + gap, cy0], [U.l - gap - w, cy0], [cx0, U.b + gap], [cx0, U.t - gap - h]];
    const incape = ([vx, vy]) => vx >= 8 && vx + w <= body.width - 8 && vy >= 8 && vy + h <= bh - 8;
    // nimic nu incape langa desen (zona de sol foarte mare): macar nu peste camera
    [x, y] = variante.find(incape) || [Math.max(8, Math.min(mx + gap, body.width - w - 8)), Math.max(8, my + gap)];
  }
  card.style.left = `${x}px`; card.style.top = `${y}px`;
  $('#harta-card-x').focus({ preventScroll: true });
}
function hartaCardInchide() {
  const card = $('#harta-card');
  if (card.classList.contains('hidden')) return;
  card.classList.add('hidden');
  const id = hartaCardCam && hartaCardCam.id;
  hartaCardCam = null;
  if (!hartaEdit) {
    hartaSel = null;
    for (const m of Object.values(hartaMk)) m.classList.remove('sel');
    if (id != null) hartaMk[id]?.focus({ preventScroll: true });
  }
}
$('#harta-card-x').onclick = hartaCardInchide;
$('#harta-card-hd').onclick = () => { const c = hartaCardCam; hartaCardInchide(); if (c) openHD(c); };
$('#harta-card-pb').onclick = () => { const c = hartaCardCam; hartaCardInchide(); if (c) location.hash = `#pb/${c.id}`; };
// clic in afara cardului si in afara unui marcaj = inchide
document.addEventListener('pointerdown', (e) => {
  if ($('#harta-card').classList.contains('hidden')) return;
  if (e.target.closest('#harta-card') || e.target.closest('#harta-svg .hm')) return;
  hartaCardInchide();
});
function hartaEvents(d) {
  if ($('#harta').classList.contains('hidden')) return;
  for (const [id, g] of Object.entries(hartaMk)) {
    const a = (d.alarms || {})[id], det = (d.detect || {})[id];
    g.classList.toggle('alarm', !!(a && a.active && a.active.length));
    g.classList.toggle('detect', !!(det && det.labels && det.labels.length));
  }
}
async function hartaStare() {
  if ($('#harta').classList.contains('hidden') || !Object.keys(hartaMk).length) return;
  try {
    const rows = await (await fetch('/api/pulse')).json();
    for (const r of rows) { const g = hartaMk[r.id]; if (g) { g.classList.toggle('rec-off', !r.rec && r.mode !== 'off'); g.classList.toggle('alarm', !!r.alarm); } }
  } catch {}
}
setInterval(() => { if (!document.hidden) hartaStare(); }, 6000);
function hartaCritic(camId) {
  const g = hartaMk[+camId];
  if (!g) return;
  g.classList.add('critic');
  setTimeout(() => g.classList.remove('critic'), 8000);
}

limitaInit({ plan: hartaPlan, edit: () => hartaEdit, reda: renderHarta, actualizeaza: hartaActualizeaza,
             alteModuri: () => { hartaMasoara = null; } });

export { hartaCritic, hartaEvents, loadHarta };
