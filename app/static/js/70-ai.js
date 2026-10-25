/* ====================================================================
   AI: Face ID, LPR, numarare, navigare, motorul de reguli
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, DET_RO, EV_RO, html, lista, raw } from './00-core.js';
import { MOD_ICO } from './05-navigare.js';
import { post } from './10-live.js';
import { wireClips } from './40-evenimente.js';
import { SET_LABELS, flash } from './60-setari.js';
/* ---------- recunoastere persoane (Face ID) ---------- */
async function loadFaces() {
  try {
    const d = await (await fetch('/api/faces')).json();
    $('#fc-on').checked = d.enabled;
    $('#fc-notify').checked = d.notify;
    $('#fc-unknown').checked = d.unknown_save;
    $('#fc-spoof').checked = d.antispoof;
    $('#fc-desc').checked = d.describe;
    $('#fc-thr').value = d.threshold;
    $('#fc-persons').innerHTML = lista(d.persons, (p) => html`
      <div class="fc-person" data-name="${p.name}">
        ${p.cover ? html`<img src="${p.cover}" alt="">` : ''}
        <b>${p.name}</b> <span class="hint">${p.photos} poze</span>
        <button class="fc-hist link-btn" title="Când a mai apărut">istoric</button>
        <button class="fc-ren link-btn" title="Schimbă numele">redenumește</button>
        <button class="fc-del link-btn">șterge</button>
      </div>`, '<p class="hint">Nicio persoana inca.</p>');
    for (const row of document.querySelectorAll('#fc-persons .fc-person')) {
      row.querySelector('.fc-ren').onclick = async () => {
        const n = row.dataset.name;
        const nou = prompt(`Numele nou pentru "${n}":`, n);
        if (!nou || nou.trim() === n) return;
        const r = await post(`/api/faces/person/${encodeURIComponent(n)}/rename`, { nou: nou.trim() });
        if (r && r.ok) { flash('#fc-msg', `"${n}" e acum "${r.nume}" ✓`); loadFaces(); }
        else flash('#fc-msg', (r && r.error) || 'Eșuat', true);
      };
      row.querySelector('.fc-del').onclick = async () => {
        const n = row.dataset.name;
        if (!confirm(`Ștergi persoana "${n}" și toate pozele ei?`)) return;
        await fetch(`/api/faces/person/${encodeURIComponent(n)}`, { method: 'DELETE' });
        loadFaces();
      };
      row.querySelector('.fc-hist').onclick = async () => {
        const n = row.dataset.name;
        const d2 = await (await fetch(`/api/faces/history?name=${encodeURIComponent(n)}`)).json();
        const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
        const box = $('#fc-history');
        box.classList.remove('hidden');
        box.innerHTML = html`<h3 class="wh-h3">Aparițiile lui „${n}”
          <button class="link-btn" id="fc-hist-close">închide</button></h3>${
          lista(d2.history || [], (h) => html`<div class="ev-row">
              <span class="ev-t">${h.t}</span>
              <span class="ev-cam">${names[h.cam] || 'cam ' + h.cam}</span>
              <span class="ev-det">similaritate ${Math.round(h.sim * 100)}%</span>
              <button class="clip-btn link-btn" data-cam="${h.cam}" data-t="${h.t}"
                data-cap="${n} — ${h.t}">▶ clip</button>
            </div>`, '<p class="hint">Nicio aparitie inregistrata inca.</p>')}`;
        $('#fc-hist-close').onclick = () => box.classList.add('hidden');
        wireClips(box);
      };
    }
    // necunoscutii ca persoane: grupurile permanente (regrupate in nvr-ai), cu sugestie si unire
    const persoaneNume = (d.persons || []).map((p) => p.name);
    const camNume = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    $('#fc-groups-t').textContent = d.grupuri_t ? `regrupat ${d.grupuri_t.slice(11, 16)}` : 'încă neregrupat';
    $('#fc-groups').innerHTML = lista(d.grupuri || [], (g) => html`
      <div class="fc-group" data-id="${g.id}">
        <div class="fc-g-imgs">${g.mostre.map((m) => html`<img src="${m.url}" alt="" loading="lazy">`)}</div>
        <div class="fc-g-meta"><b>Necunoscut ${g.id.replace('nec-', '')}</b> · ${g.n} poze în ${g.zile} ${g.zile === 1 ? 'zi' : 'zile'}
          <div class="hint">${g.ultima ? `ultima oară ${g.ultima.zi} ${g.ultima.ora} · ${camNume[g.ultima.cam] || 'cam ' + g.ultima.cam}` : g.interval}</div>
          ${g.sugestie ? html`<div>seamănă cu <b>${g.sugestie.nume}</b> (${Math.round(g.sugestie.sim * 100)}%)</div>` : ''}</div>
        <div class="fc-g-act">
          ${g.sugestie ? html`<button class="btn sm fc-g-sug" data-nume="${g.sugestie.nume}">e ${g.sugestie.nume}</button>` : ''}
          ${persoaneNume.length ? html`<select class="fc-g-sel" aria-label="Persoană existentă">
            <option value="">altă persoană…</option>${persoaneNume.map((p) => html`<option value="${p}">${p}</option>`)}</select>` : ''}
          <input class="fc-g-new" placeholder="nume nou" autocomplete="off" aria-label="Numele persoanei noi">
          <button class="fc-g-ok link-btn" title="Dă-i numele ales: devine persoană cunoscută">✓</button>
          <button class="fc-g-ign link-btn" title="Nu e nimeni de interes: șterge pozele grupului">ignoră</button>
        </div>
      </div>`, '<p class="hint">Niciun grup încă: fețele necunoscute apar aici grupate pe persoană, la două minute după ce sunt capturate.</p>');
    const grupuriMap = Object.fromEntries((d.grupuri || []).map((g) => [g.id, g]));
    const trimiteGrup = async (row, actiune, nume) => {
      const g = grupuriMap[row.dataset.id];
      const r = await post('/api/faces/groups/assign', { poze: g.poze, actiune, nume });
      if (r && r.ok) { flash('#fc-msg', actiune === 'persoana' ? `„${r.nume}”: ${r.inrolate} poze învățate ✓` : `${r.sterse} poze ignorate ✓`); post('/api/faces/regrupeaza', {}).then(loadFaces); }
    };
    for (const row of document.querySelectorAll('#fc-groups .fc-group')) {
      const sug = row.querySelector('.fc-g-sug');
      if (sug) sug.onclick = () => trimiteGrup(row, 'persoana', sug.dataset.nume);
      row.querySelector('.fc-g-ok').onclick = () => {
        const sel = row.querySelector('.fc-g-sel');
        const nume = row.querySelector('.fc-g-new').value.trim() || (sel && sel.value) || '';
        if (!nume) { flash('#fc-msg', 'Alege o persoană sau scrie un nume nou.', true); return; }
        trimiteGrup(row, 'persoana', nume);
      };
      row.querySelector('.fc-g-ign').onclick = () => trimiteGrup(row, 'ignora', '');
    }
    // perechi de persoane cunoscute care par aceeasi: propunere de unire pe cardul persoanei
    for (const p of (d.perechi || [])) {
      const row = document.querySelector(`#fc-persons .fc-person[data-name="${CSS.escape(p.b)}"]`);
      if (!row || row.querySelector('.fc-unire')) continue;
      row.insertAdjacentHTML('beforeend', html`<span class="fc-unire hint">pare aceeași cu <b>${p.a}</b> (${Math.round(p.sim * 100)}%)
        <button class="link-btn fc-unire-btn" data-in="${p.a}">unește</button></span>`);
      row.querySelector('.fc-unire-btn').onclick = async () => {
        if (!confirm(`Unești „${p.b}” în „${p.a}”? Pozele se mută, „${p.b}” dispare.`)) return;
        const r = await post(`/api/faces/person/${encodeURIComponent(p.b)}/uneste`, { in: p.a });
        if (r && r.ok) { flash('#fc-msg', `„${p.b}” a intrat în „${p.a}” (${r.poze_mutate} poze) ✓`); loadFaces(); }
      };
    }
    $('#fc-unknown-grid').innerHTML = lista(d.unknown, (u) => html`
      <div class="fc-unk" data-rel="${u.rel}">
        <img src="${u.url}" alt="" loading="lazy" title="cam ${u.cam} — ${u.time}">
        <div class="fc-unk-act">
          <input placeholder="cine e?" autocomplete="off">
          <button class="fc-name-btn" title="Înrolează față cu acest nume">✓</button>
          <button class="fc-unk-del" title="Șterge crop-ul">✕</button>
        </div>
      </div>`, '<p class="hint">Nicio fata neetichetata.</p>');
    for (const box of document.querySelectorAll('#fc-unknown-grid .fc-unk')) {
      const rel = box.dataset.rel;
      box.querySelector('.fc-name-btn').onclick = async () => {
        const name = box.querySelector('input').value.trim();
        if (!name) { alert('Scrie numele persoanei.'); return; }
        const r = await post('/api/faces/enroll', { name, rel });
        if (r && !r.error) { flash('#fc-msg', `"${r.name}" învățat ✓`); loadFaces(); }
      };
      box.querySelector('.fc-unk-del').onclick = async () => {
        await post('/api/faces/unknown/clear', { rel });
        box.remove();
      };
    }
  } catch {}
}
for (const [id, key] of [['fc-on', 'enabled'], ['fc-notify', 'notify'],
                         ['fc-unknown', 'unknown_save'], ['fc-spoof', 'antispoof'],
                         ['fc-desc', 'describe']]) {
  const el = $('#' + id);
  if (el) el.onchange = async () => {
    await post('/api/faces/config', { [key]: el.checked });
    flash('#fc-msg', 'Salvat ✓');
  };
}
$('#fc-thr') && ($('#fc-thr').onchange = async () => {
  await post('/api/faces/config', { threshold: +$('#fc-thr').value });
  flash('#fc-msg', 'Prag salvat ✓');
});
$('#fc-file') && ($('#fc-file').onchange = () => {
  const name = $('#fc-name').value.trim();
  const f = $('#fc-file').files[0];
  if (!name) { alert('Scrie întâi numele persoanei.'); $('#fc-file').value = ''; return; }
  if (!f) return;
  const rd = new FileReader();
  rd.onload = async () => {
    const r = await post('/api/faces/enroll', { name, data: rd.result });
    if (r && !r.error) { flash('#fc-msg', `Poza adăugată la "${r.name}" ✓`); loadFaces(); }
    $('#fc-file').value = '';
  };
  rd.readAsDataURL(f);
});
$('#fc-unk-clear') && ($('#fc-unk-clear').onclick = async () => {
  if (!confirm('Ștergi toate fețele neetichetate?')) return;
  await post('/api/faces/unknown/clear', {});
  loadFaces();
});
/* fetele necunoscute grupate pe persoana: sugestie + confirmare cu un click
   (inlocuieste inrolarea automata, care amesteca persoane) */
async function loadGroups() {
  const box = $('#fc-groups');
  flash('#fc-msg', 'Grupez fețele capturate... (prima dată poate dura un minut)');
  let d;
  try { d = await (await fetch('/api/faces/groups')).json(); } catch { d = { error: 'rețea' }; }
  if (!d || d.error) { flash('#fc-msg', (d && d.error) || 'Eșuat', true); return; }
  box.classList.remove('hidden');
  const persoane = d.persoane || [];
  box.innerHTML = lista(d.grupuri, (g) => html`
    <div class="fc-group" data-id="${g.id}">
      <div class="fc-g-imgs">${g.mostre.map((m) => html`<img src="${m.url}" alt="" loading="lazy">`)}</div>
      <div class="fc-g-meta"><b>${g.n} poze</b> în ${g.zile} ${g.zile === 1 ? 'zi' : 'zile'}
        <div class="hint">${g.interval}</div>
        ${g.sugestie ? html`<div>seamănă cu <b>${g.sugestie.nume}</b> (${Math.round(g.sugestie.sim * 100)}%)</div>` : ''}</div>
      <div class="fc-g-act">
        ${g.sugestie ? html`<button class="btn sm fc-g-sug" data-nume="${g.sugestie.nume}">e ${g.sugestie.nume}</button>` : ''}
        ${persoane.length ? html`<select class="fc-g-sel" aria-label="Persoană existentă">
          <option value="">altă persoană…</option>${persoane.map((p) => html`<option value="${p}">${p}</option>`)}</select>` : ''}
        <input class="fc-g-new" placeholder="persoană nouă" autocomplete="off" aria-label="Numele persoanei noi">
        <button class="fc-g-ok link-btn" title="Înrolează grupul cu numele ales">✓</button>
        <button class="fc-g-ign link-btn" title="Nu e nimeni de interes: șterge pozele grupului">ignoră</button>
      </div>
    </div>`, '<p class="hint">Niciun grup: fiecare față capturată e singură (sau nu există fețe).</p>');
  const grupuri = Object.fromEntries((d.grupuri || []).map((g) => [g.id, g]));
  const trimite = async (row, actiune, nume) => {
    const g = grupuri[row.dataset.id];
    const r = await post('/api/faces/groups/assign', { poze: g.poze, actiune, nume });
    if (r && r.ok) {
      flash('#fc-msg', actiune === 'persoana' ? `„${r.nume}”: ${r.inrolate} poze învățate, ${r.sterse} scoase din necunoscute ✓` : `${r.sterse} poze ignorate ✓`);
      row.remove();
      loadFaces();
    }
  };
  for (const row of box.querySelectorAll('.fc-group')) {
    const sug = row.querySelector('.fc-g-sug');
    if (sug) sug.onclick = () => trimite(row, 'persoana', sug.dataset.nume);
    row.querySelector('.fc-g-ok').onclick = () => {
      const sel = row.querySelector('.fc-g-sel');
      const nume = row.querySelector('.fc-g-new').value.trim() || (sel && sel.value) || '';
      if (!nume) { flash('#fc-msg', 'Alege o persoană sau scrie un nume nou.', true); return; }
      trimite(row, 'persoana', nume);
    };
    row.querySelector('.fc-g-ign').onclick = () => trimite(row, 'ignora', '');
  }
  flash('#fc-msg', `${(d.grupuri || []).length} grupuri`);
}
$('#fc-group') && ($('#fc-group').onclick = async () => {
  flash('#fc-msg', 'Regrupez… (prima dată poate dura un minut)');
  const r = await post('/api/faces/regrupeaza', {});
  if (r && r.ok) { flash('#fc-msg', `${r.grupuri} grupuri, ${r.perechi} perechi de persoane care par aceeași`); loadFaces(); }
});
$('#fc-regula-plecat') && ($('#fc-regula-plecat').onclick = async () => {
  if (!confirm('Creez regula „Față necunoscută când sunt plecat”: notificare (Telegram/email/push) la orice față care nu e în bibliotecă, doar în modurile „plecat” și „noapte”, cel mult una la 2 minute?')) return;
  const r = await post('/api/rules', { nume: 'Față necunoscută când sunt plecat', declansator: { tip: 'persoana_necunoscuta', valoare: '' },
    moduri: ['plecat', 'noapte'], cooldown: 120, actiuni: [{ tip: 'notificare' }] });
  flash('#fc-msg', r && r.ok ? 'Regula a fost creată; o vezi și o modifici în AI → Reguli.' : (r && r.error) || 'Eroare', !!(r && r.error));
});

/* ---------- numere de inmatriculare (LPR) ---------- */
async function loadPlates() {
  try {
    const q = ($('#lp-q') && $('#lp-q').value.trim()) || '';
    const d = await (await fetch('/api/plates' + (q ? `?q=${encodeURIComponent(q)}` : ''))).json();
    $('#lp-on').checked = d.enabled;
    $('#lp-nk').checked = d.notify_known;
    $('#lp-na').checked = d.notify_all;
    $('#lp-nu').checked = d.notify_unknown;
    $('#lp-conf').value = d.min_conf;
    $('#lp-sure').value = d.sure_conf;
    const sel = $('#lp-cams');
    if (sel.options.length !== S.cameras.length) {
      sel.innerHTML = html`${S.cameras.map((c) => html`<option value="${c.id}">${c.name}</option>`)}`;
    }
    for (const o of sel.options) o.selected = (d.alert_cams || []).includes(+o.value);
    const st = d.stats || {};
    $('#lp-stats').textContent = `Citiri: ${st.azi || 0} azi, ${st.total7 || 0} în ultimele 7 zile`
      + ` (${st.necunoscute7 || 0} fără nume)`;
    $('#lp-known').innerHTML = lista(d.known, (k) => html`
      <div class="u-row" data-plate="${k.plate}">
        <b>${k.plate}</b> <span class="hint">${k.name}</span>
        <button class="lp-del link-btn">șterge</button>
      </div>`, '<p class="hint">Nicio masina cunoscuta.</p>');
    for (const row of document.querySelectorAll('#lp-known .u-row')) {
      row.querySelector('.lp-del').onclick = async () => {
        await fetch(`/api/plates/known/${encodeURIComponent(row.dataset.plate)}`,
                    { method: 'DELETE' });
        loadPlates();
      };
    }
    $('#lp-reads').innerHTML = lista(d.reads, (r) => html`
      <div class="lp-read">
        ${r.url ? html`<img src="${r.url}" alt="" loading="lazy">` : ''}
        <div><b>${r.numar}</b>${r.nume ? html` <span class="u-role">${r.nume}</span>` : ''}
        <div class="hint">cam ${r.cam} — ${r.t} — ${Math.round(r.incredere * 100)}%</div>
        <button class="clip-btn link-btn" data-cam="${r.cam}" data-t="${r.t}"
          data-cap="${r.numar}${r.nume ? ' (' + r.nume + ')' : ''} — ${r.t}">▶ clip</button>
        ${r.nume ? '' : html`<button class="lp-name-btn link-btn" data-plate="${r.numar}">
          da-i un nume</button>`}</div>
      </div>`, '<p class="hint">Nicio citire inca.</p>');
    wireClips($('#lp-reads'));
    for (const b of document.querySelectorAll('#lp-reads .lp-name-btn')) {
      b.onclick = async () => {
        const name = prompt(`Numele mașinii cu numărul ${b.dataset.plate}:`);
        if (!name) return;
        const r = await post('/api/plates/known', { plate: b.dataset.plate, name });
        if (r && !r.error) { flash('#lp-msg', 'Mașină salvată ✓'); loadPlates(); }
      };
    }
  } catch {}
}
$('#lp-cams') && ($('#lp-cams').onchange = async () => {
  const ids = [...$('#lp-cams').selectedOptions].map((o) => +o.value);
  await post('/api/plates/config', { alert_cams: ids });
  flash('#lp-msg', ids.length ? `Alertă doar pe ${ids.length} camere ✓` : 'Alertă pe toate camerele ✓');
});
$('#lp-sure') && ($('#lp-sure').onchange = async () => {
  await post('/api/plates/config', { sure_conf: +$('#lp-sure').value });
  flash('#lp-msg', 'Prag salvat ✓');
});
for (const [id, key] of [['lp-on', 'enabled'], ['lp-nk', 'notify_known'],
                         ['lp-na', 'notify_all'], ['lp-nu', 'notify_unknown']]) {
  const el = $('#' + id);
  if (el) el.onchange = async () => {
    await post('/api/plates/config', { [key]: el.checked });
    flash('#lp-msg', 'Salvat ✓');
  };
}
$('#lp-conf') && ($('#lp-conf').onchange = async () => {
  await post('/api/plates/config', { min_conf: +$('#lp-conf').value });
  flash('#lp-msg', 'Prag salvat ✓');
});
$('#lp-add') && ($('#lp-add').onclick = async () => {
  const r = await post('/api/plates/known',
                       { plate: $('#lp-plate').value, name: $('#lp-name').value });
  if (r && !r.error) {
    $('#lp-plate').value = ''; $('#lp-name').value = '';
    flash('#lp-msg', 'Mașină salvată ✓');
    loadPlates();
  }
});
$('#lp-clear') && ($('#lp-clear').onclick = async () => {
  if (!confirm('Golești jurnalul de citiri și pozele lui?')) return;
  await post('/api/plates/clear', {});
  loadPlates();
});
let lpQTimer = null;
$('#lp-q') && ($('#lp-q').oninput = () => {
  clearTimeout(lpQTimer);
  lpQTimer = setTimeout(loadPlates, 350);
});

/* ---------- numarare exacta (urmarire obiecte, ByteTrack) ---------- */
async function loadTrack() {
  try {
    const d = await (await fetch('/api/track')).json();
    const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    $('#tk-cams').innerHTML = html`${S.cameras.map((c) =>
      html`<option value="${c.id}" ${d.cams.includes(c.id) ? 'selected' : ''}>${c.id}. ${c.name}</option>`)}`;
    const rows = Object.entries(d.state || {}).map(([cid, s]) => {
      const azi = Object.entries(s.azi || {})
        .map(([l, n]) => `${DET_RO[l] || l}: ${n}`).join(', ') || 'nimic încă azi';
      return `${names[cid] || 'cam ' + cid}: ${s.activ ? '🟢' : '🔴'} ` +
        `${azi}${s.in_cadru ? ` · ${s.in_cadru} in cadru acum` : ''}`;
    });
    $('#tk-state').innerHTML = lista(rows, (r, i) => html`${i ? raw('<br>') : ''}${r}`,
      'Nicio camera urmărită — bifează mai sus și salvează.');
  } catch {}
}
$('#tk-save') && ($('#tk-save').onclick = async () => {
  const cams = [...$('#tk-cams').selectedOptions].map((o) => +o.value);
  const r = await post('/api/track/config', { cams });
  if (r && !r.error) { flash('#tk-msg', 'Salvat — lucratorii pornesc în câteva secunde ✓'); }
  setTimeout(loadTrack, 3000);
});

/* ---------- tabul AI: o fraza de stare, din date, sub fiecare intrebare ---------- */
async function loadAiStare() {
  const j = async (u) => { try { return await (await fetch(u)).json(); } catch { return null; } };
  const pune = (id, t) => { const el = $('#' + id); if (el) el.textContent = t; };
  const [f, h, p, r, a] = await Promise.all([j('/api/faces'), j('/api/faces/history?limit=1000'), j('/api/plates'), j('/api/rules'), j('/api/adnotare?limit=1')]);
  if (f) {
    const de30 = (h && h.history ? h.history : []).filter((x) => (Date.now() - new Date(x.t.replace(' ', 'T')).getTime()) < 30 * 86400e3).length;
    pune('ai-stare-pers', `${(f.persons || []).length} persoane cunoscute · ${de30} recunoașteri în 30 de zile · ${(f.unknown || []).length} fețe neetichetate${f.enabled ? '' : ' · Face ID oprit'}`);
  }
  if (p) {
    const st = p.stats || {};
    pune('ai-stare-auto', `${(p.known || []).length} mașini cunoscute · ${st.total7 || 0} citiri în 7 zile (${st.necunoscute7 || 0} fără nume)${p.notify_unknown ? ' · alertă la necunoscute pornită' : ' · fără alertă la necunoscute'}${p.enabled ? '' : ' · LPR oprit'}`);
  }
  if (r) {
    const lst = Array.isArray(r) ? r : (r.rules || []);
    pune('ai-stare-reguli', `${lst.filter((x) => x.activ !== false).length} reguli active din ${lst.length}`);
  }
  if (a) pune('ai-stare-adnot', `${a.total || 0} cadre etichetate · ${a.verificate || 0} verificate de om · ${a.nesigure || 0} în coada de nesigure`);
}
$('#tab-ai') && $('#tab-ai').addEventListener('click', loadAiStare);
if (!$('#ai').classList.contains('hidden')) setTimeout(loadAiStare, 500);

/* ---------- tabul AI: navigarea pe sectiuni ---------- */
for (const b of document.querySelectorAll('.ainav-item')) {
  b.onclick = () => {
    loadAiStare();
    for (const x of document.querySelectorAll('.ainav-item')) x.classList.toggle('on', x === b);
    for (const p of document.querySelectorAll('.ai-pane'))
      p.classList.toggle('hidden', p.id !== 'aipane-' + b.dataset.pane);
    localStorage.setItem('aiPane', b.dataset.pane);
    window.scrollTo({ top: 0 });
  };
}

/* ---------- motorul de reguli ---------- */
let ruMeta = { triggers: {}, actions: {}, capabilities: {}, persons: [], plates: [] };
async function loadRules() {
  loadRuLinii();   // sugestiile de linii/zone pentru formular
  try {
    const d = await (await fetch('/api/rules')).json();
    ruMeta = d;
    const trig = $('#ru-trigger');
    if (!trig.options.length) {
      trig.innerHTML = html`${Object.entries(d.triggers)
        .map(([k, v]) => html`<option value="${k}">${v}</option>`)}`;
      trig.onchange = buildRuleValueList;
    }
    const camSel = $('#ru-cams');
    camSel.innerHTML = html`${S.cameras.map((c) =>
      html`<option value="${c.id}">${c.id}. ${c.name}</option>`)}`;
    $('#ru-groups').innerHTML = html`${(d.groups || []).map((g) =>
      html`<option value="${g}">${g}</option>`)}`;
    if (!$('#ru-days').children.length) {
      $('#ru-days').innerHTML = html`${(d.zile || []).map((z, i) =>
        html`<label class="inline"><input type="checkbox" value="${i}"> ${z}</label>`)}`;
    }
    buildRuleValueList();
    const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    $('#ru-list').innerHTML = lista(d.rules || [], (r) => {
      const parti = [...(r.grupuri || []),
                     ...(r.camere || []).map((c) => names[c] || 'cam ' + c)];
      const unde = parti.length ? parti.join(', ') : 'oriunde';
      const zileTxt = (r.zile && r.zile.length)
        ? ' · ' + r.zile.map((z) => (d.zile[z] || z)).join(',') : '';
      const modTxt = (r.moduri && r.moduri.length) ? ' · ' + r.moduri.map((m) => MOD_ICO[m] || m).join('') : '';
      const cand = (r.interval ? ` · ${r.interval[0]}–${r.interval[1]}` : '') + zileTxt + modTxt;
      const ce = r.actiuni.map((a) => a.tip).join(' + ');
      return html`<div class="u-row ${r.activ ? '' : 'ru-off'}" data-id="${r.id}">
        <b>${r.nume}</b>
        <span class="hint">${d.triggers[r.declansator.tip] || r.declansator.tip}${
          r.declansator.valoare ? ` „${r.declansator.valoare}”` : ''} · ${unde}${cand} → ${ce}</span>
        <button class="ru-toggle link-btn">${r.activ ? 'opreste' : 'porneste'}</button>
        <button class="ru-test link-btn" title="Rulează acțiunile acum">testează</button>
        <button class="ru-edit link-btn">modifică</button>
        <button class="ru-del link-btn">șterge</button>
      </div>`;
    }, '<p class="hint">Nicio regula inca. Fa una mai jos — dureaza 30 de secunde.</p>');
    for (const row of document.querySelectorAll('#ru-list .u-row')) {
      const rule = d.rules.find((r) => r.id === row.dataset.id);
      row.querySelector('.ru-del').onclick = async () => {
        if (!confirm(`Ștergi regula "${rule.nume}"?`)) return;
        await fetch(`/api/rules/${rule.id}`, { method: 'DELETE' });
        loadRules();
      };
      row.querySelector('.ru-toggle').onclick = async () => {
        await post('/api/rules', { ...rule, activ: !rule.activ });
        loadRules();
      };
      row.querySelector('.ru-test').onclick = async () => {
        const r = await post(`/api/rules/${rule.id}/test`, {});
        if (r && !r.error) flash('#ru-msg', 'Acțiunile au rulat — vezi Evenimente ✓');
      };
      row.querySelector('.ru-edit').onclick = () => fillRuleForm(rule);
    }
  } catch {}
}
function buildRuleValueList() {
  const tip = $('#ru-trigger').value;
  const dl = $('#ru-value-list');
  const wrap = $('#ru-value-wrap');
  const noValue = ['persoana_necunoscuta', 'fata_falsa', 'zgomot',
                   'camera_offline', 'camera_online', 'obiect_nou', 'obiect_disparut'].includes(tip);
  wrap.style.display = noValue ? 'none' : '';
  if (tip === 'alarma_camera') {
    dl.innerHTML = html`${['VMD', 'linedetection', 'fielddetection', 'regionEntrance',
                           'regionExiting', 'shelteralarm', 'tamperdetection', 'scenechangedetection']
      .map((v) => html`<option value="${v}">${EV_RO[v.toLowerCase()] || v}</option>`)}`;
  } else if (tip === 'persoana') {
    dl.innerHTML = html`${ruMeta.persons.map((p) => html`<option value="${p}">`)}`;
  } else if (tip === 'numar') {
    dl.innerHTML = html`${['cunoscut', 'necunoscut', ...ruMeta.plates]
      .map((p) => html`<option value="${p}">`)}`;
  } else if (tip === 'obiect') {
    dl.innerHTML = html`<option value="biciclist">biciclist (om cu viteză de bicicletă)</option>${SET_LABELS.map((l) => html`<option value="${l}">${DET_RO[l] || l}</option>`)}`;
  } else if (tip === 'detectie' || tip === 'intrus' || tip === 'parcare_inceput') {
    dl.innerHTML = html`${SET_LABELS.map((l) => html`<option value="${l}">${DET_RO[l] || l}</option>`)}`;
  } else if (tip === 'trecere') {
    // liniile desenate pe camere (Camere → Linii/zone): „poarta”, „poarta:intra”, „poarta:iese”
    const opts = [['intra', 'orice linie, sens intră'], ['iese', 'orice linie, sens iese']];
    for (const [cam, linii] of Object.entries(ruLinii.linii || {})) for (const l of linii) {
      const c = S.cameras.find((x) => x.id === +cam); const cn = c ? c.name : 'cam ' + cam;
      opts.push([l.name, `${l.name} (${cn}), orice sens`], [`${l.name}:intra`, `${l.name} (${cn}), intră`], [`${l.name}:iese`, `${l.name} (${cn}), iese`]);
    }
    dl.innerHTML = html`${opts.map(([v, t]) => html`<option value="${v}">${t}</option>`)}`;
  } else if (tip === 'intrare_zona') {
    const opts = [];
    for (const [cam, zone] of Object.entries(ruLinii.zone || {})) for (const z of zone) {
      const c = S.cameras.find((x) => x.id === +cam); opts.push([z.name, `${z.name} (${c ? c.name : 'cam ' + cam})`]);
    }
    dl.innerHTML = html`${opts.map(([v, t]) => html`<option value="${v}">${t}</option>`)}`;
  } else dl.innerHTML = '';
}
// liniile si zonele desenate pe camere, pentru sugestiile din formular (din setari; admin)
let ruLinii = { linii: {}, zone: {} };
async function loadRuLinii() {
  try { const s = await (await fetch('/api/settings')).json(); ruLinii = { linii: s.count_lines || {}, zone: s.count_zones || {} }; } catch {}
}
/* Formularul de reguli precompletat dintr-un eveniment, o linie sau o zona: „regula din asta”.
   {tip, valoare, cam, nume} -> AI → Reguli, cu declansatorul, valoarea, camera si numele puse. */
async function regulaDin({ tip, valoare = '', cam = null, nume = '' }) {
  document.querySelector('#tab-ai')?.click();
  document.querySelector('.ainav-item[data-pane="reguli"]')?.click();
  await loadRules();        // optiunile declansatorului si lista de camere se refac aici
  await loadRuLinii();
  const trig = $('#ru-trigger');
  if (trig && [...trig.options].some((o) => o.value === tip)) { trig.value = tip; buildRuleValueList(); }
  if ($('#ru-value')) $('#ru-value').value = valoare;
  const cams = $('#ru-cams'); if (cams) for (const o of cams.options) o.selected = cam !== null && +o.value === +cam;
  if ($('#ru-name')) $('#ru-name').value = nume;
  $('#ru-name')?.scrollIntoView({ block: 'center' }); $('#ru-name')?.focus();
}
function fillRuleForm(r) {
  $('#ru-id').value = r.id;
  $('#ru-form-title').textContent = `Modifici: ${r.nume}`;
  $('#ru-cancel').classList.remove('hidden');
  $('#ru-name').value = r.nume;
  $('#ru-trigger').value = r.declansator.tip;
  buildRuleValueList();
  $('#ru-value').value = r.declansator.valoare || '';
  for (const o of $('#ru-cams').options) o.selected = (r.camere || []).includes(+o.value);
  for (const o of $('#ru-groups').options) o.selected = (r.grupuri || []).includes(o.value);
  for (const cb of document.querySelectorAll('#ru-days input'))
    cb.checked = (r.zile || []).includes(+cb.value);
  $('#ru-interval-on').checked = !!r.interval;
  if (r.interval) { $('#ru-int-a').value = r.interval[0]; $('#ru-int-b').value = r.interval[1]; }
  for (const cb of document.querySelectorAll('#ru-moduri input')) cb.checked = (r.moduri || []).includes(cb.value);
  $('#ru-cooldown').value = r.cooldown;
  for (const cb of document.querySelectorAll('.ru-actions input[data-act]')) {
    const a = r.actiuni.find((x) => x.tip === cb.dataset.act);
    cb.checked = !!a;
    if (a) {
      if (a.tip === 'sirena') { $('#ru-a-vol').value = a.volum || 90; $('#ru-a-rep').value = a.repetari || 3; }
      if (a.tip === 'strobo') $('#ru-a-sec').value = a.secunde || 30;
      if (a.tip === 'anunt') $('#ru-a-text').value = a.text || '';
      if (a.tip === 'releu') { $('#ru-a-port').value = a.port || 1; $('#ru-a-rsec').value = a.secunde ?? 10; }
      if (a.tip === 'notificare') $('#ru-n-text').value = a.text || '';
      if (a.tip === 'mqtt') { $('#ru-m-topic').value = a.topic || ''; $('#ru-m-payload').value = a.payload || ''; }
      if (a.tip === 'mod') $('#ru-a-mod').value = a.mod || 'acasa';
    }
  }
  window.scrollTo({ top: $('#ru-form-title').offsetTop - 80 });
}
function clearRuleForm() {
  $('#ru-id').value = '';
  $('#ru-form-title').textContent = 'Adaugă o regula';
  $('#ru-cancel').classList.add('hidden');
  $('#ru-name').value = ''; $('#ru-value').value = '';
  for (const o of $('#ru-cams').options) o.selected = false;
  for (const o of $('#ru-groups').options) o.selected = false;
  for (const cb of document.querySelectorAll('#ru-days input')) cb.checked = false;
  $('#ru-interval-on').checked = false;
  for (const cb of document.querySelectorAll('#ru-moduri input')) cb.checked = false;
  for (const cb of document.querySelectorAll('.ru-actions input[data-act]')) cb.checked = false;
}
$('#ru-cancel') && ($('#ru-cancel').onclick = clearRuleForm);
$('#ru-save') && ($('#ru-save').onclick = async () => {
  const actiuni = [];
  for (const cb of document.querySelectorAll('.ru-actions input[data-act]:checked')) {
    const t = cb.dataset.act;
    if (t === 'sirena') actiuni.push({ tip: t, volum: +$('#ru-a-vol').value, repetari: +$('#ru-a-rep').value });
    else if (t === 'strobo') actiuni.push({ tip: t, secunde: +$('#ru-a-sec').value });
    else if (t === 'anunt') actiuni.push({ tip: t, text: $('#ru-a-text').value.trim() });
    else if (t === 'releu') actiuni.push({ tip: t, port: +$('#ru-a-port').value, secunde: +$('#ru-a-rsec').value });
    else if (t === 'notificare') actiuni.push({ tip: t, text: $('#ru-n-text').value.trim() });
    else if (t === 'mqtt') actiuni.push({ tip: t, topic: $('#ru-m-topic').value.trim(),
                                          payload: $('#ru-m-payload').value.trim() });
    else if (t === 'mod') actiuni.push({ tip: t, mod: $('#ru-a-mod').value });
    else actiuni.push({ tip: t });
  }
  const body = {
    id: $('#ru-id').value || undefined,
    nume: $('#ru-name').value.trim(),
    activ: true,
    declansator: { tip: $('#ru-trigger').value, valoare: $('#ru-value').value.trim() },
    camere: [...$('#ru-cams').selectedOptions].map((o) => +o.value),
    grupuri: [...$('#ru-groups').selectedOptions].map((o) => o.value),
    zile: [...document.querySelectorAll('#ru-days input:checked')].map((c) => +c.value),
    moduri: [...document.querySelectorAll('#ru-moduri input:checked')].map((c) => c.value),
    interval: $('#ru-interval-on').checked ? [$('#ru-int-a').value, $('#ru-int-b').value] : null,
    cooldown: +$('#ru-cooldown').value || 120,
    actiuni,
  };
  const r = await post('/api/rules', body);
  if (r && !r.error) { clearRuleForm(); flash('#ru-msg', 'Regula salvată ✓'); loadRules(); }
});

export { loadFaces, loadPlates, loadRules, loadTrack, regulaDin };
