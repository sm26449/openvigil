/* ====================================================================
   AI: Face ID, LPR, numarare, navigare, motorul de reguli
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
import { S } from './state.js';
import { $, DET_RO, EV_RO, html, lista, raw, t } from './00-core.js';
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
        <b>${p.name}</b> <span class="hint">${t('{n} poze', { n: p.photos })}</span>
        <button class="fc-hist link-btn" title="${t('Când a mai apărut')}">${t('istoric')}</button>
        <button class="fc-ren link-btn" title="${t('Schimbă numele')}">${t('redenumește')}</button>
        <button class="fc-del link-btn">${t('șterge')}</button>
      </div>`, html`<p class="hint">${t('Nicio persoana inca.')}</p>`);
    for (const row of document.querySelectorAll('#fc-persons .fc-person')) {
      row.querySelector('.fc-ren').onclick = async () => {
        const n = row.dataset.name;
        const nou = prompt(t('Numele nou pentru "{n}":', { n }), n);
        if (!nou || nou.trim() === n) return;
        const r = await post(`/api/faces/person/${encodeURIComponent(n)}/rename`, { nou: nou.trim() });
        if (r && r.ok) { flash('#fc-msg', t('"{vechi}" e acum "{nou}" ✓', { vechi: n, nou: r.nume })); loadFaces(); }
        else flash('#fc-msg', (r && r.error) || t('Eșuat'), true);
      };
      row.querySelector('.fc-del').onclick = async () => {
        const n = row.dataset.name;
        if (!confirm(t('Ștergi persoana "{n}" și toate pozele ei?', { n }))) return;
        await fetch(`/api/faces/person/${encodeURIComponent(n)}`, { method: 'DELETE' });
        loadFaces();
      };
      row.querySelector('.fc-hist').onclick = async () => {
        const n = row.dataset.name;
        const d2 = await (await fetch(`/api/faces/history?name=${encodeURIComponent(n)}`)).json();
        const names = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
        const box = $('#fc-history');
        box.classList.remove('hidden');
        box.innerHTML = html`<h3 class="wh-h3">${t('Aparițiile lui „{n}”', { n })}
          <button class="link-btn" id="fc-hist-close">${t('închide')}</button></h3>${
          lista(d2.history || [], (h) => html`<div class="ev-row">
              <span class="ev-t">${h.t}</span>
              <span class="ev-cam">${names[h.cam] || t('cam {id}', { id: h.cam })}</span>
              <span class="ev-det">${t('similaritate {p}%', { p: Math.round(h.sim * 100) })}</span>
              <button class="clip-btn link-btn" data-cam="${h.cam}" data-t="${h.t}"
                data-cap="${n} — ${h.t}">${t('▶ clip')}</button>
            </div>`, html`<p class="hint">${t('Nicio aparitie inregistrata inca.')}</p>`)}`;
        $('#fc-hist-close').onclick = () => box.classList.add('hidden');
        wireClips(box);
      };
    }
    // necunoscutii ca persoane: grupurile permanente (regrupate in nvr-ai), cu sugestie si unire
    const persoaneNume = (d.persons || []).map((p) => p.name);
    const camNume = Object.fromEntries(S.cameras.map((c) => [c.id, c.name]));
    $('#fc-groups-t').textContent = d.grupuri_t ? t('regrupat {ora}', { ora: d.grupuri_t.slice(11, 16) }) : t('încă neregrupat');
    $('#fc-groups').innerHTML = lista(d.grupuri || [], (g) => html`
      <div class="fc-group" data-id="${g.id}">
        <div class="fc-g-imgs">${g.mostre.map((m) => html`<img src="${m.url}" alt="" loading="lazy">`)}</div>
        <div class="fc-g-meta"><b>${t('Necunoscut {id}', { id: g.id.replace('nec-', '') })}</b> · ${g.zile === 1
            ? t('{n} poze în {zile} zi', { n: g.n, zile: g.zile }) : t('{n} poze în {zile} zile', { n: g.n, zile: g.zile })}
          <div class="hint">${g.ultima ? t('ultima oară {zi} {ora} · {cam}', { zi: g.ultima.zi, ora: g.ultima.ora, cam: camNume[g.ultima.cam] || t('cam {id}', { id: g.ultima.cam }) }) : g.interval}</div>
          ${g.sugestie ? html`<div>${raw(t('seamănă cu {nume} ({p}%)', { nume: html`<b>${g.sugestie.nume}</b>`, p: Math.round(g.sugestie.sim * 100) }))}</div>` : ''}</div>
        <div class="fc-g-act">
          ${g.sugestie ? html`<button class="btn sm fc-g-sug" data-nume="${g.sugestie.nume}">${t('e {nume}', { nume: g.sugestie.nume })}</button>` : ''}
          ${persoaneNume.length ? html`<select class="fc-g-sel" aria-label="${t('Persoană existentă')}">
            <option value="">${t('altă persoană…')}</option>${persoaneNume.map((p) => html`<option value="${p}">${p}</option>`)}</select>` : ''}
          <input class="fc-g-new" placeholder="${t('nume nou')}" autocomplete="off" aria-label="${t('Numele persoanei noi')}">
          <button class="fc-g-ok link-btn" title="${t('Dă-i numele ales: devine persoană cunoscută')}">✓</button>
          <button class="fc-g-ign link-btn" title="${t('Nu e nimeni de interes: șterge pozele grupului')}">${t('ignoră')}</button>
        </div>
      </div>`, html`<p class="hint">${t('Niciun grup încă: fețele necunoscute apar aici grupate pe persoană, la două minute după ce sunt capturate.')}</p>`);
    const grupuriMap = Object.fromEntries((d.grupuri || []).map((g) => [g.id, g]));
    const trimiteGrup = async (row, actiune, nume) => {
      const g = grupuriMap[row.dataset.id];
      const r = await post('/api/faces/groups/assign', { poze: g.poze, actiune, nume });
      if (r && r.ok) { flash('#fc-msg', actiune === 'persoana' ? t('„{nume}”: {n} poze învățate ✓', { nume: r.nume, n: r.inrolate }) : t('{n} poze ignorate ✓', { n: r.sterse })); post('/api/faces/regrupeaza', {}).then(loadFaces); }
    };
    for (const row of document.querySelectorAll('#fc-groups .fc-group')) {
      const sug = row.querySelector('.fc-g-sug');
      if (sug) sug.onclick = () => trimiteGrup(row, 'persoana', sug.dataset.nume);
      row.querySelector('.fc-g-ok').onclick = () => {
        const sel = row.querySelector('.fc-g-sel');
        const nume = row.querySelector('.fc-g-new').value.trim() || (sel && sel.value) || '';
        if (!nume) { flash('#fc-msg', t('Alege o persoană sau scrie un nume nou.'), true); return; }
        trimiteGrup(row, 'persoana', nume);
      };
      row.querySelector('.fc-g-ign').onclick = () => trimiteGrup(row, 'ignora', '');
    }
    // perechi de persoane cunoscute care par aceeasi: propunere de unire pe cardul persoanei
    for (const p of (d.perechi || [])) {
      const row = document.querySelector(`#fc-persons .fc-person[data-name="${CSS.escape(p.b)}"]`);
      if (!row || row.querySelector('.fc-unire')) continue;
      row.insertAdjacentHTML('beforeend', html`<span class="fc-unire hint">${raw(t('pare aceeași cu {nume} ({p}%)', { nume: html`<b>${p.a}</b>`, p: Math.round(p.sim * 100) }))}
        <button class="link-btn fc-unire-btn" data-in="${p.a}">${t('unește')}</button></span>`);
      row.querySelector('.fc-unire-btn').onclick = async () => {
        if (!confirm(t('Unești „{b}” în „{a}”? Pozele se mută, „{b}” dispare.', { a: p.a, b: p.b }))) return;
        const r = await post(`/api/faces/person/${encodeURIComponent(p.b)}/uneste`, { in: p.a });
        if (r && r.ok) { flash('#fc-msg', t('„{b}” a intrat în „{a}” ({n} poze) ✓', { a: p.a, b: p.b, n: r.poze_mutate })); loadFaces(); }
      };
    }
    $('#fc-unknown-grid').innerHTML = lista(d.unknown, (u) => html`
      <div class="fc-unk" data-rel="${u.rel}">
        <img src="${u.url}" alt="" loading="lazy" title="${t('cam {id}', { id: u.cam })} — ${u.time}">
        <div class="fc-unk-act">
          <input placeholder="${t('cine e?')}" autocomplete="off">
          <button class="fc-name-btn" title="${t('Înrolează față cu acest nume')}">✓</button>
          <button class="fc-unk-del" title="${t('Șterge crop-ul')}">✕</button>
        </div>
      </div>`, html`<p class="hint">${t('Nicio fata neetichetata.')}</p>`);
    for (const box of document.querySelectorAll('#fc-unknown-grid .fc-unk')) {
      const rel = box.dataset.rel;
      box.querySelector('.fc-name-btn').onclick = async () => {
        const name = box.querySelector('input').value.trim();
        if (!name) { alert(t('Scrie numele persoanei.')); return; }
        const r = await post('/api/faces/enroll', { name, rel });
        if (r && !r.error) { flash('#fc-msg', t('"{nume}" învățat ✓', { nume: r.name })); loadFaces(); }
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
    flash('#fc-msg', t('Salvat ✓'));
  };
}
$('#fc-thr') && ($('#fc-thr').onchange = async () => {
  await post('/api/faces/config', { threshold: +$('#fc-thr').value });
  flash('#fc-msg', t('Prag salvat ✓'));
});
$('#fc-file') && ($('#fc-file').onchange = () => {
  const name = $('#fc-name').value.trim();
  const f = $('#fc-file').files[0];
  if (!name) { alert(t('Scrie întâi numele persoanei.')); $('#fc-file').value = ''; return; }
  if (!f) return;
  const rd = new FileReader();
  rd.onload = async () => {
    const r = await post('/api/faces/enroll', { name, data: rd.result });
    if (r && !r.error) { flash('#fc-msg', t('Poza adăugată la "{nume}" ✓', { nume: r.name })); loadFaces(); }
    $('#fc-file').value = '';
  };
  rd.readAsDataURL(f);
});
$('#fc-unk-clear') && ($('#fc-unk-clear').onclick = async () => {
  if (!confirm(t('Ștergi toate fețele neetichetate?'))) return;
  await post('/api/faces/unknown/clear', {});
  loadFaces();
});
/* fetele necunoscute grupate pe persoana: sugestie + confirmare cu un click
   (inlocuieste inrolarea automata, care amesteca persoane) */
async function loadGroups() {
  const box = $('#fc-groups');
  flash('#fc-msg', t('Grupez fețele capturate... (prima dată poate dura un minut)'));
  let d;
  try { d = await (await fetch('/api/faces/groups')).json(); } catch { d = { error: t('rețea') }; }
  if (!d || d.error) { flash('#fc-msg', (d && d.error) || t('Eșuat'), true); return; }
  box.classList.remove('hidden');
  const persoane = d.persoane || [];
  box.innerHTML = lista(d.grupuri, (g) => html`
    <div class="fc-group" data-id="${g.id}">
      <div class="fc-g-imgs">${g.mostre.map((m) => html`<img src="${m.url}" alt="" loading="lazy">`)}</div>
      <div class="fc-g-meta"><b>${t('{n} poze', { n: g.n })}</b> ${g.zile === 1 ? t('în {n} zi', { n: g.zile }) : t('în {n} zile', { n: g.zile })}
        <div class="hint">${g.interval}</div>
        ${g.sugestie ? html`<div>${raw(t('seamănă cu {nume} ({p}%)', { nume: html`<b>${g.sugestie.nume}</b>`, p: Math.round(g.sugestie.sim * 100) }))}</div>` : ''}</div>
      <div class="fc-g-act">
        ${g.sugestie ? html`<button class="btn sm fc-g-sug" data-nume="${g.sugestie.nume}">${t('e {nume}', { nume: g.sugestie.nume })}</button>` : ''}
        ${persoane.length ? html`<select class="fc-g-sel" aria-label="${t('Persoană existentă')}">
          <option value="">${t('altă persoană…')}</option>${persoane.map((p) => html`<option value="${p}">${p}</option>`)}</select>` : ''}
        <input class="fc-g-new" placeholder="${t('persoană nouă')}" autocomplete="off" aria-label="${t('Numele persoanei noi')}">
        <button class="fc-g-ok link-btn" title="${t('Înrolează grupul cu numele ales')}">✓</button>
        <button class="fc-g-ign link-btn" title="${t('Nu e nimeni de interes: șterge pozele grupului')}">${t('ignoră')}</button>
      </div>
    </div>`, html`<p class="hint">${t('Niciun grup: fiecare față capturată e singură (sau nu există fețe).')}</p>`);
  const grupuri = Object.fromEntries((d.grupuri || []).map((g) => [g.id, g]));
  const trimite = async (row, actiune, nume) => {
    const g = grupuri[row.dataset.id];
    const r = await post('/api/faces/groups/assign', { poze: g.poze, actiune, nume });
    if (r && r.ok) {
      flash('#fc-msg', actiune === 'persoana' ? t('„{nume}”: {n} poze învățate, {sterse} scoase din necunoscute ✓', { nume: r.nume, n: r.inrolate, sterse: r.sterse }) : t('{n} poze ignorate ✓', { n: r.sterse }));
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
      if (!nume) { flash('#fc-msg', t('Alege o persoană sau scrie un nume nou.'), true); return; }
      trimite(row, 'persoana', nume);
    };
    row.querySelector('.fc-g-ign').onclick = () => trimite(row, 'ignora', '');
  }
  flash('#fc-msg', t('{n} grupuri', { n: (d.grupuri || []).length }));
}
$('#fc-group') && ($('#fc-group').onclick = async () => {
  flash('#fc-msg', t('Regrupez… (prima dată poate dura un minut)'));
  const r = await post('/api/faces/regrupeaza', {});
  if (r && r.ok) { flash('#fc-msg', t('{grupuri} grupuri, {perechi} perechi de persoane care par aceeași', { grupuri: r.grupuri, perechi: r.perechi })); loadFaces(); }
});
$('#fc-regula-plecat') && ($('#fc-regula-plecat').onclick = async () => {
  if (!confirm(t('Creez regula „Față necunoscută când sunt plecat”: notificare (Telegram/email/push) la orice față care nu e în bibliotecă, doar în modurile „plecat” și „noapte”, cel mult una la 2 minute?'))) return;
  const r = await post('/api/rules', { nume: t('Față necunoscută când sunt plecat'), declansator: { tip: 'persoana_necunoscuta', valoare: '' },
    moduri: ['plecat', 'noapte'], cooldown: 120, actiuni: [{ tip: 'notificare' }] });
  flash('#fc-msg', r && r.ok ? t('Regula a fost creată; o vezi și o modifici în AI → Reguli.') : (r && r.error) || t('Eroare'), !!(r && r.error));
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
    $('#lp-stats').textContent = t('Citiri: {azi} azi, {total} în ultimele 7 zile ({nec} fără nume)',
      { azi: st.azi || 0, total: st.total7 || 0, nec: st.necunoscute7 || 0 });
    $('#lp-known').innerHTML = lista(d.known, (k) => html`
      <div class="u-row" data-plate="${k.plate}">
        <b>${k.plate}</b> <span class="hint">${k.name}</span>
        <button class="lp-del link-btn">${t('șterge')}</button>
      </div>`, html`<p class="hint">${t('Nicio masina cunoscuta.')}</p>`);
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
        <div class="hint">${t('cam {id}', { id: r.cam })} — ${r.t} — ${Math.round(r.incredere * 100)}%</div>
        <button class="clip-btn link-btn" data-cam="${r.cam}" data-t="${r.t}"
          data-cap="${r.numar}${r.nume ? ' (' + r.nume + ')' : ''} — ${r.t}">${t('▶ clip')}</button>
        ${r.nume ? '' : html`<button class="lp-name-btn link-btn" data-plate="${r.numar}">
          ${t('da-i un nume')}</button>`}</div>
      </div>`, html`<p class="hint">${t('Nicio citire inca.')}</p>`);
    wireClips($('#lp-reads'));
    for (const b of document.querySelectorAll('#lp-reads .lp-name-btn')) {
      b.onclick = async () => {
        const name = prompt(t('Numele mașinii cu numărul {numar}:', { numar: b.dataset.plate }));
        if (!name) return;
        const r = await post('/api/plates/known', { plate: b.dataset.plate, name });
        if (r && !r.error) { flash('#lp-msg', t('Mașină salvată ✓')); loadPlates(); }
      };
    }
  } catch {}
}
$('#lp-cams') && ($('#lp-cams').onchange = async () => {
  const ids = [...$('#lp-cams').selectedOptions].map((o) => +o.value);
  await post('/api/plates/config', { alert_cams: ids });
  flash('#lp-msg', ids.length ? t('Alertă doar pe {n} camere ✓', { n: ids.length }) : t('Alertă pe toate camerele ✓'));
});
$('#lp-sure') && ($('#lp-sure').onchange = async () => {
  await post('/api/plates/config', { sure_conf: +$('#lp-sure').value });
  flash('#lp-msg', t('Prag salvat ✓'));
});
for (const [id, key] of [['lp-on', 'enabled'], ['lp-nk', 'notify_known'],
                         ['lp-na', 'notify_all'], ['lp-nu', 'notify_unknown']]) {
  const el = $('#' + id);
  if (el) el.onchange = async () => {
    await post('/api/plates/config', { [key]: el.checked });
    flash('#lp-msg', t('Salvat ✓'));
  };
}
$('#lp-conf') && ($('#lp-conf').onchange = async () => {
  await post('/api/plates/config', { min_conf: +$('#lp-conf').value });
  flash('#lp-msg', t('Prag salvat ✓'));
});
$('#lp-add') && ($('#lp-add').onclick = async () => {
  const r = await post('/api/plates/known',
                       { plate: $('#lp-plate').value, name: $('#lp-name').value });
  if (r && !r.error) {
    $('#lp-plate').value = ''; $('#lp-name').value = '';
    flash('#lp-msg', t('Mașină salvată ✓'));
    loadPlates();
  }
});
$('#lp-clear') && ($('#lp-clear').onclick = async () => {
  if (!confirm(t('Golești jurnalul de citiri și pozele lui?'))) return;
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
        .map(([l, n]) => `${t(DET_RO[l] || l)}: ${n}`).join(', ') || t('nimic încă azi');
      return `${names[cid] || t('cam {id}', { id: cid })}: ${s.activ ? '🟢' : '🔴'} ` +
        `${azi}${s.in_cadru ? ' · ' + t('{n} in cadru acum', { n: s.in_cadru }) : ''}`;
    });
    $('#tk-state').innerHTML = lista(rows, (r, i) => html`${i ? raw('<br>') : ''}${r}`,
      html`${t('Nicio camera urmărită — bifează mai sus și salvează.')}`);
  } catch {}
}
$('#tk-save') && ($('#tk-save').onclick = async () => {
  const cams = [...$('#tk-cams').selectedOptions].map((o) => +o.value);
  const r = await post('/api/track/config', { cams });
  if (r && !r.error) { flash('#tk-msg', t('Salvat — lucratorii pornesc în câteva secunde ✓')); }
  setTimeout(loadTrack, 3000);
});

/* ---------- tabul AI: o fraza de stare, din date, sub fiecare intrebare ---------- */
async function loadAiStare() {
  const j = async (u) => { try { return await (await fetch(u)).json(); } catch { return null; } };
  const pune = (id, txt) => { const el = $('#' + id); if (el) el.textContent = txt; };
  const [f, h, p, r, a] = await Promise.all([j('/api/faces'), j('/api/faces/history?limit=1000'), j('/api/plates'), j('/api/rules'), j('/api/adnotare?limit=1')]);
  if (f) {
    const de30 = (h && h.history ? h.history : []).filter((x) => (Date.now() - new Date(x.t.replace(' ', 'T')).getTime()) < 30 * 86400e3).length;
    pune('ai-stare-pers', t('{pers} persoane cunoscute · {rec} recunoașteri în 30 de zile · {nec} fețe neetichetate',
      { pers: (f.persons || []).length, rec: de30, nec: (f.unknown || []).length }) + (f.enabled ? '' : ' · ' + t('Face ID oprit')));
  }
  if (p) {
    const st = p.stats || {};
    pune('ai-stare-auto', t('{masini} mașini cunoscute · {citiri} citiri în 7 zile ({nec} fără nume)',
      { masini: (p.known || []).length, citiri: st.total7 || 0, nec: st.necunoscute7 || 0 })
      + ' · ' + (p.notify_unknown ? t('alertă la necunoscute pornită') : t('fără alertă la necunoscute'))
      + (p.enabled ? '' : ' · ' + t('LPR oprit')));
  }
  if (r) {
    const lst = Array.isArray(r) ? r : (r.rules || []);
    pune('ai-stare-reguli', t('{active} reguli active din {total}', { active: lst.filter((x) => x.activ !== false).length, total: lst.length }));
  }
  if (a) pune('ai-stare-adnot', t('{total} cadre etichetate · {verificate} verificate de om · {nesigure} în coada de nesigure',
    { total: a.total || 0, verificate: a.verificate || 0, nesigure: a.nesigure || 0 }));
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
                     ...(r.camere || []).map((c) => names[c] || t('cam {id}', { id: c }))];
      const unde = parti.length ? parti.join(', ') : t('oriunde');
      const zileTxt = (r.zile && r.zile.length)
        ? ' · ' + r.zile.map((z) => (d.zile[z] || z)).join(',') : '';
      const modTxt = (r.moduri && r.moduri.length) ? ' · ' + r.moduri.map((m) => MOD_ICO[m] || m).join('') : '';
      const cand = (r.interval ? ` · ${r.interval[0]}–${r.interval[1]}` : '') + zileTxt + modTxt;
      const ce = r.actiuni.map((a) => a.tip).join(' + ');
      return html`<div class="u-row ${r.activ ? '' : 'ru-off'}" data-id="${r.id}">
        <b>${r.nume}</b>
        <span class="hint">${d.triggers[r.declansator.tip] || r.declansator.tip}${
          r.declansator.valoare ? ` „${r.declansator.valoare}”` : ''} · ${unde}${cand} → ${ce}</span>
        <button class="ru-toggle link-btn">${r.activ ? t('opreste') : t('porneste')}</button>
        <button class="ru-test link-btn" title="${t('Rulează acțiunile acum')}">${t('testează')}</button>
        <button class="ru-edit link-btn">${t('modifică')}</button>
        <button class="ru-del link-btn">${t('șterge')}</button>
      </div>`;
    }, html`<p class="hint">${t('Nicio regula inca. Fa una mai jos — dureaza 30 de secunde.')}</p>`);
    for (const row of document.querySelectorAll('#ru-list .u-row')) {
      const rule = d.rules.find((r) => r.id === row.dataset.id);
      row.querySelector('.ru-del').onclick = async () => {
        if (!confirm(t('Ștergi regula "{nume}"?', { nume: rule.nume }))) return;
        await fetch(`/api/rules/${rule.id}`, { method: 'DELETE' });
        loadRules();
      };
      row.querySelector('.ru-toggle').onclick = async () => {
        await post('/api/rules', { ...rule, activ: !rule.activ });
        loadRules();
      };
      row.querySelector('.ru-test').onclick = async () => {
        const r = await post(`/api/rules/${rule.id}/test`, {});
        if (r && !r.error) flash('#ru-msg', t('Acțiunile au rulat — vezi Evenimente ✓'));
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
      .map((v) => html`<option value="${v}">${t(EV_RO[v] || EV_RO[v.toLowerCase()] || v)}</option>`)}`;
  } else if (tip === 'persoana') {
    dl.innerHTML = html`${ruMeta.persons.map((p) => html`<option value="${p}">`)}`;
  } else if (tip === 'numar') {
    dl.innerHTML = html`${['cunoscut', 'necunoscut', ...ruMeta.plates]
      .map((p) => html`<option value="${p}">`)}`;
  } else if (tip === 'obiect') {
    dl.innerHTML = html`<option value="biciclist">${t('biciclist (om cu viteză de bicicletă)')}</option>${SET_LABELS.map((l) => html`<option value="${l}">${t(DET_RO[l] || l)}</option>`)}`;
  } else if (tip === 'detectie' || tip === 'intrus' || tip === 'parcare_inceput') {
    dl.innerHTML = html`${SET_LABELS.map((l) => html`<option value="${l}">${t(DET_RO[l] || l)}</option>`)}`;
  } else if (tip === 'trecere') {
    // liniile desenate pe camere (Camere → Linii/zone): „poarta”, „poarta:intra”, „poarta:iese”
    const opts = [['intra', t('orice linie, sens intră')], ['iese', t('orice linie, sens iese')]];
    for (const [cam, linii] of Object.entries(ruLinii.linii || {})) for (const l of linii) {
      const c = S.cameras.find((x) => x.id === +cam); const cn = c ? c.name : t('cam {id}', { id: cam });
      opts.push([l.name, t('{linie} ({cam}), orice sens', { linie: l.name, cam: cn })],
        [`${l.name}:intra`, t('{linie} ({cam}), intră', { linie: l.name, cam: cn })],
        [`${l.name}:iese`, t('{linie} ({cam}), iese', { linie: l.name, cam: cn })]);
    }
    dl.innerHTML = html`${opts.map(([v, txt]) => html`<option value="${v}">${txt}</option>`)}`;
  } else if (tip === 'intrare_zona') {
    const opts = [];
    for (const [cam, zone] of Object.entries(ruLinii.zone || {})) for (const z of zone) {
      const c = S.cameras.find((x) => x.id === +cam); opts.push([z.name, `${z.name} (${c ? c.name : t('cam {id}', { id: cam })})`]);
    }
    dl.innerHTML = html`${opts.map(([v, txt]) => html`<option value="${v}">${txt}</option>`)}`;
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
  $('#ru-form-title').textContent = t('Modifici: {nume}', { nume: r.nume });
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
  $('#ru-form-title').textContent = t('Adaugă o regula');
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
    const act = cb.dataset.act;
    if (act === 'sirena') actiuni.push({ tip: act, volum: +$('#ru-a-vol').value, repetari: +$('#ru-a-rep').value });
    else if (act === 'strobo') actiuni.push({ tip: act, secunde: +$('#ru-a-sec').value });
    else if (act === 'anunt') actiuni.push({ tip: act, text: $('#ru-a-text').value.trim() });
    else if (act === 'releu') actiuni.push({ tip: act, port: +$('#ru-a-port').value, secunde: +$('#ru-a-rsec').value });
    else if (act === 'notificare') actiuni.push({ tip: act, text: $('#ru-n-text').value.trim() });
    else if (act === 'mqtt') actiuni.push({ tip: act, topic: $('#ru-m-topic').value.trim(),
                                            payload: $('#ru-m-payload').value.trim() });
    else if (act === 'mod') actiuni.push({ tip: act, mod: $('#ru-a-mod').value });
    else actiuni.push({ tip: act });
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
  if (r && !r.error) { clearRuleForm(); flash('#ru-msg', t('Regula salvată ✓')); loadRules(); }
});

export { loadFaces, loadPlates, loadRules, loadTrack, regulaDin };
