// Modulele reale: verdict, nota, recuperare dupa redeschidere, arhiva si roluri.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { startStatic, installApi } = require('./ui-fixtures');

(async () => {
  const server = await startStatic();
  const browser = await chromium.launch().catch(async error => { await server.close(); throw error; });
  let verdict = null, note = '', fail = false;
  const image = { day: '2026-11-13', time: '10:00:00', cam: 1, fn: '100000_cam1_car.jpg',
    sursa: 'detectie', label: 'car', _fingerprint: 'version-one', url: '/snapshots/test.jpg', score: .08 };
  const stats = () => verdict ? { [verdict]: 1 } : {};
  const context = await browser.newContext({ bypassCSP: true, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const calls = await installApi(page, {
    '/api/search': { kind: 'images', q: 'mașină de gunoi', results: [image] },
    '/api/plates': { reads: [] }, '/api/faces/history': { history: [] }, '/api/journal': { events: [] },
    '/api/semantic/review': ({ url }) => ({ concept: 'mașină de gunoi',
      results: [{ ...image, feedback: verdict, feedback_note: note }], stats: stats(),
      more: url.searchParams.get('mode') === 'archive' && url.searchParams.get('offset') === '0',
      concepts: [{ title: 'mașină de gunoi', examples: verdict ? 1 : 0 }] }),
    '/api/semantic/review/feedback': ({ method, body }) => {
      if (fail) return { _status: 500, _json: { error: 'Salvare indisponibilă' } };
      verdict = method === 'DELETE' ? null : body.verdict;
      note = method === 'DELETE' ? '' : body.note;
      return { ok: true, feedback: verdict ? { verdict, note } : null, stats: stats() };
    },
  });
  try {
    await page.goto(server.origin, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('html[data-gata="2"]');
    await page.fill('#q-global', 'mașină de gunoi'); await page.press('#q-global', 'Enter');
    await page.waitForSelector('#cauta .q-item'); await page.click('#cauta-learn');
    await page.waitForSelector('.semantic-review-card');
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('#semantic-review')).color === getComputedStyle(document.body).color), true);
    const card = page.locator('.semantic-review-card').first();
    await card.locator('[data-note]').fill('Vidanjă <img src=x onerror=alert(1)>');
    await card.locator('[data-verdict=no]').click();
    await page.waitForFunction(() => document.querySelector('.semantic-review-card').dataset.verdict === 'no');
    const saved = calls.findLast(c => c.path === '/api/semantic/review/feedback');
    assert.equal(saved.body.asset._fingerprint, 'version-one');
    assert.equal(saved.body.concept, 'mașină de gunoi');
    assert.match(await page.textContent('#semantic-review-count'), /1 greșite/);
    await page.click('#semantic-review-close'); await page.click('#cauta-learn');
    await page.waitForSelector('.semantic-review-card[data-verdict=no]');
    assert.equal(await card.locator('[data-note]').inputValue(), 'Vidanjă <img src=x onerror=alert(1)>');
    assert.equal(await page.locator('.semantic-review-card label img').count(), 0);
    await card.locator('[data-note]').fill('Dubă albă'); await card.locator('[data-save-note]').click();
    await page.waitForFunction(() => document.querySelector('[data-state]').textContent === 'Verdict salvat');
    assert.equal(note, 'Dubă albă');
    fail = true; await card.locator('[data-verdict=yes]').click();
    await page.waitForSelector('#toast');
    await page.waitForFunction(() => document.querySelector('#toast').textContent === 'Salvare indisponibilă');
    assert.equal(await card.getAttribute('data-verdict'), 'no');
    fail = false; await card.locator('[data-verdict=unknown]').click();
    await page.waitForSelector('.semantic-review-card[data-verdict=unknown]');
    await card.locator('[data-forget]').click();
    await page.waitForFunction(() => document.querySelector('.semantic-review-card').dataset.verdict === '');
    assert.equal(verdict, null);
    await page.fill('#semantic-review-q', 'alt concept');
    assert.equal(await page.locator('#semantic-review-grid').evaluate(e => e.inert), true);
    await page.press('#semantic-review-q', 'Enter');
    await page.waitForSelector('.semantic-review-card');
    assert.equal(await page.locator('#semantic-review-grid').evaluate(e => e.inert), false);
    await page.selectOption('#semantic-review-mode', 'archive');
    await page.waitForSelector('.semantic-review-card');
    await page.waitForFunction(() => !document.querySelector('#semantic-review-next').disabled);
    await page.click('#semantic-review-next');
    await page.waitForFunction(() => document.querySelector('#semantic-review-info').textContent.includes('101'));
    assert.equal(calls.findLast(c => c.path === '/api/semantic/review').body, null);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.querySelector('#semantic-review').scrollWidth <= document.querySelector('#semantic-review').clientWidth + 2), true);
    assert.deepEqual(errors, []);
    console.log('PASS: verdict + nota, persistenta UI, erori vizibile, anulare, arhiva paginata, XSS si telefon');
    for (const role of ['viewer', 'operator']) {
      const restricted = await browser.newContext({ bypassCSP: true }); const p = await restricted.newPage();
      await installApi(p, { '/api/me': { user: role, role, csrf: 'x' } });
      await p.goto(server.origin, { waitUntil: 'domcontentloaded' }); await p.waitForSelector('html[data-gata="2"]');
      assert.equal(await p.locator('#sem-learn').isVisible(), false);
      assert.equal(await p.evaluate(() => getComputedStyle(document.querySelector('#cauta-learn')).display), 'none');
      await restricted.close();
    }
    console.log('PASS: invatarea este disponibila numai administratorului');
  } finally { await context.close(); await browser.close(); await server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
