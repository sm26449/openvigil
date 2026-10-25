/* ====================================================================
   Camere: adaugare, editoare (zona, linii), operatii in masa, tabel, panoul de detaliu
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, GO2RTC, JOB_STARE_RO, html, icon, lista, runJob, toast } from './00-core.js';
import { show } from './05-navigare.js';
import { buildLive, deleteCamera, filt, loadCameras, matches, post, renderGroupChips, renderViews, zoomBtns } from './10-live.js';
import { openHD } from './20-hd.js';
import { buildPbCams } from './30-redare.js';
import { storageData } from './40-evenimente.js';
import { flash } from './60-setari.js';
import { regulaDin } from './70-ai.js';
/* ---------- adaugare camera ---------- */
const addOv = $('#addcam-overlay'), addForm = $('#addcam-form'), addMsg = $('#addcam-msg');
function openAdd() { addMsg.textContent = ''; addOv.classList.remove('hidden'); addForm.name.focus(); }
function closeAdd() { addOv.classList.add('hidden'); addForm.reset(); }
$('#add-cam').onclick = openAdd;
$('#addcam-vendor').onchange = () => $('#addcam-http').classList.toggle('hidden', $('#addcam-vendor').value !== 'onvif');
$('#addcam-cancel').onclick = closeAdd;
addOv.onclick = (e) => { if (e.target === addOv) closeAdd(); };

addForm.onsubmit = async (e) => {
  e.preventDefault();
  const body = {
    name: addForm.name.value.trim(),
    ip: addForm.ip.value.trim(),
    port: +addForm.port.value || 554,
    user: addForm.user.value.trim(),
    pass: addForm.pass.value,
    vendor: addForm.vendor.value,                      // hik (ISAPI) sau onvif (generic)
    http_port: +addForm.http_port.value || 80,
  };
  addMsg.className = ''; addMsg.textContent = 'Verific camera...';
  $('#addcam-ok').disabled = true;
  try {
    // verificarile iau ~40 s si ruleaza ca job; mesajele lui apar aici pas cu pas
    const j = await runJob('/api/cameras', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, { download: false, onProgress: (x) => { addMsg.textContent = x.mesaj || JOB_STARE_RO[x.stare] || '...'; } });
    if (!j || j.stare !== 'gata') {
      addMsg.className = 'err'; addMsg.textContent = (j && j.mesaj) || 'Eroare';
      return;
    }
    closeAdd();
    await loadCameras();   // reincarca grila cu noua camera
  } finally {
    $('#addcam-ok').disabled = false;
  }
};

/* ---------- editor zona de detectie ---------- */
let zoneCam = null, zonePts = [], zoneImg = null;
async function openZoneEditor(c) {
  zoneCam = c;
  zonePts = (c.zone || []).slice();
  $('#zone-title').textContent = `▣ Zona de detecție — ${c.name}`;
  $('#zone-overlay').classList.remove('hidden');
  zoneImg = new Image();
  zoneImg.onload = drawZone;
  zoneImg.src = `${GO2RTC}/api/frame.jpeg?src=cam${c.id}&_=${Date.now()}`;
}
function drawZone() {
  const cv = $('#zone-canvas'), body = $('#zone-body');
  if (!zoneImg || !zoneImg.width) return;
  const scale = Math.min(body.clientWidth / zoneImg.width, body.clientHeight / zoneImg.height);
  cv.width = zoneImg.width * scale;
  cv.height = zoneImg.height * scale;
  const g = cv.getContext('2d');
  g.drawImage(zoneImg, 0, 0, cv.width, cv.height);
  if (!zonePts.length) return;
  // umbrim exteriorul, conturam zona
  g.save();
  g.fillStyle = 'rgba(0,0,0,.55)';
  g.beginPath();
  g.rect(0, 0, cv.width, cv.height);
  for (let i = 0; i < zonePts.length; i++) {
    const [x, y] = zonePts[i];
    if (i === 0) g.moveTo(x * cv.width, y * cv.height);
    else g.lineTo(x * cv.width, y * cv.height);
  }
  g.closePath();
  g.fill('evenodd');
  g.restore();
  g.strokeStyle = '#57ab5a';
  g.lineWidth = 2;
  g.beginPath();
  zonePts.forEach(([x, y], i) => {
    if (i === 0) g.moveTo(x * cv.width, y * cv.height);
    else g.lineTo(x * cv.width, y * cv.height);
  });
  g.closePath();
  g.stroke();
  g.fillStyle = '#57ab5a';
  for (const [x, y] of zonePts) {
    g.beginPath();
    g.arc(x * cv.width, y * cv.height, 5, 0, 7);
    g.fill();
  }
}
$('#zone-canvas').onclick = (e) => {
  const cv = $('#zone-canvas');
  const r = cv.getBoundingClientRect();
  zonePts.push([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
  drawZone();
};
$('#zone-undo').onclick = () => { zonePts.pop(); drawZone(); };
$('#zone-save').onclick = async () => {
  if (zonePts.length < 3) { alert('Desenează minim 3 colțuri (sau alege "Fără zona").'); return; }
  const d = await post(`/api/cam/${zoneCam.id}/zone`, { points: zonePts });
  if (d && !d.error) { zoneCam.zone = zonePts.slice(); closeZone(); }
};
$('#zone-clear').onclick = async () => {
  const d = await post(`/api/cam/${zoneCam.id}/zone`, { points: null });
  if (d && !d.error) { zoneCam.zone = null; zonePts = []; closeZone(); }
};
function closeZone() {
  $('#zone-overlay').classList.add('hidden');
  loadCameras();  // reincarca starea butoanelor ▣
}
$('#zone-close').onclick = closeZone;
window.addEventListener('resize', drawZone);

/* ---------- editor linii/zone de numarare (nvr-ai) ----------
   Linie de numarare = tripwire cu 2 puncte: cine o traverseaza e numarat, cu sensul.
   Zona de intrare = poligon: cine intra declanseaza un eveniment. Ambele normalizate
   [0,1], mai multe per camera, salvate in settings.count_lines / count_zones. */
let countCam = null, countLines = [], countZones = [], countMode = 'linie',
  countDraft = [], countImg = null;
const COUNT_HELP = { linie: 'Click = capat · o linie are 2 capete, apoi „Adauga"',
  zona: 'Click = colt · minim 3 colturi, apoi „Adauga"' };

async function openCountEditor(c) {
  countCam = c;
  countDraft = []; countMode = 'linie';
  try {
    const s = await (await fetch('/api/settings')).json();
    countLines = ((s.count_lines || {})[c.id] || []).map((l) => ({ ...l }));
    countZones = ((s.count_zones || {})[c.id] || []).map((z) => ({ ...z }));
    if ($('#count-intrus')) $('#count-intrus').value = (s.intrus_mode || {})[c.id] || 'persoane';
    if ($('#count-scene')) $('#count-scene').checked = (s.scene_cams || []).includes(c.id);
  } catch { countLines = []; countZones = []; }
  $('#count-title').textContent = `⳹ Linii/zone de numărare — ${c.name}`;
  for (const b of document.querySelectorAll('#count-mode button'))
    b.classList.toggle('on', b.dataset.m === countMode);
  $('#count-help').textContent = COUNT_HELP[countMode];
  $('#count-overlay').classList.remove('hidden');
  countImg = new Image();
  countImg.onload = drawCount;
  countImg.src = `${GO2RTC}/api/frame.jpeg?src=cam${c.id}&_=${Date.now()}`;
  renderCountList();
}

function drawCount() {
  const cv = $('#count-canvas'), body = $('#count-body');
  if (!countImg || !countImg.width) return;
  const scale = Math.min(body.clientWidth / countImg.width, body.clientHeight / countImg.height);
  cv.width = countImg.width * scale;
  cv.height = countImg.height * scale;
  const g = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  g.drawImage(countImg, 0, 0, W, H);
  // liniile existente (albastru) cu sageata pe sensul "intra"
  for (const l of countLines) drawLine(g, l.p1, l.p2, '#4184e4', W, H, l.name);
  // zonele existente (verde)
  for (const z of countZones) drawPoly(g, z.poly, '#57ab5a', W, H, z.name);
  // desenul curent (galben)
  if (countMode === 'linie' && countDraft.length)
    drawLine(g, countDraft[0], countDraft[1] || countDraft[0], '#e3b341', W, H);
  else if (countMode === 'zona' && countDraft.length)
    drawPoly(g, countDraft, '#e3b341', W, H, null, true);
}
function drawLine(g, p1, p2, col, W, H, nume) {
  if (!p1) return;
  const [x1, y1] = [p1[0] * W, p1[1] * H], [x2, y2] = [p2[0] * W, p2[1] * H];
  g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 3;
  g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
  for (const [x, y] of [[x1, y1], [x2, y2]]) { g.beginPath(); g.arc(x, y, 5, 0, 7); g.fill(); }
  // sageata pe normala (sensul "intra" = partea pozitiva)
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;
  g.beginPath(); g.moveTo(mx, my); g.lineTo(mx + nx * 22, my + ny * 22); g.stroke();
  g.beginPath(); g.arc(mx + nx * 22, my + ny * 22, 3, 0, 7); g.fill();
  if (nume) { g.font = '13px system-ui'; g.fillText(nume, mx + 6, my - 6); }
}
function drawPoly(g, pts, col, W, H, nume, open) {
  if (!pts || !pts.length) return;
  g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 2;
  g.beginPath();
  pts.forEach(([x, y], i) => { const X = x * W, Y = y * H; i ? g.lineTo(X, Y) : g.moveTo(X, Y); });
  if (!open) g.closePath();
  g.stroke();
  for (const [x, y] of pts) { g.beginPath(); g.arc(x * W, y * H, 4, 0, 7); g.fill(); }
  if (nume) { g.font = '13px system-ui'; g.fillText(nume, pts[0][0] * W + 6, pts[0][1] * H - 6); }
}

function renderCountList() {
  const items = [
    ...countLines.map((l, i) => ({ t: 'linie', i, name: l.name })),
    ...countZones.map((z, i) => ({ t: 'zona', i, name: z.name })),
  ];
  $('#count-list').innerHTML = html`<h4>Definite</h4>${lista(items, (it) => html`
    <div class="count-item">
      <span class="count-badge count-${it.t}">${it.t === 'linie' ? '⳹' : '▱'}</span>
      <span>${it.name}</span>
      <button class="count-regula link-btn" data-t="${it.t}" data-name="${it.name}" title="${it.t === 'linie' ? 'Regulă la trecerea acestei linii (alege sensul în formular)' : 'Regulă la intrarea în această zonă'}">⚙ regulă</button>
      <button class="count-del link-btn" data-t="${it.t}" data-i="${it.i}">✕</button>
    </div>`, '<p class="hint">Nimic inca. Deseneaza pe imagine.</p>')}`;
  for (const b of $('#count-list').querySelectorAll('.count-del'))
    b.onclick = () => {
      (b.dataset.t === 'linie' ? countLines : countZones).splice(+b.dataset.i, 1);
      renderCountList(); drawCount();
    };
  for (const b of $('#count-list').querySelectorAll('.count-regula'))
    b.onclick = () => {
      // salvat sau nu, regula se refera la nume; daca linia e noua, salveaza intai cu „Salveaza tot”
      $('#count-overlay').classList.add('hidden');
      const linie = b.dataset.t === 'linie';
      regulaDin({ tip: linie ? 'trecere' : 'intrare_zona', valoare: linie ? `${b.dataset.name}:intra` : b.dataset.name,
                  cam: countCam ? countCam.id : null, nume: `${linie ? 'Trecere ' : 'Intrare în '}${b.dataset.name} — ${countCam ? countCam.name : ''}` });
    };
}

$('#count-canvas') && ($('#count-canvas').onclick = (e) => {
  const r = $('#count-canvas').getBoundingClientRect();
  const p = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  if (countMode === 'linie') {
    if (countDraft.length >= 2) countDraft = [];
    countDraft.push(p);
  } else countDraft.push(p);
  drawCount();
});
for (const b of document.querySelectorAll('#count-mode button'))
  b.onclick = () => {
    countMode = b.dataset.m; countDraft = [];
    for (const x of document.querySelectorAll('#count-mode button')) x.classList.toggle('on', x === b);
    $('#count-help').textContent = COUNT_HELP[countMode];
    drawCount();
  };
$('#count-undo') && ($('#count-undo').onclick = () => { countDraft.pop(); drawCount(); });
$('#count-add') && ($('#count-add').onclick = () => {
  if (countMode === 'linie') {
    if (countDraft.length < 2) { toast('O linie are nevoie de 2 capete', true); return; }
    const nume = prompt('Numele liniei (ex. „Poarta"):', `linie ${countLines.length + 1}`);
    if (!nume) return;
    countLines.push({ name: nume.trim(), p1: countDraft[0], p2: countDraft[1] });
  } else {
    if (countDraft.length < 3) { toast('O zona are nevoie de minim 3 colțuri', true); return; }
    const nume = prompt('Numele zonei (ex. „Curte"):', `zona ${countZones.length + 1}`);
    if (!nume) return;
    countZones.push({ name: nume.trim(), poly: countDraft.slice() });
  }
  countDraft = []; renderCountList(); drawCount();
});
$('#count-save') && ($('#count-save').onclick = async () => {
  // citesc config-ul complet, actualizez doar camera curenta, salvez
  const s = await (await fetch('/api/settings')).json();
  const cl = { ...(s.count_lines || {}) }, cz = { ...(s.count_zones || {}) };
  const im = { ...(s.intrus_mode || {}) };
  if (countLines.length) cl[countCam.id] = countLines; else delete cl[countCam.id];
  if (countZones.length) cz[countCam.id] = countZones; else delete cz[countCam.id];
  im[countCam.id] = $('#count-intrus') ? $('#count-intrus').value : 'persoane';
  // scene_cams = lista: adaug/scot camera curenta dupa checkbox
  const sc = new Set(s.scene_cams || []);
  if ($('#count-scene') && $('#count-scene').checked) sc.add(countCam.id); else sc.delete(countCam.id);
  const r = await post('/api/settings', { count_lines: cl, count_zones: cz, intrus_mode: im,
    scene_cams: [...sc] });
  if (r && !r.error) { toast('Salvat.'); $('#count-overlay').classList.add('hidden'); }
});
$('#count-deploy') && ($('#count-deploy').onclick = async () => {
  // salvez intai (ca settings sa aiba desenul curent), apoi trimit pe camera
  const s = await (await fetch('/api/settings')).json();
  const cl = { ...(s.count_lines || {}) }, cz = { ...(s.count_zones || {}) };
  if (countLines.length) cl[countCam.id] = countLines; else delete cl[countCam.id];
  if (countZones.length) cz[countCam.id] = countZones; else delete cz[countCam.id];
  await post('/api/settings', { count_lines: cl, count_zones: cz });
  const r = await post(`/api/cam/${countCam.id}/deploy-smart`, {});
  if (r && r.ok) {
    const rez = (r.rezultat || []).map((x) => `${x[0]}: ${x[2] ? 'OK' : 'esec (' + x[1] + ')'}`).join(' · ');
    const ign = (r.zone_ignorate || []).length
      ? ` · zone ignorate (min 4 colțuri pe camera): ${r.zone_ignorate.join(', ')}` : '';
    toast('Trimis pe camera — ' + rez + ign);
  } else toast(r && r.error ? r.error : 'Deploy eșuat', true);
});
$('#count-close') && ($('#count-close').onclick = () => $('#count-overlay').classList.add('hidden'));
window.addEventListener('resize', () => { if (!$('#count-overlay').classList.contains('hidden')) drawCount(); });

/* ---------- selectie multipla + operatii in masa ---------- */
const selected = new Set();
function refreshBulkBar() {
  const bar = $('#bulk-bar');
  bar.classList.toggle('hidden', selected.size === 0);
  $('#bulk-count').textContent = selected.size === 1
    ? '1 camera selectată' : `${selected.size} camere selectate`;
}
$('#sel-all').onclick = () => {
  const shown = S.cameras.filter((c) => matches(c, filt.cams));
  const allIn = shown.every((c) => selected.has(c.id));
  for (const c of shown) allIn ? selected.delete(c.id) : selected.add(c.id);
  buildCamsTable();
};
$('#bulk-clear').onclick = () => { selected.clear(); buildCamsTable(); };
$('#bulk-apply').onclick = async () => {
  const body = { ids: [...selected] };
  if ($('#bulk-record').value) body.record = $('#bulk-record').value;
  if ($('#bulk-group').value.trim()) body.group = $('#bulk-group').value.trim();
  const st = {};
  if ($('#bulk-codec').value) st.codec = $('#bulk-codec').value;
  if ($('#bulk-kbps').value) st.kbps = +$('#bulk-kbps').value;
  if ($('#bulk-fps').value) st.fps = +$('#bulk-fps').value;
  if (Object.keys(st).length) body.stream = st;
  if ($('#bulk-detect').value !== '') body.detect = $('#bulk-detect').value === '1';
  if (Object.keys(body).length < 2) { flash('#bulk-msg', 'Alege ce să se schimbe', true); return; }
  const n = selected.size;
  if (!confirm(`Aplici modificările pe ${n} camere?`)) return;
  const btn = $('#bulk-apply');
  btn.disabled = true;
  flash('#bulk-msg', `Aplic pe ${n} camere (durează ~${Math.ceil(n * 0.6)}s)...`);
  const d = await post('/api/cameras/bulk', body);
  btn.disabled = false;
  if (d && !d.error) {
    const rele = (d.results || []).filter((r) => !r.ok);
    flash('#bulk-msg', `Aplicat pe ${d.reusite}/${d.total}` +
      (rele.length ? ` — probleme: ${rele.map((r) => 'cam' + r.id).join(', ')}` : ' ✓'),
      rele.length > 0);
    for (const el of ['#bulk-record','#bulk-group','#bulk-codec','#bulk-kbps','#bulk-fps','#bulk-detect'])
      $(el).value = '';
    await loadCameras();
    buildCamsTable();
  }
};

/* ---------- tabul Camere (administrare) ---------- */
const HW_RO = { mic: [icon('talk', 13), 'microfon'], talk: [icon('listen', 13), 'difuzor'],
                siren: [icon('siren', 13), 'sirena'], strobe: [icon('strobe', 13), 'stroboscop'],
                zoom: [icon('zoomin', 13), 'zoom optic'], aac: null };
const SMART_RO = { motion: 'Mișcare', line: 'Trecere linie', field: 'Intruziune',
                   enter: 'Intrare zona', exit: 'Ieșire zona', scene: 'Schimbare scena' };
// Cele 12 sunete din firmware, traduse. Sunt rostite in engleza de catre camera;
// eticheta spune ce se aude, ca sa stii ce alegi.
const SOUND_RO = {
  1: 'Sirena', 2: 'Avertisment: zona cu acces interzis',
  3: 'Avertisment: zona interzisă, păstrați distanța',
  4: 'Avertisment: parcarea interzisă', 5: 'Avertisment: parcare interzisă, indepartati-va',
  6: 'Atenție: zona este supravegheată', 7: 'Bun venit; zona este supravegheată',
  8: 'Bun venit', 9: 'Pericol! Păstrați distanța',
  10: 'Sirena + Pericol, păstrați distanța', 11: 'Avertisment sonor', 12: 'Bip scurt',
};
const REC_RO = { continuous: 'Continuu', motion: 'La mișcare', off: 'Oprit' };

/* sortare + paginare, retinute pe dispozitiv */
let sortBy = localStorage.getItem('camSort') || 'id';
let sortDir = localStorage.getItem('camSortDir') === '-1' ? -1 : 1;
let page = 1;
let pageSize = +(localStorage.getItem('camPageSize') ?? 25);
let openCam = null;      // camera deschisa in panou (pentru evidentiere)
let pulseData = {};      // stare live per camera (din /api/pulse)
    // spatiu ocupat per camera (din /api/storage)

function sortCams(list) {
  const key = (c) => ({
    id: c.id, name: (c.name || '').toLowerCase(), group: (c.group || '').toLowerCase(),
    record: c.record || '', detect: c.detect ? 0 : 1,
    gb: -(storageData[c.id] || 0),
  }[sortBy] ?? c.id);
  return [...list].sort((a, b) => {
    const ka = key(a), kb = key(b);
    return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortDir;
  });
}
function setSort(col) {
  if (sortBy === col) sortDir = -sortDir; else { sortBy = col; sortDir = 1; }
  localStorage.setItem('camSort', sortBy);
  localStorage.setItem('camSortDir', String(sortDir));
  buildCamsTable();
}

// LISTA COMPACTA: un rand per camera (~52px) in loc de un card de 400px. Cu 100 de
// camere, o pagina de 5000px nu se poate folosi — un tabel de 5000px nici atat, dar
// randurile scurte se scaneaza din ochi si detaliile se deschid la cerere.
function buildCamsTable() {
  const box = $('#cams-table');
  box.innerHTML = '';
  renderGroupChips('#cams-groups', 'cams', buildCamsTable);
  $('#group-list').innerHTML = html`${[...new Set(S.cameras.map((x) => x.group || 'Altele'))]
    .sort().map((g) => html`<option value="${g}">`)}`;
  refreshBulkBar();
  const all = sortCams(S.cameras.filter((c) => matches(c, filt.cams)));
  $('#cams-info').textContent = all.length === S.cameras.length
    ? `${S.cameras.length} camere` : `${all.length} din ${S.cameras.length}`;
  const per = pageSize || all.length || 1;
  const pages = Math.max(1, Math.ceil(all.length / per));
  if (page > pages) page = pages;
  const shown = pageSize ? all.slice((page - 1) * per, page * per) : all;
  renderPager(all.length, pages);

  const head = document.createElement('div');
  head.className = 'cam-row cam-row-head';
  const cols = [['', ''], ['', ''], ['Camera', 'name'], ['Grup', 'group'], ['Dotări', ''],
                ['Stare', ''], ['Înregistrare', 'record'], ['AI', 'detect'], ['Spațiu', 'gb'], ['', '']];
  for (const [label, key] of cols) {
    const s = document.createElement('span');
    if (key) {
      s.className = 'sortable' + (sortBy === key ? ' sorted' : '');
      s.textContent = label + (sortBy === key ? (sortDir > 0 ? ' ↑' : ' ↓') : '');
      s.onclick = () => setSort(key);
    } else s.textContent = label;
    head.appendChild(s);
  }
  box.appendChild(head);

  if (!shown.length) {
    const empty = document.createElement('div');
    empty.className = 'cams-empty';
    empty.textContent = 'Nicio camera nu corespunde filtrelor.';
    box.appendChild(empty);
  }

  for (const c of shown) {
    const f = c.features || {};
    const row = document.createElement('div');
    row.className = 'cam-row' + (selected.has(c.id) ? ' picked' : '');

    const pick = document.createElement('input');
    pick.type = 'checkbox';
    pick.className = 'cam-pick';
    pick.checked = selected.has(c.id);
    pick.title = 'Selectează pentru operatii în masa';
    pick.onclick = (e) => e.stopPropagation();
    pick.onchange = () => {
      pick.checked ? selected.add(c.id) : selected.delete(c.id);
      row.classList.toggle('picked', pick.checked);
      refreshBulkBar();
    };

    const th = document.createElement('img');
    th.className = 'row-thumb';
    th.loading = 'lazy';
    th.alt = '';
    th.src = `/api/thumb/${c.id}`;
    th.onerror = () => th.classList.add('broken');

    const nm = document.createElement('span');
    nm.className = 'row-name';
    nm.innerHTML = html`<b>${c.name}</b><small>${c.ip}${c.model ? ' · ' + c.model : ''}${c.vendor === 'onvif' ? ' · ONVIF' : ''}</small>`;

    const grp = document.createElement('span');
    grp.className = 'row-group';
    grp.textContent = c.group || 'Altele';

    const caps = document.createElement('span');
    caps.className = 'row-caps';
    const capIcons = [['mic', 'talk', 'microfon'], ['talk', 'listen', 'difuzor'],
                      ['siren', 'siren', 'sirena'], ['strobe', 'strobe', 'stroboscop'],
                      ['zoom', 'zoomin', 'zoom optic']];
    caps.innerHTML = lista(capIcons.filter(([k]) => f[k]),
      ([, ic, t]) => html`<span title="${t}">${icon(ic, 14)}</span>`, '<i>—</i>');

    const p = pulseData[c.id] || {};
    const st = document.createElement('span');
    st.className = 'row-state ' + (p.alarm ? 's-alarm' : p.rec === false ? 's-down' : 's-ok');
    st.textContent = p.alarm ? 'alarma' : p.rec === false ? 'nu scrie' : 'în regula';
    st.dataset.cam = c.id;   // completat cu ultimul segment / golurile de azi (camSanatate)

    const rec = document.createElement('span');
    rec.className = 'row-rec r-' + (c.record || 'continuous');
    rec.textContent = REC_RO[c.record || 'continuous'];

    const ai = document.createElement('span');
    ai.className = 'row-ai' + (c.detect ? ' on' : '');
    ai.textContent = c.detect ? 'pornit' : '—';

    const gb = document.createElement('span');
    gb.className = 'row-gb';
    gb.textContent = storageData[c.id] !== undefined ? `${storageData[c.id]} GB` : '—';

    const go = document.createElement('span');
    go.className = 'row-go';
    go.textContent = '›';

    row.append(pick, th, nm, grp, caps, st, rec, ai, gb, go);
    if (openCam === c.id) row.classList.add('open');
    row.onclick = () => openCamPanel(c);
    box.appendChild(row);
  }
  camSanatate();   // ultimul segment, reporniri, golurile de azi, intr-un singur apel
}

function renderPager(total, pages) {
  const bar = $('#cams-pager');
  bar.classList.toggle('hidden', !pageSize || total <= pageSize);
  const per = pageSize || total;
  const from = total ? (page - 1) * per + 1 : 0;
  $('#pager-info').textContent = `${from}–${Math.min(page * per, total)} din ${total}`;
  $('#pg-pos').textContent = `${page} / ${pages}`;
  $('#pg-first').disabled = $('#pg-prev').disabled = page <= 1;
  $('#pg-last').disabled = $('#pg-next').disabled = page >= pages;
  $('#pg-size').value = String(pageSize);
}
$('#pg-first').onclick = () => { page = 1; buildCamsTable(); };
$('#pg-prev').onclick = () => { page = Math.max(1, page - 1); buildCamsTable(); };
$('#pg-next').onclick = () => { page += 1; buildCamsTable(); };
$('#pg-last').onclick = () => { page = 1e9; buildCamsTable(); };
$('#pg-size').onchange = (e) => {
  pageSize = +e.target.value;
  localStorage.setItem('camPageSize', String(pageSize));
  page = 1;
  buildCamsTable();
};

/* ---------- panou de detaliu: toate setarile unei camere, la cerere ---------- */
function section(title, ...els) {
  const s = document.createElement('div');
  s.className = 'panel-sec';
  s.innerHTML = html`<h3>${title}</h3>`;
  s.append(...els.filter(Boolean));
  return s;
}
function field(label, control, hint) {
  const w = document.createElement('label');
  w.className = 'panel-field';
  w.innerHTML = html`<span>${label}</span>`;
  w.appendChild(control);
  if (hint) {
    const h = document.createElement('small');
    h.textContent = hint;
    w.appendChild(h);
  }
  return w;
}
function openCamPanel(c) {
  openCam = c.id;
  document.body.classList.add('panel-open');
  const p = $('#cam-panel');
  const body = $('#cam-panel-body');
  const f = c.features || {};
  $('#cam-panel-title').textContent = c.name;
  body.innerHTML = '';
  const msg = document.createElement('span');
  msg.className = 'cam-msg';
  const flashP = (t) => { msg.textContent = t; setTimeout(() => { msg.textContent = ''; }, 2500); };

  const img = document.createElement('img');
  img.className = 'panel-thumb';
  img.src = `/api/thumb/${c.id}?t=${Date.now()}`;
  img.onclick = () => { closeCamPanel(); show('live'); openHD(c); };
  img.title = 'Click pentru imagine mare';
  body.appendChild(img);

  // General
  const nm = document.createElement('input');
  nm.value = c.name;
  nm.onchange = async () => {
    await post(`/api/cam/${c.id}/config`, { name: nm.value });
    c.name = nm.value; $('#cam-panel-title').textContent = nm.value;
    buildCamsTable(); buildLive(); buildPbCams(); flashP('Salvat');
  };
  const gr = document.createElement('input');
  gr.value = c.group || 'Altele';
  gr.setAttribute('list', 'group-list');
  gr.onchange = async () => {
    await post(`/api/cam/${c.id}/config`, { group: gr.value });
    c.group = gr.value.trim() || 'Altele';
    buildCamsTable(); buildLive(); flashP('Salvat');
  };
  const links = document.createElement('div');
  links.className = 'panel-links';
  links.innerHTML = html`<a href="http://${c.ip}" target="_blank">${icon('external', 13)} Interfața camerei</a>${
    c.nvr_channel ? html`<span class="hint">canal NVR ${c.nvr_channel} (istoric vechi)</span>` : ''}`;
  body.appendChild(section('General', field('Nume', nm), field('Grup', gr), links));

  if (f.zoom) {
    const bara = document.createElement('div');
    bara.className = 'zoom-bara';
    const reload = () => { img.src = `/api/thumb/${c.id}?t=${Date.now()}`; };
    for (const b of zoomBtns(c, () => setTimeout(reload, 1500))) bara.appendChild(b);
    const nota = document.createElement('p');
    nota.className = 'hint';
    const scrieNota = () => {
      nota.textContent = 'Ține apăsat cât vrei să se apropie. '
        + (f.ptz ? 'Camera se și rotește — comenzile sunt în meniul de peste imagine.'
                 : f.ptz === false ? 'Camera nu se rotește, are doar lentila motorizată.'
                 : 'Nu știm încă dacă se rotește.')
        + ' Imaginea de sus se reîmprospătează după fiecare reglaj.';
    };
    scrieNota();
    const proba = document.createElement('button');
    proba.className = 'btn sm';
    proba.textContent = f.ptz === undefined ? 'Verifică dacă se rotește' : 'Verifică din nou';
    proba.title = 'Rotește puțin camera și compară imaginile. Poziția se salvează înainte '
      + 'și se reface după, iar dacă nu se poate salva, proba nici nu se face.';
    proba.onclick = async () => {
      proba.disabled = true; proba.textContent = 'Verific...';
      try {
        const r = await (await fetch(`/api/cam/${c.id}/detect-ptz`, { method: 'POST' })).json();
        Object.assign(f, r);
        c.features = f;
        scrieNota();
        flashP(r.ptz ? 'Se rotește — au apărut comenzile de rotire' : 'Nu se rotește');
        buildLive();
      } catch (e) { flashP('Nu a mers'); }
      proba.disabled = false; proba.textContent = 'Verifică din nou';
    };
    body.appendChild(section('Zoom și rotire', bara, proba, nota));
  }

  // Conexiune — pana acum se putea schimba doar editand fisierul de pe server
  const ip = document.createElement('input');
  ip.value = c.ip || '';
  const port = document.createElement('input');
  port.type = 'number'; port.value = c.port || 554;
  const user = document.createElement('input');
  user.value = c.user || 'admin';
  user.autocomplete = 'off';
  const pass = document.createElement('input');
  pass.type = 'password';
  pass.placeholder = 'necompletat = ramane parola actuala';
  pass.autocomplete = 'new-password';
  const connBtn = document.createElement('button');
  connBtn.className = 'panel-action';
  connBtn.textContent = 'Verifică și salvează conexiunea';
  connBtn.onclick = async () => {
    const body = { ip: ip.value.trim(), port: +port.value || 554, user: user.value.trim() };
    if (pass.value) body.pass = pass.value;
    connBtn.disabled = true;
    connBtn.textContent = 'Verific...';
    const d = await post(`/api/cam/${c.id}/config`, body);
    connBtn.disabled = false;
    connBtn.textContent = 'Verifică și salvează conexiunea';
    if (d && !d.error) {
      Object.assign(c, body);
      pass.value = '';
      flashP('Conexiune verificată și salvată');
      buildCamsTable();
    }
  };
  body.appendChild(section('Conexiune',
    field('Adresa IP', ip), field('Port RTSP', port),
    field('Utilizator', user), field('Parola', pass,
      'Se testează conexiunea înainte de salvare — dacă datele sunt greșite, nu se schimbă nimic'),
    connBtn));

  // Inregistrare
  const sel = document.createElement('select');
  for (const [v, l] of Object.entries(REC_RO)) {
    const o = document.createElement('option');
    o.value = v; o.textContent = l;
    sel.appendChild(o);
  }
  sel.value = c.record || 'continuous';
  sel.onchange = async () => {
    const d = await post(`/api/cam/${c.id}/config`, { record: sel.value });
    if (d && !d.error) { c.record = sel.value; buildCamsTable(); flashP('Salvat'); }
  };
  const rd = document.createElement('input');
  rd.type = 'number'; rd.min = 1; rd.max = 365;
  rd.placeholder = 'implicit';
  rd.value = (c.retention || {}).days || '';
  const re = document.createElement('input');
  re.type = 'number'; re.min = 1; re.max = 730;
  re.placeholder = 'implicit';
  re.value = (c.retention || {}).event_days || '';
  const saveRet = async () => {
    const r = {};
    if (rd.value) r.days = +rd.value;
    if (re.value) r.event_days = +re.value;
    const d = await post(`/api/cam/${c.id}/config`,
      { retention: Object.keys(r).length ? r : null });
    if (d && !d.error) { c.retention = Object.keys(r).length ? r : null; flashP('Salvat'); }
  };
  rd.onchange = saveRet; re.onchange = saveRet;
  body.appendChild(section('Înregistrare',
    field('Mod', sel, 'La miscare = se pastreaza doar segmentele cu evenimente (±30s)'),
    field('Păstrează înregistrarea obișnuită (zile)', rd, 'gol = foloseste setarea generala'),
    field('Păstrează segmentele cu evenimente (zile)', re, 'gol = foloseste setarea generala')));

  // Calitate
  const qbox = document.createElement('div');
  qbox.className = 'panel-loading';
  qbox.textContent = 'Citesc setările din camera...';
  body.appendChild(section('Calitate și spațiu', qbox));
  fetch(`/api/cam/${c.id}/stream`).then((r) => r.json()).then((s) => {
    if (s.error) { qbox.textContent = 'Indisponibil: ' + s.error; return; }
    qbox.className = '';
    qbox.innerHTML = '';
    const codec = document.createElement('select');
    for (const cd of (s.codecs.length ? s.codecs : [s.codec])) {
      const o = document.createElement('option');
      o.value = cd; o.textContent = cd + (cd === 'H.265' ? ' (recomandat)' : '');
      codec.appendChild(o);
    }
    codec.value = s.codec;
    const kb = document.createElement('input');
    kb.type = 'number'; kb.step = 256; kb.min = 256; kb.max = 16384; kb.value = s.kbps;
    const fp = document.createElement('input');
    fp.type = 'number'; fp.min = 1; fp.max = 30; fp.value = s.fps;
    const est = document.createElement('small');
    const refresh = () => { est.textContent = `${s.width}x${s.height} · ~${(kb.value * 0.0108).toFixed(1)} GB/zi`; };
    refresh();
    const save = async () => {
      const d = await post(`/api/cam/${c.id}/stream`,
        { codec: codec.value, kbps: +kb.value, fps: +fp.value });
      if (d && !d.error) { refresh(); flashP('Salvat'); }
    };
    codec.onchange = save; kb.onchange = save; fp.onchange = save; kb.oninput = refresh;
    qbox.append(field('Codec', codec), field('Bitrate (kbps)', kb),
                field('Cadre pe secunda', fp,
                      'Unele camere revin singure la maximul lor — vezi nota din README'),
                est);
  });

  // Detectie AI
  const aiWrap = document.createElement('div');
  const aiBtn = document.createElement('button');
  aiBtn.className = 'panel-toggle' + (c.detect ? ' on' : '');
  aiBtn.innerHTML = icon('paw', 14) + (c.detect ? ' Detecție pornită' : ' Detecție oprită');
  aiBtn.onclick = async () => {
    c.detect = !c.detect;
    await post(`/api/cam/${c.id}/detect`, { on: c.detect });
    aiBtn.className = 'panel-toggle' + (c.detect ? ' on' : '');
    aiBtn.innerHTML = icon('paw', 14) + (c.detect ? ' Detecție pornită' : ' Detecție oprită');
    buildCamsTable(); flashP('Salvat');
  };
  const zoneBtn = document.createElement('button');
  zoneBtn.className = 'panel-toggle' + (c.zone && c.zone.length ? ' on' : '');
  zoneBtn.innerHTML = icon('zone', 14) + (c.zone && c.zone.length ? ' Zona definită' : ' Fără zona');
  zoneBtn.onclick = () => { closeCamPanel(); openZoneEditor(c); };
  aiWrap.append(aiBtn, zoneBtn);
  if (f.siren) {
    const sirenBtn = document.createElement('button');
    sirenBtn.className = 'panel-toggle' + (c.auto_siren ? ' on' : '');
    sirenBtn.innerHTML = icon('alarm', 14) + (c.auto_siren ? ' Anunț automat pornit' : ' Anunț automat oprit');
    sirenBtn.onclick = async () => {
      c.auto_siren = !c.auto_siren;
      await post(`/api/cam/${c.id}/detect`, { auto_siren: c.auto_siren });
      sirenBtn.className = 'panel-toggle' + (c.auto_siren ? ' on' : '');
      sirenBtn.innerHTML = icon('alarm', 14) + (c.auto_siren ? ' Anunț automat pornit' : ' Anunț automat oprit');
      flashP('Salvat');
    };
    aiWrap.append(sirenBtn);
  }
  body.appendChild(section('Detecție AI (pe server)', aiWrap));

  // Detectii in firmware
  const sm = f.smart || {};
  if (Object.keys(sm).length) {
    const box = document.createElement('div');
    box.className = 'panel-checks';
    for (const [k, on] of Object.entries(sm)) {
      const lab = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = on;
      cb.onchange = async () => {
        cb.disabled = true;
        const d = await post(`/api/cam/${c.id}/config`, { smart: { [k]: cb.checked } });
        cb.disabled = false;
        if (d && d.error) cb.checked = !cb.checked; else { sm[k] = cb.checked; flashP('Salvat'); }
      };
      lab.append(cb, document.createTextNode(' ' + (SMART_RO[k] || k)));
      box.appendChild(lab);
    }
    body.appendChild(section('Detecții în camera', box));
  }

  // Audio
  if (f.siren || f.talk) {
    const a = document.createElement('div');
    if (f.siren) {
      const s2 = document.createElement('select');
      for (const snd of (f.sounds || [])) {
        const o = document.createElement('option');
        o.value = snd.id; o.textContent = SOUND_RO[snd.id] || snd.name;
        s2.appendChild(o);
      }
      for (let i = 1; i <= 3; i++) {
        const o = document.createElement('option');
        o.value = 'c' + i; o.textContent = `Sunetul meu ${i}`;
        s2.appendChild(o);
      }
      const vol = document.createElement('input');
      vol.type = 'range'; vol.min = 1; vol.max = 100; vol.value = 70;
      const play = document.createElement('button');
      play.className = 'panel-action';
      play.innerHTML = icon('siren', 14) + ' Redă anunțul';
      play.onclick = async () => {
        play.disabled = true;
        const v = s2.value;
        await post(`/api/cam/${c.id}/siren`, {
          sound: +String(v).replace('c', ''), volume: +vol.value, times: 1,
          custom: String(v).startsWith('c') });
        flashP('Redat');
        setTimeout(() => { play.disabled = false; }, 2000);
      };
      a.append(field('Anunț', s2), field('Volum', vol), play);
    }
    if (f.talk) {
      const txt = document.createElement('input');
      txt.placeholder = 'Scrie ce să spună camera...';
      const say = document.createElement('button');
      say.className = 'panel-action';
      say.innerHTML = icon('talk', 14) + ' Rostește';
      say.onclick = async () => {
        if (!txt.value.trim()) return;
        say.disabled = true;
        const d = await post(`/api/cam/${c.id}/say`, { text: txt.value.trim(), repeat: 2 });
        say.disabled = false;
        if (d && !d.error) flashP(`Transmis ${d.secunde}s`);
      };
      a.append(field('Mesaj vocal', txt, 'Necesită difuzor conectat la ieșirea audio'), say);
    }
    body.appendChild(section('Audio', a));
  }

  const maint = document.createElement('div');
  const reprobe = document.createElement('button');
  reprobe.className = 'panel-toggle';
  reprobe.textContent = 'Reinteroghează dotările';
  reprobe.title = 'După un upgrade de firmware sau după ce ai conectat un difuzor';
  reprobe.onclick = async () => {
    reprobe.disabled = true;
    reprobe.textContent = 'Interoghez...';
    const d = await post(`/api/cam/${c.id}/reprobe`);
    reprobe.disabled = false;
    reprobe.textContent = 'Reinteroghează dotările';
    if (d && !d.error) { await loadCameras(); flashP('Dotări actualizate'); buildCamsTable(); }
  };
  const reboot = document.createElement('button');
  reboot.className = 'panel-toggle';
  reboot.textContent = 'Repornește camera';
  reboot.onclick = async () => {
    if (!confirm(`Repornești camera "${c.name}"?\nRevine în aproximativ 40 de secunde.`)) return;
    const d = await post(`/api/cam/${c.id}/reboot`);
    if (d && !d.error) flashP('Camera repornește...');
  };
  maint.append(reprobe, reboot);
  body.appendChild(section('Întreținere', maint));

  if (c.id >= 101) {
    const del = document.createElement('button');
    del.className = 'panel-action danger';
    del.innerHTML = icon('trash', 14) + ' Șterge camera';
    del.onclick = () => { closeCamPanel(); deleteCamera(c); };
    body.appendChild(section('Zona periculoasă', del));
  }
  body.appendChild(msg);
  p.classList.remove('hidden');
  buildCamsTable();   // evidentiaza randul deschis
}
function closeCamPanel() {
  openCam = null;
  document.body.classList.remove('panel-open');
  $('#cam-panel').classList.add('hidden');
  buildCamsTable();
}

/* scanner de retea */
$('#cam-panel-close').onclick = closeCamPanel;
// sageti sus/jos parcurg lista, Enter deschide camera
document.addEventListener('keydown', (e) => {
  if ($('#cams').classList.contains('hidden')) return;
  if (!['ArrowDown', 'ArrowUp'].includes(e.key)) return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
  const rows = [...document.querySelectorAll('#cams-table .cam-row:not(.cam-row-head)')];
  if (!rows.length) return;
  e.preventDefault();
  const cur = rows.findIndex((r) => r.classList.contains('kb'));
  rows.forEach((r) => r.classList.remove('kb'));
  const next = e.key === 'ArrowDown' ? Math.min(rows.length - 1, cur + 1) : Math.max(0, cur - 1);
  rows[next].classList.add('kb');
  rows[next].scrollIntoView({ block: 'nearest' });
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#cam-panel').classList.contains('hidden')) closeCamPanel();
});
$('#cams-search').oninput = (e) => { filt.cams.q = e.target.value; buildCamsTable(); };
$('#live-search').oninput = (e) => { filt.live.q = e.target.value; S.currentView = ''; buildLive(); renderViews(); };
$('#scan-open').onclick = () => {
  $('#scan-panel').classList.toggle('hidden');
  if (!$('#scan-cidr').value && S.cameras.length) {
    const ip = S.cameras[0].ip || '192.168.1.1';
    $('#scan-cidr').value = ip.split('.').slice(0, 3).join('.') + '.0/24';
  }
};
let scanTimer = null;
$('#scan-go').onclick = async () => {
  const d = await post('/api/discover', {
    cidr: $('#scan-cidr').value.trim(),
    user: $('#scan-user').value.trim(),
    pass: $('#scan-pass').value,
  });
  if (!d || d.error) return;
  $('#scan-results').innerHTML = '';
  $('#scan-add').classList.add('hidden');
  clearInterval(scanTimer);
  scanTimer = setInterval(pollScan, 1000);
  pollScan();
};
async function pollScan() {
  const s = await (await fetch('/api/discover')).json();
  $('#scan-status').textContent = s.error ? 'Eroare: ' + s.error
    : s.running ? `Scanez... ${s.done}/${s.total} adrese, ${s.found.length} găsite`
                : `Gata: ${s.found.length} dispozitive găsite`;
  if (!s.running) clearInterval(scanTimer);
  const box = $('#scan-results');
  box.innerHTML = '';
  for (const it of s.found) {
    const row = document.createElement('label');
    row.className = 'scan-row' + (it.known ? ' known' : '');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.disabled = it.known || !it.auth;
    cb.checked = !it.known && it.auth;
    cb.dataset.info = JSON.stringify(it);
    row.appendChild(cb);
    row.append(` ${it.ip} — ${it.model || it.vendor}` + (it.vendor === 'onvif' ? ' [ONVIF]' : '') +
      (it.known ? ' (deja adăugată)' : it.auth ? '' : ' (parola necunoscută)'));
    box.appendChild(row);
  }
  const addable = s.found.some((i) => !i.known && i.auth);
  $('#scan-add').classList.toggle('hidden', !addable || s.running);
}
$('#scan-add').onclick = async () => {
  const picked = [...$('#scan-results').querySelectorAll('input:checked')]
    .map((cb) => JSON.parse(cb.dataset.info));
  if (!picked.length) return;
  $('#scan-status').textContent = `Adaug ${picked.length} camere...`;
  const d = await post('/api/cameras/import', { cameras: picked });
  if (d && !d.error) {
    $('#scan-status').textContent = `Adăugate ${d.added.length} camere ✓`;
    await loadCameras();
    buildCamsTable();
  }
};

export { SOUND_RO, buildCamsTable, openCountEditor, openZoneEditor, pulseData };

/* ---------- sanatatea fiecarei camere in lista: ultimul segment, reporniri, golurile de azi ----------
   Datele vin din /api/continuity (un singur apel pentru toata lista), dupa ce randurile exista;
   cardul camerei spune singur daca „in regula” inseamna chiar „scrie de 2 secunde” sau „3 goluri azi”. */
async function camSanatate() {
  let d, sab = {};
  try { d = await (await fetch('/api/continuity?days=1')).json(); } catch { return; }
  try { sab = (await (await fetch('/api/sabotaj')).json()).sabotaj || {}; } catch {}
  const acum = Object.fromEntries((d.acum || []).map((x) => [x.id, x]));
  const goluri = d.goluri_azi || {};
  for (const st of document.querySelectorAll('#cams-table .row-state[data-cam]')) {
    const id = +st.dataset.cam, a = acum[id], g = goluri[id] || [];
    const parti = [];
    if (a && a.mod === 'continuous') {
      parti.push(a.scrie ? `scrie, ultimul segment acum ${a.varsta < 90 ? a.varsta + ' s' : Math.round(a.varsta / 60) + ' min'}` : `nu scrie de ${Math.round((a.varsta || 0) / 60)} min`);
      if (a.reporniri) parti.push(`${a.reporniri} reporniri`);
    } else if (a) parti.push(a.mod === 'motion' ? 'la mișcare' : 'înregistrare oprită');
    if (g.length) { const min = g.reduce((s, x) => s + (x.minute || 0), 0); parti.push(`${g.length} ${g.length === 1 ? 'gol' : 'goluri'} azi (${min} min)`); st.classList.add('s-goluri'); }
    if (sab[id]) { parti.push(`sabotaj raportat de ${sab[id]} ori azi${sab[id] >= 10 ? ' (probabil IR/insecte noaptea)' : ''}`); st.classList.add('s-goluri'); }
    const old = st.querySelector('small'); if (old) old.remove();
    if (parti.length) { const sm = document.createElement('small'); sm.textContent = parti.join(' · '); st.appendChild(sm); }
  }
}
