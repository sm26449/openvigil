/* ====================================================================
   Live: grila, vizualizari, filtre, controalele camerei, badge-uri, pop-up la critic, panoul I/O, densitate/mod perete
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, CAN_ACT, DET_RO, EV_RO, HLS_SUFFIX, html, icon, lista, streamEl, talkStart, talkStop, toast } from './00-core.js';
import { camLinks } from './05-navigare.js';
import { hdCam, openHD } from './20-hd.js';
import { hartaEvents } from './25-harta.js';
import { buildPbCams, openMaskEditor, shotFromVideo, stamp } from './30-redare.js';
import { openCountEditor, openZoneEditor } from './50-camere.js';
/* ---------- vizualizari salvate: filtru + aranjare, reutilizabile cu un click ---------- */
let views = [], defaultView = '';
async function loadViews(applyDefault) {
  try {
    const d = await (await fetch('/api/views')).json();
    views = d.views || [];
    defaultView = d.default || '';
    renderViews();
    if (applyDefault && defaultView) {
      const v = views.find((x) => x.name === defaultView);
      if (v) applyView(v, true);
    }
  } catch {}
}
function renderViews() {
  const box = $('#views');
  box.innerHTML = '';
  if (!views.length) {
    box.innerHTML = '<span class="hint">Filtreaza si apasa "Salveaza vizualizarea"</span>';
    return;
  }
  for (const v of views) {
    const wrap = document.createElement('span');
    wrap.className = 'view-chip' + (v.name === S.currentView ? ' on' : '');
    const b = document.createElement('button');
    b.className = 'view-go';
    b.textContent = v.name + (v.name === defaultView ? ' •' : '');
    b.title = `${v.group || 'toate grupurile'}${v.q ? ', cauta "' + v.q + '"' : ''}, ` +
              `${v.cols === 'auto' ? 'auto' : v.cols + ' coloane'}` +
              (v.preview ? ', previzualizare' : '') +
              (v.name === defaultView ? ' — se încarcă la pornire' : '');
    b.onclick = () => applyView(v);
    const del = document.createElement('button');
    del.className = 'view-del';
    del.innerHTML = icon('trash', 12);
    del.title = 'Șterge vizualizarea';
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Ștergi vizualizarea "${v.name}"?`)) return;
      await fetch('/api/views', { method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: v.name }) });
      if (S.currentView === v.name) S.currentView = '';
      loadViews(false);
    };
    wrap.append(b, del);
    box.appendChild(wrap);
  }
}
S.currentView = '';
function applyView(v, silent) {
  S.currentView = v.name;
  filt.live.g = v.group || '';
  filt.live.q = v.q || '';
  $('#live-search').value = v.q || '';
  if (previewMode !== !!v.preview) {
    previewMode = !!v.preview;
    localStorage.setItem('preview', previewMode ? '1' : '0');
    $('#preview').classList.toggle('on', previewMode);
  }
  applyDensity(v.cols || 'auto');
  buildLive();
  renderViews();
  if (!silent) $('#status').textContent += ` — ${v.name}`;
}
$('#view-save').onclick = async () => {
  const name = prompt('Cum se numește vizualizarea?\n(ex. "Perete curte", "Intrări noaptea")',
                      S.currentView || filt.live.g || 'Vizualizarea mea');
  if (!name) return;
  const asDefault = confirm(`Vrei ca "${name}" să se încarce automat la deschiderea paginii?`);
  const d = await post('/api/views', {
    name, group: filt.live.g, q: filt.live.q,
    cols: localStorage.getItem('cols') || 'auto',
    preview: previewMode, make_default: asDefault,
  });
  if (d && !d.error) { S.currentView = name; loadViews(false); }
};

/* ---------- mod previzualizare: imagini reimprospatate, in loc de fluxuri video ---------- */
let previewMode = localStorage.getItem('preview') === '1';
let previewImgs = [];
setInterval(() => {
  if (document.hidden || !previewMode) return;
  for (const p of previewImgs) {
    if (p.el.isConnected) p.el.src = `/api/thumb/${p.id}?t=${Date.now()}`;
  }
}, 5000);

/* ---------- filtre comune: grup + cautare (scalabil la sute de camere) ---------- */
const filt = { live: { q: '', g: '' }, cams: { q: '', g: '' } };
function matches(c, f) {
  if (f.g && (c.group || 'Altele') !== f.g) return false;
  if (!f.q) return true;
  const s = `${c.id} ${c.name} ${c.ip || ''} ${c.model || ''} ${c.group || ''}`.toLowerCase();
  return s.includes(f.q.toLowerCase());
}
function renderGroupChips(boxId, key, onChange) {
  const box = $(boxId);
  if (!box) return;
  const counts = {};
  for (const c of S.cameras) counts[c.group || 'Altele'] = (counts[c.group || 'Altele'] || 0) + 1;
  box.innerHTML = '';
  const mk = (val, label, n) => {
    const b = document.createElement('button');
    b.className = 'chip-btn' + (filt[key].g === val ? ' on' : '');
    b.textContent = n === undefined ? label : `${label} ${n}`;
    b.onclick = () => { filt[key].g = val; onChange(); };
    box.appendChild(b);
  };
  mk('', 'Toate', S.cameras.length);
  for (const g of Object.keys(counts).sort()) mk(g, g, counts[g]);
}

/* ---------- LIVE ---------- */
function buildLive() {
  const grid = $('#grid');
  grid.innerHTML = '';
  previewImgs = [];
  renderGroupChips('#live-groups', 'live', buildLive);
  const shown = S.cameras.filter((c) => matches(c, filt.live));
  $('#status').textContent = shown.length === S.cameras.length
    ? `${S.cameras.length} camere` : `${shown.length} din ${S.cameras.length} camere`;
  for (const c of shown) {
    const tile = document.createElement('div');
    tile.className = 'tile';
    tile._cam = c.id;
    // camerele cu microfon: modul care duce si audio (AAC -> MSE, G.711 -> WebRTC)
    const f = c.features || {};
    if (previewMode) {
      const im = document.createElement('img');
      im.className = 'tile-img';
      im.loading = 'lazy';
      im.alt = c.name;
      im.src = `/api/thumb/${c.id}?t=${Date.now()}`;
      tile.appendChild(im);
      previewImgs.push({ el: im, id: c.id });
    } else {
      // Fortam WebRTC doar cand camera chiar are sunet pe care MSE nu-l poate reda (G.711).
      // Cele 5 camere IZS raporteaza mic=true dar au intrare de linie fara microfon, deci
      // fluxul n-are pista audio: negocierea WebRTC cadea cu "no codecs" si dadea 502.
      const areSunet = f.mic && f.audio_ok;
      const mode = (areSunet ? (f.aac ? 'mse' : 'webrtc') : 'mse,webrtc') + HLS_SUFFIX;
      // grila: buffer de jitter ~1s (mod fluid). Netezimea conteaza cand privesti multe
      // camere; +1s intarziere e neglijabil aici (nu comanzi PTZ din grila).
      tile.appendChild(streamEl(`cam${c.id}`, mode, true, 1.0));
    }
    // un singur rand jos: numele la stanga, adresa la dreapta (nu mai concureaza cu restul)
    const lbl = document.createElement('div');
    lbl.className = 'label';
    lbl.innerHTML = html`<span class="nm">${c.id}. ${c.name}</span>`;
    if (c.ip) {
      const ip = document.createElement('span');
      ip.className = 'ip';
      ip.textContent = c.ip;
      lbl.appendChild(ip);
    }
    tile.appendChild(lbl);
    // punct de stare in coltul de sus (verde = live, rosu pulsand = alarma)
    const dot = document.createElement('div');
    dot.className = 'dot';
    tile.appendChild(dot);
    tile.title = 'Click pentru HD';
    tile.tabIndex = 0;
    tile.onclick = () => openHD(c);
    tile.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openHD(c); } };
    const al = document.createElement('div');
    al.className = 'alert hidden';
    tile.appendChild(al);
    alertEls[c.id] = al;
    if (c.source === 'ext' && c.id >= 101) {
      const del = document.createElement('button');
      del.className = 'del';
      del.innerHTML = icon('trash', 14);
      del.title = 'Șterge camera';
      del.onclick = (e) => { e.stopPropagation(); deleteCamera(c); };
      tile.appendChild(del);
    }
    tile.appendChild(buildCtrls(c, tile, false));  // toate camerele au macar 🐾
    const det = document.createElement('div');
    det.className = 'detect hidden';
    tile.appendChild(det);
    detectEls[c.id] = det;
    grid.appendChild(tile);
  }
}

/* ---------- controale camera (mic, difuzor, sirena, stroboscop, releu) ---------- */
const strobeState = {}, outState = {};

function mkBtn(inner, title, cls) {
  const b = document.createElement('button');
  b.innerHTML = inner;
  b.title = title;
  if (cls) b.className = cls;
  b.onclick = (e) => e.stopPropagation();
  return b;
}

// bara de butoane; full=true (overlay HD) adauga si selectorul de sunet + volum + releu
/* Zoom optic: apesi si tine, se opreste cand ridici degetul.
   Camerele astea (bullet IZS) au DOAR zoom motorizat — pan/tilt si focus raspund 200 dar
   nu misca nimic, iar presetarile se accepta si se pierd. Verificat pe cam7 comparand
   cadrele: pan a dat diferenta 1.0 (cat zgomotul de fond), zoom a dat 39.4.
   Opritorul de siguranta conteaza: daca se pierde evenimentul de eliberare (degetul iese
   din ecran, fereastra isi pierde focusul), comanda ramane activa si lentila merge pana
   la capat. De aceea oprim si dupa 4 secunde, si la pierderea focusului. */
const ZOOM_MAX_MS = 4000;
function zoomBtns(c, dupa) {
  const out = [];
  for (const [ic, spd, txt] of [['zoomin', 60, 'Apropie'], ['zoomout', -60, 'Depărtează']]) {
    const b = mkBtn(icon(ic), txt + ' (ține apăsat)');
    let timer = null;
    const stop = async (e) => {
      if (e) e.stopPropagation();
      if (timer === null) return;
      clearTimeout(timer); timer = null;
      b.classList.remove('zooming');
      await post(`/api/cam/${c.id}/ptz`, { zoom: 0 });
      if (dupa) dupa();
    };
    b.onpointerdown = (e) => {
      e.stopPropagation(); e.preventDefault();
      b.setPointerCapture?.(e.pointerId);
      b.classList.add('zooming');
      timer = setTimeout(() => stop(), ZOOM_MAX_MS);
      post(`/api/cam/${c.id}/ptz`, { zoom: spd });
    };
    b.onpointerup = stop;
    b.onpointercancel = stop;
    b.onlostpointercapture = () => stop();
    window.addEventListener('blur', () => stop());
    out.push(b);
  }
  return out;
}

function buildCtrls(c, container, full) {
  // Viewerul primeste doar controalele care nu schimba nimic (ascultat, captura);
  // operatorul primeste si actiunile live (sirena, vorbit, strobo, releu, zoom) —
  // dar nu si configurarea (zona, detectia, sirena automata), care ramane la admin.
  // Serverul refuza oricum ce nu e permis — aici doar nu aratam butoane moarte.
  const f = CAN_ACT ? (c.features || {}) : {};
  const mic = (c.features || {}).mic;
  const bar = document.createElement('div');
  bar.className = 'ctrl';
  bar.onclick = (e) => e.stopPropagation();

  if (mic) {
    const b = mkBtn(icon('listen'), 'Ascultă microfonul camerei');
    b.onclick = (e) => {
      e.stopPropagation();
      const v = container.querySelector('video');
      if (!v) return;
      v.muted = !v.muted;
      b.classList.toggle('on', !v.muted);
    };
    bar.appendChild(b);
  }
  if (f.talk) {
    const b = mkBtn(icon('talk'), 'Ține apăsat ca să vorbești prin difuzorul camerei');
    const down = (e) => { e.stopPropagation(); e.preventDefault(); talkStart(c, b); };
    const up = (e) => { e.stopPropagation(); talkStop(); };
    b.onmousedown = down; b.onmouseup = up; b.onmouseleave = up;
    b.ontouchstart = down; b.ontouchend = up;
    bar.appendChild(b);
  }
  if (f.siren) {
    const b = mkBtn(icon('siren'), 'Redă sunetul de alarma (o data)');
    b.onclick = async (e) => {
      e.stopPropagation();
      b.disabled = true;
      const sel = bar.querySelector('.snd'), vol = bar.querySelector('.vol');
      await post(`/api/cam/${c.id}/siren`, {
        sound: sel ? +sel.value : undefined,
        volume: vol ? +vol.value : undefined,
      });
      setTimeout(() => { b.disabled = false; }, 1500);
    };
    bar.appendChild(b);
    if (full && (f.sounds || []).length) {
      const sel = document.createElement('select');
      sel.className = 'snd';
      sel.title = 'Sunetul de alarma';
      for (const s of f.sounds) {
        const o = document.createElement('option');
        o.value = s.id; o.textContent = s.name;
        sel.appendChild(o);
      }
      bar.appendChild(sel);
      const vol = document.createElement('input');
      vol.type = 'range'; vol.min = 1; vol.max = 100; vol.value = 50;
      vol.className = 'vol'; vol.title = 'Volum sirena';
      bar.appendChild(vol);
    }
  }
  if (ADMIN && f.siren) {
    const b = mkBtn(icon('alarm'), 'Sirena automată la câine (necesită detecția 🐾 pornită)');
    b.classList.toggle('on', !!c.auto_siren);
    b.onclick = async (e) => {
      e.stopPropagation();
      c.auto_siren = !c.auto_siren;
      b.classList.toggle('on', c.auto_siren);
      await post(`/api/cam/${c.id}/detect`, { auto_siren: c.auto_siren });
    };
    bar.appendChild(b);
  }
  if (f.strobe) {
    const b = mkBtn(icon('strobe'), 'Stroboscop pornit/oprit (se stinge singur dupa 60s)');
    b.classList.toggle('on', !!strobeState[c.id]);
    b.onclick = async (e) => {
      e.stopPropagation();
      strobeState[c.id] = !strobeState[c.id];
      b.classList.toggle('on', strobeState[c.id]);
      await post(`/api/cam/${c.id}/strobe`, { on: strobeState[c.id] });
      if (strobeState[c.id]) setTimeout(() => {
        strobeState[c.id] = false;
        b.classList.remove('on');
      }, 60000);
    };
    bar.appendChild(b);
  }
  if (full && f.io_out) {
    const b = mkBtn(icon('relay'), 'Releu (iesirea de alarma) pornit/oprit');
    b.classList.toggle('on', !!outState[c.id]);
    b.onclick = async (e) => {
      e.stopPropagation();
      outState[c.id] = !outState[c.id];
      b.classList.toggle('on', outState[c.id]);
      await post(`/api/cam/${c.id}/output`, { on: outState[c.id] });
    };
    bar.appendChild(b);
  }
  if (f.zoom) {
    for (const b of zoomBtns(c)) bar.appendChild(b);
  }
  if (full && ADMIN) {
    const bz = mkBtn(icon('zone'), 'Zona de detecție (desenează unde să se uite AI-ul)');
    bz.classList.toggle('on', !!(c.zone && c.zone.length));
    bz.onclick = (e) => { e.stopPropagation(); openZoneEditor(c); };
    bar.appendChild(bz);
    const bl = mkBtn('⳹', 'Linii de numarare / zone de intrare (numara treceri, viteza)');
    bl.onclick = (e) => { e.stopPropagation(); openCountEditor(c); };
    bar.appendChild(bl);
    const bm = mkBtn(icon('lock'), 'Masca de confidențialitate (zone înnegrite pe live, redare și exporturi)');
    bm.classList.toggle('on', !!(c.mask && c.mask.length));
    bm.onclick = (e) => { e.stopPropagation(); openMaskEditor(c); };
    bar.appendChild(bm);
    const bw = mkBtn(icon('external'), 'Deschide interfața web a camerei (login-ul ei propriu)');
    bw.onclick = (e) => {
      e.stopPropagation();
      const url = c.source === 'ext' ? camLinks[c.id] : camLinks.nvr;
      if (url) window.open(url, '_blank');
    };
    bar.appendChild(bw);
  }
  if (full) {
    const bs = mkBtn(icon('camera'), 'Salvează cadrul curent ca imagine');
    bs.onclick = (e) => {
      e.stopPropagation();
      shotFromVideo(container.querySelector('video'),
                    `cam${c.id}_${stamp()}.jpg`);
    };
    bar.appendChild(bs);
  }
  // detectie animale/persoane (YOLO pe server) — disponibila pe orice camera
  if (ADMIN) {
    const bd = mkBtn(icon('paw'), 'Detectie animale/persoane pornita/oprita');
    bd.classList.toggle('on', !!c.detect);
    bd.onclick = async (e) => {
      e.stopPropagation();
      c.detect = !c.detect;
      bd.classList.toggle('on', c.detect);
      await post(`/api/cam/${c.id}/detect`, { on: c.detect });
      if (!c.detect && detectEls[c.id]) detectEls[c.id].classList.add('hidden');
    };
    bar.appendChild(bd);
  }
  return bar;
}

async function post(url, body) {
  try {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const d = await r.json();
    if (d.error) alert('Eroare: ' + d.error);
    return d;
  } catch (e) { alert('Eroare rețea: ' + e); }
}

/* ---------- evenimente de alarma + detectii (badge-uri pe tile) ---------- */
const alertEls = {}, detectEls = {};
async function pollEvents() {
  try {
    const r = await fetch('/api/events');
    const d = await r.json();
    for (const [id, st] of Object.entries(d.alarms || {})) {
      const el = alertEls[id];
      if (!el) continue;
      if (st.active.length) {
        el.innerHTML = html`${icon('warn', 12)} ${st.active.map((t) => EV_RO[t] || t).join(', ')}`;
        el.classList.remove('hidden');
        el.parentElement.classList.add('alerting');
      } else {
        el.classList.add('hidden');
        el.parentElement.classList.remove('alerting');
      }
    }
    hartaEvents(d);
    for (const [id, el] of Object.entries(detectEls)) {
      const st = (d.detect || {})[id];
      if (st && st.labels.length) {
        el.innerHTML = html`${icon('paw', 12)} ${st.labels.map((l) => DET_RO[l] || l).join(', ')}`;
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
    }
  } catch {}
}
// starile de alarma/detectie se cer doar unde se vad: grila, harta sau HD-ul (nu si cand
// utilizatorul sta in Setari sau Evenimente — acolo nu le afiseaza nimeni)
setInterval(() => {
  if (document.hidden) return;
  if (hdCam || !$('#live').classList.contains('hidden') || !$('#harta').classList.contains('hidden')) pollEvents();
}, 3000);

async function deleteCamera(c) {
  if (!confirm(`Ștergi camera "${c.name}" (${c.ip})?`)) return;
  const r = await fetch(`/api/cameras/${c.id}`, { method: 'DELETE' });
  const d = await r.json();
  if (d.error) { alert('Eroare: ' + d.error); return; }
  await loadCameras();
}

/* ---------- Etapa 4: pop-up la incident critic in grila ----------
   Dala camerei se mareste 8 secunde (ca „layout switch on event" la Synology). Pe un
   singur rand de coloane nu avem ce mari, deci doar o aprindem si o aducem in ecran. */
let popupOn = localStorage.getItem('popup') !== '0';
$('#popup-toggle').classList.toggle('on', popupOn);
$('#popup-toggle').onclick = () => {
  popupOn = !popupOn;
  localStorage.setItem('popup', popupOn ? '1' : '0');
  $('#popup-toggle').classList.toggle('on', popupOn);
};
function popupTile(camId) {
  if (!popupOn || $('#live').classList.contains('hidden') || hdCam) return;
  const tile = [...document.querySelectorAll('#grid .tile')].find((t) => t._cam === +camId);
  if (!tile) return;
  const cols = getComputedStyle($('#grid')).gridTemplateColumns.split(' ').length;
  tile.classList.add('popup');
  if (cols < 2) tile.style.gridColumn = 'auto';
  tile.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  clearTimeout(tile._popT);
  tile._popT = setTimeout(() => { tile.classList.remove('popup'); tile.style.gridColumn = ''; }, 8000);
}

/* ---------- Etapa 4: panoul I/O pe Live (sirena, stroboscop, releu, anunt) ----------
   Aceleasi actiuni ca pe dalele camerelor, adunate intr-un panou: paznicul vede dintr-o
   privire ce poate comanda si pe ce camera, fara sa caute butonul pe fiecare dala. */
function buildIoPanel() {
  const box = $('#io-list');
  const cams = S.cameras.filter((c) => { const f = c.features || {}; return CAN_ACT && (f.siren || f.strobe || f.io_out || f.talk); });
  box.innerHTML = lista(cams, (c) => {
    const f = c.features || {};
    return html`<div class="io-row" data-id="${c.id}"><span class="nm" title="${c.name}">${c.id}. ${c.name}</span>
      ${f.siren ? html`<button data-io="siren" title="Sirena (o data)">${icon('siren', 14)}</button>` : ''}
      ${f.strobe ? html`<button data-io="strobe" class="${strobeState[c.id] ? 'on' : ''}" title="Stroboscop pornit/oprit">${icon('strobe', 14)}</button>` : ''}
      ${f.io_out ? html`<button data-io="output" class="${outState[c.id] ? 'on' : ''}" title="Releu pornit/oprit">${icon('relay', 14)}</button>` : ''}
      ${f.talk ? html`<button data-io="say" title="Rosteste textul de sus in difuzor">${icon('talk', 14)}</button>` : ''}
    </div>`;
  }, '<p class="hint">Nicio camera cu sirena, stroboscop, releu sau difuzor (sau nu ai drept de actiune).</p>');
}
$('#io-toggle').onclick = () => {
  const p = $('#io-panel');
  p.classList.toggle('hidden');
  $('#io-toggle').classList.toggle('on', !p.classList.contains('hidden'));
  if (!p.classList.contains('hidden')) buildIoPanel();
};
$('#io-close').onclick = () => { $('#io-panel').classList.add('hidden'); $('#io-toggle').classList.remove('on'); };
$('#io-list').onclick = async (e) => {
  const b = e.target.closest('button[data-io]');
  if (!b) return;
  const id = +b.closest('.io-row').dataset.id, act = b.dataset.io;
  b.disabled = true;
  try {
    if (act === 'siren') await post(`/api/cam/${id}/siren`, {});
    else if (act === 'strobe') { strobeState[id] = !strobeState[id]; b.classList.toggle('on', strobeState[id]); await post(`/api/cam/${id}/strobe`, { on: strobeState[id] }); }
    else if (act === 'output') { outState[id] = !outState[id]; b.classList.toggle('on', outState[id]); await post(`/api/cam/${id}/output`, { on: outState[id] }); }
    else if (act === 'say') {
      const text = $('#io-text').value.trim();
      if (!text) { toast('Scrie întâi textul anunțului', true); }
      else await post(`/api/cam/${id}/say`, { text });
    }
  } finally { setTimeout(() => { b.disabled = false; }, 800); }
};

/* ---------- densitatea grilei si modul perete ---------- */
// Alegerea se tine local (per dispozitiv): un monitor mare vrea 6 coloane, telefonul 2.
function applyDensity(cols) {
  const g = $('#grid');
  if (cols === 'auto') g.removeAttribute('data-cols'); else g.dataset.cols = cols;
  for (const b of $('#density').children) b.classList.toggle('on', b.dataset.cols === cols);
  localStorage.setItem('cols', cols);
}
for (const b of $('#density').children) b.onclick = () => applyDensity(b.dataset.cols);
applyDensity(localStorage.getItem('cols') || 'auto');

function setWall(on) {
  document.body.classList.toggle('wall', on);
  if (on && document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else if (!on && document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
}
$('#preview').onclick = () => {
  previewMode = !previewMode;
  localStorage.setItem('preview', previewMode ? '1' : '0');
  $('#preview').classList.toggle('on', previewMode);
  buildLive();
};
$('#preview').classList.toggle('on', previewMode);
$('#wall').onclick = () => setWall(true);
$('#wall-exit').onclick = () => setWall(false);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.body.classList.contains('wall')) setWall(false);
});

// mutat din nucleu: incarca registrul si construieste grila (depinde de ecranul Live)
async function loadCameras() {
  const r = await fetch('/api/cameras');
  S.cameras = await r.json();
  if (S.cameras.error) { $('#status').textContent = 'Eroare NVR: ' + S.cameras.error; return; }
  buildLive();
  buildPbCams();
  if (!views.length) loadViews(true);
}

export { ZOOM_MAX_MS, buildCtrls, buildLive, deleteCamera, filt, loadCameras, matches, pollEvents, popupTile, post, renderGroupChips, renderViews, zoomBtns };
