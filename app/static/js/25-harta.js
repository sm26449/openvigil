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
let hartaPlanuri = [], hartaPid = localStorage.getItem('hartaPlan') || '', hartaEdit = false, hartaSel = null, hartaEroare = false;
const hartaMk = {};
const hartaPlan = () => hartaPlanuri.find((p) => p.id === hartaPid) || hartaPlanuri[0];
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
  renderHarta();
}
function renderHarta() {
  const p = hartaPlan();
  const img = $('#harta-img'), svg = $('#harta-svg'), wrap = $('#harta-wrap'), empty = $('#harta-empty');
  for (const k in hartaMk) delete hartaMk[k];
  svg.innerHTML = '';
  hartaCardInchide();
  wrap.classList.toggle('edit', hartaEdit);
  $('#harta-tools').classList.toggle('hidden', !hartaEdit);
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
  hartaStare();
}
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
    // raza conului pe ecran (drawHarta: R = 12% din latura mica a planului)
    const a = pos.dir * Math.PI / 180, rs = 0.12 * Math.min(wr.width, wr.height), sn = Math.sin(a);
    const dreapta = mx + gap + (sn > 0.2 ? rs : 0), stanga = mx - gap - w - (sn < -0.2 ? rs : 0);
    const incape = (v) => v >= 8 && v + w <= body.width - 8;
    x = sn > 0.2 ? (incape(stanga) ? stanga : dreapta) : (incape(dreapta) ? dreapta : stanga);
    y = Math.cos(a) < 0 ? my - h - 8 : my + 8;                 // conul in jos -> cardul deasupra
    if (y < 8) y = my + 8;
    x = Math.max(8, Math.min(x, body.width - w - 8));
    y = Math.max(8, y);
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

export { hartaCritic, hartaEvents, loadHarta };
