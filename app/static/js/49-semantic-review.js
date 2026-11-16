// Exemple umane pentru cautarea vizuala: candidati, arhiva si exemple pastrate.
import { $, ADMIN, html, lista, t, toast } from './00-core.js';
import { S } from './state.js';

let generation = 0, rows = [], concept = '', offset = 0;
const dialog = $('#semantic-review');
function counts(stats = {}) {
  $('#semantic-review-count').textContent = t('Exemple pentru acest concept: {yes} corecte · {no} greșite · {unknown} neclare', {
    yes: stats.yes || 0, no: stats.no || 0, unknown: stats.unknown || 0,
  });
}
function state(card, row) {
  card.dataset.verdict = row.feedback || '';
  for (const b of card.querySelectorAll('[data-verdict]')) b.setAttribute('aria-pressed', String(b.dataset.verdict === row.feedback));
  card.querySelector('[data-forget]').disabled = !row.feedback;
  card.querySelector('[data-state]').textContent = row.feedback
    ? t('Verdict salvat') : t('Fără verdict');
}
async function save(card, row, verdict, remove = false) {
  if (card.dataset.busy === 'true') return;
  const gen = generation, selectedConcept = concept;
  const note = card.querySelector('[data-note]').value;
  card.dataset.busy = 'true';
  for (const button of card.querySelectorAll('[data-verdict], [data-forget]')) button.disabled = true;
  card.querySelector('[data-state]').textContent = t('Se salvează…');
  try {
    const response = await fetch('/api/semantic/review/feedback', {
      method: remove ? 'DELETE' : 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ concept: selectedConcept, asset: { day: row.day, cam: row.cam, fn: row.fn, sursa: row.sursa || 'detectie', _fingerprint: row._fingerprint }, verdict, note }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw Error(data.error || t('Salvarea nu a reușit.'));
    if (gen !== generation) return;
    row.feedback = remove ? null : verdict;
    row.feedback_note = remove ? '' : note;
    if (remove) card.querySelector('[data-note]').value = '';
    counts(data.stats);
    state(card, row);
  } catch (error) {
    if (gen === generation) { toast(error.message, true); state(card, row); }
  } finally {
    card.dataset.busy = 'false';
    for (const button of card.querySelectorAll('[data-verdict]')) button.disabled = false;
    card.querySelector('[data-forget]').disabled = !row.feedback;
  }
}
async function loadReview(reset = true) {
  if (!ADMIN || !dialog.open) return;
  const q = $('#semantic-review-q').value.trim();
  if (!q) { toast(t('Scrie descrierea pe care vrei să o învețe căutarea.'), true); return; }
  if (reset) offset = 0;
  const gen = ++generation;
  const mode = $('#semantic-review-mode').value;
  $('#semantic-review-day').classList.toggle('hidden', mode !== 'archive');
  const params = new URLSearchParams({ q, mode, days: $('#semantic-review-days').value, offset: String(offset) });
  if ($('#semantic-review-cam').value) params.set('cam', $('#semantic-review-cam').value);
  if (mode === 'archive') params.set('day', $('#semantic-review-day').value);
  $('#semantic-review-info').textContent = t('Caut...');
  $('#semantic-review-next').disabled = true;
  $('#semantic-review-prev').disabled = true;
  $('#semantic-review-grid').innerHTML = '';
  $('#semantic-review-grid').inert = false;
  try {
    const response = await fetch('/api/semantic/review?' + params);
    const data = await response.json();
    if (gen !== generation) return;
    if (!response.ok || data.error) throw Error(data.error || t('Căutarea nu a mers — reîncearcă'));
    concept = data.concept;
    rows = data.results || [];
    $('#semantic-review-q').value = concept;
    $('#semantic-review-concepts').innerHTML = lista(data.concepts || [], c => html`<option value="${c.title}"></option>`, '');
    counts(data.stats);
    $('#semantic-review-info').textContent = mode === 'archive'
      ? t('{n} imagini din arhivă · ziua {day} · de la {offset}', { n: rows.length, day: $('#semantic-review-day').value, offset: offset + 1 })
      : mode === 'saved' ? t('{n} exemple salvate afișate', { n: rows.length })
      : t('{n} candidați în ultimele {days} zile, înaintea corecțiilor tale', { n: rows.length, days: $('#semantic-review-days').value });
    $('#semantic-review-grid').innerHTML = lista(rows, (row, i) => html`
      <article class="semantic-review-card" data-index="${i}">
        <button class="semantic-review-zoom" aria-label="${t('Mărește imaginea')}"><img src="${row.url}" loading="lazy" alt="${t('Imagine de verificat')}"></button>
        <p>${row.day} ${row.time} · ${S.cameras.find(c => c.id === row.cam)?.name || t('cam {id}', { id: row.cam })}</p>
        <div class="semantic-review-choices" role="group" aria-label="${t('Se potrivește descrierii?')}">
          <button data-verdict="yes" aria-pressed="false">${t('Corect')}</button>
          <button data-verdict="no" aria-pressed="false">${t('Greșit')}</button>
          <button data-verdict="unknown" aria-pressed="false">${t('Neclar')}</button>
        </div>
        <label>${t('Observație (opțional)')}<input data-note maxlength="500" value="${row.feedback_note || ''}"></label>
        <div class="semantic-review-foot"><span data-state class="hint" aria-live="polite"></span><button data-save-note class="link-btn">${t('Salvează observația')}</button><button data-forget class="link-btn">${t('Elimină exemplul')}</button></div>
      </article>`, t('Nu sunt imagini în această selecție.'));
    for (const card of $('#semantic-review-grid').querySelectorAll('article')) {
      const row = rows[+card.dataset.index];
      state(card, row);
      for (const button of card.querySelectorAll('[data-verdict]')) button.onclick = () => save(card, row, button.dataset.verdict);
      card.querySelector('[data-forget]').onclick = () => save(card, row, null, true);
      card.querySelector('[data-note]').oninput = () => { card.querySelector('[data-state]').textContent = t('Observație nesalvată'); };
      card.querySelector('[data-save-note]').onclick = () => {
        if (row.feedback) save(card, row, row.feedback);
        else toast(t('Alege mai întâi Corect, Greșit sau Neclar.'), true);
      };
      card.querySelector('.semantic-review-zoom').onclick = () => {
        $('#semantic-review-photo-img').src = row.url;
        $('#semantic-review-photo-caption').textContent = `${row.day} ${row.time}`;
        $('#semantic-review-photo').showModal();
      };
    }
    $('#semantic-review-next').disabled = !data.more;
    $('#semantic-review-prev').disabled = offset === 0;
  } catch (error) { if (gen === generation) $('#semantic-review-info').textContent = error.message; }
}
function openSemanticReview(q, { days = 90, cam = null } = {}) {
  if (!ADMIN || !dialog) return;
  $('#semantic-review-q').value = q || '';
  $('#semantic-review-days').value = String([7, 30, 90].includes(+days) ? days : 90);
  $('#semantic-review-cam').innerHTML = html`<option value="">${t('Toate camerele')}</option>` + lista(S.cameras || [], c => html`<option value="${c.id}">${c.name}</option>`, '');
  $('#semantic-review-cam').value = cam ? String(cam) : '';
  $('#semantic-review-mode').value = 'candidates';
  const now = new Date();
  $('#semantic-review-day').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  if (!dialog.open) dialog.showModal();
  loadReview();
}
if (dialog) {
  $('#semantic-review-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { generation++; });
  $('#semantic-review-load').onclick = () => loadReview();
  $('#semantic-review-q').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); loadReview(); } };
  $('#semantic-review-q').oninput = () => {
    generation++;
    $('#semantic-review-grid').inert = true;
    $('#semantic-review-info').textContent = t('Apasă Arată pentru a încărca noua descriere.');
    $('#semantic-review-next').disabled = true;
    $('#semantic-review-prev').disabled = true;
  };
  for (const id of ['mode', 'days', 'cam', 'day']) $('#semantic-review-' + id).onchange = () => loadReview();
  $('#semantic-review-next').onclick = () => { offset += 100; loadReview(false); };
  $('#semantic-review-prev').onclick = () => { offset = Math.max(0, offset - 100); loadReview(false); };
  $('#semantic-review-photo-close').onclick = () => $('#semantic-review-photo').close();
}
export { openSemanticReview };
