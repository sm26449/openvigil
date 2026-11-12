/* ====================================================================
   Navigare: taburi, rutare pe hash, taste globale, modul acasa/plecat/noapte, pulsul si legaturile din antet
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, CAN_ACT, LIMBA, ME, html, icon, t, toast } from './00-core.js';
import { ZOOM_MAX_MS, pollEvents, post } from './10-live.js';
import { closeHD, hdCam, hdIsLive, openHD } from './20-hd.js';
import { loadHarta } from './25-harta.js';
import { localDay, pbDate, pbPos, playFrom, searchRecordings } from './30-redare.js';
import { initView, loadAlarms, loadIncidents, loadJournal, refreshIncBadge } from './40-evenimente.js';
import { closeCamPanel, openSanatate, pulseData } from './50-camere.js';
import { openProgram } from './65-program.js';
import { meDlg } from './60-setari.js';
/* ---------- taburi ---------- */
const VIEWS = ['live', 'harta', 'ev', 'pb', 'ai', 'cams', 'set'];
// Evenimente = un singur ecran cu trei sub-panouri (audit, Etapa 2): De tratat (triajul de
// incidente), Jurnal (istoricul permanent, episoade) si Poze (snapshot-urile + cautarea).
// Numele vechi 'inc' si 'al' raman valabile pentru apelantii din cod si pentru #inc / #al.
const EV_PANES = { inc: loadIncidents, jurnal: loadJournal, poze: loadAlarms };
const EV_ALIAS = { inc: 'inc', al: 'poze' };
let evPane = 'inc';
function show(view, updateHash = true) {
  if (EV_ALIAS[view]) { showEv(EV_ALIAS[view], updateHash); return; }
  for (const v of VIEWS) {
    $('#' + v).classList.toggle('hidden', view !== v);
    $('#tab-' + v).classList.toggle('active', view === v);
  }
  if (view !== 'cams') closeCamPanel();   // panoul de administrare a camerei nu ramane peste alt ecran (audit C3)
  if (view === 'harta') loadHarta();
  if (updateHash) setHash(view === 'pb' && pbDate !== null ? pbHash() : view === 'ev' ? '#ev/' + evPane : '#' + view);
}
function showEv(pane, updateHash = true) {
  if (!EV_PANES[pane] || (pane === 'inc' && !CAN_ACT)) pane = CAN_ACT ? 'inc' : 'jurnal';
  evPane = pane;
  for (const b of document.querySelectorAll('#ev-nav button')) b.classList.toggle('on', b.dataset.evpane === pane);
  for (const el of document.querySelectorAll('#ev .ev-pane')) el.classList.toggle('hidden', el.id !== { inc: 'inc', jurnal: 'evjurnal', poze: 'al' }[pane]);
  show('ev', updateHash);
  EV_PANES[pane]();
}
const evPaneVisible = (pane) => !$('#ev').classList.contains('hidden') && evPane === pane;
$('#ev-nav').onclick = (e) => { const b = e.target.closest('button[data-evpane]'); if (b) showEv(b.dataset.evpane); };
/* ---------- telefon: bara de jos cu 4 taburi + „Mai mult”; restul antetului intra in foaie ----------
   Elementele sunt ACELEASI (handlerele raman), doar se muta in DOM dupa latime. */
const navMobil = window.matchMedia('(max-width: 760px)');
const IN_FOAIE = ['tab-harta', 'tab-ai', 'tab-set', 'tab-stare', 'tab-program', 'add-cam', 'q-global', 'quick-links', 'user-chip', 'status'];
const navAcasa = {};   // id -> [parinte, urmatorul frate] ca sa le punem inapoi
function asazaNav() {
  const foaie = $('#sheet-cont');
  if (!foaie) return;
  if (navMobil.matches) {
    for (const id of IN_FOAIE) {
      const el = document.getElementById(id);
      if (!el || el.parentElement === foaie) continue;
      navAcasa[id] = [el.parentElement, el.nextSibling];
      el.classList.add('in-foaie');
      foaie.appendChild(el);
    }
    // versiunea, la sfarsit, discret: pe desktop sta in Setari -> Stare
    if (!$('#sheet-ver')) {
      const v = document.createElement('div');
      v.id = 'sheet-ver'; v.className = 'hint';
      v.textContent = t('OpenVigil {v}', { v: ME.versiune || 'dev' });
      foaie.appendChild(v);
    }
  } else {
    inchideFoaia();
    for (const id of IN_FOAIE) {
      const el = document.getElementById(id);
      if (!el || !navAcasa[id] || el.parentElement !== foaie) continue;
      const [p, next] = navAcasa[id];
      el.classList.remove('in-foaie');
      p.insertBefore(el, next && next.parentElement === p ? next : null);
    }
  }
}
function deschideFoaia() { $('#sheet').classList.remove('hidden'); $('#nav-more').setAttribute('aria-expanded', 'true'); $('#nav-more').classList.add('active'); }
function inchideFoaia() { const s = $('#sheet'); if (!s || s.classList.contains('hidden')) return; s.classList.add('hidden'); $('#nav-more').setAttribute('aria-expanded', 'false'); $('#nav-more').classList.remove('active'); }
$('#nav-more') && ($('#nav-more').onclick = () => ($('#sheet').classList.contains('hidden') ? deschideFoaia() : inchideFoaia()));
$('#sheet') && $('#sheet').addEventListener('click', (e) => {
  if (e.target.closest('[data-inchide]')) { inchideFoaia(); return; }
  if (e.target.closest('button, a')) setTimeout(inchideFoaia, 0);   // dupa handlerul tabului
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#sheet') && !$('#sheet').classList.contains('hidden')) inchideFoaia(); });
navMobil.addEventListener('change', asazaNav);
asazaNav();

/* ---------- rutare pe hash: #live, #inc, #pb/<cam>/<zi>/<sec>, #hd/<cam> ----------
   Adresa spune unde esti, deci se poate trimite (Telegram: "vezi aici") si deschide direct
   momentul; Inapoi pe telefon inchide overlay-ul HD in loc sa iasa din aplicatie. */
function setHash(h) { if (location.hash !== h) history.replaceState(history.state, '', h); }
function pbHash() { return `#pb/${$('#pb-cam').value}/${pbDate}/${Math.floor(pbPos || 0)}`; }
let routing = false;
async function routeHash() {
  const h = location.hash.slice(1);
  if (!h) { setHash('#live'); return; }        // adresa spune mereu unde esti
  if (routing) return;
  routing = true;
  try {
    const [view, a, b, c] = h.split('/');
    if (view === 'hd' && a) {
      const cam = S.cameras.find((x) => x.id === +a);
      show('live', false);
      if (cam && (!hdCam || hdCam.id !== cam.id)) openHD(cam, true);
      return;
    }
    if (view === 'ev' || EV_ALIAS[view]) { showEv(EV_ALIAS[view] || a || evPane, false); return; }
    if (!VIEWS.includes(view)) return;
    if (view !== 'pb') {
      show(view, false); initView(view);   // deep-link: arata SI initializeaza ecranul
      // subpagina din adresa (audit T3): #ai/<pane>, #set/<pane> — activam tab-ul respectiv
      if (a && (view === 'ai' || view === 'set')) {
        document.querySelector(`.${view === 'ai' ? 'ainav' : 'snav'}-item[data-pane="${a}"]`)?.click();
      }
      return;
    }
    if (view === 'pb' && a) {
      show('pb', false);
      if (!$('#pb-cam').querySelector(`option[value="${+a}"]`)) return;
      const zi = b && /^\d{4}-\d{2}-\d{2}$/.test(b) ? b : localDay();
      const sec = c !== undefined ? Math.max(0, Math.min(86399, +c || 0)) : null;
      // aceeasi redare deja pe ecran (Inapoi dupa HD): nu repornim fluxul
      if (+$('#pb-cam').value === +a && pbDate === zi && sec !== null && Math.abs(sec - (pbPos || 0)) < 15) return;
      $('#pb-cam').value = String(+a);
      $('#pb-date').value = zi;
      await searchRecordings();
      if (sec !== null) playFrom(zi, sec);
      return;
    }
    show(view, false); initView(view);   // #pb fara camera: tot cauta inregistrarile zilei
  } finally { routing = false; }
}
window.addEventListener('hashchange', routeHash);
window.addEventListener('popstate', (e) => {
  if (S.popIgnora) { const r = S.popIgnora; S.popIgnora = null; r(); return; }   // back()-ul din closeHD
  if (hdCam && !(e.state && e.state.hd)) { closeHD(true); return; }
  routeHash();
});
/* ---------- taste: Esc inchide orice overlay, sagetile rotesc camera in HD ----------
   Esc: intai HD-ul, apoi orice overlay vizibil (mascat, regiune, zona, dosare...), prin
   butonul lui de inchidere — un singur loc, in loc de un handler per modal. Sagetile
   (si +/-) in HD: aceeasi comanda ca butoanele PTZ, cu oprire la ridicarea tastei si
   opritorul de siguranta de 4 s; doar cand nu scrii intr-un camp. */
const OVERLAY_CLOSE = [['#mask-overlay', '#mask-close'], ['#heat-overlay', '#heat-close'], ['#reg-overlay', '#reg-close'],
  ['#zone-overlay', '#zone-close'], ['#count-overlay', '#count-close'], ['#addcam-overlay', '#addcam-cancel'],
  ['#cases-modal', '#cases-close'], ['#digest-modal', '#digest-close'], ['#clip-modal', '#clip-close'], ['#ph-overlay', '#ph-close'],
  ['#exports-modal', '#exports-close'], ['#health-overlay', '#health-close'],
  ['#program-overlay', '#program-close']];
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;           // si din campurile formularului (focusul e in overlay)
  if (meDlg && meDlg.open) return;                      // dialogul nativ se inchide singur
  for (const [ov, btn] of OVERLAY_CLOSE) {
    const el = $(ov);
    if (el && !el.classList.contains('hidden')) { const b = $(btn); if (b) b.click(); return; }
  }
  if (hdCam) closeHD();
});
/* Accesibilitatea modalelor „facute de mana" (cele cu clasa .hidden, plus HD si foaia „Mai
   mult"): <dialog>-urile native (fisa, cauta, contul meu, re-ID) au deja din showModal() focus
   trap + fundal inert + revenirea focusului; aici le aducem si pe celelalte la acelasi nivel
   (audit #16). La deschidere: tinem minte ce era focusat, inertam restul paginii si mutam
   focusul in modal. Cat e deschis, Tab se invarte doar in el. La inchidere: scoatem inert si
   readucem focusul pe elementul care l-a deschis. Toate sunt copii directi ai <body>. */
const MODALE = [...OVERLAY_CLOSE.map(([ov]) => ov), '#hd-overlay', '#sheet'];
let focusInainte = null;
const focusabile = (el) => [...el.querySelectorAll(
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
  .filter((x) => x.getClientRects().length);
function inerteazaFundal(modal, on) {
  for (const copil of document.body.children) {
    // <dialog>-urile native isi fac singure modalitatea (showModal pune restul inert prin
    // top-layer). Daca le-am pune NOI `inert`, un dialog nativ deschis peste foaia „Mai mult"
    // ramanea inert si fara focus — showModal nu scoate atributul (audit #16, regresie).
    if (copil === modal || copil.tagName === 'DIALOG') continue;
    if (on) copil.setAttribute('inert', '');
    else copil.removeAttribute('inert');
  }
}
function modalDeschis(el) {
  focusInainte = document.activeElement;
  // Daca un modal exterior (ex. HD) l-a inertat ca fundal, acum, cand se deschide PESTE el
  // (editor zona/linie/masca deschis din HD), ii scoatem `inert` — altfel ar fi vizibil dar
  // complet mort la click si ar parea ca s-a blocat tot (utilizatorul trebuia sa dea refresh).
  el.removeAttribute('inert');
  inerteazaFundal(el, true);
  if (!el.contains(document.activeElement)) {
    // Preferam containerul (are tabindex=-1): cititorul de ecran ii anunta numele, iar focusul
    // „neutru" lasa sagetile sa comande camera in HD (nu un control anume). Foaia „Mai mult"
    // n-are tabindex, deci acolo focusam primul element.
    const tinta = el.hasAttribute('tabindex') ? el : (focusabile(el)[0] || el);
    try { tinta.focus({ preventScroll: true }); } catch (_) {}
  }
}
function modalInchis(el) {
  inerteazaFundal(el, false);   // intai scoatem inert, altfel elementul-tinta ar fi needfocusabil
  // Modale stivuite: daca inchidem un modal deschis PESTE altul (editor peste HD), modalul
  // exterior ramane deschis — reinertam fundalul in jurul lui, ca pagina de sub el sa nu
  // redevina accesibila la Tab/click.
  const extern = MODALE.map((s) => $(s)).find((m) => m && m !== el && !m.classList.contains('hidden'));
  if (extern) inerteazaFundal(extern, true);
  if (focusInainte && document.contains(focusInainte)) {
    try { focusInainte.focus({ preventScroll: true }); } catch (_) {}
  }
  focusInainte = null;
}
for (const sel of MODALE) {
  const el = $(sel);
  if (!el) continue;
  new MutationObserver(() => {
    const deschis = !el.classList.contains('hidden');
    if (deschis && !el._modalOn) { el._modalOn = true; modalDeschis(el); }
    else if (!deschis && el._modalOn) { el._modalOn = false; modalInchis(el); }
  }).observe(el, { attributes: true, attributeFilter: ['class'] });
}
// capcana de Tab: cat e un modal propriu deschis, focusul nu iese din el
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Tab') return;
  // Un <dialog> nativ deschis deasupra (ex. „Contul meu" peste foaia „Mai mult") isi face
  // singur focus trap-ul prin top-layer; capcana noastra ar fura Tab-ul inapoi in foaia de
  // dedesubt (audit #4). Cat exista un dialog nativ deschis, ne dam la o parte.
  if (document.querySelector('dialog[open]')) return;
  const modal = MODALE.map((s) => $(s)).find((el) => el && !el.classList.contains('hidden'));
  if (!modal) return;
  const f = focusabile(modal);
  if (!f.length) { e.preventDefault(); return; }
  const prim = f[0], ultim = f[f.length - 1];
  if (!modal.contains(document.activeElement)) { e.preventDefault(); prim.focus(); }
  else if (e.shiftKey && document.activeElement === prim) { e.preventDefault(); ultim.focus(); }
  else if (!e.shiftKey && document.activeElement === ultim) { e.preventDefault(); prim.focus(); }
});
let ptzKey = null, ptzKeyT = null;
const PTZ_KEYS = { ArrowLeft: { pan: -60 }, ArrowRight: { pan: 60 }, ArrowUp: { tilt: 60 }, ArrowDown: { tilt: -60 },
  '+': { zoom: 60 }, '=': { zoom: 60 }, '-': { zoom: -60 }, PageUp: { zoom: 60 }, PageDown: { zoom: -60 } };
function ptzKeyStop() {
  if (!ptzKey) return;
  const cam = ptzKey.cam; ptzKey = null; clearTimeout(ptzKeyT);
  post(`/api/cam/${cam}/ptz`, { pan: 0, tilt: 0, zoom: 0 });
}
document.addEventListener('keydown', (e) => {
  // Doar in HD LIVE (in time-shift sagetile ar roti liveul in timp ce privesti trecutul) SI doar
  // cand focusul e „neutru" — pe overlay-ul HD insusi sau pe body, nu pe un control (buton,
  // slider, timeline): altfel sagetile furau tastatura controlului focusat (audit #3).
  const a = document.activeElement;
  const neutru = !a || a === document.body || a.id === 'hd-overlay';
  if (!hdCam || !CAN_ACT || !hdIsLive() || !neutru || e.repeat || !(e.key in PTZ_KEYS)) return;
  const f = hdCam.features || {}, d = PTZ_KEYS[e.key];
  if ((d.zoom && !f.zoom) || ((d.pan || d.tilt) && !f.ptz)) return;
  e.preventDefault();
  if (ptzKey) return;
  ptzKey = { cam: hdCam.id, key: e.key };
  post(`/api/cam/${hdCam.id}/ptz`, { pan: 0, tilt: 0, zoom: 0, ...d });
  ptzKeyT = setTimeout(ptzKeyStop, ZOOM_MAX_MS);     // opritor: tasta ramasa „apasata"
});
document.addEventListener('keyup', (e) => { if (ptzKey && e.key === ptzKey.key) ptzKeyStop(); });
window.addEventListener('blur', ptzKeyStop);

/* ---------- Etapa 4: modul acasa/plecat/noapte (comutatorul din antet) ----------
   Starea globala pe care o citesc regulile. Se schimba de aici, din Telegram, MQTT, API sau
   dintr-o regula; o reimprospatam la 20 s ca antetul sa arate ce a comutat telefonul. */
const MOD_ICO = { acasa: '🏠', plecat: '🔒', noapte: '🌙' };
let modCurent = 'acasa';
async function loadMod() {
  try {
    const d = await (await fetch('/api/mod')).json();
    modCurent = d.mod || 'acasa';
    for (const b of document.querySelectorAll('#mod-chip button')) b.classList.toggle('on', b.dataset.mod === modCurent);
    const cand = d.de_cand ? new Date(d.de_cand * 1000).toLocaleString(LIMBA, { dateStyle: 'short', timeStyle: 'short' }) : '';
    $('#mod-chip').title = [t('Modul: {mod}', { mod: d.nume ? d.nume[modCurent] : modCurent }),
      cand ? t('din {cand}', { cand }) : '', d.cine ? `(${d.cine}, ${d.sursa || ''})` : ''].filter(Boolean).join(' ');
  } catch {}
}
$('#mod-chip').onclick = async (e) => {
  const b = e.target.closest('button[data-mod]');
  if (!b || !CAN_ACT || b.dataset.mod === modCurent) return;
  const d = await post('/api/mod', { mod: b.dataset.mod });
  if (d && !d.error) { toast(`${MOD_ICO[d.mod]} ${t('Mod: {mod}', { mod: b.textContent.trim().slice(2) })}`); loadMod(); }
};
loadMod();
setInterval(() => { if (!document.hidden) loadMod(); }, 20000);

/* ---------- banda de puls din antet ---------- */
// Cate o bara per camera: inaltimea si culoarea spun starea. E singurul loc din care
// vezi sanatatea intregului sistem fara sa fii in tabul Live.
async function pollPulse() {
  try {
    const rows = await (await fetch('/api/pulse')).json();
    const box = $('#pulse');
    if (box.children.length !== rows.length) {
      box.innerHTML = '';
      for (const r of rows) {
        const b = document.createElement('button');
        b.className = 'pz';
        b.onclick = () => {
          const cam = S.cameras.find((c) => c.id === r.id);
          if (cam) { show('live'); openHD(cam); }
        };
        box.appendChild(b);
      }
    }
    for (const r of rows) pulseData[r.id] = r;
    rows.forEach((r, i) => {
      const b = box.children[i];
      if (!b) return;
      b.className = 'pz' + (r.alarm ? ' alarm' : r.detect ? ' detect'
        : !r.rec ? ' down' : r.mode === 'motion' ? ' motion' : ' rec');
      const stare = r.alarm ? t('alarma acum')
        : r.detect ? t('detecție AI acum')
        : !r.rec ? (r.age ? t('NU înregistrează (de {min} min)', { min: Math.round(r.age / 60) }) : t('NU înregistrează'))
        : r.mode === 'motion' ? t('înregistrează la mișcare') : t('înregistrează continuu');
      b.title = `${r.name} — ${stare}`;
    });
  } catch {}
}
setInterval(() => { if (!document.hidden) pollPulse(); }, 5000);

/* ---------- directorul de inregistrari nu se poate scrie (volum, permisiuni, cheie) ---------- */
async function pollBlocat() {
  try {
    const h = await (await fetch('/healthz')).json();
    $('#blocat').classList.toggle('hidden', !h.blocat);
  } catch {}
}
pollBlocat();
setInterval(() => { if (!document.hidden) pollBlocat(); }, 30000);

/* ---------- legaturi rapide (header + Setari) ---------- */
let camLinks = {};
async function loadLinks() {
  try {
    const l = await (await fetch('/api/links')).json();
    camLinks = Object.fromEntries(l.cameras.map((c) => [c.id, c.url]));
    camLinks.nvr = l.nvr;
    const mk = (txt, url, title) =>
      html`<a href="${url}" target="_blank" title="${title || url}">${txt}</a>`;
    // l.nvr lipseste cand NVR-ul e retras (Setari -> Sistem): nu-l mai aratam nicaieri
    $('#quick-links').innerHTML = html`${
      mk(html`${icon('tools')} go2rtc`, l.go2rtc, t('Motorul de streaming (statusul fluxurilor)'))} ${
      l.nvr ? mk(html`${icon('server')} NVR`, l.nvr, t('Interfata NVR-ului Hikvision')) : ''}`;
    const rows = [
      [html`${icon('tools')} ${t('go2rtc — statusul fluxurilor video')}`, l.go2rtc],
      ...(l.nvr ? [[html`${icon('server')} ${t('NVR Hikvision (16 camere)')}`, l.nvr]] : []),
      ...l.cameras.map((c) => [html`${icon('camera')} ${t('{name} — interfața camerei', { name: c.name })}`, c.url]),
    ];
    $('#s-links').innerHTML = html`${rows
      .map(([txt, u]) => html`<a href="${u}" target="_blank">${txt} ↗</a>`)}`;
  } catch {}
}
loadLinks();
pollPulse();
// panoul de incidente: badge + alarma la critic nou, verificat global la 6s
if (CAN_ACT) {
  refreshIncBadge();
  setInterval(() => { if (!document.hidden) refreshIncBadge(); }, 6000);
  // reincarca lista cat timp esti in tabul Incidente — nedistructiv (nu reconstruieste daca
  // nimic nu s-a schimbat, pastreaza scroll + paginarea)
  setInterval(() => {
    if (!document.hidden && evPaneVisible('inc')) loadIncidents(true);
  }, 6000);
} else {
  // viewerul nu face triaj: sub-panoul "De tratat" dispare, Evenimente se deschide pe Jurnal
  $('#ev-nav button[data-evpane="inc"]').classList.add('hidden');
  evPane = 'jurnal';
}

// titlurile cu data-ic primesc iconita corespunzatoare
for (const h of document.querySelectorAll('h2[data-ic]')) {
  h.insertAdjacentHTML('afterbegin', icon(h.dataset.ic, 15));
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { pollEvents(); pollPulse(); }
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

export { MOD_ICO, camLinks, evPane, pbHash, routeHash, setHash, show, showEv };

// „Stare” in antet: deschide Centrul de sanatate (serverul, canalele, AI-ul, fiecare camera)
// intr-un overlay propriu, de oriunde — un singur loc pentru „merge totul?".
$('#tab-stare') && ($('#tab-stare').onclick = openSanatate);
$('#tab-program') && ($('#tab-program').onclick = openProgram);
