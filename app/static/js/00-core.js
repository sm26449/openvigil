/* ====================================================================
   Nucleu: importuri, html``, toast, sesiunea (ME), coada de joburi
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { VideoRTC } from '../video-rtc.js';
import { icon as _icon } from '../icons.js';
import { S } from './state.js';
if (!customElements.get('video-stream')) customElements.define('video-stream', VideoRTC);

// fluxurile video vin prin releul autentificat din nvr-web (:8076), nu direct din go2rtc.
// Pe HTTPS (prin Traefik) releul e pe aceeasi origine: proxy-ul trimite /api/ws si
// /api/frame.jpeg catre :8076, iar browserul nu mai are nevoie de un al doilea port.
const STREAM_PORT = document.querySelector('meta[name="ov-stream-port"]')?.content || '8076';
const GO2RTC = location.protocol === 'https:' ? location.origin
  : `${location.protocol}//${location.hostname}:${STREAM_PORT}`;
const $ = (s) => document.querySelector(s);

/* ---------- scaparea HTML: tot ce intra in innerHTML trece prin html`` ----------
   html`<b>${x}</b>` scapa automat orice valoare interpolata (nume de camere, texte de
   evenimente, raspunsuri AI...) — un sir obisnuit e TEXT, niciodata markup. Ce e deja
   HTML (iconite, alte fragmente html``, tablouri de fragmente) trece ca atare. Rezultatul
   e un obiect SafeHtml, pe care innerHTML il converteste singur la sir; atentie ca e mereu
   truthy, deci "lista goala -> text de rezerva" se face cu lista(), nu cu ||. */
class SafeHtml { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = (s) => new SafeHtml(String(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const toHtml = (v) => v instanceof SafeHtml ? v.s
  : Array.isArray(v) ? v.map(toHtml).join('')
  : v == null || v === false ? '' : esc(v);
function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => { out += toHtml(v) + strings[i + 1]; });
  return new SafeHtml(out);
}
// lista de fragmente sau, cand e goala, un text de rezerva (markup de incredere)
const lista = (items, fn, gol) => (items.length ? html`${items.map(fn)}` : raw(gol || ''));
// iconitele sunt SVG de incredere: le marcam ca atare, ca sa poata intra in html``
const icon = (name, size) => raw(_icon(name, size));

// mesaj tranzitoriu in coltul ecranului (info sau eroare)
let _toastT = null;
function toast(msg, err = false, urgent = false) {
  let el = $('#toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  // cititoarele de ecran: status (politicos) pentru mesaje, alert pentru erori si critice
  el.setAttribute('role', err || urgent ? 'alert' : 'status');
  el.setAttribute('aria-live', err || urgent ? 'assertive' : 'polite');
  el.textContent = msg;
  el.classList.toggle('err', !!err);
  el.classList.add('show');
  clearTimeout(_toastT);
  _toastT = setTimeout(() => el.classList.remove('show'), 3200);
}

/* ---------- limba interfetei ----------
   Cheia unui text e textul romanesc insusi (vezi app/i18n.py): t('Adaugă camera'). Dictionarul
   limbii alese de server (<html lang>) vine de la /i18n/<limba>.json; fara traducere, t()
   intoarce cheia. Parametrii se pun DUPA traducere: t('{n} camere', { n }) — ordinea
   cuvintelor difera intre limbi, deci textele nu se lipesc din bucati. O valoare-obiect
   {one, few, other} e o forma de plural, aleasa dupa `n` cu regulile limbii. */
const _fetch = window.fetch.bind(window);
const LIMBA = document.documentElement.lang || 'ro';
let _DICT = {};
try {
  const r = await _fetch(`/i18n/${LIMBA}.json`);
  if (r.ok) _DICT = await r.json();
} catch {}
const _plural = new Intl.PluralRules(LIMBA);
function t(cheie, p) {
  let v = _DICT[cheie];
  if (v && typeof v === 'object') v = (p && p.n != null ? v[_plural.select(+p.n)] : null) ?? v.other ?? cheie;
  if (typeof v !== 'string' || !v) v = cheie;
  return p ? v.replace(/\{(\w+)\}/g, (m, k) => (k in p ? String(p[k]) : m)) : v;
}

/* ---------- cine sunt eu (rol + jeton CSRF), inainte de orice ----------
   Toate cererile care modifica ceva poarta jetonul CSRF al sesiunii in antetul
   X-CSRF — serverul refuza POST/DELETE fara el. Wrapper-ul il adauga singur, ca sa
   nu depindem de fiecare loc de apel; tot el prinde 401 (sesiune expirata) si duce
   la login in loc sa lase pagina sa esueze tacut. */
let ME = { user: '', role: 'admin', csrf: '' };
window.fetch = (url, opts = {}) => {
  const method = (opts.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD'
      && typeof url === 'string' && url.startsWith('/')) {
    opts.headers = { ...(opts.headers || {}), 'X-CSRF': ME.csrf };
  }
  return _fetch(url, opts).then((r) => {
    if (r.status === 401) location.href = '/login';
    return r;
  });
};
try {
  const r = await _fetch('/api/me');
  if (r.status === 401) location.href = '/login';
  ME = await r.json();
} catch {}
const ADMIN = ME.role === 'admin';
// operatorul (paznicul) vede + actioneaza (sirena, vorbit, PTZ), dar nu configureaza
const CAN_ACT = ADMIN || ME.role === 'operator';
if (!ADMIN) document.body.classList.add('viewer');
if (ME.user) {
  const rol = ADMIN ? '' : ` (${ME.role === 'operator' ? t('operator') : t('vizualizare')})`;
  $('#user-chip').innerHTML = html`<span class="me-open" title="${t('Contul meu: 2FA, notificari push, dispozitive')}">${ME.user}${rol}${ME.totp ? ' 🔐' : ''}</span> · <a href="/logout">${t('ieși')}</a>`;
}

// mode 'mse,webrtc' pt. grila (substream Main, WebRTC merge); 'mse' pt. HD/playback
// (rezolutie nativa 6MP -> WebRTC o refuza, MSE suporta orice rezolutie/profil).
// iOS/iPadOS Safari nu are MSE fiabil si WebRTC refuza HD-ul 6MP -> HLS nativ Apple.
// canPlayType(m3u8) e adevarat doar pe Safari; pe restul e fals, deci 'hls' se ignora.
// maxTouchPoints>1 separa iOS/iPadOS de Safari desktop (unde MSE merge si pe HD).
const HLS_NATIVE = !!document.createElement('video').canPlayType('application/vnd.apple.mpegurl');
const IOS = HLS_NATIVE && navigator.maxTouchPoints > 1;
const HLS_SUFFIX = HLS_NATIVE ? ',hls' : '';   // fallback HLS pe orice Safari
// HD pe MSE peste tot (cu HLS doar ca rezerva pe Apple fara MSE). Calea HLS a go2rtc
// advertizeaza audio FLAC in manifest (CODECS="avc1...,fLaC"), pe care Apple/Chrome nu-l
// parseaza -> tot stream-ul pica (DEMUXER_ERROR_COULD_NOT_PARSE) si HD-ul ramanea gri,
// tinandu-ne pe substream (640x480 = calitate slaba). MSE nu are pista FLAC si merge
// dovedit pe Apple (substream-ul si Synology 4K ruleaza tot prin MSE/VideoToolbox).
const HD_MODE = 'mse' + HLS_SUFFIX;

function streamEl(name, mode = 'mse,webrtc', lazy = false, bufferTarget = 0) {
  const el = document.createElement('video-stream');
  el.mode = mode;
  el.background = false;
  // Buffer de jitter (secunde): 0 = low-latency (lipit de live, pt. HD/PTZ); >0 = mod fluid
  // (grila), tine un buffer care absoarbe variatiile de retea -> imagine neteda ca "Fluent".
  el.bufferTarget = bufferTarget;
  // Cand schimbi tabul, playerul se deconecteaza ca sa nu consume degeaba — dar abia
  // dupa DISCONNECT_TIMEOUT. Implicit 5s inseamna ca orice comutare scurta de tab cere
  // reconectare completa la intoarcere (WebSocket nou + asteptare keyframe ~2.5s).
  // Cu 90s, reveniri sub un minut si jumatate sunt instantanee.
  el.DISCONNECT_TIMEOUT = 90000;
  // Streaming lenes: un tile derulat in afara ecranului se deconecteaza (dupa
  // DISCONNECT_TIMEOUT) si se reconecteaza singur la revenire. La 100 de camere e
  // diferenta dintre a merge si a ingenunchea browserul.
  if (lazy) el.visibilityThreshold = 0.01;
  el.src = `${GO2RTC}/api/ws?src=${encodeURIComponent(name)}`;
  return el;
}

// Cadrul curent al unei camere din grila (deja decodat in browser) ca data-URL — poster
// instant, 0ms, imaginea reala de acum. null daca grila nu ruleaza acel flux (-> /api/thumb).
function gridFrame(id) {
  try {
    const vs = [...document.querySelectorAll('#grid video-stream')]
      .find((el) => (el.src || '').endsWith(`src=cam${id}`));
    const v = vs && (vs.video || vs.querySelector('video'));
    if (v && v.readyState >= 2 && v.videoWidth > 0) {
      const cv = document.createElement('canvas');
      cv.width = v.videoWidth; cv.height = v.videoHeight;
      cv.getContext('2d').drawImage(v, 0, 0);
      return cv.toDataURL('image/jpeg', 0.7);
    }
  } catch (e) { /* CORS/decode -> cadem pe /api/thumb */ }
  return null;
}

S.cameras = [];

/* ---------- coada de joburi: lucrul lung ruleaza pe server, interfata urmareste ----------
   Export MP4, fisa probatorie, clip, chenare, dosar ZIP, camera noua: ruta raspunde 202 +
   job, noi intrebam /api/jobs/<id> pana e gata si descarcam fisierul. Tava din dreapta-jos
   arata progresul si lasa anularea. */
const JOB_RO = { export: 'Export MP4', raport: 'Fișă probatorie', clip: 'Clip', chenare: 'Chenare pe clip',
                 dosar: 'Dosar ZIP', camera_noua: 'Camera nouă' };
const JOB_STARE_RO = { asteapta: 'în coada', ruleaza: 'ruleaza', gata: 'gata', eroare: 'eroare', anulat: 'anulat' };
const jobsSeen = new Map();   // id -> ultimul job vazut (pentru tava)
function jobsTray() {
  let el = $('#jobs-tray');
  if (!el) { el = document.createElement('div'); el.id = 'jobs-tray'; document.body.appendChild(el); }
  return el;
}
function renderJobs() {
  const tray = jobsTray();
  const jobs = [...jobsSeen.values()].slice(-6).reverse();
  tray.innerHTML = lista(jobs, (j) => {
    const activ = j.stare === 'asteapta' || j.stare === 'ruleaza';
    return html`<div class="job ${j.stare}" data-id="${j.id}">
      <div class="job-head">
        <span class="job-t">${t(JOB_RO[j.tip] || j.tip)}</span>
        <span class="job-m">${j.mesaj || t(JOB_STARE_RO[j.stare] || j.stare)}${activ && j.progres ? ' · ' + j.progres + '%' : ''}</span>
        ${activ ? html`<button class="job-x link-btn" title="${t('Anuleaza')}">✕</button>`
          : j.stare === 'gata' && j.are_fisier ? html`<button class="job-dl link-btn" title="${t('Descarca din nou')}">⬇</button>`
          : html`<button class="job-hide link-btn" title="${t('Inchide')}">✕</button>`}
      </div>
      ${activ ? html`<div class="job-bar"><i style="width:${j.progres || 0}%"></i></div>` : ''}
    </div>`;
  }, '');
  for (const el of tray.querySelectorAll('.job')) {
    const id = el.dataset.id;
    const x = el.querySelector('.job-x'), dl = el.querySelector('.job-dl'), h = el.querySelector('.job-hide');
    if (x) x.onclick = () => fetch(`/api/jobs/${id}`, { method: 'DELETE' });
    if (dl) dl.onclick = () => descarcaJob(jobsSeen.get(id));
    if (h) h.onclick = () => { jobsSeen.delete(id); renderJobs(); };
  }
}
function descarcaJob(j) {
  const a = document.createElement('a');
  a.href = `/api/jobs/${j.id}/fisier`;
  a.download = j.nume_fisier || '';
  document.body.appendChild(a); a.click(); a.remove();
}
async function jobWait(job, { onProgress, download = true } = {}) {
  for (;;) {
    jobsSeen.set(job.id, job);
    renderJobs();
    if (onProgress) onProgress(job);
    if (['gata', 'eroare', 'anulat'].includes(job.stare)) break;
    await new Promise((r) => setTimeout(r, 800));
    try { job = await (await fetch(`/api/jobs/${job.id}`)).json(); } catch {}
    if (job.error) { job = { ...jobsSeen.get(job.id || ''), stare: 'eroare', mesaj: job.error }; }
  }
  if (job.stare === 'eroare') toast(`${t(JOB_RO[job.tip] || job.tip)}: ${job.mesaj}`, true);
  else if (job.stare === 'gata' && job.are_fisier && download) descarcaJob(job);
  setTimeout(() => { if (jobsSeen.get(job.id) === job && job.stare !== 'eroare') { jobsSeen.delete(job.id); renderJobs(); } }, 12000);
  return job;
}
// fetch + (daca raspunsul e 202) asteptarea jobului. Intoarce jobul final, sau pentru un
// raspuns sincron {stare:'gata', rezultat, sincron:true}; null la eroare (cu toast).
async function runJob(url, opts = {}, ui = {}) {
  let r;
  try { r = await fetch(url, opts); } catch (e) { toast(t('Eroare rețea: {e}', { e }), true); return null; }
  const d = await r.json().catch(() => ({}));
  if (r.status === 202 && d.id) return jobWait(d, ui);
  if (!r.ok || d.error) { toast(d.error || t('Eroare {status}', { status: r.status }), true); return null; }
  return { stare: 'gata', rezultat: d, sincron: true };
}
// clipul e gata in cache? (altfel asteapta jobul care il genereaza)
async function clipReady(src) {
  const j = await runJob(src + '&job=1', {}, { download: false });
  return !!j && j.stare === 'gata';
}
// legaturile de descarcare (MP4, raport, ZIP) raman <a href>, dar trec prin coada
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.job-link');
  if (!a) return;
  e.preventDefault();
  runJob(a.getAttribute('href'));
});

// push-to-talk: element ascuns doar-microfon pe acelasi stream (go2rtc face backchannel ISAPI)
function talkStart(c, btn) {
  if (ptt) talkStop();
  btn.classList.add('on');
  ptt = document.createElement('video-stream');
  ptt.mode = 'webrtc';
  ptt.media = 'microphone';
  ptt.style.display = 'none';
  ptt.src = `${GO2RTC}/api/ws?src=${encodeURIComponent('cam' + c.id)}`;
  document.body.appendChild(ptt);
  ptt._btn = btn;
}
function talkStop() {
  if (!ptt) return;
  try { if (ptt.pc) ptt.pc.getSenders().forEach((s) => s.track && s.track.stop()); } catch {}
  ptt.remove();
  try { ptt.ondisconnect(); } catch {}
  if (ptt._btn) ptt._btn.classList.remove('on');
  ptt = null;
}

// starea microfonului (push-to-talk): scrisa de talkStart/talkStop din nucleu
let ptt = null;

// tabel de etichete folosit la evaluare de mai multe ecrane: sta in nucleu (evaluat primul)
const DET_RO = {
  person: 'Om', bird: 'Pasăre', cat: 'Pisică', dog: 'Câine',
  horse: 'Cal', sheep: 'Oaie', cow: 'Vacă', bear: 'Urs',
  car: 'Mașină', motorcycle: 'Motocicletă', bus: 'Autobuz', truck: 'Camion',
  chicken: 'Găină', pigeon: 'Porumbel',
};

// tabel de etichete folosit la evaluare de mai multe ecrane: sta in nucleu (evaluat primul)
const EV_RO = {
  VMD: 'Mișcare', linedetection: 'Linie trecută', fielddetection: 'Intruziune',
  regionEntrance: 'Intrare în zona', regionExiting: 'Ieșire din zona', IO: 'Alarma I/O',
  shelteralarm: 'Obiectiv acoperit', tamperdetection: 'Sabotaj', audioexception: 'Zgomot',
  scenechangedetection: 'Scena schimbată',
};

// text fara diacritice si cu litere mici, pentru cautari („Masina” gaseste „mașină”)
const fdia = (s) => (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

export { $, ADMIN, CAN_ACT, DET_RO, EV_RO, GO2RTC, HD_MODE, HLS_SUFFIX, JOB_STARE_RO, LIMBA, ME, clipReady, esc, fdia, gridFrame, html, icon, lista, raw, runJob, streamEl, t, talkStart, talkStop, toast };
