// Regresii pe modulele reale: intrebari globale/galerie, camera in cerere, clip si XSS.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { startStatic, installApi } = require('./ui-fixtures');

(async () => {
  const srv = await startStatic();
  const browser = await chromium.launch().catch(async e => { await srv.close(); throw e; });
  const ctx = await browser.newContext({ bypassCSP: true, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const requests = [];
  const answer = {
    concept: 'masina de gunoi', raspuns: 'Prima apariție găsită: ieri la 09:00. <img src=x onerror=alert(1)>',
    period: 'între 2026-11-12 00:00 și 2026-11-13 00:00',
    coverage: { saved: 2, indexed: 1, missing: 1, video_complete: false },
    aparitii: [{ inceput: '2026-11-12 09:00:00', sfarsit: '2026-11-12 09:00:00', camere: [1],
      cover_cam: 1, cover_t: '2026-11-12 09:00:00', poze: 1, weak: true, scor: .07, coperta: null },
      { inceput: '2026-11-12 12:00:00', sfarsit: '2026-11-12 12:00:00', camere: [1],
        cover_cam: 1, cover_t: '2026-11-12 12:00:00', poze: 1, weak: false, scor: .12, coperta: null }]
  };
  await installApi(page, {
    '/api/search': ({url}) => { requests.push(url); return answer; },
    '/api/plates': { reads: [] }, '/api/faces/history': { history: [] },
    '/api/journal': { events: [] }
  });
  try {
    await page.goto(srv.origin, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('html[data-gata="2"]');
    await page.fill('#q-global', 'cand a trecut prima data masina de gunoi ieri?');
    await page.press('#q-global', 'Enter');
    await page.waitForSelector('#cauta .ask-answer');
    assert.match(await page.textContent('#cauta .ask-answer'), /Prima apariție/);
    assert.match(await page.textContent('#cauta-info'), /2026-11-12/);
    assert.equal(await page.locator('#cauta .ask-answer img').count(), 0);
    assert.equal(await page.locator('#cauta .q-item').count(), 2);
    assert.match(await page.locator('#cauta .q-item').first().textContent(), /09:00:00/);
    assert.match(await page.locator('#cauta .q-item').first().textContent(), /Potrivire slabă/);
    await page.locator('#cauta .q-item').first().click();
    await page.waitForSelector('#fisa[open]');
    console.log('PASS: intrebarea globala arata raspunsul, perioada si fisa; HTML-ul e escapate');
    await page.evaluate(() => { document.querySelector('#fisa').close(); });
    // Activam ecranul galeriei fara sa dependem de navigarea principala.
    await page.evaluate(() => {
      for (const e of document.querySelectorAll('main > section, .ev-pane')) e.classList.remove('hidden');
      const al = document.querySelector('#al');
      for (let e = al; e; e = e.parentElement) { e.classList.remove('hidden'); e.style.display = 'block'; }
      const c = document.querySelector('#al-cam');
      if (!c.querySelector('option[value="1"]')) c.add(new Option('Poarta', '1'));
      c.value = '1';
    });
    await page.fill('#sem-q', 'cand a trecut masina de gunoi ieri?');
    await page.click('#sem-go');
    await page.waitForSelector('#al-grid .ask-answer');
    assert.equal(requests.at(-1).searchParams.get('cam'), '1');
    assert.equal(await page.locator('#al-grid .clip-btn').count(), 2);
    assert.match(await page.textContent('#al-info'), /2026-11-12/);
    assert.deepEqual(errors, []);
    console.log('PASS: galeria foloseste acelasi endpoint, camera ajunge la server si dovada are clip');
  } finally { await ctx.close(); await browser.close(); await srv.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
