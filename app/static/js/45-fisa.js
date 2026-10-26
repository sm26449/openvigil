/* ====================================================================
   Fisa evenimentului: un singur loc pentru orice eveniment (din flux, jurnal, incidente):
   poza, clipul, ce / cine / unde / cand, si toate actiunile — reda momentul, confirma / fals,
   la dosar, export 30 s, regula din asta. Dialog nativ (Esc, focus, inertizare gratis).
   Deschidere: openFisa({cam, t: 'YYYY-MM-DD HH:MM:SS', tip, sev, id, det, poza, cat}) sau orice
   element cu data-fisa='{...json...}' (delegare pe document).
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js.
   ==================================================================== */
import { S } from './state.js';
import { $, ADMIN, CAN_ACT, DET_RO, html, t } from './00-core.js';
import { post } from './10-live.js';
import { AL_RO, EV_TIP_RO, evCompact, openCases, openClip } from './40-evenimente.js';
import { regulaDin } from './70-ai.js';

const SEV_RO = { info: 'informativ', atentie: 'atenție', critic: 'critic' };
let fisaEv = null;

function fisaCe(ev) {
  const tip = t(EV_TIP_RO[ev.tip] || AL_RO[ev.tip] || DET_RO[ev.tip] || ev.tip || 'Eveniment');
  const det = ev.det ? t(DET_RO[ev.det] || AL_RO[ev.det] || ev.det) : '';
  return det && det !== tip ? `${tip}: ${det}` : tip;
}
function fisaSec(t) { const [h, m, s] = t.slice(11).split(':').map(Number); return h * 3600 + m * 60 + s; }

function openFisa(ev) {
  const dlg = $('#fisa'); if (!dlg || !ev || !ev.t) return;
  fisaEv = ev;
  const cam = S.cameras.find((c) => c.id === +ev.cam);
  const zi = ev.t.slice(0, 10), ora = ev.t.slice(11, 19);
  $('#fisa-title').textContent = fisaCe(ev);
  $('#fisa-sev').textContent = SEV_RO[ev.sev] ? t(SEV_RO[ev.sev]) : '';
  $('#fisa-sev').className = 'chip sev-' + (ev.sev || 'info') + (ev.sev ? '' : ' hidden');
  $('#fisa-meta').innerHTML = html`<div><b>${t('Unde')}</b> ${cam ? cam.name : 'cam ' + ev.cam}</div>
    <div><b>${t('Când')}</b> ${zi} ${ora}</div>${ev.cine ? html`<div><b>${t('Cine')}</b> ${ev.cine}</div>` : ''}${
    ev.stare ? html`<div><b>${t('Stare')}</b> ${ev.stare === 'confirmat' ? t('confirmat') : ev.stare === 'fals' ? t('alarmă falsă') : t('de tratat')}${ev.tratat_de ? ' ' + t('de {cine}', { cine: ev.tratat_de }) : ''}</div>` : ''}`;
  const img = $('#fisa-img');
  if (ev.poza) { img.src = ev.poza; img.classList.remove('hidden'); } else { img.removeAttribute('src'); img.classList.add('hidden'); }
  $('#fisa-noimg').classList.toggle('hidden', !!ev.poza);
  $('#fisa-tratare').classList.toggle('hidden', !(ev.id && CAN_ACT));
  $('#fisa-regula').classList.toggle('hidden', !ADMIN);
  $('#fisa-reid').classList.toggle('hidden', !(ev.poza && /\/snapshots\/|\/api\/flux\/thumb\//.test(ev.poza)));
  const iv = `channel=${ev.cam}&start=${evCompact(ev.t, -30)}&end=${evCompact(ev.t, 30)}`;
  $('#fisa-export').href = `/api/export?${iv}&wm=1`;
  $('#fisa-sec').value = fisaSec(ev.t);
  dlg.showModal();
}

function fisaInchide() { const d = $('#fisa'); if (d && d.open) d.close(); }

$('#fisa') && (() => {
  $('#fisa-close').onclick = fisaInchide;
  $('#fisa').onclick = (e) => { if (e.target.id === 'fisa') fisaInchide(); };
  $('#fisa-play').onclick = () => { if (!fisaEv) return; fisaInchide(); location.hash = `#pb/${fisaEv.cam}/${fisaEv.t.slice(0, 10)}/${fisaSec(fisaEv.t)}`; };
  $('#fisa-clip').onclick = () => { if (!fisaEv) return; fisaInchide(); openClip(+fisaEv.cam, fisaEv.t, fisaCe(fisaEv) + ' — ' + fisaEv.t); };
  $('#fisa-reid').onclick = () => { if (!fisaEv) return; fisaInchide(); document.dispatchEvent(new CustomEvent('reid-cauta', { detail: { poza: fisaEv.poza } })); };
  $('#fisa-dosar').onclick = () => {
    if (!fisaEv) return;
    S.pendingProba = { cam: +fisaEv.cam, start: evCompact(fisaEv.t, -30), end: evCompact(fisaEv.t, 30), nota: fisaCe(fisaEv), event_id: fisaEv.id || null };
    fisaInchide(); openCases(true);
  };
  for (const [id, stare] of [['fisa-ok', 'confirmat'], ['fisa-fals', 'fals']]) {
    $('#' + id).onclick = async () => {
      if (!fisaEv || !fisaEv.id) return;
      const d = await post(`/api/incidents/${fisaEv.id}/stare`, { stare });
      if (d && !d.error) { fisaEv.stare = stare; openFisa(fisaEv); document.dispatchEvent(new CustomEvent('incident-schimbat')); }
    };
  }
  $('#fisa-regula').onclick = () => {
    if (!fisaEv) return;
    fisaInchide();
    const cn = (S.cameras.find((c) => c.id === +fisaEv.cam) || {}).name || 'cam ' + fisaEv.cam;
    regulaDin({ tip: fisaEv.tip, valoare: fisaEv.det || '', cam: fisaEv.cam, nume: t('{ce} pe {cam}', { ce: fisaCe(fisaEv), cam: cn }) });
  };
})();

// orice element cu data-fisa (JSON) deschide fisa — butoanele din jurnal, incidente, flux
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-fisa]');
  if (!el) return;
  e.preventDefault();
  try { openFisa(JSON.parse(el.dataset.fisa)); } catch {}
});

export { openFisa, fisaInchide };
