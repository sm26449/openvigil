// Harness IZOLAT pentru interfata (audit 5, T6): serveste app/static ca fisiere statice si
// intercepteaza /api/* cu date sintetice. Fara server Flask, baza de date, MQTT, GPU sau
// camere reale — merge in CI, pe orice masina cu Chromium (playwright). Deosebirea fata de
// ui-test.js: acela cere o INSTANTA LIVE (18 camere, go2rtc, login real) si creeaza date;
// acesta izoleaza frontendul si reproduce stari pe care o instalare reala le atinge greu
// (HTTP 500 la salvare, telemetrie absenta, raspunsuri intarziate), ca regresiile din audit
// sa fie prinse la fiecare schimbare.
//
// Pagina reala e index.html + traducere i18n pe server; cheia unui text ESTE textul romanesc
// (app/i18n.py), deci index.html brut e deja RO valid. Il servim ca atare, impreuna cu JS-ul
// real (app/static/js/*.js) — testam modulele adevarate, nu o macheta.
const http = require('http');
const fs = require('fs');
const path = require('path');

const STATIC_ROOT = path.join(__dirname, '..', 'app', 'static');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  // ES modules cer tip JS: fara asta browserul refuza <script type="module">
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.ico': 'image/x-icon', '.gif': 'image/gif',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf',
  '.map': 'application/json',
};

// Serveste app/static pe un port efemer local. /i18n/<limba>.json (ruta Flask in productie)
// se rezolva singur din app/static/i18n/<limba>.json.
function startStatic() {
  const srv = http.createServer((req, res) => {
    let p = decodeURIComponent(req.url.split('?')[0]);
    if (p === '/healthz') { res.writeHead(200, { 'Content-Type': 'application/json' }).end('{"ok":true}'); return; }
    if (p === '/') p = '/index.html';
    const cale = path.normalize(path.join(STATIC_ROOT, p));
    if (!cale.startsWith(STATIC_ROOT)) { res.writeHead(403).end(); return; }  // anti path-traversal
    fs.readFile(cale, (err, buf) => {
      if (err) { res.writeHead(404).end('not found'); return; }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(cale).toLowerCase()] || 'application/octet-stream' });
      res.end(buf);
    });
  });
  return new Promise((resolve) => {
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      resolve({ origin: `http://127.0.0.1:${port}`, close: () => new Promise((r) => srv.close(r)) });
    });
  });
}

// O regula/camera/setare completa, ca modulele reale sa se initializeze fara a arunca.
const CAM = (id, nume, grup) => ({
  id, name: nume, nume, grup: grup || 'Curte', model: 'DS-SINTETIC', ip: `10.0.0.${id}`,
  record: 'continuous', ai: true, vendor: 'hik',
  features: { zoom: false, smart: false, mic: false, strobo: false, sirena: false },
});
const REGULA = (over = {}) => ({
  id: 'r1', nume: 'Regula de test', activ: false,
  declansator: { tip: 'persoana_necunoscuta', valoare: '' },
  camere: [], grupuri: [], zile: [], moduri: [], interval: null, cooldown: 120,
  actiuni: [{ tip: 'sirena', volum: 90, repetari: 3 }], ...over,
});
// Obiect complet: loadSettings (60-setari.js) citeste direct aceste campuri si CADE daca
// notify_labels / auto_siren_labels nu sunt array-uri (checksHtml face `.includes`). Fara el,
// loadSettings arunca inainte de linia care (de)blocheaza #s-save — deci S1/S2 nu mai au sens.
const SETTINGS = {
  telegram_token: '', telegram_chat: '', web_url: '', telegram_comenzi: false,
  digest_telegram: false, digest_ora: '21:00', mod_auto_noapte: false, nvr_retras: false,
  email_enabled: false, email_smtp: '', email_port: 587, email_user: '', email_pass: '', email_to: '',
  notify_cooldown: 120, notify_labels: [], auto_siren_labels: [], reencode_enabled: false,
  retention_days: 14, events_keep_days: 30,
  backup_dir: '', backup_remote: '', backup_pastreaza: 14, backup_fete: false,
  incident_gap_sec: 300,
  webhook_urls: [], webhook_secret: '', webhook_events: [], api_token: '',
  noise_alarm_enabled: false, noise_threshold_db: -25, noise_sustain_sec: 5, noise_spike_db: 18,
  auto_siren_sound: 'sirena', auto_siren_volume: 90, auto_siren_times: 3,
  mqtt_enabled: false, mqtt_host: '', mqtt_port: 1883, mqtt_user: '', mqtt_pass: '', mqtt_topic: 'nvr',
  llm_url: '', llm_model: '', nume_sistem: 'OpenVigil', limba: 'ro',
};

// Raspunsuri implicite pe care boot-ul si taburile le ating. Un test le poate suprascrie prin
// `api`. Valoarea e un obiect JSON sau o functie (ctx) => obiect | { _status, _json }.
function apiDefaults() {
  return {
    '/api/me': { user: 'admin', role: 'admin', csrf: 'test-csrf', totp: false, versiune: 'test' },
    '/api/cameras': [CAM(1, 'Poartă', 'Curte'), CAM(2, 'Terasă', 'Curte')],
    '/api/events': { alarms: {}, detections: {} },
    '/api/pulse': {},
    '/api/settings': { ...SETTINGS },
    '/api/keyframes': { per_cam: {}, common: 2000 },
    '/api/faces': { persoane: [] },
    '/api/faces/history': { items: [] },
    '/api/faces/config': { enabled: false, notify: false, unknown_save: false, antispoof: false },
    '/api/plates': { placi: [] },
    '/api/plates/config': { enabled: false },
    '/api/semantic/config': { enabled: false },
    '/api/adnotare': { items: [] },
    // d.zile e ARRAY de nume de zile (render-ul face (d.zile||[]).map si d.zile[z]); d.triggers e obiect
    '/api/rules': { rules: [], triggers: { persoana_necunoscuta: 'Persoană necunoscută' }, zile: ['Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sa', 'Du'], groups: [] },
    // sub-loaderele din Setari care pot bloca salvarea (loadNoise / loadHooks) au nevoie de
    // forme valide, altfel ar comuta incarcareOk=false si ar dezactiva #s-save pe degeaba
    // cei patru loaderi AWAITED din loadSettings care comuta incarcareOk daca arunca
    // (loadNoise / loadHooks / loadRetention / loadBackupNocturn) — forme complete, ca
    // happy-path-ul sa pastreze #s-save activ in mod determinist
    '/api/noise': { reguli: { noise_alarm_enabled: false, incident_gap_sec: 300, noise_threshold_db: -25, noise_sustain_sec: 5, noise_spike_db: 18 }, camere: [] },
    '/api/noise/stats': { camere: [] },
    '/api/webhooks': { evenimente: {}, jurnal: [] },
    '/api/retention': { setari: { retention_days: 14, retention_event_days: 30, retention_snapshots_days: 30, retention_min_free_gb: 50, events_keep_days: 30, arhiva_dir: '', arhiva_zile: 0, arhiva_min_free_gb: 20 }, arhiva: { activ: false }, camere: [], total_gb: 0 },
    '/api/backup/stare': { setari: {}, stare: null, copii: [] },
    // loadSystem doar logheaza la eroare (nu blocheaza salvarea), dar il hranim ca sa nu
    // polueze jurnalul CI cu „starea sistemului nu s-a putut afisa"
    '/api/system': { cpu: 5, cores: 4, load: [0.1, 0.1, 0.1], ram_used_gb: 2, ram_total_gb: 8, disk_total_gb: 100, disk_free_gb: 50, rec_gb: 10, net: { in_mbps: 1, out_mbps: 1 }, uptime_h: 24, notificari: null, nvr: null, ai: null },
    '/api/tile': { cams: [] },
    '/api/flux': { zi: '2026-01-01', cadre: [] },   // fluxLoad citeste d.cadre.length
    '/api/vreme': {},
    '/api/mod': { mod: 'acasa' },
    '/api/stare': {},
    '/api/health': { ok: true },
    '/api/program': { reguli: [], orar: {} },
    '/api/views': [],
    '/api/groups': [],
  };
}

// Instaleaza interceptarea /api/* pe pagina. `overrides` au prioritate peste implicite.
// Intoarce `calls`: lista { path, method, body } pentru verificari (ce a trimis UI-ul).
async function installApi(page, overrides = {}) {
  const defaults = apiDefaults();
  const calls = [];
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const method = req.method();
    let body = null;
    try { body = req.postData() ? JSON.parse(req.postData()) : null; } catch { body = req.postData(); }
    calls.push({ path: p, method, body });

    // fluxuri: WebSocket-ul nu trece pe aici, dar cadrul JPEG si pingurile de flux da — le
    // inchidem scurt ca sa nu zgomote testul
    if (p === '/api/ws' || p.startsWith('/api/frame')) { return route.fulfill({ status: 204, body: '' }); }

    let spec = overrides[p] !== undefined ? overrides[p] : defaults[p];
    if (typeof spec === 'function') spec = await spec({ method, body, path: p, url });
    // { _abort: true } rupe reteaua (loaderul prinde exceptia) — asa reproducem „sectiunea nu
    // s-a incarcat" din S1, pe care un 500 cu corp JSON NU-l declanseaza (fetch nu arunca)
    if (spec && typeof spec === 'object' && spec._abort) { return route.abort('failed'); }
    if (spec === undefined) spec = {};   // endpoint necunoscut: 200 {} (codul UI e defensiv: `|| []`)

    let status = 200, payload = spec;
    if (spec && typeof spec === 'object' && ('_status' in spec || '_json' in spec)) {
      status = spec._status || 200;
      payload = '_json' in spec ? spec._json : {};
    }
    await route.fulfill({
      status,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(payload),
    });
  });
  return calls;
}

// scurtaturi pentru teste
const err500 = (json = { error: 'eroare sintetica' }) => ({ _status: 500, _json: json });
const delay = (ms, value) => async () => { await new Promise((r) => setTimeout(r, ms)); return value; };

module.exports = { startStatic, installApi, apiDefaults, CAM, REGULA, SETTINGS, err500, delay };
