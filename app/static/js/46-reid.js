/* ====================================================================
   „Gaseste-l pe toate camerele”: omul dintr-o poza (sau dintr-o aparitie gasita) e cautat
   dupa silueta si haine (re-ID, OSNet) in arhiva de corpuri de pe zile; aparitiile vin pe
   zile, cu camera, ora, similaritatea si crop-ul; click pe crop = fisa / redarea momentului,
   „cauta dupa acesta” = lant de cautari. Modul ES al interfetei; intrarea e app/static/app.js.
   ==================================================================== */
import { S } from './state.js';
import { $, html, lista } from './00-core.js';
import { openFisa } from './45-fisa.js';

let reidUltim = null;

async function reidCauta(body) {
  const dlg = $('#reid'); if (!dlg) return;
  reidUltim = body;
  $('#reid-info').textContent = 'caut…'; $('#reid-list').innerHTML = '';
  if (!dlg.open) dlg.showModal();
  let d;
  try {
    const q = new URLSearchParams({ ...body, zile: String(+$('#reid-zile').value || 7) });
    d = await (await fetch('/api/reid/cauta?' + q)).json();
  } catch { d = { error: 'rețea' }; }
  if (!d || d.error) { $('#reid-info').textContent = (d && d.error) || 'eșuat'; return; }
  const q = $('#reid-q');
  if (d.interogare) { q.src = 'data:image/jpeg;base64,' + d.interogare; q.classList.remove('hidden'); } else q.classList.add('hidden');
  const ap = d.aparitii || [], a = d.arhiva || {};
  $('#reid-info').textContent = ap.length
    ? `${ap.length} apariții în ${$('#reid-zile').selectedOptions[0].textContent} · arhiva: ${a.esantioane_7_zile || 0} eșantioane în 7 zile`
    : `nimic asemănător în ${$('#reid-zile').selectedOptions[0].textContent} (arhiva are ${a.esantioane_7_zile || 0} eșantioane în 7 zile; re-ID-ul eșantionează doar camerele urmărite)`;
  // pe zile, cele mai noi primele; in zi, dupa ora
  const zile = [...new Set(ap.map((x) => x.zi))].sort().reverse();
  $('#reid-list').innerHTML = html`${zile.map((zi) => html`<div class="reid-zi">${zi}</div>${
    lista(ap.filter((x) => x.zi === zi).sort((p, r) => r.t - p.t), (x) => html`
      <div class="reid-item" data-zi="${x.zi}" data-fn="${x.fn}" data-cam="${x.cam}" data-t="${x.t}">
        <img src="${x.crop}" alt="" loading="lazy" title="deschide momentul">
        <span class="r-main"><span><span class="r-sim">${Math.round(x.sim * 100)}%</span> · ${x.cam_nume} · ${x.ora}${x.cine ? ' · ' + x.cine : ''}</span></span>
        <span class="r-acts"><button class="link-btn r-fisa">fișă</button><button class="link-btn r-mai" title="Caută mai departe după această apariție">caută după acesta</button></span>
      </div>`, '')}`)}`;
  for (const it of $('#reid-list').querySelectorAll('.reid-item')) {
    const deschide = () => {
      const t = new Date(+it.dataset.t * 1000), p = (n) => String(n).padStart(2, '0');
      const ts = `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
      $('#reid').close();
      openFisa({ cam: +it.dataset.cam, t: ts, tip: 'detectie', det: 'person', poza: null });
    };
    it.querySelector('img').onclick = deschide; it.querySelector('.r-fisa').onclick = deschide;
    it.querySelector('.r-mai').onclick = () => reidCauta({ crop_zi: it.dataset.zi, crop_fn: it.dataset.fn });
  }
}

$('#reid') && (() => {
  $('#reid-close').onclick = () => $('#reid').close();
  $('#reid').onclick = (e) => { if (e.target.id === 'reid') $('#reid').close(); };
  $('#reid-zile').onchange = () => { if (reidUltim) reidCauta(reidUltim); };
  document.addEventListener('reid-cauta', (e) => reidCauta({ poza: e.detail.poza }));
})();

export { reidCauta };
