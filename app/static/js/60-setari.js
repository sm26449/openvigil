/* ====================================================================
   Setari: incarcare/salvare, semantic, card-tabs, MQTT, utilizatori, Contul meu, automatizari, continuitate, backup
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, DET_RO, LIMBA, ME, esc, html, lista, raw, runJob, t, toast, fdia } from './00-core.js';
import { loadCameras, post } from './10-live.js';
import { hdGoLive } from './20-hd.js';
import { AL_ICO } from './40-evenimente.js';
import { SOUND_RO } from './50-camere.js';
/* ---------- tabul Setari ---------- */
const SET_LABELS = ['person', 'dog', 'cat', 'bird', 'chicken', 'pigeon',
                    'horse', 'sheep', 'cow', 'bear',
                    'car', 'motorcycle', 'bus', 'truck'];
function checksHtml(containerId, checked) {
  const box = $(containerId);
  box.innerHTML = '';
  for (const l of SET_LABELS) {
    const lab = document.createElement('label');
    lab.className = 'inline';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = l;
    cb.checked = checked.includes(l);
    lab.appendChild(cb);
    lab.append(` ${AL_ICO[l] || ''} ${t(DET_RO[l] || l)}`);
    box.appendChild(lab);
  }
}
const checkedOf = (id) => [...$(id).querySelectorAll('input:checked')].map((c) => c.value);

async function loadSettings() {
  const s = await (await fetch('/api/settings')).json();
  $('#s-tg-token').value = s.telegram_token;
  $('#s-tg-chat').value = s.telegram_chat;
  $('#s-web-url').value = s.web_url || '';
  $('#s-tg-cmd').checked = !!s.telegram_comenzi;
  if ($('#s-digest-on')) { $('#s-digest-on').checked = !!s.digest_telegram; $('#s-digest-ora').value = s.digest_ora || '21:00'; }
  $('#s-mod-noapte').checked = !!s.mod_auto_noapte;
  $('#s-nvr-retras').checked = !!s.nvr_retras;
  $('#s-em-on').checked = s.email_enabled;
  $('#s-em-smtp').value = s.email_smtp;
  $('#s-em-port').value = s.email_port;
  $('#s-em-user').value = s.email_user;
  $('#s-em-pass').value = s.email_pass;
  $('#s-em-to').value = s.email_to;
  $('#s-cooldown').value = s.notify_cooldown;
  checksHtml('#s-notify-labels', s.notify_labels);
  checksHtml('#s-siren-labels', s.auto_siren_labels);
  $('#s-reencode').checked = s.reencode_enabled;
  fillSoundSelect($('#s-auto-sound'), s.auto_siren_sound);
  $('#s-auto-vol').value = s.auto_siren_volume;
  $('#s-auto-times').value = s.auto_siren_times;
  loadStorage();
  loadKeyframes();
  loadUsers();
  loadPushAdmin();
  loadSemantic();
  loadTile();
  loadMqtt(s);
  loadTrainStatus();
  // Cele trei umplu campuri din formular, deci instantaneul de referinta (mai jos) nu se
  // poate lua inainte de ele — dar nu depind una de alta, deci merg in paralel. Secvential
  // durau 2.8 secunde, adica atat astepta si butonul "Renunta" pana sa se vada ceva.
  // loadBackupNocturn umple si el campuri din formular -> INAINTE de instantaneul de referinta,
  // altfel Setarile se cred „modificate" si cer confirmare la fiecare schimbare de tab
  await Promise.all([loadRetention(), loadNoise(), loadHooks(), loadBackupNocturn()]);
  loadNoiseProfile();
  loadContinuity();
  loadSystem(); loadTimeStatus(); loadLog();
  clearInterval(noiseTimer);
  noiseTimer = setInterval(() => {
    if (document.hidden || $('#set').classList.contains('hidden')) return;
    loadNoise();
    if (Date.now() - noiseProfAt > 300000) { noiseProfAt = Date.now(); loadNoiseProfile(); }
  }, 4000);
  savedSnapshot = snapshotSettings();
  checkDirty();
  const pane = localStorage.getItem('setPane');
  if (pane && $('#pane-' + pane)) document.querySelector(`.snav-item[data-pane="${pane}"]`)?.click();
  clearInterval(sysTimer);
  sysTimer = setInterval(() => { if (!document.hidden && !$('#set').classList.contains('hidden')) loadSystem(); }, 5000);
}
/* ---------- cautare semantica: stare si activare (Setari -> Detectie) ---------- */
let semTimer = null;
async function loadSemantic() {
  try {
    const s = await (await fetch('/api/semantic/status')).json();
    $('#sem-on').checked = s.enabled;
    const b = s.backfill;
    const txt = t('{n} poze indexate din {zile} zile', { n: s.indexed, zile: s.days });
    $('#sem-status').textContent = b.running
      ? `${txt} — ${t('indexez istoricul: {done}/{total}', { done: b.done, total: b.total })}` : txt;
    clearInterval(semTimer);
    if (b.running && !document.hidden && !$('#set').classList.contains('hidden')) {
      semTimer = setInterval(loadSemantic, 3000);
    }
  } catch {}
}
$('#sem-on') && ($('#sem-on').onchange = async () => {
  await post('/api/semantic/config', { enabled: $('#sem-on').checked });
  flash('#sem-msg', $('#sem-on').checked
    ? t('Pornită — istoricul se indexeaza în fundal ✓') : t('Oprită ✓'));
  loadSemantic();
});
/* detectie pe regiuni: camerele alese (obiecte la distanta) */
async function loadTile() {
  try {
    const d = await (await fetch('/api/tile')).json();
    $('#tl-cams').innerHTML = html`${S.cameras.map((c) =>
      html`<option value="${c.id}" ${d.cams.includes(c.id) ? 'selected' : ''}>${c.id}. ${c.name}</option>`)}`;
  } catch {}
}
$('#tl-save') && ($('#tl-save').onclick = async () => {
  const cams = [...$('#tl-cams').selectedOptions].map((o) => +o.value);
  const r = await post('/api/tile', { cams });
  if (r && !r.error) flash('#tl-msg', t('Salvat ✓'));
});
$('#sem-backfill') && ($('#sem-backfill').onclick = async () => {
  const r = await post('/api/semantic/backfill', {});
  flash('#sem-msg', r && r.started ? t('Indexarea a pornit ✓') : t('Indexarea rulează deja'));
  loadSemantic();
});

/* ---------- tab-uri interne per sectiune: un card vizibil o data ----------
   Cardurile insirate (unele late, unele pe coloane) obosesc; fiecare sectiune cu
   2+ carduri primeste o bara de tab-uri construita automat din titlurile h2.
   Sectiunile-dashboard (data-notabs) raman cu toate cardurile la vedere. */
function buildCardTabs() {
  for (const pane of document.querySelectorAll('.set-pane, .ai-pane')) {
    if (pane.hasAttribute('data-notabs')) continue;
    const cols = pane.querySelector('.set-cols');
    if (!cols) continue;
    const cards = [...cols.querySelectorAll(':scope > .set-card')];
    if (cards.length < 2) continue;
    const bar = document.createElement('div');
    bar.className = 'card-tabs';
    const key = 'cardTab:' + pane.id;
    const activate = (i, save) => {
      cards.forEach((c, j) => { c.style.display = j === i ? '' : 'none'; });
      [...bar.children].forEach((b, j) => b.classList.toggle('on', j === i));
      if (save) localStorage.setItem(key, i);
    };
    cards.forEach((c, i) => {
      const h = c.querySelector('h2');
      const b = document.createElement('button');
      b.textContent = (h ? h.textContent : t('Secțiunea {n}', { n: i + 1 })).trim();
      if (c.hasAttribute('data-avansat')) b.dataset.avansat = '1';   // ascuns fara „avansate”
      b.onclick = () => activate(i, true);
      bar.appendChild(b);
    });
    cols.classList.add('tabbed');
    cols.parentNode.insertBefore(bar, cols);
    const saved = +localStorage.getItem(key);
    activate(Number.isInteger(saved) && saved >= 0 && saved < cards.length ? saved : 0);
  }
}
buildCardTabs();

/* ---------- cautare in setari + „avansate” ----------
   Cautarea arata, din toate sectiunile, doar cardurile al caror titlu sau text contine
   cuvintele cautate (fara diacritice); „avansate” ascunde cardurile rar atinse (data-avansat)
   si tab-urile lor, ca sa ramana la vedere ce se schimba de obicei. */
function setCauta(q) {
  q = fdia(q.trim());
  const set = $('#set');
  set.classList.toggle('cautare', !!q);
  const vechi = set.querySelector('.q-niciun'); if (vechi) vechi.remove();
  if (!q) { for (const c of set.querySelectorAll('.set-card.q-hit')) c.classList.remove('q-hit'); return; }
  const cuv = q.split(/\s+/).filter(Boolean);
  let n = 0;
  for (const c of set.querySelectorAll('.set-card')) {
    const txt = fdia(c.textContent);
    const hit = cuv.every((w) => txt.includes(w));
    c.classList.toggle('q-hit', hit);
    if (hit) n++;
  }
  if (!n) { const p = document.createElement('p'); p.className = 'q-niciun'; p.textContent = t('Nicio setare pentru „{q}”.', { q }); set.querySelector('.set-body').prepend(p); }
}
$('#set-q') && ($('#set-q').oninput = () => setCauta($('#set-q').value));
function setAvansat(on, save = true) {
  $('#set').classList.toggle('avansat', on);
  $('#set-adv').checked = on;
  if (save) try { localStorage.setItem('setAvansat', on ? '1' : '0'); } catch {}
  if (!on) {
    // daca sub-tabul activ e unul avansat, trecem pe primul vizibil
    for (const bar of document.querySelectorAll('#set .card-tabs')) {
      const act = bar.querySelector('button.on');
      if (act && act.dataset.avansat) { const b = [...bar.children].find((x) => !x.dataset.avansat); if (b) b.click(); }
    }
  }
}
$('#set-adv') && ($('#set-adv').onchange = () => setAvansat($('#set-adv').checked));
try { if ($('#set-adv')) setAvansat(localStorage.getItem('setAvansat') === '1', false); } catch {}

/* ---------- puntea MQTT (Setari -> Sistem) ---------- */
function loadMqtt(s) {
  $('#mq-on').checked = !!s.mqtt_enabled;
  $('#mq-host').value = s.mqtt_host || '';
  $('#mq-port').value = s.mqtt_port || 1883;
  $('#mq-user').value = s.mqtt_user || '';
  $('#mq-pass').value = s.mqtt_pass || '';
  $('#mq-topic').value = s.mqtt_topic || 'nvr';
}
$('#mq-test') && ($('#mq-test').onclick = async () => {
  $('#mq-status').textContent = t('Mă conectez...');
  const r = await post('/api/mqtt/test', {
    mqtt_enabled: $('#mq-on').checked,
    mqtt_host: $('#mq-host').value.trim(),
    mqtt_port: +$('#mq-port').value || 1883,
    mqtt_user: $('#mq-user').value.trim(),
    mqtt_pass: $('#mq-pass').value,
    mqtt_topic: $('#mq-topic').value.trim() || 'nvr',
  });
  $('#mq-status').textContent = r && !r.error
    ? t('Conectat la {broker} ✓ — proba publicata pe „{topic}/proba”', { broker: r.broker, topic: $('#mq-topic').value || 'nvr' })
    : (r && r.error) || t('eroare');
});

/* ---------- utilizatori si roluri (doar adminul ajunge aici) ---------- */
async function loadUsers() {
  try {
    const list = await (await fetch('/api/users')).json();
    const rol = (u) => (u.role === 'admin' ? t('administrator')
      : u.role === 'operator' ? t('operator') : t('vizualizare'));
    const scop = (u) => {
      const parti = [];
      if (u.groups && u.groups.length) parti.push(t('grupuri: {lista}', { lista: u.groups.join(', ') }));
      if (u.cameras && u.cameras.length) parti.push(t('camere: {lista}', { lista: u.cameras.join(', ') }));
      return parti.length ? parti.join(' · ') : t('toate camerele');
    };
    $('#u-list').innerHTML = lista(list, (u) => html`
      <div class="u-row" data-user="${u.user}">
        <b>${u.user}</b> <span class="u-role">${rol(u)}</span>
        ${u.totp ? html`<span class="u-flag" title="${t('autentificare in doi pasi activa')}">🔐 2FA</span>` : ''}
        <span class="hint">${u.role === 'admin' ? '' : scop(u)}</span>
        <button class="u-edit link-btn" title="${t('Încarcă în formular')}">${t('modifică')}</button>
        <button class="u-revoke link-btn" title="${t('Taie toate sesiunile contului (telefon pierdut, paznic plecat)')}">${t('deconectează')}</button>
        ${u.totp ? html`<button class="u-tfa link-btn" title="${t('Scoate al doilea pas (telefon pierdut, fara coduri de recuperare)')}">${t('scoate 2FA')}</button>` : ''}
        ${u.user === ME.user ? '' : html`<button class="u-del link-btn">${t('sterge')}</button>`}
      </div>`, html`<p class="hint">${t('Niciun utilizator.')}</p>`);
    for (const row of document.querySelectorAll('#u-list .u-row')) {
      const name = row.dataset.user;
      const u = list.find((x) => x.user === name);
      row.querySelector('.u-edit').onclick = () => fillUserForm(u);
      const del = row.querySelector('.u-del');
      if (del) del.onclick = async () => {
        if (!confirm(t('Ștergi utilizatorul "{name}"?', { name }))) return;
        const r = await fetch(`/api/users/${encodeURIComponent(name)}`, { method: 'DELETE' });
        const d = await r.json();
        if (d.error) { alert(t('Eroare: {e}', { e: d.error })); return; }
        loadUsers();
      };
      row.querySelector('.u-revoke').onclick = async () => {
        if (!confirm(t('Deconectezi toate dispozitivele lui "{name}"?', { name }))) return;
        const d = await post(`/api/users/${encodeURIComponent(name)}/revoca`);
        if (d && !d.error) flash('#u-msg', t('Sesiunile lui {name} au fost tăiate ✓', { name }));
      };
      const tfa = row.querySelector('.u-tfa');
      if (tfa) tfa.onclick = async () => {
        if (!confirm(t('Scoți autentificarea în doi pași de pe "{name}"? Contul rămâne doar cu parola.', { name }))) return;
        const r = await fetch(`/api/users/${encodeURIComponent(name)}/2fa`, { method: 'DELETE' });
        const d = await r.json();
        if (d.error) { alert(t('Eroare: {e}', { e: d.error })); return; }
        loadUsers();
      };
    }
    buildUserScope();
  } catch {}
}
function fillUserForm(u) {
  $('#u-form-title').textContent = t('Modifici: {nume}', { nume: u.user });
  $('#u-cancel').classList.remove('hidden');
  $('#u-name').value = u.user;
  $('#u-pass').value = '';
  $('#u-pass').placeholder = t('parola noua (gol = neschimbata)');
  $('#u-role').value = u.role;
  buildUserScope(u);
  window.scrollTo({ top: $('#u-form-title').offsetTop - 80 });
}
function clearUserForm() {
  $('#u-form-title').textContent = t('Adaugă un utilizator');
  $('#u-cancel').classList.add('hidden');
  $('#u-name').value = '';
  $('#u-pass').value = '';
  $('#u-pass').placeholder = t('minim 6 caractere');
  $('#u-role').value = 'viewer';
  buildUserScope();
}
$('#u-cancel') && ($('#u-cancel').onclick = clearUserForm);

// bifele de grupuri + lista de camere pentru restrictia unui viewer
function buildUserScope(u) {
  const groups = [...new Set(S.cameras.map((c) => c.group || 'Altele'))].sort();
  $('#u-groups').innerHTML = html`${groups.map((g) => html`
    <label class="inline"><input type="checkbox" value="${g}"
      ${u && u.groups && u.groups.includes(g) ? 'checked' : ''}> ${g}</label>`)}`;
  const sel = $('#u-cams');
  sel.innerHTML = html`${S.cameras.map((c) => html`
    <option value="${c.id}" ${u && u.cameras && u.cameras.includes(c.id) ? 'selected' : ''}>
      ${c.id}. ${c.name}</option>`)}`;
  $('#u-scope').classList.toggle('hidden', ($('#u-role').value || 'viewer') === 'admin');
}
$('#u-role') && ($('#u-role').onchange = () => buildUserScope());

$('#u-save') && ($('#u-save').onclick = async () => {
  const name = $('#u-name').value.trim();
  if (!name) { alert(t('Scrie numele utilizatorului.')); return; }
  const body = {
    user: name,
    pass: $('#u-pass').value || undefined,
    role: $('#u-role').value,
    groups: [...document.querySelectorAll('#u-groups input:checked')].map((i) => i.value),
    cameras: [...$('#u-cams').selectedOptions].map((o) => +o.value),
  };
  const d = await post('/api/users', body);
  if (d && !d.error) {
    clearUserForm();
    flash('#u-msg', t('Utilizator salvat ✓'));
    loadUsers();
  }
});
/* ---------- Contul meu (orice rol): 2FA, notificari push pe acest dispozitiv, dispozitive ----------
   Dialog nativ: Esc si focus vin gratis; se deschide din numele utilizatorului (sus) si din
   Setari. 2FA: serverul da un secret + QR, aplicatia de autentificare il scaneaza, codul
   de 6 cifre confirma; abia atunci se salveaza. Push: browserul se aboneaza la serviciul
   lui (cheia VAPID a instalarii), noi tinem abonamentul pe server si trimitem incidentele. */
const meDlg = $('#me-dlg');
let pushSub = null;
async function openMe() {
  if (!meDlg || typeof meDlg.showModal !== 'function') return;
  $('#me-user').textContent = `— ${ME.user}`;
  limbiSelect();
  $('#tfa-pas1').classList.add('hidden'); $('#tfa-pas2').classList.add('hidden');
  $('#tfa-off-box').classList.add('hidden'); $('#tfa-btns').classList.remove('hidden');
  if (!meDlg.open) meDlg.showModal();
  await Promise.all([loadTfa(), loadPushState()]);
}
for (const b of document.querySelectorAll('.me-open')) b.onclick = openMe;

// limba contului: optiunea goala = dupa browser; lista vine din /api/me (ME.limbi)
function limbiSelect() {
  const sel = $('#me-limba');
  if (!sel || sel.dataset.gata) return;
  sel.dataset.gata = '1';
  for (const l of ME.limbi || []) {
    const o = document.createElement('option');
    o.value = l.cod; o.textContent = l.nume;
    sel.appendChild(o);
  }
  sel.value = ME.lang_cont || '';
  sel.onchange = async () => {
    const d = await post('/api/me/limba', { limba: sel.value });
    if (!d || !d.ok) return;
    flash('#me-limba-msg', t('Limba salvată. Reîncarc pagina…'));
    setTimeout(() => location.reload(), 600);
  };
}
$('#me-close').onclick = () => meDlg.close();

async function loadTfa() {
  try {
    const me = await (await fetch('/api/me')).json();
    ME.totp = !!me.totp;
    $('#tfa-stare').textContent = me.totp
      ? t('Activă: la fiecare login, după parola, ti se cere codul din aplicația de autentificare.')
      : t('Inactivă: contul e protejat doar de parola. Cu al doilea pas, o parola furată nu ajunge.');
    $('#tfa-on').classList.toggle('hidden', me.totp);
    $('#tfa-off').classList.toggle('hidden', !me.totp);
  } catch {}
}
$('#tfa-on').onclick = async () => {
  const d = await post('/api/me/2fa/pregateste');
  if (!d || d.error) return;
  $('#tfa-qr').src = d.qr || '';
  $('#tfa-qr').classList.toggle('hidden', !d.qr);
  $('#tfa-secret').textContent = d.secret.replace(/(.{4})/g, '$1 ').trim();
  $('#tfa-cod').value = '';
  $('#tfa-btns').classList.add('hidden'); $('#tfa-pas1').classList.remove('hidden');
  $('#tfa-cod').focus();
};
$('#tfa-cancel').onclick = () => { $('#tfa-pas1').classList.add('hidden'); $('#tfa-btns').classList.remove('hidden'); };
$('#tfa-confirm').onclick = async () => {
  const d = await post('/api/me/2fa/activeaza', { cod: $('#tfa-cod').value.trim() });
  if (!d || d.error) return;
  $('#tfa-rec').textContent = (d.recuperare || []).join('\n');
  $('#tfa-pas1').classList.add('hidden'); $('#tfa-pas2').classList.remove('hidden');
};
$('#tfa-cod').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#tfa-confirm').click(); } };
$('#tfa-done').onclick = async () => {
  $('#tfa-pas2').classList.add('hidden'); $('#tfa-btns').classList.remove('hidden');
  flash('#tfa-msg', t('Autentificarea în doi pași e activă ✓'));
  await loadTfa();
  if (ME.user) {
    const rolTxt = ADMIN ? '' : ' (' + (ME.role === 'operator' ? t('operator') : t('vizualizare')) + ')';
    $('#user-chip .me-open').textContent = `${ME.user}${rolTxt} 🔐`;
  }
  if (ADMIN) loadUsers();
};
$('#tfa-off').onclick = () => { $('#tfa-parola').value = ''; $('#tfa-btns').classList.add('hidden'); $('#tfa-off-box').classList.remove('hidden'); $('#tfa-parola').focus(); };
$('#tfa-off-cancel').onclick = () => { $('#tfa-off-box').classList.add('hidden'); $('#tfa-btns').classList.remove('hidden'); };
$('#tfa-off-confirm').onclick = async () => {
  const d = await post('/api/me/2fa/dezactiveaza', { parola: $('#tfa-parola').value });
  if (!d || d.error) return;
  $('#tfa-off-box').classList.add('hidden'); $('#tfa-btns').classList.remove('hidden');
  flash('#tfa-msg', t('Autentificarea în doi pași a fost dezactivată'));
  await loadTfa();
  if (ADMIN) loadUsers();
};
$('#sess-all').onclick = async () => {
  if (!confirm(t('Deconectezi toate celelalte dispozitive ale contului tău? Aici rămâi logat.'))) return;
  const d = await post('/api/me/deconectare');
  if (d && !d.error) flash('#sess-msg', t('Toate celelalte sesiuni au fost tăiate ✓'));
};

// --- push pe acest dispozitiv ---
function b64url(s) {
  const pad = '='.repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
async function pushReg() {
  // `ready` nu se rezolva niciodata daca inregistrarea a picat (http fara localhost)
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return null;
  return Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), 3000))]);
}
async function loadPushState() {
  const st = $('#push-stare');
  const btnOn = $('#push-on'), btnOff = $('#push-off'), btnTest = $('#push-test');
  btnOn.classList.add('hidden'); btnOff.classList.add('hidden'); btnTest.classList.add('hidden');
  pushSub = null;
  try {
    const d = await (await fetch('/api/push/cheie')).json();
    if (!d.activ) { st.textContent = t('Serverul nu are cheile de push (lipsește pywebpush).'); return; }
    if (!('PushManager' in window) || !('Notification' in window)) {
      st.textContent = t('Browserul asta nu știe notificari push (pe iPhone: instalează aplicația pe ecranul de start, din Safari).'); return;
    }
    if (!window.isSecureContext) {
      st.textContent = t('Notificarile push cer HTTPS: deschide interfata prin adresa https (ex. https://nvr.exemplu.ro), nu prin {host}.', { host: location.host }); return;
    }
    const reg = await pushReg();
    if (!reg) { st.textContent = t('Service worker-ul nu s-a înregistrat (reîncarcă pagina).'); return; }
    pushSub = await reg.pushManager.getSubscription();
    const al = pushSub && (d.abonamente || []).find((a) => a.endpoint === pushSub.endpoint);
    if (Notification.permission === 'denied') {
      st.textContent = t('Notificarile sunt BLOCATE pentru acest site din setările browserului.'); return;
    }
    if (al) {
      st.textContent = t('Pornite pe acest dispozitiv ({nivel}). Alte dispozitive abonate pe contul tău: {altele}.', {
        nivel: al.nivel === 'atentie' ? t('toate incidentele') : t('doar critice'),
        altele: Math.max(0, (d.abonamente || []).length - 1) });
      $('#push-nivel').value = al.nivel;
      btnOff.classList.remove('hidden'); btnTest.classList.remove('hidden');
    } else {
      st.textContent = (d.abonamente || []).length
        ? t('Oprite aici; contul tău are {n} dispozitiv(e) abonat(e).', { n: d.abonamente.length })
        : t('Oprite. Pornește-le ca incidentele să ajungă pe acest dispozitiv.');
      btnOn.classList.remove('hidden');
    }
  } catch (e) { st.textContent = t('Nu pot citi starea notificarilor: {e}', { e }); }
}
$('#push-on').onclick = async () => {
  try {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') { flash('#push-msg', t('Nu ai permis notificarile'), true); return; }
    const d = await (await fetch('/api/push/cheie')).json();
    const reg = await pushReg();
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64url(d.cheie) });
    const r = await post('/api/push/abonare', { abonament: sub.toJSON(), nivel: $('#push-nivel').value });
    if (r && !r.error) flash('#push-msg', t('Notificarile sunt pornite pe acest dispozitiv ✓'));
  } catch (e) { flash('#push-msg', t('Nu am putut porni: {e}', { e: e.message || e }), true); }
  loadPushState();
  if (ADMIN) loadPushAdmin();
};
$('#push-nivel').onchange = async () => {
  if (!pushSub) return;
  const r = await post('/api/push/abonare', { abonament: pushSub.toJSON(), nivel: $('#push-nivel').value });
  if (r && !r.error) flash('#push-msg', t('Nivel salvat ✓'));
  loadPushState();
};
$('#push-off').onclick = async () => {
  try {
    if (pushSub) {
      await fetch('/api/push/abonare', { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: pushSub.endpoint }) });
      await pushSub.unsubscribe();
    }
    flash('#push-msg', t('Notificarile sunt oprite pe acest dispozitiv'));
  } catch (e) { flash('#push-msg', t('Eroare: {e}', { e }), true); }
  loadPushState();
  if (ADMIN) loadPushAdmin();
};
$('#push-test').onclick = async () => {
  const d = await post('/api/push/test');
  if (d && !d.error) flash('#push-msg', t('Trimisă pe {n} dispozitiv(e) — ar trebui să apără în câteva secunde', { n: d.trimise }));
};
// Setari -> Notificari: adminul vede toate dispozitivele abonate si poate scoate unul
async function loadPushAdmin() {
  const box = $('#push-lista');
  if (!box) return;
  try {
    const d = await (await fetch('/api/push/abonamente')).json();
    const cand = (ts) => (ts ? new Date(ts * 1000).toLocaleString(LIMBA, { dateStyle: 'short', timeStyle: 'short' }) : '-');
    box.innerHTML = lista(d.abonamente || [], (a) => html`
      <div class="push-row" data-ep="${a.endpoint}">
        <b>${a.user}</b> <span>${a.nivel === 'atentie' ? t('toate') : t('critice')}</span>
        <span class="hint" title="${a.agent || ''}">${(a.agent || '').replace(/^Mozilla\/5\.0 /, '').slice(0, 60)}</span>
        <span class="hint" title="${t('abonat / ultima notificare')}">${cand(a.creat)}${a.ultima ? ' · ' + cand(a.ultima) : ''}${a.erori ? ' · ' + t('erori: {n}', { n: a.erori }) : ''}</span>
        <button class="push-del link-btn">${t('scoate')}</button>
      </div>`, html`<p class="hint">${t('Niciun dispozitiv abonat.')}</p>`);
    for (const row of box.querySelectorAll('.push-row')) {
      row.querySelector('.push-del').onclick = async () => {
        await fetch('/api/push/abonare', { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: row.dataset.ep }) });
        loadPushAdmin();
      };
    }
  } catch {}
}

function settingsBody() {
  return {
    telegram_token: $('#s-tg-token').value.trim(),
    telegram_chat: $('#s-tg-chat').value.trim(),
    web_url: $('#s-web-url').value.trim(),
    telegram_comenzi: $('#s-tg-cmd').checked,
    digest_telegram: $('#s-digest-on').checked,
    digest_ora: $('#s-digest-ora').value || '21:00',
    mod_auto_noapte: $('#s-mod-noapte').checked,
    nvr_retras: $('#s-nvr-retras').checked,
    email_enabled: $('#s-em-on').checked,
    email_smtp: $('#s-em-smtp').value.trim(),
    email_port: +$('#s-em-port').value || 587,
    email_user: $('#s-em-user').value.trim(),
    email_pass: $('#s-em-pass').value,
    email_to: $('#s-em-to').value.trim(),
    notify_cooldown: +$('#s-cooldown').value || 300,
    notify_labels: checkedOf('#s-notify-labels'),
    auto_siren_labels: checkedOf('#s-siren-labels'),
    reencode_enabled: $('#s-reencode').checked,
    retention_days: +$('#ret-days').value || 14,
    retention_event_days: +$('#ret-ev').value || 60,
    retention_snapshots_days: +$('#ret-snap').value || 90,
    retention_min_free_gb: +$('#ret-free').value || 50,
    events_keep_days: +$('#ret-jurnal').value || 365,
    arhiva_dir: $('#arh-dir').value.trim(),
    arhiva_zile: +$('#arh-zile').value || 180,
    arhiva_min_free_gb: +$('#arh-free').value || 20,
    backup_ora: $('#bn-ora').value || '03:30',
    backup_dir: $('#bn-dir').value.trim(),
    backup_remote: $('#bn-remote').value.trim(),
    backup_pastreaza: +$('#bn-keep').value || 14,
    backup_fete: $('#bn-fete').checked,
    incident_gap_sec: +$('#inc-gap').value || 300,
    webhook_urls: $('#wh-urls').value.split('\n').map((s) => s.trim()).filter(Boolean),
    webhook_secret: $('#wh-secret').value.trim(),
    webhook_events: [...$('#wh-events').querySelectorAll('input:checked')].map((c) => c.dataset.ev),
    api_token: $('#wh-token').value.trim(),
    noise_alarm_enabled: $('#noise-on').checked,
    noise_threshold_db: +$('#noise-thr').value || -25,
    noise_sustain_sec: +$('#noise-sustain').value || 5,
    noise_spike_db: +$('#noise-spike').value || 18,
    auto_siren_sound: $('#s-auto-sound').value,
    auto_siren_volume: +$('#s-auto-vol').value,
    auto_siren_times: +$('#s-auto-times').value,
  };
}

async function loadKeyframes() {
  try {
    const k = await (await fetch('/api/keyframes')).json();
    const vals = Object.values(k.per_cam).filter(Boolean);
    const uniq = [...new Set(vals)].sort((a, b) => a - b);
    $('#s-kf-status').textContent = uniq.length === 1
      ? t('Acum: {s}s pe toate cele {n} camere.', { s: (uniq[0] / 1000).toFixed(1), n: vals.length })
      : t('Acum: valori diferite ({lista}).', { lista: uniq.map((v) => (v / 1000).toFixed(1) + 's').join(', ') });
    if (k.common) $('#s-kf').value = String(k.common);
  } catch {}
}
$('#s-kf-apply').onclick = async () => {
  const btn = $('#s-kf-apply');
  btn.disabled = true;
  flash('#s-msg-kf', t('Aplic pe toate camerele...'));
  const d = await post('/api/keyframes', { ms: +$('#s-kf').value });
  btn.disabled = false;
  if (d && !d.error) {
    flash('#s-msg-kf', t('Aplicat pe {ok}/{total} camere ✓', { ok: d.applied, total: d.total }) +
      (d.errors && d.errors.length ? ' ' + t('(erori: {lista})', { lista: d.errors.join('; ') }) : ''),
      !!(d.errors && d.errors.length));
  }
  loadKeyframes();
};

/* anuntul automat + generarea de anunturi noi */
function fillSoundSelect(sel, current) {
  sel.innerHTML = '';
  for (const [id, name] of Object.entries(SOUND_RO)) {
    const o = document.createElement('option');
    o.value = id; o.textContent = t(name);
    sel.appendChild(o);
  }
  for (let i = 1; i <= 3; i++) {
    const o = document.createElement('option');
    o.value = 'c' + i; o.textContent = t('Sunetul meu {n} (încărcat în camera)', { n: i });
    sel.appendChild(o);
  }
  sel.value = String(current);
}
$('#s-auto-test').onclick = async () => {
  const cam = S.cameras.find((c) => (c.features || {}).siren);
  if (!cam) { flash('#s-msg-auto', t('Nicio camera cu difuzor'), true); return; }
  const v = $('#s-auto-sound').value;
  await post(`/api/cam/${cam.id}/siren`, {
    sound: +String(v).replace('c', ''), volume: +$('#s-auto-vol').value,
    times: +$('#s-auto-times').value, custom: String(v).startsWith('c'),
  });
  flash('#s-msg-auto', t('Redat pe {cam}', { cam: cam.name }));
};
$('#s-tts-play').onclick = async () => {
  const text = $('#s-tts-text').value.trim();
  if (!text) { flash('#s-msg-tts', t('Scrie textul întâi'), true); return; }
  flash('#s-msg-tts', t('Generez...'));
  const r = await fetch('/api/tts', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, speed: +$('#s-tts-speed').value }),
  });
  if (!r.ok) { flash('#s-msg-tts', t('Eroare: {e}', { e: (await r.json()).error }), true); return; }
  const a = $('#s-tts-audio');
  a.src = URL.createObjectURL(await r.blob());
  a.classList.remove('hidden');
  a.play();
  flash('#s-msg-tts', t('Gata — ascultă și, dacă îți place, descarcă'));
};
$('#s-tts-dl').onclick = async () => {
  const text = $('#s-tts-text').value.trim();
  if (!text) { flash('#s-msg-tts', t('Scrie textul întâi'), true); return; }
  const r = await fetch('/api/tts', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, speed: +$('#s-tts-speed').value, download: true }),
  });
  if (!r.ok) { flash('#s-msg-tts', t('Eroare la generare'), true); return; }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url; a.download = 'anunt.wav'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  flash('#s-msg-tts', t('Descărcat — încarcă-l în camera, în Configurare → Audio'));
};

/* starea serverului, copie de siguranta, ora camerelor, jurnal */
let sysTimer = null;
let noiseTimer = null, noiseProfAt = 0;

/* Profilul pe ora din zi: cat de tare e locul, in mod obisnuit, la fiecare ora.
   Coloana arata intervalul in care sta nivelul 80% din timp (fond..zgomot care se
   remarca), linia e media energetica, iar mustatile subtiri sunt extremele. */
async function loadNoiseProfile() {
  const box = $('#noise-prof');
  if (!box) return;
  try {
    const d = await (await fetch('/api/noise/stats?days=7')).json();
    if (!d.camere.length) { box.innerHTML = ''; return; }
    box.innerHTML = html`${d.camere.map((c) => {
      const p = c.propunere;
      if (!p) return html`<div class="prof-cam"><div class="prof-head"><b>${c.name}</b>
        <span class="prof-cov">${t('se colecteaza…')}</span></div>
        <p class="hint">${t('Inca nu avem nicio ora completa. Primele cifre apar dupa o ora.')}</p></div>`;
      const ore = c.profil.filter((h) => h.n);
      const lo = Math.floor(Math.min(...ore.map((h) => h.min)) / 5) * 5;
      const hi = Math.ceil(Math.max(...ore.map((h) => h.max)) / 5) * 5;
      const y = (v) => ((v - lo) / (hi - lo)) * 100;
      const cols = c.profil.map((h) => {
        if (!h.n) return html`<div class="prof-col empty" title="${t('ora {ora}: fara date', { ora: h.ora })}"></div>`;
        return html`<div class="prof-col" title="${t('ora {ora}:00 — fond {l90} dB, mediu {leq} dB, se remarca {l10} dB, extreme {min}…{max} dB ({zile} zile)',
          { ora: h.ora, l90: h.l90, leq: h.leq, l10: h.l10, min: h.min, max: h.max, zile: h.zile })}">
          <b style="bottom:${y(h.min)}%;height:${y(h.max) - y(h.min)}%"></b>
          <i style="bottom:${y(h.l90)}%;height:${Math.max(2, y(h.l10) - y(h.l90))}%"></i>
          <u style="bottom:${y(h.leq)}%"></u></div>`;
      });
      const zn = p.dif_zi_noapte === null ? ''
        : ' ' + (p.dif_zi_noapte >= 0
          ? t('Ziua e cu {n} dB mai zgomotoasa decat noaptea.', { n: Math.abs(p.dif_zi_noapte) })
          : t('Ziua e cu {n} dB mai linistita decat noaptea.', { n: Math.abs(p.dif_zi_noapte) }));
      return html`<div class="prof-cam">
        <div class="prof-head"><b>${c.name}</b>
          <span class="prof-cov${p.gata ? ' ok' : ''}">${t('{n} din 7 zile', { n: p.zile })}</span></div>
        <div class="prof-chart">
          <div class="prof-y"><span>${hi}</span><span>${lo}</span></div>
          <div class="prof-cols">${cols}</div></div>
        <div class="prof-x">${c.profil.map((h) => html`<span>${h.ora % 3 ? '' : h.ora}</span>`)}</div>
        <div class="prof-leg"><i class="lg-band"></i> ${t('nivelul obisnuit')}
          <i class="lg-avg"></i> ${t('mediu')} <i class="lg-ext"></i> ${t('extreme')}</div>
        <p class="hint">${t('Fondul obisnuit e {fond} dBFS, iar cel mai tare zgomot masurat a fost {varf} dBFS.', { fond: p.fond, varf: p.varf_masurat })}${zn}
          ${p.gata ? '' : ' ' + t('Propunerea se schimba pe masura ce se aduna date.')}</p>
        <button class="btn sm" data-thr="${p.prag_db}" data-spike="${p.spike_db}">
          ${t('Pune pragul la {prag} dB si varful la {varf} dB', { prag: p.prag_db, varf: p.spike_db })}</button>
      </div>`;
    })}`;
    if (d.camere.some((c) => c.propunere)) {
      box.insertAdjacentHTML('beforeend',
        html`<p class="hint"><a href="/api/noise/export.csv?days=30" download>${t('Descarca masuratorile (CSV)')}</a> — ${t('o linie pe ora, daca vrei sa le analizezi separat.')}</p>`);
    }
    box.querySelectorAll('button[data-thr]').forEach((b) => {
      b.onclick = () => {
        $('#noise-thr').value = b.dataset.thr;
        $('#noise-spike').value = b.dataset.spike;
        checkDirty();
        toast(t('Praguri completate — apasă Salvează ca să le aplici'));
      };
    });
  } catch {}
}

async function loadNoise() {
  try {
    const d = await (await fetch('/api/noise?minutes=15')).json();
    $('#noise-on').checked = d.reguli.noise_alarm_enabled;
    $('#inc-gap').value = d.reguli.incident_gap_sec ?? 300;
    $('#noise-thr').value = d.reguli.noise_threshold_db;
    $('#noise-sustain').value = d.reguli.noise_sustain_sec;
    $('#noise-spike').value = d.reguli.noise_spike_db;
    if (!d.camere.length) {
      $('#noise-live').innerHTML = html`<p class="hint">${t('Nicio camera cu microfon conectat. Camerele cu intrare de linie au nevoie de un microfon extern.')}</p>`;
      return;
    }
    const thr = d.reguli.noise_threshold_db;
    $('#noise-live').innerHTML = html`${d.camere.map((c) => {
      // Scara se potriveste pe ce s-a masurat, nu e fixa: o camera linistita sta pe la
      // -65 dBFS, iar pe o scara fixa de la -60 in sus graficul ei ar fi mereu gol.
      const s = (c.serie || []).slice(-90);
      const vals = s.map((p) => p.db).concat([thr]);
      const lo = Math.floor((Math.min(...vals) - 3) / 5) * 5;
      const hi = Math.ceil((Math.max(...vals) + 3) / 5) * 5;
      const pos = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
      const bars = s.map((p) => {
        const cls = p.db >= thr ? 'over' : '';
        return html`<i class="${cls}" style="height:${Math.max(2, pos(p.db))}%"></i>`;
      });
      return html`<div class="noise-cam">
        <div class="noise-head"><b>${c.name}</b>
          <span class="noise-now${c.db >= thr ? ' loud' : ''}">${c.db ?? '—'} dBFS</span>
          <small>${t('fond {fond} · prag {prag}', { fond: c.baseline ?? '—', prag: thr })}</small></div>
        <div class="noise-wrap">
          <div class="prof-y"><span>${hi}</span><span>${lo}</span></div>
          <div class="noise-graph" title="${t('ultimele minute')}">${bars}
            <u style="bottom:${pos(thr)}%"></u></div></div></div>`;
    })}`;
  } catch {}
}
$('#noise-on').onchange = checkDirty;
$('#inc-gap').oninput = checkDirty;
$('#wh-urls').oninput = checkDirty;
$('#wh-secret').oninput = checkDirty;

/* ---------- automatizari: webhook-uri + jeton API ---------- */
async function loadHooks() {
  try {
    const [w, s] = await Promise.all([
      (await fetch('/api/webhooks')).json(),
      (await fetch('/api/settings')).json(),
    ]);
    $('#wh-urls').value = (s.webhook_urls || []).join('\n');
    $('#wh-secret').value = s.webhook_secret || '';
    $('#wh-token').value = s.api_token || '';
    const alese = new Set(s.webhook_events || Object.keys(w.evenimente));
    $('#wh-events').innerHTML = html`<b>${t('Ce evenimente trimitem')}</b>${
      Object.entries(w.evenimente).map(([k, txt]) =>
        html`<label class="inline"><input type="checkbox" data-ev="${k}"${alese.has(k) ? ' checked' : ''}>
          <span><code>${k}</code> — ${txt}</span></label>`)}`;
    for (const c of $('#wh-events').querySelectorAll('input')) c.onchange = checkDirty;
    $('#wh-log').innerHTML = lista(w.jurnal, (r) => html`<div class="wh-row ${r.ok ? 'ok' : 'rau'}">
          <span>${r.t.slice(11)}</span><b>${r.ok ? t('trimis') : t('esuat')}</b>
          <i>${r.detaliu}</i><u>${r.url}</u></div>`, html`<p class="hint">${t('Nicio livrare inca.')}</p>`);
  } catch {}
}
$('#wh-test').onclick = async () => {
  const el = $('#wh-test-res');
  el.textContent = t('Trimit...');
  try {
    const r = await (await fetch('/api/webhooks/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    if (r.eroare) { el.textContent = t('Nu a mers: {e}', { e: r.eroare }); return; }
    const rez = Object.entries(r.rezultat);
    el.textContent = rez.map(([u, ok]) => `${u}: ${ok ? t('a raspuns') : t('nu a raspuns')}`).join(' · ');
    loadHooks();
  } catch (e) { el.textContent = t('Nu a mers: {e}', { e: e.message }); }
};
$('#wh-token-new').onclick = () => {
  if (!confirm(t('Fluxurile care folosesc jetonul actual se vor opri. Generezi altul?'))) return;
  // jeton generat in browser: nu are nevoie sa treaca prin server ca sa fie bun
  const b = new Uint8Array(24);
  crypto.getRandomValues(b);
  $('#wh-token').value = btoa(String.fromCharCode(...b)).replace(/[+/=]/g, '').slice(0, 32);
  checkDirty();
};

/* ---------- continuitatea inregistrarii ---------- */
const nume2 = (d, cid) => (d.acum.find((c) => c.id === +cid) || {}).name || t('cam {id}', { id: cid });

async function loadContinuity() {
  try {
    const d = await (await fetch('/api/continuity?days=14')).json();

    // 1. cine scrie chiar acum
    const mute = d.acum.filter((c) => !c.scrie);
    $('#cont-live').innerHTML = html`<div class="cont-sum ${mute.length ? 'rau' : 'bun'}">${
        mute.length ? t('{n} camere nu scriu pe disc', { n: mute.length }) : t('Toate cele {n} camere scriu pe disc', { n: d.acum.length })
      }</div><div class="cont-cams">${d.acum.map((c) => {
        const v = c.varsta === null ? '—' : c.varsta < 90 ? `${c.varsta}s` : `${Math.round(c.varsta / 60)} min`;
        return html`<div class="cont-cam${c.scrie ? '' : ' mut'}" title="${t('{cam} — ultima scriere acum {v}', { cam: c.name, v })}${
          c.reporniri ? ', ' + t('repornit de {n} ori', { n: c.reporniri }) : ''}">
          <b>${c.name}</b><span>${v}</span></div>`;
      })}</div>${Object.keys(d.goluri_azi).length ? html`<div class="cont-goluri"><b>${t('Goluri azi:')}</b> ${
        Object.entries(d.goluri_azi).map(([cid, gs]) => gs.map((g) =>
          html`<span>${nume2(d, cid)} ${g.de_la}–${g.pana_la} (${g.minute} min)</span>`))}</div>` : ''}`;

    // 2. acoperirea pe zile: o linie per camera, o celula per zi
    const zile = [...new Set(Object.values(d.jurnal).flat().map((r) => r.zi))].sort();
    const nume = Object.fromEntries(d.acum.map((c) => [c.id, c.name]));
    $('#cont-cov').innerHTML = !zile.length ? html`<p class="hint">${t('Jurnalul se completeaza pe parcurs.')}</p>`
      : html`<div class="cov-grid" style="grid-template-columns:150px repeat(${zile.length}, 1fr)">
          <span></span>${zile.map((z) => html`<i class="cov-zi">${z.slice(8)}</i>`)}
          ${Object.entries(d.jurnal).map(([cid, rows]) => {
            const byDay = Object.fromEntries(rows.map((r) => [r.zi, r]));
            return html`<b class="cov-nume">${nume[cid] || t('cam {id}', { id: cid })}</b>${zile.map((z) => {
              const r = byDay[z];
              if (!r) return html`<span class="cov-cel gol" title="${t('fara date')}"></span>`;
              if (r.mod !== 'continuous') {
                return html`<span class="cov-cel misc" title="${t('{zi}: mod la miscare, {p}% pastrat', { zi: z, p: r.procent })}"></span>`;
              }
              const cls = r.procent >= 99 ? 'ok' : r.procent >= 90 ? 'part' : 'rau';
              return html`<span class="cov-cel ${cls}" title="${t('{zi}: {p}% acoperit', { zi: z, p: r.procent })}${
                r.goluri ? ', ' + t('{n} goluri, cel mai mare {max} min', { n: r.goluri, max: Math.round(r.gol_max / 60) }) : ''}"></span>`;
            })}`;
          })}
        </div>
        <div class="cov-leg"><span><i class="cov-cel ok"></i> ${t('zi întreagă')}</span>
          <span><i class="cov-cel part"></i> ${t('lipsesc sub 10%')}</span>
          <span><i class="cov-cel rau"></i> ${t('lipsește mai mult')}</span>
          <span><i class="cov-cel misc"></i> ${t('la mișcare')}</span></div>`;

    // 3. incape retentia?
    const s = d.spatiu, p = s.propunere;
    const tb = (g) => (g >= 1024 ? `${(g / 1024).toFixed(1)} TB` : `${Math.round(g)} GB`);
    const proc = Math.min(100, s.necesar_gb ? s.disponibil_gb / s.necesar_gb * 100 : 100);
    $('#cont-space').innerHTML = html`
      <div class="cont-sum ${s.incape ? 'bun' : 'rau'}">${s.incape
        ? t('Retentia configurata incape: cere {necesar} din {disponibil} disponibili.', { necesar: tb(s.necesar_gb), disponibil: tb(s.disponibil_gb) })
        : t('Retentia configurata nu incape. Cere {necesar}, dar avem loc de {disponibil}.', { necesar: tb(s.necesar_gb), disponibil: tb(s.disponibil_gb) })}</div>
      <div class="sp-bar"><u style="width:${proc}%"></u></div>
      <p class="hint">${raw(t('Scriem {gbzi}, iar {p}% din segmente conțin evenimente. Pastrand tot, discul ajunge pentru {zile}.', {
        gbzi: html`<b>${t('{gb} GB pe zi', { gb: s.gb_pe_zi })}</b>`, p: esc(s.cu_evenimente),
        zile: html`<b>${t('{n} zile', { n: s.zile_posibile })}</b>` }))}</p>
      ${p && !s.incape ? html`<div class="sp-fix">
        <p>${raw(t('Propunere care chiar se respecta: {zile} pentru tot.', { zile: html`<b>${t('{n} zile', { n: p.zile })}</b>` }))}</p>
        <p class="hint">${p.motiv}</p>
        <button class="btn sm" id="sp-apply" data-z="${p.zile}" data-e="${p.zile_evenimente}">
          ${t('Pune retentia la {n} zile', { n: p.zile })}</button></div>` : ''}
      <details class="sp-det"><summary>${t('Cât scrie fiecare camera')}</summary>
        <table class="sp-tab"><thead><tr><th>${t('Camera')}</th><th>${t('GB/zi')}</th>
          <th>${t('Cu evenimente')}</th><th>${t('Cere')}</th></tr></thead><tbody>
          ${s.camere.map((c) => html`<tr><td>${c.name}</td><td>${c.gb_pe_zi}</td>
            <td>${c.cu_evenimente}%</td><td>${tb(c.necesar_gb)}</td></tr>`)}
        </tbody></table></details>`;

    const ap = $('#sp-apply');
    if (ap) {
      ap.onclick = () => {
        $('#ret-days').value = ap.dataset.z;
        $('#ret-ev').value = ap.dataset.e;
        checkDirty();
        toast(t('Retenția completată — apasă Salvează ca să se aplice'));
      };
    }
  } catch {}
}

async function loadRetention() {
  try {
    const d = await (await fetch('/api/retention')).json();
    $('#ret-days').value = d.setari.retention_days;
    $('#ret-ev').value = d.setari.retention_event_days;
    $('#ret-snap').value = d.setari.retention_snapshots_days;
    $('#ret-free').value = d.setari.retention_min_free_gb;
    $('#ret-jurnal').value = d.setari.events_keep_days;
    $('#arh-dir').value = d.setari.arhiva_dir || '';
    $('#arh-zile').value = d.setari.arhiva_zile;
    $('#arh-free').value = d.setari.arhiva_min_free_gb;
    const a = d.arhiva || {};
    $('#arh-report').innerHTML = !a.activ ? esc(t('Oprit: segmentele cu evenimente se șterg după retenția lor.'))
      : !a.exista ? raw(t('{titlu} ({dir}) — nimic nu se mută până nu apare.', { titlu: html`<b>${t('Directorul nu există')}</b>`, dir: esc(a.dir) }))
      : !a.scriibil ? raw(t('{titlu} ({dir}) — verifică montarea/drepturile.', { titlu: html`<b>${t('Directorul nu se poate scrie')}</b>`, dir: esc(a.dir) }))
      : raw(t('În arhiva: {gb}, {n} segmente; liber acolo: {liber}.', { gb: html`<b>${a.gb} GB</b>`, n: esc(a.segmente), liber: html`<b>${a.liber_gb} GB</b>` }));
    const zile = d.camere.length ? Math.max(...d.camere.map((c) => c.zile)) : 0;
    $('#ret-report').innerHTML =
      raw(t('Acum: {gb} de înregistrări, cel mai vechi material are {zile}.', { gb: html`<b>${d.total_gb} GB</b>`, zile: html`<b>${t('{n} zile', { n: zile })}</b>` })) +
      ' ' + esc(zile < d.setari.retention_days
        ? t('Încă nu s-a atins limita de vârstă — spațiul decide.')
        : t('Se șterge după vârstă, conform setărilor.'));
  } catch {}
}


async function loadSystem() {
  try {
    const s = await (await fetch('/api/system')).json();
    const pct = (a, b) => Math.min(100, Math.round(a / b * 100));
    const bar = (label, val, txt, warn) =>
      html`<div class="sys-item"><span>${label}</span>
      <div class="sys-bar"><i style="width:${val}%" class="${warn ? 'warn' : ''}"></i></div>
      <small>${txt}</small></div>`;
    const diskUsed = pct(s.disk_total_gb - s.disk_free_gb, s.disk_total_gb);
    $('#sys-stats').innerHTML = html`${
      bar(t('Procesor'), s.cpu, t('{cpu}% din {cores} nuclee · incarcare {load}', { cpu: s.cpu, cores: s.cores, load: s.load[0] }), s.cpu > 85)}${
      bar(t('Memorie'), pct(s.ram_used_gb, s.ram_total_gb), `${s.ram_used_gb} / ${s.ram_total_gb} GB`, false)}${
      bar(t('Disc'), diskUsed, t('{free} GB liberi · inregistrari {rec} GB', { free: s.disk_free_gb, rec: s.rec_gb }), diskUsed > 90)}
      <div class="sys-item"><span>${t('Rețea')}</span><div class="sys-net">
      ${raw(t('{in} Mbps intră {out} Mbps ies', { in: html`<b>${s.net.in_mbps}</b>`, out: html`<b>${s.net.out_mbps}</b>` }))}</div>
      <small>${t('pornit de {n} zile', { n: Math.round(s.uptime_h / 24) })}</small></div>
      <div class="sys-item"><span>${t('Versiune')}</span><small>OpenVigil ${ME.versiune || 'dev'} · <a href="https://github.com/sm26449/openvigil/releases" target="_blank" rel="noopener">${t('release-uri')}</a></small></div>${notifStatusHtml(s.notificari)}${nvrStatusHtml(s.nvr)}${aiStatusHtml(s.ai)}`;
  } catch (e) { console.error('[stare] starea sistemului nu s-a putut afișa:', e); }
}

// pe unde pot pleca alertele; rosu cand nu exista niciun canal (altfel totul ramane in jurnal)
function notifStatusHtml(n) {
  if (!n) return '';
  const el = (ok, txt) => html`<b class="${ok ? 'ok' : 'off'}">${ok ? '✓' : '✗'} ${txt}</b>`;
  return html`<div class="sys-item ${n.canal ? '' : 'sys-alarm'}"><span>${t('Notificări')}</span>
    <div class="sys-net">${el(n.telegram, 'Telegram')} ${el(n.email, t('email'))} ${el(n.push > 0, t('push ({n} dispozitive)', { n: n.push }))}</div>
    <small>${n.canal ? t('alertele pot pleca') : t('NICIUN canal: alertele rămân doar în jurnal — Setări → Notificări')}</small></div>`;
}

// NVR-ul Hikvision (istoricul vechi + exportul fara inregistrari locale trec prin el)
function nvrStatusHtml(n) {
  if (!n || !n.ip || n.retras) return '';   // retras din Setări → Sistem: nu mai e în peisaj
  const necunoscut = n.online === null || n.online === undefined;
  return html`<div class="sys-item ${n.online === false ? 'sys-alarm' : ''}"><span>NVR Hikvision</span>
    <div class="sys-net"><b class="${n.online ? 'ok' : necunoscut ? '' : 'off'}">${n.online ? t('✓ online') : necunoscut ? t('… se verifică') : t('✗ OFFLINE')}</b> ${n.ip}</div>
    <small>${n.online === false ? t('istoricul de pe NVR și exporturile fără înregistrări locale nu merg') : t('istoric vechi și rezervă pentru export')}</small></div>`;
}

// --- Test viteza client<->server (bandwidth), pentru alegerea rezolutiei ----------------
// Descarca un bloc de la server si il cronometreaza. Warm-up intai (TCP slow-start face
// prima descarcare artificial lenta), apoi masuratoarea reala.
function bwTier(mbps) {
  if (mbps < 3) return { nume: 'Economic', height: 'substream', text: 'substream 640×480' };
  if (mbps < 8) return { nume: 'Bun', height: 720, text: 'HD 720p' };
  if (mbps < 15) return { nume: 'Foarte bun', height: 1080, text: 'HD 1080p' };
  return { nume: 'Excelent (LAN)', height: 0, text: 'nativ / 4K' };
}
async function measureBandwidth() {
  const dl = async (mb) => {
    const t0 = performance.now();
    const buf = await (await fetch(`/api/speedtest?mb=${mb}&_=${Date.now()}`,
                                   { cache: 'no-store' })).arrayBuffer();
    return { bytes: buf.byteLength, sec: (performance.now() - t0) / 1000 };
  };
  await dl(1);                    // warm-up (ignorat): incalzeste conexiunea
  const r = await dl(6);         // masuratoare reala
  const mbps = (r.bytes * 8) / r.sec / 1e6;
  const rez = { mbps: Math.round(mbps * 10) / 10, treapta: bwTier(mbps) };
  window.__bw = rez;
  try { localStorage.setItem('bw_last', JSON.stringify({ ...rez, t: Date.now() })); } catch {}
  return rez;
}
window.__bw = null;              // ultima masuratoare (Auto o foloseste la alegerea rezolutiei)
// masuratoarea din vizita anterioara e reprezentativa <1h (aceeasi retea, de regula):
// Auto nimereste treapta buna de la PRIMA redare, nu abia dupa un test manual
try {
  const c = JSON.parse(localStorage.getItem('bw_last'));
  if (c && Date.now() - c.t < 3600e3) window.__bw = { mbps: c.mbps, treapta: c.treapta };
} catch {}

// --- treapta de rezolutie HD/playback (pas 3 din rezolutia adaptiva) --------------------
// 'auto' = dupa bandwidth-ul masurat; 'eco' = mereu 720p; 'max' = mereu nativ.
// Alegerea e per-DISPOZITIV (localStorage): laptopul de pe 4G vrea Economic,
// desktopul de pe LAN nativ — nu e o setare de server.
function qualityMode() {
  try { return localStorage.getItem('hd_quality') || 'auto'; } catch { return 'auto'; }
}
function setQualityMode(m) {
  try { localStorage.setItem('hd_quality', m); } catch {}
  for (const id of ['hd-quality', 'pb-quality']) {
    const el = $('#' + id);
    if (el) el.value = m;
  }
}
let bwProbe = null;              // o singura masurare in fundal, nu una per redare
function hdHeightParam() {
  const m = qualityMode();
  if (m === 'eco') return '&height=720';
  if (m === 'max') return '&height=0';
  const t = window.__bw && window.__bw.treapta;
  if (!t) {
    // Auto fara masuratoare: pornim UNA in fundal si redam nativ intre timp (ca inainte).
    // Redarea curenta nu asteapta dupa test; urmatoarea nimereste treapta.
    if (!bwProbe) bwProbe = measureBandwidth().catch(() => { bwProbe = null; });
    return '';
  }
  return `&height=${typeof t.height === 'number' ? t.height : 720}`;  // 'substream' -> minim 720
}
// Bufferul de jitter creste cand reteaua e stramta: pe LAN stam lipiti de live (0s),
// pe legaturi inguste un buffer mai mare absoarbe variatiile -> imagine neteda.
function hdBufferTarget() {
  const b = window.__bw;
  if (!b || qualityMode() === 'max') return 0;
  if (b.mbps < 8) return 1.5;
  if (b.mbps < 15) return 0.75;
  return 0;
}
$('#speed-btn') && ($('#speed-btn').onclick = async () => {
  const btn = $('#speed-btn'), res = $('#speed-result');
  btn.disabled = true; res.textContent = t('Masor...');
  try {
    const r = await measureBandwidth();
    window.__bw = r;
    res.innerHTML = html`<b>${r.mbps} Mbps</b> · ${t('recomandat: {text}', { text: t(r.treapta.text) })} <span class="ai-on">${t(r.treapta.nume)}</span>`;
  } catch { res.textContent = t('Test eșuat — reîncearcă'); }
  btn.disabled = false;
});

// selectoarele de calitate (HD + Inregistrari) — aceeasi alegere, sincronizate
for (const id of ['hd-quality', 'pb-quality']) {
  const el = $('#' + id);
  if (!el) continue;
  el.value = qualityMode();
  el.onchange = () => {
    setQualityMode(el.value);
    // in HD live noua treapta se aplica pe loc; in playback, la urmatoarea redare
    if (id === 'hd-quality' && !$('#hd-overlay').classList.contains('hidden')
        && $('#hd-live').classList.contains('on')) hdGoLive();
  };
}

// starea subsistemelor AI si a puntii MQTT — dintr-o privire, in pagina Stare
function aiStatusHtml(ai) {
  if (!ai) return '';
  const st = (cfg, extra) => !cfg.pornit ? html`<i class="ai-off">${t('oprit')}</i>`
    : (cfg.model === false ? html`<i class="ai-warm">${t('se încarcă')}</i>`
       : html`<b class="ai-on">${t('activ')}</b>${extra || ''}`);
  const bf = ai.semantic.backfill && ai.semantic.backfill.running
    ? ' · ' + t('indexez {done}/{total}', { done: ai.semantic.backfill.done, total: ai.semantic.backfill.total }) : '';
  const mq = !ai.mqtt.pornit ? html`<i class="ai-off">${t('oprit')}</i>`
    : ai.mqtt.connected ? html`<b class="ai-on">${t('conectat')}</b> · ${ai.mqtt.broker}`
    : html`<i class="ai-warm">${t('neconectat (broker/user/parola?)')}</i>`;
  const mg = ai.motion_gate
    ? (ai.motion_gate.pornit
        ? html`<b class="ai-on">${t('pornit')}</b> · ${t('{p}% scene statice sarite', { p: Math.round(ai.motion_gate.sarite * 100) })}`
        : html`<i class="ai-off">${t('oprit')}</i>`)
    : '';
  // metrici per model (faza 6): cine munceste si cat de repede — doar cele active recent
  const NUME_M = { detect: 'Detecție', tracking: 'Urmărire', tiling: 'Regiuni',
    face: 'Face ID', lpr: 'LPR', descriere: 'Descriere', semantic: 'Index',
    confirmare: 'Confirmare' };
  let met = '';
  if (ai.metrici) {
    const parts = Object.entries(ai.metrici)
      .filter(([k, v]) => NUME_M[k] && v.pe_min > 0)
      .map(([k, v]) => `${t(NUME_M[k])} ${v.pe_min}/min · ${v.ms_mediu}ms`);
    if (ai.metrici.semantic_coada > 20) parts.push(t('coada index: {n}', { n: ai.metrici.semantic_coada }));
    if (parts.length) {
      met = html`<span class="ai-metrici" title="${t('rata pe minut · latență medie per inferenta')}">
        ${raw(parts.map(esc).join(' &nbsp;|&nbsp; '))}</span>`;
    }
  }
  return html`<div class="sys-item ai-row"><span>AI</span><div class="ai-cells">
    <span>Face ID: ${st(ai.face)}</span>
    <span>LPR: ${st(ai.lpr)}</span>
    <span>${t('Căutare:')} ${st(ai.semantic, bf)}</span>
    <span>MQTT: ${mq}</span>
    <span>${t('Reguli active:')} <b>${ai.reguli}</b></span>
    ${mg ? html`<span>Motion-gate: ${mg}</span>` : ''}
    ${met}</div></div>`;
}
/* ---------- Etapa 6: copia nocturna criptata ---------- */
async function loadBackupNocturn() {
  try {
    const d = await (await fetch('/api/backup/stare')).json();
    const st = d.setari || {};
    $('#bn-ora').value = st.backup_ora || '03:30';
    $('#bn-dir').value = st.backup_dir || '';
    $('#bn-remote').value = st.backup_remote || '';
    $('#bn-keep').value = st.backup_pastreaza || 14;
    $('#bn-fete').checked = !!st.backup_fete;
    const u = (d.stare || {}).ultima;
    const cand = (ts) => (ts ? new Date(ts * 1000).toLocaleString(LIMBA, { dateStyle: 'short', timeStyle: 'short' }) : '-');
    $('#bn-stare').innerHTML = !d.stare || !d.stare.la ? esc(t('Nicio copie încă.'))
      : d.stare.ok ? raw(t('Ultima copie: {fisier} ({mb} MB, {s}s), la {cand}; în afara: {extern}. Cheie: {cheie}. Director: {dir}', {
        fisier: html`<b>${u.fisier}</b>`, mb: (u.octeti / 2 ** 20).toFixed(1), s: esc(u.durata_s), cand: esc(cand(d.stare.la)),
        extern: esc(u.extern ? (u.extern.ok === null ? t('neconfigurat') : u.extern.ok ? t('trimisa ✓') : t('ESUAT: {e}', { e: u.extern.mesaj })) : '-'),
        cheie: esc(d.cheie_amprenta), dir: esc(d.director) }))
      : raw(t('{titlu} la {cand}: {e}', { titlu: html`<b>${t('Ultima copie a EȘUAT')}</b>`, cand: esc(cand(d.stare.la)), e: esc(d.stare.eroare) }));
    $('#bn-lista').innerHTML = lista((d.copii || []).slice(0, 8), (b) => html`<div class="push-row"><b>${b.creat}</b><span class="hint">${t('{mb} MB · {n} elemente', { mb: (b.octeti / 2 ** 20).toFixed(1), n: (b.continut || []).length })}</span></div>`, '');
  } catch {}
}
$('#bn-acum').onclick = async () => {
  const j = await runJob('/api/backup/acum', { method: 'POST' }, { download: false });
  if (j && j.stare === 'gata') { flash('#bn-msg', t('Copia e gata ✓')); loadBackupNocturn(); }
};
$('#bn-cheie').onclick = async () => {
  const el = $('#bn-cheie-val');
  if (!el.classList.contains('hidden')) { el.classList.add('hidden'); el.textContent = ''; return; }
  const d = await (await fetch('/api/backup/cheie')).json();
  if (d.error) { alert(t('Eroare: {e}', { e: d.error })); return; }
  el.textContent = `${d.cheie}

${d.nota}`;
  el.classList.remove('hidden');
};
$('#bk-full').onclick = () => { window.location = '/api/backup'; };
$('#bk-safe').onclick = () => { window.location = '/api/backup?fara_parole=1'; };
$('#bk-file').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (!confirm(t('Refaci configurația din acest fișier?\nConfigurația curentă se păstrează ca fișier de rezerva.'))) {
    e.target.value = ''; return;
  }
  try {
    const d = await post('/api/restore', JSON.parse(await f.text()));
    if (d && !d.error) {
      flash('#bk-msg', t('Refacut: {sectiuni}. Reîncarc pagina...', { sectiuni: d.sectiuni.join(', ') }));
      setTimeout(() => location.reload(), 2000);
    }
  } catch (err) { flash('#bk-msg', t('Fișier nevalid: {e}', { e: err }), true); }
  e.target.value = '';
};
async function loadTimeStatus() {
  try {
    const d = await (await fetch('/api/time')).json();
    const bad = d.cameras.filter((c) => c.error || c.drift === null || Math.abs(c.drift) > 60);
    $('#ntp-host').value = d.ntp || '';
    $('#time-status').innerHTML = bad.length
      ? html`<span class="warn">${t('{n} camere cu ora greșită: {lista}', { n: bad.length, lista: bad.map((c) => c.name).join(', ') })}</span>`
      : html`${t('Toate cele {n} camere au ora corectă (abatere sub un minut).', { n: d.cameras.length })}`;
  } catch {}
}
$('#time-sync').onclick = async () => {
  const b = $('#time-sync');
  b.disabled = true;
  flash('#time-msg', t('Sincronizez (durează ~30s)...'));
  const d = await post('/api/time/sync', { ntp: $('#ntp-host').value.trim() });
  b.disabled = false;
  if (d && !d.error) flash('#time-msg', t('Sincronizate {ok}/{total}', { ok: d.reusite, total: d.total }));
  loadTimeStatus();
};
async function loadLog() {
  try {
    const rows = await (await fetch(`/api/changes?q=${encodeURIComponent($('#log-q').value)}`)).json();
    $('#log-list').innerHTML = lista(rows, (r) =>
      html`<div class="log-row"><span class="log-t">${r.t.replace('T', ' ')}</span>
      <span class="log-a">${r.action}</span>
      <span class="log-g">${r.target || ''}</span>
      <span class="log-d">${r.detail ? JSON.stringify(r.detail) : ''}</span>
      <span class="log-w" title="${r.ip || ''}">${r.who || ''}</span></div>`,
      html`<p class="hint">${t('Nicio modificare inregistrata inca.')}</p>`);
  } catch {}
}
$('#log-q').oninput = loadLog;

async function loadStorage() {
  try {
    const s = await (await fetch('/api/storage')).json();
    const re = s.reencode || {};
    const cb = $('#s-reencode');
    cb.disabled = !re.available;
    $('#s-storage').innerHTML =
      html`${raw(t('Înregistrări pe disc: {used} · spațiu liber: {free}', { used: html`<b>${s.used_gb} GB</b>`, free: html`<b>${s.free_gb} GB</b>` }))}${
      re.available
        ? (re.running ? html`<br>${t('Re-encodare in curs: {n} fisiere, {mb} MB economisiti', { n: re.done, mb: re.saved_mb })}`
                      : (re.done ? html`<br>${t('Re-encodate: {n} fisiere, {mb} MB economisiti', { n: re.done, mb: re.saved_mb })}` : ''))
        : html`<br><span class="warn">${t('Re-encodarea nu e disponibila: placa video actuala (Quadro M6000) nu are encoder HEVC/AV1. Cu un GPU modern (ex. RTX 2000 Ada) optiunea se activeaza singura.')}</span>`}`;
  } catch {}
}
function flash(id, txt, err) {
  const el = $(id);
  el.textContent = txt;
  el.className = 's-msg' + (err ? ' err' : ' ok');
  setTimeout(() => { el.textContent = ''; }, 6000);
}
/* Navigare pe sectiuni: o pagina de 14 carduri nu se poate parcurge; una pe sectiune, da. */
$('#s-digest-now') && ($('#s-digest-now').onclick = async () => {
  flash('#s-msg-digest', t('Trimit…'));
  const d = await post('/api/digest/trimite', {});
  flash('#s-msg-digest', d && d.ok ? t('Rezumatul a plecat ✓') : (d && d.error) || t('Eroare'), !!(d && d.error));
});

for (const b of document.querySelectorAll('.snav-item')) {
  b.onclick = () => {
    for (const x of document.querySelectorAll('.snav-item')) x.classList.toggle('on', x === b);
    for (const p of document.querySelectorAll('.set-pane'))
      p.classList.toggle('hidden', p.id !== 'pane-' + b.dataset.pane);
    localStorage.setItem('setPane', b.dataset.pane);
    window.scrollTo({ top: 0 });
  };
}

/* Un singur model de salvare: modificarile se aduna, bara de jos apare doar cand exista
   ceva nesalvat. Butoanele care FAC ceva (test, sincronizare, redare) raman separate. */
let savedSnapshot = null;
function snapshotSettings() { return JSON.stringify(settingsBody()); }
function checkDirty() {
  if (savedSnapshot === null) return;
  const now = snapshotSettings();
  const dirty = now !== savedSnapshot;
  $('#set-savebar').classList.toggle('hidden', !dirty);
  if (dirty) {
    const a = JSON.parse(savedSnapshot), b = JSON.parse(now);
    const n = Object.keys(b).filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k])).length;
    $('#set-dirty').textContent = n === 1 ? t('O modificare nesalvată') : t('{n} modificări nesalvate', { n });
  }
}
document.addEventListener('input', (e) => {
  if (e.target.closest('#set')) checkDirty();
});
document.addEventListener('change', (e) => {
  if (e.target.closest('#set')) checkDirty();
});
$('#s-save').onclick = async () => {
  const d = await post('/api/settings', settingsBody());
  if (d && !d.error) {
    savedSnapshot = snapshotSettings();
    checkDirty();
    flash('#s-msg-save', t('Salvat'));
  } else flash('#s-msg-save', t('Eroare la salvare'), true);
};
$('#s-revert').onclick = () => { loadSettings(); };
for (const btn of document.querySelectorAll('.s-test')) {
  btn.onclick = async () => {
    const ch = btn.dataset.ch;
    btn.disabled = true;
    flash('#s-msg-' + ch, t('Trimit...'));
    try {
      const r = await fetch(`/api/settings/test/${ch}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settingsBody()),
      });
      const d = await r.json();
      flash('#s-msg-' + ch,
        d.ok ? (ch === 'telegram' ? t('Trimis ✓ verifică Telegram') : t('Trimis ✓ verifică inboxul'))
             : t('Eroare: {e}', { e: d.error }), !d.ok);
    } catch (e) {
      flash('#s-msg-' + ch, t('Eroare: {e}', { e }), true);
    } finally {
      btn.disabled = false;
    }
  };
}

/* antrenament local (data/dataset -> data/finetune -> model activ) */
async function loadTrainStatus() {
  try {
    const s = await (await fetch('/api/train/status')).json();
    $('#s-train-status').innerHTML =
      html`${raw(t('Cadre curate strânse: {colectate} · în datasetul etichetat: {finetune}', { colectate: html`<b>${s.collected}</b>`, finetune: html`<b>${s.finetune}</b>` }))}<br>
      ${raw(t('Model activ din: {data}', { data: html`<b>${s.model_date}</b>` }))}${
      s.trained ? raw(' · ' + t('ultimul antrenament: {nume} ({data})', { nume: html`<b>${s.runs[0].nume}</b>`, data: esc(s.trained_date) }))
                : ' · ' + t('niciun antrenament încă')}${
      s.backup ? html`<br>${t('Backup disponibil (model anterior, din {data})', { data: s.backup_date })}` : ''}`;
    $('#s-train-revert').classList.toggle('hidden', !s.backup);
    const j = s.job, box = $('#s-train-job');
    if (j === null) {
      box.classList.remove('hidden');
      box.textContent = t('nvr-ai nu rulează: datasetul și antrenarea au nevoie de el.');
    } else if (j && (j.activ || j.rc !== null)) {
      box.classList.remove('hidden');
      const cat = j.total ? ` ${Math.round(100 * j.progres / j.total)}%` : '';
      const durata = j.inceput ? Math.round(((j.sfarsit || Date.now() / 1000) - j.inceput) / 60) : 0;
      box.innerHTML = html`<b>${j.tip === 'dataset' ? t('Dataset') : t('Antrenament')}${j.nume ? ' ' + j.nume : ''}</b>:
        ${j.activ ? t('rulează') : (j.rc === 0 ? t('gata') : t('eșuat'))} · ${j.mesaj}${cat} · ${durata} min${
        j.log && j.log.length ? html`<details><summary>${t('jurnal')}</summary><pre>${j.log.join('\n')}</pre></details>` : ''}`;
    } else box.classList.add('hidden');
    $('#s-train-stop').classList.toggle('hidden', !(j && j.activ));
    for (const id of ['s-train-dataset', 's-train-run']) $('#' + id).disabled = !!(j && j.activ);
    clearTimeout(trainTimer);
    if (j && j.activ) trainTimer = setTimeout(loadTrainStatus, 5000);
  } catch {}
}
let trainTimer = null;
$('#s-train-dataset') && ($('#s-train-dataset').onclick = async () => {
  const max = +$('#s-train-max').value || 0;
  const intrebare = max
    ? t('Construiești datasetul etichetat din cel mult {n} cadre?\nÎnlocuiește datasetul curent (setul de validare se reface) și ține GPU-ul ocupat.', { n: max })
    : t('Construiești datasetul etichetat din toate cadrele?\nÎnlocuiește datasetul curent (setul de validare se reface) și ține GPU-ul ocupat.');
  if (!confirm(intrebare)) return;
  const d = await post('/api/train/dataset', { max });
  flash('#s-msg-train', d && !d.error ? t('Datasetul se construiește în fundal.') : (d && d.error) || t('Eroare'), !!(d && d.error));
  loadTrainStatus();
});
$('#s-train-run') && ($('#s-train-run').onclick = async () => {
  const epoci = +$('#s-train-epoci').value || 30;
  if (!confirm(t('Antrenezi un model nou, {n} epoci?\nDurează ore și încetinește detecția cât rulează.', { n: epoci }))) return;
  const d = await post('/api/train/run', { epoci });
  flash('#s-msg-train', d && !d.error ? t('Antrenamentul {nume} a pornit.', { nume: d.nume }) : (d && d.error) || t('Eroare'), !!(d && d.error));
  loadTrainStatus();
});
$('#s-train-stop') && ($('#s-train-stop').onclick = async () => {
  if (!confirm(t('Oprești jobul de antrenament?'))) return;
  await post('/api/train/stop');
  setTimeout(loadTrainStatus, 1500);
});
$('#s-train-revert').onclick = async () => {
  if (!confirm(t('Schimbi modelul activ cu cel din backup?\n(Apăsând din nou revii la cel curent — e o comutare, nu o ștergere.)'))) return;
  const d = await post('/api/train/revert');
  flash('#s-msg-train', d && !d.error ? t('Model comutat cu backup-ul ✓') : t('Eroare'), !!(d && d.error));
  loadTrainStatus();
};
$('#s-train-harvest').onclick = async () => {
  const d = await post('/api/train/harvest');
  if (d && !d.error) flash('#s-msg-train', t('Extragere pornită în fundal — revino în câteva minute și vezi contorul.'));
  setTimeout(loadTrainStatus, 4000);
};
$('#s-train-apply').onclick = async () => {
  if (!confirm(t('Înlocuiești modelul de detecție cu ultimul antrenament local?\n(Se păstrează backup și se aplică instant.)'))) return;
  const d = await post('/api/train/apply');
  flash('#s-msg-train', d && !d.error ? t('Model aplicat: {model} ✓', { model: d.model }) : t('Eroare'), !!(d && d.error));
  loadTrainStatus();
};

export { SET_LABELS, flash, hdBufferTarget, hdHeightParam, loadSettings, meDlg };

/* ---------- mod demo: camere sintetice (Setari -> Unelte) ---------- */
$('#s-demo-add') && ($('#s-demo-add').onclick = async () => {
  const d = await post('/api/demo/camere', { actiune: 'adauga' });
  flash('#s-msg-demo', d && d.ok ? t('Camere demo: {n}. Apar în Live în câteva secunde.', { n: d.demo.length }) : (d && d.error) || t('Eroare'), !(d && d.ok));
  if (d && d.ok) loadCameras();
});
$('#s-demo-del') && ($('#s-demo-del').onclick = async () => {
  const d = await post('/api/demo/camere', { actiune: 'sterge' });
  flash('#s-msg-demo', d && d.ok ? t('Camerele demo au fost scoase.') : (d && d.error) || t('Eroare'), !(d && d.ok));
  if (d && d.ok) loadCameras();
});
