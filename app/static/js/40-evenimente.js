/* ====================================================================
   Evenimente: incidente, dosare, rezumat, poze, cautare semantica, lightbox, clip, jurnal
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, CAN_ACT, DET_RO, EV_RO, clipReady, esc, html, icon, lista, raw, runJob, t, toast } from './00-core.js';
import { evPane, show, showEv } from './05-navigare.js';
import { popupTile, post } from './10-live.js';
import { closeHD } from './20-hd.js';
import { hartaCritic } from './25-harta.js';
import { curSegments, localDay, pad, pbSetSpeed, playFrom, renderMultiList, searchRecordings } from './30-redare.js';
import { buildCamsTable } from './50-camere.js';
import { loadSettings } from './60-setari.js';
import { loadFaces, loadPlates, loadRules, loadTrack } from './70-ai.js';
/* ---------- panou de incidente: triaj in timp real ---------- */
let incFilter = 'noi';
/* lista se incarca progresiv: 30 de randuri, apoi inca 30 cand ajungi la capat (120 odata
   insemnau 16 ecrane de derulat pe telefon). Ordinea ramane cea de prioritate (critic sus). */
const INC_PAS = 30;
let incToate = [], incArat = 0, incRand = null, incObs = null;
function cand(ts) {
  // „18:57:16” azi, „ieri 18:57”, altfel „23.10 18:57” — ca sa stii dintr-o privire cat de vechi e
  const zi = ts.slice(0, 10), ora = ts.slice(11, 19);
  const azi = new Date(), ieri = new Date(Date.now() - 86400000);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  if (zi === iso(azi)) return ora;
  if (zi === iso(ieri)) return t('ieri {ora}', { ora: ora.slice(0, 5) });
  return `${zi.slice(8, 10)}.${zi.slice(5, 7)} ${ora.slice(0, 5)}`;
}
function arataInc() {
  const list = $('#inc-list');
  const vechi = list.querySelector('.inc-mai'); if (vechi) vechi.remove();
  const felie = incToate.slice(incArat, incArat + INC_PAS);
  const frag = document.createElement('div');
  frag.className = 'inc-felie';
  frag.innerHTML = html`${felie.map(incRand)}`;
  list.appendChild(frag);
  leagaInc(frag);
  incArat += felie.length;
  const ramase = incToate.length - incArat;
  if (ramase > 0) {
    const mai = document.createElement('button');
    mai.className = 'inc-mai';
    mai.type = 'button';
    mai.textContent = t('Încă {n} din {ramase} rămase', { n: Math.min(INC_PAS, ramase), ramase });
    mai.onclick = arataInc;
    list.appendChild(mai);
    if ('IntersectionObserver' in window) {
      if (incObs) incObs.disconnect();
      incObs = new IntersectionObserver((en) => { if (en.some((x) => x.isIntersecting)) { incObs.disconnect(); arataInc(); } }, { rootMargin: '300px' });
      incObs.observe(mai);
    }
  }
}
function leagaInc(container) {
  wireClips(container);
  wireDosar(container);
  for (const b of container.querySelectorAll('.inc-ok, .inc-no, .inc-reopen')) {
    const stare = b.classList.contains('inc-ok') ? 'confirmat'
      : b.classList.contains('inc-no') ? 'fals' : 'nou';
    b.onclick = async () => {
      await post(`/api/incidents/${b.dataset.id}/stare`, { stare });
      loadIncidents(); refreshIncBadge();
    };
  }
}
const EV_ICO = { persoana_necunoscuta: '👤', persoana: '👤', fata_falsa: '🚫',
  numar: '🚗', detectie: '👁', obiect: '🎯', alarma_camera: '⚠', zgomot: '🔊',
  camera_offline: '📵', intrus: '🚨', trecere: '⳹', intrare_zona: '▱',
  parcare_inceput: '🅿', parcare_sfarsit: '🅿', episod: '🚶' };
async function loadIncidents() {
  try {
    const qs = incFilter === 'noi' ? 'noi=1'
      : incFilter === 'critic' ? 'limit=120' : 'limit=120';
    const d = await (await fetch(`/api/incidents?${qs}`)).json();
    const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    let inc = d.incidente || [];
    if (incFilter === 'critic') inc = inc.filter((e) => e.sev === 'critic');
    $('#inc-info').textContent = incFilter === 'noi'
      ? t('{n} de tratat', { n: inc.length }) : t('{n} incidente', { n: inc.length });
    incRand = (e) => {
      const det = e.text || e.nume || e.numar || (e.eticheta && t(DET_RO[e.eticheta] || e.eticheta))
        || (e.subtip && t(EV_RO[(e.subtip || '').toLowerCase()] || e.subtip)) || '';
      const cam = e.cam ? (names[e.cam] || 'cam ' + e.cam) : '—';
      const tratat = e.stare !== 'nou'
        ? html`<span class="inc-done inc-${e.stare}">${e.stare === 'confirmat' ? t('✓ tratat') : t('✕ fals')}${e.tratat_de ? ' · ' + e.tratat_de : ''}</span>` : '';
      const act = e.stare === 'nou' ? html`
        <button class="inc-ok" data-id="${e.id}" title="${t('Am tratat')}">${t('✓ confirm')}</button>
        <button class="inc-no" data-id="${e.id}" title="${t('Alarma falsă')}">${t('✕ fals')}</button>` : html`
        <button class="inc-reopen" data-id="${e.id}" title="${t('Redeschide')}">↺</button>`;
      // mini-harta traiectoriei (episoade E3): pe unde s-a miscat omul in cadru —
      // punct verde = intrare, rosu = iesire; forma spune singura "a trecut/s-a invartit"
      const traseu = (e.traseu && e.traseu.length > 1) ? html`
        <svg class="inc-traseu" viewBox="0 0 64 36" title="${t('traseul prin cadru')}">
          <polyline points="${e.traseu.map((p) =>
            `${(2 + p[0] * 60).toFixed(1)},${(2 + p[1] * 32).toFixed(1)}`).join(' ')}"/>
          <circle class="tr-in" cx="${(2 + e.traseu[0][0] * 60).toFixed(1)}"
            cy="${(2 + e.traseu[0][1] * 32).toFixed(1)}" r="2.4"/>
          <circle class="tr-out" cx="${(2 + e.traseu.at(-1)[0] * 60).toFixed(1)}"
            cy="${(2 + e.traseu.at(-1)[1] * 32).toFixed(1)}" r="2.4"/>
        </svg>` : '';
      return html`<div class="inc-card sev-${e.sev}" data-id="${e.id}">
        <span class="sev-dot"></span>
        <span class="inc-ico">${EV_ICO[e.tip] || '•'}</span>
        <div class="inc-main">
          <div class="inc-top"><b>${t(EV_TIP_RO[e.tip] || e.tip)}</b>${det ? ' · ' + det : ''}
            <span class="inc-when">${cand(e.t)} · ${cam}</span></div>
          ${tratat}
        </div>
        ${traseu}
        ${e.cam ? html`<button class="clip-btn link-btn" data-cam="${e.cam}" data-t="${evClipT(e)}"
          data-cap="${t(EV_TIP_RO[e.tip] || e.tip)}: ${det} — ${evClipT(e)}">${t('▶ clip')}</button><button class="fisa-btn link-btn"
          data-fisa="${JSON.stringify({ cam: e.cam, t: evClipT(e), tip: e.tip, sev: e.sev, id: e.id, det: e.eticheta || e.nume || e.numar || '', cine: e.nume || '', stare: e.stare, tratat_de: e.tratat_de })}"
          title="${t('Fișa evenimentului: poză, clip, acțiuni')}">${t('fișă')}</button>` : ''}
        ${e.cam ? html`<button class="dosar-btn link-btn" data-cam="${e.cam}" data-t="${evClipT(e)}"
          data-id="${e.id}" data-lbl="${t(EV_TIP_RO[e.tip] || e.tip)}${det ? ': ' + det : ''}"
          title="${t('Adauga la un dosar de caz')}">${t('🗂 la dosar')}</button>` : ''}
        <div class="inc-acts">${act}</div>
      </div>`;
    };
    incToate = inc; incArat = 0;
    const list = $('#inc-list');
    list.innerHTML = inc.length ? '' : html`<p class="hint">${t('Niciun incident de tratat. Liniște.')}</p>`;
    if (inc.length) arataInc();
  } catch {}
}
for (const b of document.querySelectorAll('#inc-filter button')) {
  b.onclick = () => {
    incFilter = b.dataset.f;
    for (const x of document.querySelectorAll('#inc-filter button')) x.classList.toggle('on', x === b);
    loadIncidents();
  };
}

/* ---------- dosare de caz: pachet de probe pentru un incident ----------
   Un incident e un moment; il "pui la dosar" cu un interval in jur (±30s). Dosarul
   se exporta ca ZIP: clipuri cu filigran + manifest.json cu SHA-256 (verificabil). */
S.pendingProba = null;   // {cam, start, end, nota, event_id} in asteptarea alegerii dosarului
function wireDosar(container) {
  for (const b of container.querySelectorAll('.dosar-btn')) {
    b.onclick = () => {
      const t = b.dataset.t;
      S.pendingProba = {
        cam: +b.dataset.cam,
        start: evCompact(t, -30), end: evCompact(t, 30),
        nota: b.dataset.lbl || '', event_id: b.dataset.id || null,
      };
      openCases(true);
    };
  }
}

async function openCases(pick = false) {
  $('#cases-title').textContent = pick ? t('Alege dosarul') : t('Dosare de caz');
  $('#cases-hint').textContent = pick && S.pendingProba
    ? t('Proba: cam {cam} · {nota}', { cam: S.pendingProba.cam, nota: S.pendingProba.nota }) : '';
  $('#cases-modal').classList.remove('hidden');
  $('#cases-modal').dataset.pick = pick ? '1' : '';
  await renderCases();
}
function closeCases() {
  $('#cases-modal').classList.add('hidden');
  S.pendingProba = null;
}
async function renderCases() {
  const pick = $('#cases-modal').dataset.pick === '1';
  try {
    const d = await (await fetch('/api/cases')).json();
    const cz = d.cazuri || [];
    $('#cases-body').innerHTML = lista(cz, (c) => html`
      <div class="caz-row" data-id="${c.id}">
        <div class="caz-head">
          <b>${c.titlu}</b>
          <span class="hint">${t('{n} probe', { n: c.nr_probe })} · ${c.creat.slice(0, 16)}</span>
          <span class="caz-acts">
            ${pick ? html`<button class="caz-add link-btn" data-id="${c.id}">${t('+ adauga aici')}</button>` : ''}
            <button class="caz-view link-btn" data-id="${c.id}">${t('vezi')}</button>
            ${c.nr_probe ? html`<a class="link-btn job-link" href="/api/cases/${c.id}/export" title="${t('Descarca dosarul ZIP (clipuri cu filigran + manifest SHA-256)')}">⬇ ZIP</a>` : ''}
            <button class="caz-del link-btn" data-id="${c.id}" title="${t('Șterge dosarul')}">🗑</button>
          </span>
        </div>
        <div class="caz-probe hidden" data-for="${c.id}"></div>
      </div>`,
      `<p class="hint">${esc(t('Niciun dosar. Apasa „+ Caz nou" ca sa deschizi unul.'))}</p>`);
    for (const b of $('#cases-body').querySelectorAll('.caz-add'))
      b.onclick = () => addPending(b.dataset.id);
    for (const b of $('#cases-body').querySelectorAll('.caz-view'))
      b.onclick = () => toggleProbe(b.dataset.id);
    for (const b of $('#cases-body').querySelectorAll('.caz-del'))
      b.onclick = async () => {
        if (!confirm(t('Ștergi dosarul și toate probele lui?'))) return;
        await fetch(`/api/cases/${b.dataset.id}`, { method: 'DELETE' });
        renderCases();
      };
  } catch { $('#cases-body').innerHTML = `<p class="hint">${esc(t('Eroare la incarcarea dosarelor.'))}</p>`; }
}
async function toggleProbe(cid) {
  const wrap = $(`.caz-probe[data-for="${cid}"]`);
  if (!wrap) return;
  if (!wrap.classList.contains('hidden')) { wrap.classList.add('hidden'); return; }
  const c = await (await fetch(`/api/cases/${cid}`)).json();
  const names = Object.fromEntries(S.cameras.map((x) => [x.id, x.name]));
  wrap.innerHTML = lista(c.probe || [], (p) => html`
    <div class="proba-row">
      <span class="hint">${p.start.slice(9, 15).replace(/(..)(..)(..)/, '$1:$2:$3')} · ${names[p.cam] || 'cam ' + p.cam}</span>
      <span>${p.nota || ''}</span>
      <button class="proba-del link-btn" data-c="${cid}" data-p="${p.id}">✕</button>
    </div>`, `<p class="hint">${esc(t('Fara probe.'))}</p>`);
  wrap.classList.remove('hidden');
  for (const b of wrap.querySelectorAll('.proba-del'))
    b.onclick = async () => {
      await fetch(`/api/cases/${b.dataset.c}/proba/${b.dataset.p}`, { method: 'DELETE' });
      await toggleProbe(cid); await toggleProbe(cid); renderCases();   // reincarca lista de probe
    };
}
async function addPending(cid) {
  if (!S.pendingProba) return;
  const r = await post(`/api/cases/${cid}/proba`, S.pendingProba);
  if (r && !r.error) { toast(t('Adăugat la dosar.')); closeCases(); }
  else toast(r && r.error ? r.error : t('Eroare la adăugare'), true);
}
async function newCase() {
  const titlu = prompt(t('Titlul dosarului (ex. „Intrare neautorizată 25.08"):'));
  if (!titlu || !titlu.trim()) return;
  const r = await post('/api/cases', { titlu, descriere: '' });
  if (r && r.id) {
    if (S.pendingProba) return addPending(r.id);   // in modul „alege", adauga direct
    renderCases();
  } else toast(r && r.error ? r.error : t('Eroare'), true);
}
$('#inc-cases') && ($('#inc-cases').onclick = () => openCases(false));
$('#cases-new') && ($('#cases-new').onclick = newCase);
$('#cases-close') && ($('#cases-close').onclick = closeCases);
$('#cases-modal') && ($('#cases-modal').onclick = (e) => { if (e.target.id === 'cases-modal') closeCases(); });

/* ---------- rezumatul zilei: ce s-a intamplat, dintr-o privire ---------- */
function openDigest() {
  if (!$('#digest-day').value) $('#digest-day').value = localDay();
  $('#digest-modal').classList.remove('hidden');
  loadDigest();
}
function closeDigest() { $('#digest-modal').classList.add('hidden'); }
async function loadDigest() {
  const day = $('#digest-day').value;
  $('#digest-body').innerHTML = `<p class="hint">${esc(t('Se calculeaza...'))}</p>`;
  try {
    const d = await (await fetch(`/api/digest?day=${day}`)).json();
    const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    const s = d.severitate || {};
    // titlurile zilei: incidentele critice, enumerate
    const critHtml = (d.critice || []).length
      ? html`<ul class="dig-crit">${d.critice.map((c) => html`<li>
          <b>${c.ora}</b> · ${names[c.cam] || 'cam ' + c.cam} · ${t(EV_TIP_RO[c.tip] || c.tip)}
          ${c.detaliu ? '· ' + c.detaliu : ''}
          ${c.de_cate_ori > 1 ? html` <span class="hint">×${c.de_cate_ori}</span>` : ''}</li>`)}</ul>`
      : html`<p class="hint">${t('Niciun incident critic. Bine.')}</p>`;
    // securitate: doar categoriile cu valori > 0
    const secItems = Object.entries(d.securitate || {})
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${n} ${t(SEC_RO[k] || k)}`);
    const topCam = lista(d.top_camere || [], ([id, n]) =>
      html`<div class="dig-bar-row"><span>${names[id] || 'cam ' + id}</span>
        <span class="dig-bar"><i style="width:${Math.round(100 * n / (d.top_camere[0][1] || 1))}%"></i></span>
        <span class="dig-n">${n}</span></div>`, '<p class="hint">—</p>');
    $('#digest-body').innerHTML = html`
      <div class="dig-nums">
        <div class="dig-num"><b>${d.incidente}</b><span>${t('incidente')}</span></div>
        <div class="dig-num crit"><b>${s.critic || 0}</b><span>${t('critice')}</span></div>
        <div class="dig-num"><b>${s.atentie || 0}</b><span>${t('de atenție')}</span></div>
        <div class="dig-num soft"><b>${d.total}</b><span>${t('evenimente')}</span></div>
      </div>
      ${secItems.length ? html`<p class="dig-sec">⚠ ${secItems.join(' · ')}</p>` : ''}
      <h3>${t('Incidente critice')}</h3>
      ${critHtml}
      <div class="dig-cols">
        <div><h3>${t('Cele mai active camere')}</h3>${topCam}</div>
        <div><h3>${t('Activitate')}</h3>
          <p class="dig-when">${d.prima_activitate && d.ultima_activitate
            ? raw(t('Intre {de_la} si {pana_la}', { de_la: `<b>${esc(d.prima_activitate)}</b>`, pana_la: `<b>${esc(d.ultima_activitate)}</b>` }))
            : t('Fara activitate')}
          ${d.ora_varf ? html`<br>${t('Ora de varf:')} <b>${d.ora_varf[0]}</b> (${t('{n} evenimente', { n: d.ora_varf[1] })})` : ''}</p>
        </div>
      </div>`;
  } catch { $('#digest-body').innerHTML = `<p class="hint">${esc(t('Eroare la calcularea rezumatului.'))}</p>`; }
}
const SEC_RO = { persoana_necunoscuta: 'persoane necunoscute',
  fata_falsa: 'fețe falsificate', numar: 'numere de mașină' };
$('#inc-digest') && ($('#inc-digest').onclick = openDigest);
$('#digest-close') && ($('#digest-close').onclick = closeDigest);
$('#digest-day') && ($('#digest-day').onchange = loadDigest);
$('#digest-modal') && ($('#digest-modal').onclick = (e) => { if (e.target.id === 'digest-modal') closeDigest(); });

/* alarma: sunet scurt generat + badge pe tab; ruleaza global, nu doar in tabul Incidente */
let audioCtx = null, lastCritic = 0, incKnown = new Set();
function beep(times = 2) {
  if (!$('#inc-sound') || !$('#inc-sound').checked) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    for (let i = 0; i < times; i++) {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.connect(g); g.connect(audioCtx.destination);
      o.frequency.value = 880; o.type = 'square';
      const t = audioCtx.currentTime + i * 0.28;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.15, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
      o.start(t); o.stop(t + 0.24);
    }
  } catch {}
}
async function refreshIncBadge() {
  if (!CAN_ACT) return;   // triajul e pentru admin/operator
  try {
    const d = await (await fetch('/api/incidents?noi=1&limit=120')).json();
    const n = d.counts ? d.counts.noi : 0, crit = d.counts ? d.counts.critic_noi : 0;
    const badge = $('#inc-badge');
    badge.textContent = n; badge.classList.toggle('hidden', !n);
    badge.classList.toggle('crit', crit > 0);
    // alarma la un incident CRITIC nou (id nevazut)
    const ids = new Set((d.incidente || []).filter((e) => e.sev === 'critic').map((e) => e.id));
    let nouCritic = false;
    for (const id of ids) if (!incKnown.has(id)) nouCritic = true;
    incKnown = ids;
    if (nouCritic && Date.now() - lastCritic > 8000) {
      lastCritic = Date.now();
      beep(3);
      const e = (d.incidente || []).find((x) => x.sev === 'critic');
      if (e) toast(t('⚠ INCIDENT CRITIC: {tip} — {ora}', { tip: t(EV_TIP_RO[e.tip] || e.tip), ora: e.t.slice(11) }), false, true);
      $('#tab-ev').classList.add('flash'); setTimeout(() => $('#tab-ev').classList.remove('flash'), 4000);
      if (e && e.cam !== undefined) { popupTile(e.cam); hartaCritic(e.cam); }
    }
  } catch {}
}

$('#tab-live').onclick = () => show('live');
$('#tab-harta').onclick = () => show('harta');
$('#tab-ev').onclick = () => showEv(evPane);
$('#tab-pb').onclick = () => { show('pb'); if (!curSegments.length && $('#pb-cam').value) searchRecordings(); };
$('#tab-ai').onclick = () => {
  show('ai');
  loadFaces(); loadPlates(); loadRules(); loadTrack();
};
$('#tab-cams').onclick = async () => {
  show('cams');
  buildCamsTable();
  try {   // spatiul ocupat per camera, o singura cerere
    const s = await (await fetch('/api/storage')).json();
    storageData = s.per_cam_gb || {};
    buildCamsTable();
  } catch {}
};
$('#tab-set').onclick = () => { show('set'); loadSettings(); };
$('#pb-search').onclick = searchRecordings;
$('#pb-cam').addEventListener('change', () => {
  S.pbExtra = S.pbExtra.filter((x) => x !== +$('#pb-cam').value);
  if (!$('#pb-multi-list').classList.contains('hidden')) renderMultiList();
  searchRecordings();            // fara "Cauta": schimbi camera, vezi ziua ei
});
$('#pb-date').addEventListener('change', searchRecordings);
$('#pb-speed').onclick = (e) => {
  const b = e.target.closest('button[data-sp]');
  if (b) pbSetSpeed(+b.dataset.sp);
};
$('#hd-close').onclick = () => closeHD();

/* ---------- tabul Alarme (snapshot-uri salvate la detectii/alarme) ---------- */
const AL_RO = {
  attendedBaggage: 'Bagaj lăsat', unattendedBaggage: 'Obiect abandonat',
  parkingdetection: 'Parcare interzisă', loitering: 'Zăbovire', running: 'Alergare',
  peopleDetection: 'Persoana', vehicledetection: 'Vehicul', faceDetection: 'Față',
  audioexception: 'Zgomot', zgomot: 'Zgomot', diskfull: 'Disc plin',
  diskerror: 'Eroare disc', nicbroken: 'Rețea întreruptă', ipconflict: 'Conflict IP',
  illaccess: 'Acces respins', videomismatch: 'Semnal video pierdut', ...EV_RO, ...DET_RO };
function buildAlarmFilters() {
  const sel = $('#al-cam');
  sel.length = 1;  // pastreaza "Toate camerele"
  for (const c of S.cameras) {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = `${c.id}. ${c.name}`;
    sel.appendChild(o);
  }
  if (!$('#al-date').value) $('#al-date').value = localDay();
}
/* Camerele mai scot din cand in cand tipuri pe care nu le-am tradus inca. Le facem cel
   putin citibile ("attendedBaggage" -> "Attended baggage") in loc sa le aratam brut. */
function alNume(l) {
  if (AL_RO[l]) return t(AL_RO[l]);
  const s = l.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}
const AL_ICO = {
  dog: '🐕', cat: '🐈', bird: '🐦', person: '🧍', horse: '🐎', sheep: '🐑',
  cow: '🐄', bear: '🐻', VMD: '🏃', IO: '⚡', linedetection: '➖',
  fielddetection: '🚧', regionEntrance: '↘', regionExiting: '↗',
};
let alDayItems = [], alTypeF = '', alIncidents = [];
async function loadAlarms() {
  buildAlarmFilters();
  const date = $('#al-date').value;
  $('#al-info').textContent = t('Incarc...');
  // esec de retea (ex. serverul tocmai reporneste) NU inseamna "0 intamplari":
  // fara mesaj, lista goala arata ca o zi fara evenimente si deruteaza complet
  let d;
  try {
    const r = await fetch(`/api/alarms?date=${date}&grup=1`);
    d = await r.json();
    if (d.error) throw new Error(d.error);
  } catch (e) {
    $('#al-info').textContent = t('Nu am putut încărca alarmele ({motiv}) — reîncearcă', { motiv: e.message || t('rețea') });
    return;
  }
  alIncidents = (d.incidente || []).map((g) => ({ ...g, date }));
  alDayItems = alIncidents.flatMap((g) => g.poze.map((p) => ({ ...p, cam: g.cam, date })));
  renderAlarms();
}

/* Din intamplare direct in filmarea de la momentul respectiv — cea mai folosita actiune
   dintr-un sistem de supraveghere, si singura care lipsea complet. */
async function vezFilmarea(cam, date, sec) {
  show('pb');
  $('#pb-cam').value = String(cam);
  $('#pb-date').value = date;
  await searchRecordings();
  playFrom(date, Math.max(0, sec - 10));   // 10s inainte, ca sa prinzi si intrarea in cadru
}
function renderAlarms() {
  const camF = $('#al-cam').value;
  const byCam = alIncidents.filter((g) => !camF || g.cam === +camF);
  // pill-uri cu contor: numaram CADRE, dar filtram INTAMPLARI care contin eticheta
  const counts = {};
  for (const g of byCam) {
    for (const [l, n] of Object.entries(g.etichete)) counts[l] = (counts[l] || 0) + n;
  }
  if (alTypeF && !counts[alTypeF]) alTypeF = '';
  const pills = $('#al-pills');
  pills.innerHTML = '';
  const mkPill = (val, txt, n) => {
    const b = document.createElement('button');
    b.className = 'pill' + (alTypeF === val ? ' active' : '');
    b.textContent = n === undefined ? txt : `${txt} (${n})`;
    b.onclick = () => { alTypeF = val; renderAlarms(); };
    pills.appendChild(b);
  };
  mkPill('', t('Toate'), byCam.length);
  for (const t of Object.keys(counts).sort((a, b) => counts[b] - counts[a])) {
    mkPill(t, `${AL_ICO[t] || '⚠'} ${alNume(t)}`, counts[t]);
  }
  const list = byCam.filter((g) => !alTypeF || g.etichete[alTypeF]);
  const cadre = list.reduce((s, g) => s + g.nr, 0);
  $('#al-info').textContent = `${t('{n} întâmplări', { n: list.length })} · ${t('{n} cadre', { n: cadre })}`;
  renderAlarmChart(list.flatMap((g) => g.poze.filter((p) => !alTypeF || p.label === alTypeF)));

  const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
  // lightbox-ul parcurge cadrele in ordinea in care se vad intamplarile
  alList = list.flatMap((g) => g.poze.map((p) => ({
    ...p, cam: g.cam, date: g.date,
    cap: `${p.time} — ${names[g.cam] || 'cam ' + g.cam} — ${alNume(p.label)}`,
  })));
  const idx = {};
  let k = 0;
  for (const g of list) { idx[g.start + '-' + g.cam] = k; k += g.nr; }

  const grid = $('#al-grid');
  grid.innerHTML = '';
  for (const g of list) {
    const card = document.createElement('div');
    card.className = 'al-card inc';
    const et = Object.entries(g.etichete).sort((a, b) => b[1] - a[1]);
    const dur = g.durata >= 60 ? `${Math.round(g.durata / 60)} min` : `${g.durata}s`;
    card.innerHTML = html`
      <img loading="lazy" src="${g.coperta}" alt="">
      ${g.nr > 1 ? html`<span class="inc-nr">${t('{n} cadre', { n: g.nr })}</span>` : ''}
      <div class="al-cap">
        <div class="inc-cand">${g.start.slice(0, 5)}${g.durata ? `–${g.end.slice(0, 5)}` : ''}
          <small>${g.durata ? dur : ''}</small></div>
        <div class="inc-cam">${names[g.cam] || 'cam ' + g.cam}</div>
        <div class="inc-et">${et.map(([l, n]) =>
          html`<span>${AL_ICO[l] || '⚠'} ${alNume(l)}${n > 1 ? ` ×${n}` : ''}</span>`)}</div>
      </div>
      <button class="inc-play" title="${t('Deschide filmarea de la {ora}', { ora: g.start })}">${t('Vezi filmarea')}</button>`;
    card.querySelector('img').onclick = () => openPhoto(idx[g.start + '-' + g.cam]);
    card.querySelector('.inc-play').onclick = (e) => {
      e.stopPropagation();
      vezFilmarea(g.cam, g.date, g.sec);
    };
    grid.appendChild(card);
  }
}
// mini-histograma: evenimentele filtrate, pe ore (o singura serie, direct din date)
function renderAlarmChart(list) {
  const wrap = $('#al-chart');
  wrap.innerHTML = '';
  if (!list.length) return;
  const perHour = Array(24).fill(0);
  for (const it of list) perHour[+it.time.slice(0, 2)]++;
  const max = Math.max(...perHour);
  const peak = perHour.indexOf(max);
  perHour.forEach((n, h) => {
    const col = document.createElement('div');
    col.className = 'al-col';
    const bar = document.createElement('div');
    bar.className = 'al-bar';
    bar.style.height = n ? Math.max(3, (n / max) * 46) + 'px' : '0';
    col.title = `${pad(h)}:00–${pad(h)}:59 — ${t('{n} evenimente', { n })}`;
    if (h === peak && n) {
      const v = document.createElement('span');
      v.className = 'al-val';
      v.textContent = n;
      col.appendChild(v);
    }
    col.appendChild(bar);
    if (h % 6 === 0) {
      const t = document.createElement('span');
      t.className = 'al-tick';
      t.textContent = pad(h);
      col.appendChild(t);
    }
    wrap.appendChild(col);
  });
}

$('#al-search').onclick = loadAlarms;
$('#al-cam').onchange = renderAlarms;

/* ---------- cautare semantica: descriere libera peste pozele indexate ---------- */
// arata textul a intrebare? (oglinda parserului din app/ask.py)
function esteIntrebare(q) {
  const p = q.toLowerCase();
  return q.endsWith('?') || /^(cand|când|cine|cate|câte|de cate|de câte)\b/.test(p)
    || p.includes('ultima data') || p.includes('ultima dată') || p.includes('de câte ori');
}
async function askQuestion(q) {
  $('#al-info').textContent = t('Mă uit în poze...');
  const camF = $('#al-cam').value;   // filtrul de camera se respecta si la intrebari
  const r = await fetch(`/api/ask?q=${encodeURIComponent(q)}`
                        + (camF ? `&cam=${camF}` : ''));
  const d = await r.json();
  if (d.error) { $('#al-info').textContent = d.error; return; }
  const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
  $('#al-pills').innerHTML = '';
  $('#al-chart').innerHTML = '';
  $('#al-info').textContent = t('{n} apariții', { n: (d.aparitii || []).length });
  // in lightbox intra doar aparitiile cu poza (cele din jurnal n-au coperta)
  const cuPoza = (d.aparitii || []).filter((g) => g.coperta);
  alList = cuPoza.map((g) => ({
    url: g.coperta, time: g.inceput.slice(11), date: g.inceput.slice(0, 10),
    cam: g.camere[0], label: d.concept,
    cap: `${g.inceput} — ${g.camere.map((c) => names[c] || 'cam ' + c).join(', ')}`,
  }));
  const grid = $('#al-grid');
  grid.innerHTML = html`<div class="ask-answer">💬 ${d.raspuns}</div>`;
  (d.aparitii || []).forEach((g) => {
    const card = document.createElement('div');
    card.className = 'al-card';
    const durata = g.inceput === g.sfarsit ? '' : `–${g.sfarsit.slice(11, 16)}`;
    card.innerHTML = html`
      ${g.coperta ? html`<img loading="lazy" src="${g.coperta}" alt="">`
                  : html`<button class="ask-clip clip-btn" data-cam="${g.camere[0]}" data-t="${g.inceput}"
                           data-cap="${g.inceput} — ${g.camere.map((c) => names[c] || 'cam ' + c).join(', ')}">${t('▶ clip')}</button>`}
      <div class="al-cap">
        <div class="inc-cand">${g.inceput.slice(0, 16)}${durata}</div>
        <div class="inc-cam">${g.camere.map((c) => names[c] || 'cam ' + c).join(', ')}${
          g.poze > 1 ? ` · ${t('{n} cadre', { n: g.poze })}` : ''}</div>
      </div>`;
    const img = card.querySelector('img');
    if (img) {
      const idx = cuPoza.indexOf(g);
      img.onclick = () => openPhoto(idx);
    }
    grid.appendChild(card);
  });
}
async function semanticSearch() {
  const q = $('#sem-q').value.trim();
  if (!q) { loadAlarms(); return; }   // camp gol = inapoi la vederea pe zi
  if (esteIntrebare(q)) { askQuestion(q); return; }
  $('#al-info').textContent = t('Caut...');
  let d;
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&days=30&limit=40`);
    d = await r.json();
  } catch (e) {
    $('#al-info').textContent = t('Căutarea nu a mers ({motiv}) — reîncearcă', { motiv: e.message || t('rețea') });
    return;
  }
  if (d.error) { $('#al-info').textContent = d.error; return; }
  const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
  alList = d.results.map((p) => ({
    url: p.url, time: p.time, label: p.label, cam: p.cam, date: p.day,
    cap: `${p.day} ${p.time} — ${names[p.cam] || 'cam ' + p.cam} — ${alNume(p.label)}`,
  }));
  $('#al-pills').innerHTML = '';
  $('#al-chart').innerHTML = '';
  $('#al-info').textContent = alList.length
    ? t('{n} rezultate pentru "{q}" (cele mai potrivite primele)', { n: alList.length, q })
    : t('nimic găsit pentru "{q}"', { q });
  const grid = $('#al-grid');
  grid.innerHTML = '';
  alList.forEach((p, i) => {
    const card = document.createElement('div');
    card.className = 'al-card';
    card.innerHTML = html`
      <img loading="lazy" src="${p.url}" alt="">
      <div class="al-cap">
        <div class="inc-cand">${p.date} ${p.time.slice(0, 5)}</div>
        <div class="inc-cam">${names[p.cam] || 'cam ' + p.cam}</div>
      </div>`;
    card.querySelector('img').onclick = () => openPhoto(i);
    grid.appendChild(card);
  });
}
$('#sem-go').onclick = semanticSearch;
$('#sem-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') semanticSearch(); });

/* ---------- viewer poze (lightbox cu navigare) ---------- */
let alList = [], phIdx = -1;
function openPhoto(i) {
  if (i < 0 || i >= alList.length) return;
  phIdx = i;
  const it = alList[i];
  stopClip();
  $('#ph-img').src = it.url;
  $('#ph-cap').textContent = it.cap;
  $('#ph-count').textContent = `${i + 1} / ${alList.length}`;
  $('#ph-prev').disabled = i === 0;
  $('#ph-next').disabled = i === alList.length - 1;
  $('#ph-overlay').classList.remove('hidden');
  // preincarca vecinii, sa se navigheze instant
  for (const j of [i - 1, i + 1]) {
    if (j >= 0 && j < alList.length) new Image().src = alList[j].url;
  }
}
function closePhoto() {
  stopClip();
  $('#ph-overlay').classList.add('hidden');
  $('#ph-img').src = '';
  phIdx = -1;
}

/* clip video in jurul evenimentului (-5s..+15s, din inregistrari) */
let clipEl = null;
function stopClip() {
  if (clipEl) { clipEl.remove(); clipEl = null; }
  $('#ph-img').style.display = '';
  $('#ph-clip').textContent = t('▶ Clip');
  $('#ph-clip').disabled = false;
}
$('#ph-clip').onclick = () => {
  if (phIdx < 0) return;
  if (clipEl) { stopClip(); return; }  // inapoi la poza
  const it = alList[phIdx];
  const ts = it.date.replace(/-/g, '') + 'T' + it.time.replace(/:/g, '') + 'Z';
  $('#ph-clip').textContent = t('⏳ Încarc...');
  $('#ph-clip').disabled = true;
  clipEl = document.createElement('video');
  clipEl.controls = true;
  clipEl.autoplay = true;
  clipEl.id = 'ph-video';
  const src = `/api/clip?channel=${it.cam}&time=${ts}`;
  clipEl.oncanplay = () => { $('#ph-clip').innerHTML = icon('image') + ' ' + esc(t('Poza')); $('#ph-clip').disabled = false; };
  clipEl.onerror = () => { alert(t('Clipul nu e disponibil (fără înregistrare la acel moment).')); stopClip(); };
  $('#ph-img').style.display = 'none';
  $('#ph-img').insertAdjacentElement('afterend', clipEl);
  const el = clipEl;
  clipReady(src).then((ok) => {
    if (clipEl !== el) return;
    if (!ok) { alert(t('Clipul nu e disponibil (fără înregistrare la acel moment).')); stopClip(); return; }
    el.src = src;
  });
};
$('#ph-close').onclick = closePhoto;
$('#ph-prev').onclick = () => openPhoto(phIdx - 1);
$('#ph-next').onclick = () => openPhoto(phIdx + 1);
$('#ph-overlay').onclick = (e) => { if (e.target === $('#ph-overlay') || e.target === $('#ph-body')) closePhoto(); };
document.addEventListener('keydown', (e) => {
  if (phIdx < 0) return;
  if (e.key === 'ArrowLeft') openPhoto(phIdx - 1);
  else if (e.key === 'ArrowRight') openPhoto(phIdx + 1);
  else if (e.key === 'Escape') closePhoto();
});
// din poza -> direct in playback la secunda evenimentului
$('#ph-context').onclick = () => {
  if (phIdx < 0) return;
  const it = alList[phIdx];
  const [h, m, s] = it.time.split(':').map(Number);
  closePhoto();
  show('pb');
  $('#pb-cam').value = it.cam;
  $('#pb-date').value = it.date;
  searchRecordings().then(() => playFrom(it.date, h * 3600 + m * 60 + s));
};

/* ---------- recatalogare din lightbox ---------- */
function buildFixBar() {
  const bar = $('#ph-fix');
  bar.innerHTML = `<span class="fix-lbl">${esc(t('Corecteaza:'))}</span>`;
  for (const [en, ro] of Object.entries(DET_RO)) {
    const b = document.createElement('button');
    b.textContent = `${AL_ICO[en] || ''} ${t(ro)}`;
    b.onclick = () => relabel(en);
    bar.appendChild(b);
  }
  const del = document.createElement('button');
  del.className = 'fix-del';
  del.innerHTML = icon('trash', 14) + ' ' + esc(t('Fals / sterge'));
  del.onclick = deleteAlarm;
  bar.appendChild(del);
}
async function relabel(label) {
  if (phIdx < 0) return;
  const it = alList[phIdx];
  if (it.label === label) return;
  const d = await post('/api/alarms/relabel', { url: it.url, label });
  if (!d || d.error) return;
  const src = alDayItems.find((x) => x.url === it.url);
  if (src) { src.label = label; src.url = d.url; }
  const keepIdx = phIdx;
  renderAlarms();
  openPhoto(Math.min(keepIdx, alList.length - 1));
}
async function deleteAlarm() {
  if (phIdx < 0) return;
  const it = alList[phIdx];
  const d = await post('/api/alarms/delete', { url: it.url });
  if (!d || d.error) return;
  alDayItems = alDayItems.filter((x) => x.url !== it.url);
  const keepIdx = phIdx;
  renderAlarms();
  if (alList.length) openPhoto(Math.min(keepIdx, alList.length - 1));
  else closePhoto();
}
buildFixBar();

// swipe pe touch
let phTouchX = null;
$('#ph-overlay').addEventListener('touchstart', (e) => { phTouchX = e.touches[0].clientX; }, { passive: true });
$('#ph-overlay').addEventListener('touchend', (e) => {
  if (phTouchX === null) return;
  const dx = e.changedTouches[0].clientX - phTouchX;
  phTouchX = null;
  if (Math.abs(dx) > 40) openPhoto(phIdx + (dx < 0 ? 1 : -1));
});

/* ---------- player de clip: video in jurul unui eveniment AI ----------
   Refoloseste /api/clip (-5s..+15s din inregistrare) pentru orice moment: aparitie
   de persoana, citire de numar, obiect. Un singur player, chemat de peste tot. */
// chenare pe clip: eticheta RO + culoare pe clasa (rosu=om, albastru=vehicul, verde=animal)
const CLIP_LBL = {
  person: ['Om', '#e5484d'], car: ['Mașină', '#4184e4'], truck: ['Camion', '#4184e4'],
  bus: ['Autobuz', '#4184e4'], motorcycle: ['Motocicletă', '#e08600'],
  bicycle: ['Bicicletă', '#e08600'], dog: ['Câine', '#2da44e'], cat: ['Pisică', '#2da44e'],
  bird: ['Pasăre', '#2da44e'], horse: ['Cal', '#2da44e'], sheep: ['Oaie', '#2da44e'],
  cow: ['Vacă', '#2da44e'], bear: ['Urs', '#2da44e'] };
let clipData = null;
function _clipFrame(cur) {
  if (!clipData || !clipData.frames.length) return null;
  let best = clipData.frames[0];               // ultimul cadru esantionat cu t <= momentul curent
  for (const f of clipData.frames) { if (f.t <= cur + 0.05) best = f; else break; }
  return best;
}
function drawClipBoxes() {
  const v = $('#clip-video'), cv = $('#clip-canvas');
  if (!cv || !v) return;
  const cw = v.clientWidth, ch = v.clientHeight;
  if (cv.width !== cw) cv.width = cw;
  if (cv.height !== ch) cv.height = ch;
  const g = cv.getContext('2d');
  g.clearRect(0, 0, cw, ch);
  if (!$('#clip-boxes-on').checked || !clipData) return;
  const vw = v.videoWidth, vh = v.videoHeight;
  if (!vw || !vh) return;
  // maparea coordonatelor normalizate in zona reala a imaginii (video pastreaza aspectul)
  const scale = Math.min(cw / vw, ch / vh);
  const dw = vw * scale, dh = vh * scale, ox = (cw - dw) / 2, oy = (ch - dh) / 2;
  const f = _clipFrame(v.currentTime);
  if (!f) return;
  g.lineWidth = Math.max(2, cw / 500);
  const fs = Math.max(11, Math.round(cw / 60));
  g.font = `${fs}px system-ui, sans-serif`;
  for (const b of f.boxes) {
    const [ro, col] = CLIP_LBL[b.lbl] || [b.lbl, '#d4a72c'];
    const X = ox + b.x * dw, Y = oy + b.y * dh, W = b.w * dw, H = b.h * dh;
    g.strokeStyle = col;
    g.strokeRect(X, Y, W, H);
    const txt = `${t(ro)} ${Math.round(b.conf * 100)}%`;
    const th = fs + 6;
    g.fillStyle = col;
    g.fillRect(X, Math.max(0, Y - th), g.measureText(txt).width + 8, th);
    g.fillStyle = '#fff';
    g.fillText(txt, X + 4, Math.max(th - 5, Y - 5));
  }
}
function openClip(cam, moment, cap) {
  const tc = evCompact(moment, 0);   // "2026-08-15 09:12:33" -> compact pt. /api/clip
  const src = `/api/clip?channel=${cam}&time=${tc}`;
  const v = $('#clip-video');
  clipData = null;
  drawClipBoxes();                  // curata canvas-ul precedent
  $('#clip-cap').textContent = cap || '';
  $('#clip-msg').textContent = t('Se pregătește clipul...');
  $('#clip-dl').href = src;
  $('#clip-dl').classList.add('hidden');
  v.oncanplay = () => { $('#clip-msg').textContent = ''; $('#clip-dl').classList.remove('hidden'); };
  v.onerror = () => { $('#clip-msg').textContent = t('Fără înregistrare la acel moment (sau se generează încă).'); };
  v.ontimeupdate = drawClipBoxes;
  v.onloadedmetadata = drawClipBoxes;
  $('#clip-modal').classList.remove('hidden');
  const seq = ++clipSeq;
  // clipul se genereaza in coada de joburi; <video> primeste URL-ul abia cand e in cache
  clipReady(src).then((ok) => {
    if (seq !== clipSeq) return;                 // s-a deschis alt clip intre timp
    if (!ok) { $('#clip-msg').textContent = t('Fără înregistrare la acel moment.'); return; }
    v.src = src;
    // chenare: re-detectia YOLO pe clip (job, ~1-3s); desenam cand raspunsul vine
    const bx = `/api/clip/boxes?channel=${cam}&time=${tc}`;
    runJob(bx + '&job=1', {}, { download: false })
      .then((j) => (j && j.stare === 'gata') ? fetch(bx).then((r) => r.json()) : null)
      .then((d) => { if (seq === clipSeq && d && d.frames) { clipData = d; drawClipBoxes(); } })
      .catch(() => {});
  });
}
let clipSeq = 0;
function closeClip() {
  const v = $('#clip-video');
  v.pause(); v.removeAttribute('src'); v.load();
  v.ontimeupdate = null;
  clipData = null;
  drawClipBoxes();
  $('#clip-modal').classList.add('hidden');
}
$('#clip-boxes-on') && ($('#clip-boxes-on').onchange = drawClipBoxes);
window.addEventListener('resize', () => { if (!$('#clip-modal').classList.contains('hidden')) drawClipBoxes(); });
$('#clip-close') && ($('#clip-close').onclick = closeClip);
$('#clip-modal') && ($('#clip-modal').onclick = (e) => { if (e.target.id === 'clip-modal') closeClip(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#clip-modal').classList.contains('hidden')) closeClip();
});
// leaga toate butoanele .clip-btn dintr-un container la player
function wireClips(container) {
  for (const b of container.querySelectorAll('.clip-btn')) {
    b.onclick = () => openClip(+b.dataset.cam, b.dataset.t, b.dataset.cap || '');
  }
}

/* ---------- jurnalul de evenimente ---------- */
const SEV_RO = { info: 'Informativ', atentie: 'Atenție', critic: 'Critic' };
const EV_TIP_RO = { detectie: 'Detecție', persoana: 'Persoana', persoana_necunoscuta: 'Față necunoscută',
  fata_falsa: 'Față FALSĂ (spoofing)', descriere: 'Semnalmente', obiect: 'Obiect urmărit',
  numar: 'Număr', alarma_camera: 'Alarma camera', zgomot: 'Zgomot', sirena: 'Sirena',
  regula: 'Regula', camera_offline: 'Camera căzută', camera_online: 'Camera revenita',
  gol_inregistrare: 'Gol înregistrare', intrus: 'INTRUS confirmat',
  trecere: 'Trecere linie', intrare_zona: 'Intrare în zona',
  obiect_nou: 'Obiect nou apărut', obiect_disparut: 'Obiect dispărut',
  parcare_inceput: 'Vehicul parcat', parcare_sfarsit: 'Vehicul plecat din parcare',
  episod: 'Episod de prezență' };
// Momentul ACTIUNII unui eveniment agregat, nu al emiterii lui: episodul se
// inchide la 45s DUPA plecarea omului, parcarea se confirma la 2 min dupa oprire,
// iar "pierdut din vedere" vine la 10 min dupa ultima vedere — un clip taiat la
// emitere arata o curte goala. Aici alegem ancora corecta pentru clip.
function evClipT(e) {
  const hhmmss = (e.tip === 'episod' || e.tip === 'parcare_inceput') ? e.inceput
    : e.tip === 'parcare_sfarsit' ? e.vazut_ultima : null;
  if (!hhmmss) return e.t;
  // actiunea "mai tarziu in zi" decat emiterea = a fost ieri (episod peste miezul noptii)
  if (hhmmss > e.t.slice(11)) {
    const d = new Date(e.t.replace(' ', 'T'));
    d.setDate(d.getDate() - 1);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${hhmmss}`;
  }
  return `${e.t.slice(0, 10)} ${hhmmss}`;
}

// "2026-08-14 10:20:30" + delta secunde -> "20260814T102030Z" (formatul /api/export)
function evCompact(t, deltaSec) {
  const d = new Date(t.replace(' ', 'T'));
  d.setSeconds(d.getSeconds() + (deltaSec || 0));
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}T` +
         `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}Z`;
}
function evDurata(sec) {
  if (sec < 60) return sec + 's';
  return Math.floor(sec / 60) + 'min' + (sec % 60 ? ' ' + (sec % 60) + 's' : '');
}
async function loadJournal() {
  try {
    const tip = $('#ev-tip').value, cam = $('#ev-cam').value, day = $('#ev-day').value;
    const sev = $('#ev-sev') ? $('#ev-sev').value : '';
    const qs = new URLSearchParams();
    if (tip) qs.set('tip', tip);
    if (cam) qs.set('cam', cam);
    if (day) qs.set('day', day);
    if (sev) qs.set('sev', sev);
    $('#ev-csv').href = '/api/journal.csv?' + qs.toString();
    const grup = $('#ev-grup').checked;
    if (grup) qs.set('grup', '1');
    const d = await (await fetch('/api/journal?' + qs.toString())).json();
    if ($('#ev-tip').options.length <= 1) {
      $('#ev-tip').innerHTML = html`<option value="">${t('Toate tipurile')}</option>${
        d.types.map((ty) => html`<option value="${ty}">${t(EV_TIP_RO[ty] || ty)}</option>`)}`;
      $('#ev-cam').innerHTML = html`<option value="">${t('Toate camerele')}</option>${
        S.cameras.map((c) => html`<option value="${c.id}">${c.id}. ${c.name}</option>`)}`;
    }
    const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    if (grup) {
      // episoade: inceput–sfarsit, durata, de cate ori, clip + descarcare MP4
      $('#ev-list').innerHTML = lista(d.episodes || [], (e) => {
        const det = e.detaliu
          ? t(DET_RO[e.detaliu] || EV_RO[(e.detaliu || '').toLowerCase()] || e.detaliu) : '';
        const iv = `channel=${e.cam}&start=${evCompact(e.inceput, -5)}&end=${evCompact(e.sfarsit, 10)}`;
        const acts = e.cam ? html`
          <button class="clip-btn link-btn" data-cam="${e.cam}" data-t="${e.inceput}"
             data-cap="${det || e.tip} — ${e.inceput}">${t('▶ clip')}</button>
          <a href="/api/export?${iv}" class="link-btn job-link"
             title="${t('Descarcă intervalul ca fișier video')}">MP4</a>
          <a href="/api/export/report?${iv}" class="link-btn job-link"
             title="${t('Fișă probatorie: metadate + SHA-256 al fișierului MP4')}">${t('raport')}</a>` : '';
        return html`<div class="ev-row sev-${e.sev || 'info'}">
          <span class="sev-dot" title="${SEV_RO[e.sev] ? t(SEV_RO[e.sev]) : ''}"></span>
          <span class="ev-t">${e.inceput}–${e.sfarsit.slice(11)}</span>
          <span class="ev-dur">${evDurata(e.durata)}</span>
          <span class="ev-tip">${t(EV_TIP_RO[e.tip] || e.tip)}</span>
          <span class="ev-cam">${e.cam ? (names[e.cam] || 'cam ' + e.cam) : '—'}</span>
          <span class="ev-det">${det}${e.evenimente > 1 ? ` · ×${e.evenimente}` : ''}</span>
          ${e.cam ? html`<button class="fisa-btn link-btn" title="${t('Fișa evenimentului: poză, clip, acțiuni')}"
            data-fisa="${JSON.stringify({ cam: e.cam, t: e.inceput, tip: e.tip, sev: e.sev, id: e.id, det: e.detaliu || '', cine: e.nume || '' })}">${t('fișă')}</button>` : ''}${acts}</div>`;
      }, `<p class="hint">${esc(t('Niciun episod (inca).'))}</p>`);
      wireClips($('#ev-list'));
      return;
    }
    $('#ev-list').innerHTML = lista(d.events || [], (e) => {
      let detalii = e.text || e.nume || e.numar || (e.eticheta && t(DET_RO[e.eticheta] || e.eticheta))
        || e.regula || e.motiv || (e.subtip && t(EV_RO[(e.subtip || '').toLowerCase()] || e.subtip)) || '';
      if (e.tip === 'obiect' && e.directie) detalii += ` · ${e.directie} · ${e.durata}s`;
      if (e.tip === 'obiect' && e.biciclist) detalii = t('🚲 biciclist ({n} înălțimi/s)', { n: e.inaltimi_s }) + ' · ' + detalii;
      const clip = e.cam ? html`<button class="clip-btn link-btn" data-cam="${e.cam}"
        data-t="${evClipT(e)}" data-cap="${t(EV_TIP_RO[e.tip] || e.tip)}: ${detalii} — ${evClipT(e)}">${t('▶ clip')}</button><button class="fisa-btn link-btn"
        data-fisa="${JSON.stringify({ cam: e.cam, t: evClipT(e), tip: e.tip, sev: e.sev, id: e.id, det: e.eticheta || e.nume || e.numar || '', cine: e.nume || '' })}"
        title="${t('Fișa evenimentului: poză, clip, acțiuni')}">${t('fișă')}</button>` : '';
      return html`<div class="ev-row sev-${e.sev || 'info'}">
        <span class="sev-dot" title="${SEV_RO[e.sev] ? t(SEV_RO[e.sev]) : ''}"></span>
        <span class="ev-t">${e.t}</span>
        <span class="ev-tip">${t(EV_TIP_RO[e.tip] || e.tip)}</span>
        <span class="ev-cam">${e.cam ? (names[e.cam] || 'cam ' + e.cam) : '—'}</span>
        <span class="ev-det">${detalii}${e.context ? ' · ' + e.context : ''}${
          e.actiuni ? ' · ' + e.actiuni.join(', ') : ''}</span>
        ${clip}</div>`;
    }, `<p class="hint">${esc(t('Niciun eveniment (inca).'))}</p>`);
    wireClips($('#ev-list'));
  } catch {}
}
$('#ev-filter') && ($('#ev-filter').onclick = loadJournal);
$('#ev-grup') && ($('#ev-grup').onchange = loadJournal);

// scrisa doar de ecranul Evenimente
let storageData = {};

export { AL_ICO, AL_RO, EV_TIP_RO, alNume, evCompact, loadAlarms, loadIncidents, loadJournal, openCases, openClip, refreshIncBadge, storageData, wireClips };
document.addEventListener('incident-schimbat', () => { loadIncidents(); refreshIncBadge(); });
