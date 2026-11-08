/* Program & automatizare: orarul saptamanal de moduri (timeline) + starea regulilor.
 *
 * Doua sectiuni intr-un overlay (ca Centrul de sanatate):
 *  - Program moduri: pe fiecare zi, o lista de tranzitii {ora -> mod}. Serverul comuta modul
 *    la ora fiecarei tranzitii (edge-triggered + recuperare; vezi app/mod.py). Editabil aici,
 *    salvat prin POST /api/mod/program.
 *  - Reguli: lista regulilor cu pornit/oprit si „ultima declansare", editarea duce la AI -> Reguli.
 *
 * Pagina e doar pentru admin (ca regulile). Datele vin din /api/mod/program si /api/rules;
 * nimic nu se salveaza singur — doar la „Salvează programul" / comutatorul unei reguli. */
import { $, LIMBA, html, lista, raw, t, toast } from './00-core.js';

let PRG = null;      // programul in editare: {activ, zile:{"0".."6":[{h,mod}]}}
let META = null;     // {moduri, nume, ico, zile_nume}

function _relativ(epoch) {
  if (!epoch) return t('niciodată');
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return t('chiar acum');
  if (s < 3600) return t('acum {n} min', { n: Math.floor(s / 60) });
  if (s < 86400) return t('acum {n} h', { n: Math.floor(s / 3600) });
  return new Date(epoch * 1000).toLocaleDateString(LIMBA);
}

/* ---------- programul saptamanal ---------- */
function randSegment(z, i, s) {
  const opts = META.moduri.map((m) => html`<option value="${m}"${m === s.mod ? raw(' selected') : ''}>${(META.ico[m] || '') + ' ' + (META.nume[m] || m)}</option>`);
  return html`<div class="pg-seg" data-z="${z}" data-i="${i}">
    <input type="time" class="pg-h" value="${s.h}" aria-label="${t('Ora')}">
    <select class="pg-mod" aria-label="${t('Mod')}">${opts}</select>
    <button class="pg-del link-btn" title="${t('Șterge')}" data-z="${z}" data-i="${i}" aria-label="${t('Șterge')}">✕</button>
  </div>`;
}

function randZi(z) {
  const segs = PRG.zile[String(z)] || [];
  return html`<div class="pg-zi">
    <div class="pg-zi-cap"><b>${META.zile_nume[z] || z}</b>
      <button class="pg-add link-btn" data-z="${z}">${t('+ schimbare')}</button></div>
    ${segs.length
      ? segs.map((s, i) => randSegment(z, i, s))
      : html`<p class="hint pg-gol">${t('nicio schimbare — modul rămâne ca înainte')}</p>`}
  </div>`;
}

function renderProgram() {
  const el = $('#pg-moduri');
  if (!el) return;
  el.innerHTML = html`
    <label class="pg-activ"><input type="checkbox" id="pg-activ"${PRG.activ ? raw(' checked') : ''}>
      ${t('Comută modul automat după acest orar')}</label>
    <p class="hint">${t('La ora fiecărei schimbări, modul trece la cel ales. Între schimbări, o comutare manuală rămâne până la următoarea. „Plecat" nu se setează din fereastra de noapte, dar îl poți pune explicit aici.')}</p>
    <div class="pg-saptamana">${[0, 1, 2, 3, 4, 5, 6].map(randZi)}</div>
    <div class="pg-actiuni"><button id="pg-save" class="primary">${t('Salvează programul')}</button>
      <span class="hint" id="pg-msg"></span></div>`;
  $('#pg-activ').onchange = (e) => { PRG.activ = e.target.checked; };
  $('#pg-save').onclick = saveProgram;
  el.querySelectorAll('.pg-add').forEach((b) => (b.onclick = () => addSeg(+b.dataset.z)));
  el.querySelectorAll('.pg-del').forEach((b) => (b.onclick = () => delSeg(+b.dataset.z, +b.dataset.i)));
  el.querySelectorAll('.pg-seg').forEach((row) => {
    const z = +row.dataset.z, i = +row.dataset.i;
    row.querySelector('.pg-h').onchange = (e) => { PRG.zile[String(z)][i].h = e.target.value; };
    row.querySelector('.pg-mod').onchange = (e) => { PRG.zile[String(z)][i].mod = e.target.value; };
  });
}

function addSeg(z) {
  const k = String(z);
  (PRG.zile[k] = PRG.zile[k] || []).push({ h: '22:00', mod: 'noapte' });
  renderProgram();
}

function delSeg(z, i) {
  PRG.zile[String(z)].splice(i, 1);
  renderProgram();
}

async function saveProgram() {
  const msg = $('#pg-msg');
  if (msg) msg.textContent = '';
  try {
    const r = await fetch('/api/mod/program', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activ: PRG.activ, zile: PRG.zile }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || t('nu s-a putut salva'));
    PRG = { activ: !!d.activ, zile: d.zile || {} };   // reincarca normalizat (sortat, HH:MM)
    renderProgram();
    toast(t('Program salvat'));
  } catch (e) {
    if (msg) msg.textContent = e.message;
    toast(e.message, true);
  }
}

/* ---------- starea regulilor ---------- */
function programRegula(r) {
  const buc = [r.interval && r.interval.length === 2 ? r.interval.join('–') : t('oricând')];
  buc.push(r.zile && r.zile.length ? r.zile.map((z) => (META.zile_nume[z] || z).slice(0, 2)).join(' ') : t('zilnic'));
  buc.push(r.moduri && r.moduri.length ? r.moduri.map((m) => META.ico[m] || m).join('') : t('orice mod'));
  return buc.join(' · ');
}

function randRegula(r) {
  return html`<div class="pg-regula${r.activ ? '' : ' pg-off'}" data-id="${r.id}">
    <div class="pg-reg-main">
      <div class="pg-reg-top"><b>${r.nume}</b> <span class="hint">${programRegula(r)}</span></div>
      <div class="pg-reg-sub hint">${t('ultima declanșare')}: ${_relativ(r.ultima)}</div>
    </div>
    <div class="pg-reg-act">
      <button class="pg-reg-toggle link-btn" data-id="${r.id}">${r.activ ? t('oprește') : t('pornește')}</button>
      <button class="pg-reg-edit link-btn" data-id="${r.id}">${t('editează')}</button>
    </div>
  </div>`;
}

async function renderReguli() {
  const el = $('#pg-reguli');
  if (!el) return;
  let d;
  try { d = await fetch('/api/rules').then((r) => r.json()); } catch { d = { rules: [] }; }
  const reguli = (d.rules || []).slice()
    .sort((a, b) => (b.activ - a.activ) || ((b.ultima || 0) - (a.ultima || 0)));
  el.innerHTML = html`<div class="pg-reg-list">${lista(reguli, randRegula, html`<p class="hint">${t('nicio regulă încă')}</p>`)}</div>`;
  el.querySelectorAll('.pg-reg-toggle').forEach((b) => (b.onclick = async () => {
    const r = reguli.find((x) => x.id === b.dataset.id);
    if (!r) return;
    const { ultima, ...baza } = r;          // `ultima` e doar de afisare, nu se trimite inapoi
    await fetch('/api/rules', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...baza, activ: !r.activ }),
    });
    renderReguli();
  }));
  el.querySelectorAll('.pg-reg-edit').forEach((b) => (b.onclick = () => {
    $('#program-overlay').classList.add('hidden');
    location.hash = '#ai/reguli';
  }));
}

/* ---------- deschidere / inchidere ---------- */
async function openProgram() {
  const ov = $('#program-overlay');
  if (!ov) return;
  ov.classList.remove('hidden');
  const mEl = $('#pg-moduri');
  if (mEl) mEl.innerHTML = html`<p class="hint">${t('se încarcă…')}</p>`;
  let d;
  try { d = await fetch('/api/mod/program').then((r) => r.json()); } catch { d = {}; }
  META = { moduri: d.moduri || ['acasa', 'plecat', 'noapte'], nume: d.nume || {},
    ico: d.ico || {}, zile_nume: d.zile_nume || [] };
  PRG = { activ: !!d.activ, zile: d.zile || {} };
  renderProgram();
  renderReguli();
}

$('#program-close') && ($('#program-close').onclick = () => $('#program-overlay').classList.add('hidden'));

export { openProgram };
