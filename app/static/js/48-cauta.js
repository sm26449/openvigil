/* ====================================================================
   O singura cautare, din antet (campul sau tasta /): textul merge la cautarea semantica
   (poze), un numar de inmatriculare la istoricul LPR, un nume la istoricul persoanei, si
   orice cuvant la jurnalul de evenimente. Rezultatele vin intr-o singura grila, cu chips
   de filtru (Toate / Poze / Numere / Persoane / Jurnal) si perioada; click = fisa evenimentului.
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js.
   ==================================================================== */
import { S } from './state.js';
import { $, DET_RO, fdia, html, lista } from './00-core.js';
import { AL_RO, EV_TIP_RO } from './40-evenimente.js';
import { openFisa } from './45-fisa.js';

const PLACUTA = /^[A-Z]{1,2}\s?\d{2,3}\s?[A-Z]{3}$/i;
let qRez = [], qCat = '', qUltim = '';

async function cauta(q) {
  q = (q || '').trim();
  if (!q) return;
  qUltim = q;
  const dlg = $('#cauta'); if (!dlg) return;
  $('#cauta-q').textContent = q;
  $('#cauta-info').textContent = 'caut…';
  $('#cauta-grid').innerHTML = '';
  if (!dlg.open) dlg.showModal();
  const zile = +($('#cauta-zile').value || 30);
  const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
  const j = async (u) => { try { const r = await fetch(u); return r.ok ? await r.json() : null; } catch { return null; } };
  const qd = fdia(q);
  const [sem, pl, jur, hist] = await Promise.all([
    PLACUTA.test(q) ? null : j(`/api/search?q=${encodeURIComponent(q)}&days=${zile}&limit=40`),   // un numar nu e o descriere de poza
    j(`/api/plates?q=${encodeURIComponent(q.replace(/\s+/g, ''))}`),
    j('/api/journal?limit=400'),
    j(`/api/faces/history?name=${encodeURIComponent(q)}&limit=60`),
  ]);
  const rez = [];
  for (const p of ((sem && sem.results) || [])) {
    rez.push({ cat: 'poze', cam: p.cam, t: `${p.day} ${p.time}`, titlu: DET_RO[p.label] || AL_RO[p.label] || p.label, poza: p.url, tip: 'detectie', det: p.label });
  }
  for (const r of ((pl && pl.reads) || [])) {
    if (!qd || fdia(r.numar).includes(qd.replace(/\s+/g, '')) || fdia(r.nume || '').includes(qd)) {
      rez.push({ cat: 'numere', cam: r.cam, t: r.t, titlu: r.numar + (r.nume ? ` (${r.nume})` : ''), poza: r.url, tip: 'numar', det: r.numar });
    }
  }
  for (const h of ((hist && hist.history) || [])) {
    rez.push({ cat: 'persoane', cam: h.cam, t: h.t, titlu: `${h.nume} · ${Math.round(h.sim * 100)}%`, tip: 'persoana', det: h.nume, cine: h.nume });
  }
  const qcuv = qd.split(/\s+/).filter(Boolean);
  for (const e of ((jur && jur.events) || [])) {
    const txt = fdia([EV_TIP_RO[e.tip] || e.tip, e.eticheta ? (DET_RO[e.eticheta] || e.eticheta) : '', e.nume, e.numar, e.text, e.context, names[e.cam]].filter(Boolean).join(' '));
    if (qcuv.every((w) => txt.includes(w))) {
      rez.push({ cat: 'jurnal', cam: e.cam, t: e.t, titlu: (EV_TIP_RO[e.tip] || e.tip) + (e.eticheta || e.nume || e.numar ? ': ' + (DET_RO[e.eticheta] || e.eticheta || e.nume || e.numar) : ''), tip: e.tip, sev: e.sev, id: e.id, det: e.eticheta || e.nume || e.numar || '', cine: e.nume || '' });
    }
  }
  // dedup (jurnal vs poze la aceeasi secunda pe aceeasi camera) si ordine: cele mai noi primele
  const vazut = new Set();
  qRez = rez.filter((r) => { const k = `${r.cat}|${r.cam}|${r.t}`; if (vazut.has(k)) return false; vazut.add(k); return true; })
    .sort((a, b) => (b.t || '').localeCompare(a.t || ''));
  // pozele semantice sunt ordonate dupa potrivire, nu timp: le tinem primele cand filtrul e „Poze”
  qRender();
}

function qRender() {
  const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
  const items = (qCat ? qRez.filter((r) => r.cat === qCat) : qRez).slice(0, 120);
  const nr = {}; for (const r of qRez) nr[r.cat] = (nr[r.cat] || 0) + 1;
  for (const b of document.querySelectorAll('#cauta-chips button')) {
    const c = b.dataset.cat; b.classList.toggle('on', c === qCat);
    b.textContent = b.dataset.txt + (c ? ` ${nr[c] || 0}` : ` ${qRez.length}`);
  }
  $('#cauta-info').textContent = qRez.length ? `${items.length} rezultate${qCat ? '' : ' din toate sursele'} pentru „${qUltim}”` : `nimic pentru „${qUltim}”`;
  $('#cauta-grid').innerHTML = lista(items, (r, i) => html`
    <button class="q-item" data-i="${i}" data-cat="${r.cat}">
      ${r.poza ? html`<img src="${r.poza}" alt="" loading="lazy">` : html`<span class="q-noimg">${r.cat === 'persoane' ? '👤' : '•'}</span>`}
      <span class="q-main"><span class="q-titlu">${r.titlu}</span><span class="hint">${names[r.cam] || 'cam ' + r.cam} · ${r.t}</span></span>
    </button>`, '');
  for (const b of $('#cauta-grid').querySelectorAll('.q-item')) {
    b.onclick = () => { const r = items[+b.dataset.i]; $('#cauta').close(); openFisa({ cam: r.cam, t: r.t, tip: r.tip, sev: r.sev, id: r.id, det: r.det, poza: r.poza, cine: r.cine }); };
  }
}

$('#cauta') && (() => {
  $('#cauta-close').onclick = () => $('#cauta').close();
  $('#cauta').onclick = (e) => { if (e.target.id === 'cauta') $('#cauta').close(); };
  for (const b of document.querySelectorAll('#cauta-chips button')) { b.dataset.txt = b.textContent; b.onclick = () => { qCat = b.dataset.cat; qRender(); }; }
  $('#cauta-zile').onchange = () => cauta(qUltim);
  const inp = $('#q-global');
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { cauta(inp.value); e.preventDefault(); } });
  $('#cauta-again').addEventListener('keydown', (e) => { if (e.key === 'Enter') { cauta($('#cauta-again').value); e.preventDefault(); } });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) && !document.querySelector('dialog[open]')) { inp.focus(); inp.select(); e.preventDefault(); }
  });
})();

export { cauta };
