/* ====================================================================
   Harta proprietatii
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, html, lista, t, toast } from './00-core.js';
import { post } from './10-live.js';
import { openHD } from './20-hd.js';
/* ---------- Etapa 4: harta proprietatii ----------
   Un plan (imagine) cu camerele asezate si conurile lor de vedere. Pozitiile sunt fractii
   din imagine, deci SVG-ul de deasupra foloseste viewBox-ul imaginii si scaleaza singur.
   Starea vine din /api/events (alarma, detectie) si /api/pulse (inregistreaza?); un
   incident critic face marcajul sa pulseze. Click = HD. Adminul editeaza: trage camerele,
   roteste directia si deschiderea conului din panoul lateral. */
let hartaPlanuri = [], hartaPid = localStorage.getItem('hartaPlan') || '', hartaEdit = false, hartaSel = null;
const hartaMk = {};
const hartaPlan = () => hartaPlanuri.find((p) => p.id === hartaPid) || hartaPlanuri[0];
async function loadHarta() {
  try {
    const d = await (await fetch('/api/harta')).json();
    hartaPlanuri = d.planuri || [];
  } catch { hartaPlanuri = []; }
  const sel = $('#harta-plan');
  sel.innerHTML = html`${hartaPlanuri.map((p) => html`<option value="${p.id}">${p.nume}</option>`)}`;
  sel.classList.toggle('hidden', hartaPlanuri.length < 2 && !hartaEdit);
  const p = hartaPlan();
  hartaPid = p ? p.id : '';
  if (p) sel.value = p.id;
  $('#harta-edit').classList.toggle('hidden', !ADMIN);
  renderHarta();
}
function renderHarta() {
  const p = hartaPlan();
  const img = $('#harta-img'), svg = $('#harta-svg'), wrap = $('#harta-wrap'), empty = $('#harta-empty');
  for (const k in hartaMk) delete hartaMk[k];
  svg.innerHTML = '';
  wrap.classList.toggle('edit', hartaEdit);
  $('#harta-tools').classList.toggle('hidden', !hartaEdit);
  $('#harta-side').classList.toggle('hidden', !hartaEdit || !p);
  if (!p) {
    img.removeAttribute('src'); wrap.classList.add('gol');
    empty.textContent = ADMIN ? t('Niciun plan încă. Apasă „Editează” → „Plan nou”, apoi încarcă o imagine (schiță curții, o captura din satelit) și așază camerele.')
                              : t('Administratorul nu a configurat încă harta proprietății.');
    empty.classList.remove('hidden');
    $('#harta-info').textContent = '';
    return;
  }
  if (!p.imagine) {
    img.removeAttribute('src'); wrap.classList.add('gol');
    empty.textContent = ADMIN ? t('Planul nu are imagine: apasa „Incarca imaginea” (PNG/JPEG/WebP, pana la 8 MB).') : t('Planul nu are încă o imagine.');
    empty.classList.remove('hidden');
  } else {
    empty.classList.add('hidden'); wrap.classList.remove('gol');
    const src = `/api/harta/${p.id}/imagine?v=${p._v || 0}`;
    if (img.getAttribute('src') !== src) { img.onload = () => drawHarta(); img.src = src; }
    else drawHarta();
  }
  const n = Object.keys(p.camere || {}).length;
  $('#harta-info').textContent = hartaEdit ? t('{n} camere pe plan — trage camerele; click pe una o selectează', { n })
                                           : t('{n} camere pe plan', { n });
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
  for (const [cid, pos] of Object.entries(p.camere || {})) {
    const c = names[+cid];
    if (!c) continue;
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'hm' + (hartaSel === +cid ? ' sel' : ''));
    g.dataset.id = cid;
    const cx = pos.x * W, cy = pos.y * H;
    const a1 = (pos.dir - pos.fov / 2) * Math.PI / 180, a2 = (pos.dir + pos.fov / 2) * Math.PI / 180;
    const pt = (a) => `${(cx + R * Math.sin(a)).toFixed(1)},${(cy - R * Math.cos(a)).toFixed(1)}`;
    g.innerHTML = `<path class="con" style="stroke-width:${k}" d="M${cx.toFixed(1)},${cy.toFixed(1)} L${pt(a1)} A${R},${R} 0 ${pos.fov > 180 ? 1 : 0} 1 ${pt(a2)} Z"/>`
      + `<circle class="pct" style="stroke-width:${(2.5 * k).toFixed(2)}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${(9 * k).toFixed(2)}"/>`
      + `<text class="lbl" style="stroke-width:${(3 * k).toFixed(2)}" x="${(cx + 13 * k).toFixed(1)}" y="${(cy + 4 * k).toFixed(1)}"></text>`;
    g.querySelector('text').textContent = `${c.id}. ${c.name}`;
    g.querySelector('text').setAttribute('font-size', (12 * k).toFixed(2));
    if (!hartaEdit) {
      g.onclick = () => openHD(c);
      const ttl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      ttl.textContent = t('{nume} — click pentru HD', { nume: c.name }); g.appendChild(ttl);
    } else hartaDrag(g, c, pos);
    svg.appendChild(g);
    hartaMk[c.id] = g;
  }
  hartaStare();
}
function hartaDrag(g, c, pos) {
  const svg = $('#harta-svg');
  const toFrac = (e) => {
    const r = svg.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  let moved = false;
  g.onpointerdown = (e) => {
    e.preventDefault(); g.setPointerCapture?.(e.pointerId); g.classList.add('drag'); moved = false;
    hartaSel = c.id; renderHartaSide();
    for (const m of Object.values(hartaMk)) m.classList.toggle('sel', m === g);
    g.onpointermove = (ev) => { const f = toFrac(ev); pos.x = f.x; pos.y = f.y; moved = true; drawHarta(); hartaMk[c.id]?.classList.add('drag'); };
  };
  g.onpointerup = async (e) => {
    g.onpointermove = null; g.classList.remove('drag');
    if (moved) await hartaSave({ [c.id]: pos });
  };
  g.onpointercancel = () => { g.onpointermove = null; g.classList.remove('drag'); };
}
async function hartaSave(camere) {
  const p = hartaPlan();
  if (!p) return;
  const d = await post('/api/harta', { id: p.id, camere });
  if (d && !d.error) { p.camere = d.plan.camere; drawHarta(); renderHartaSide(); $('#harta-info').textContent = t('{n} camere pe plan — salvat ✓', { n: Object.keys(p.camere).length }); }
}
function renderHartaSide() {
  const p = hartaPlan();
  if (!p) return;
  const placed = new Set(Object.keys(p.camere || {}).map(Number));
  const libere = S.cameras.filter((c) => !placed.has(c.id));
  $('#harta-unplaced').innerHTML = lista(libere, (c) => html`<div class="u-row" data-id="${c.id}"><b>${c.id}.</b> ${c.name}</div>`,
    html`<p class="hint">${t('Toate camerele sunt pe un plan.')}</p>`);
  for (const row of document.querySelectorAll('#harta-unplaced .u-row')) {
    row.onclick = () => { const id = +row.dataset.id; hartaSel = id; hartaSave({ [id]: { x: 0.5, y: 0.5, dir: 0, fov: 70 } }); };
  }
  const selBox = $('#harta-sel');
  const pos = hartaSel !== null ? (p.camere || {})[hartaSel] : null;
  selBox.classList.toggle('hidden', !pos);
  if (!pos) return;
  const c = S.cameras.find((x) => x.id === hartaSel);
  $('#harta-sel-name').textContent = c ? `${c.id}. ${c.name}` : t('cam {id}', { id: hartaSel });
  $('#harta-dir').value = pos.dir; $('#harta-fov').value = pos.fov;
}
$('#harta-dir').oninput = () => { const p = hartaPlan(); const pos = p && p.camere[hartaSel]; if (pos) { pos.dir = +$('#harta-dir').value; drawHarta(); } };
$('#harta-fov').oninput = () => { const p = hartaPlan(); const pos = p && p.camere[hartaSel]; if (pos) { pos.fov = +$('#harta-fov').value; drawHarta(); } };
$('#harta-dir').onchange = $('#harta-fov').onchange = () => { const p = hartaPlan(); const pos = p && p.camere[hartaSel]; if (pos) hartaSave({ [hartaSel]: pos }); };
$('#harta-unplace').onclick = () => { if (hartaSel !== null) { const id = hartaSel; hartaSel = null; hartaSave({ [id]: null }); } };
$('#harta-edit').onclick = () => { hartaEdit = !hartaEdit; $('#harta-edit').textContent = hartaEdit ? t('Gata') : t('Editează'); hartaSel = null; loadHarta(); };
$('#harta-plan').onchange = () => { hartaPid = $('#harta-plan').value; localStorage.setItem('hartaPlan', hartaPid); hartaSel = null; renderHarta(); };
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
$('#harta-upload').onclick = () => { if (hartaPlan()) $('#harta-file').click(); else toast(t('Fa întâi un plan'), true); };
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

export { hartaCritic, hartaEvents, loadHarta };
