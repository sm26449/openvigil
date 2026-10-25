// Test functional al interfetei NVR: navigare, meniuri, formulare, legaturi.
// Ruleaza intr-un browser real (Chromium) si verifica ce vede utilizatorul.
const { chromium } = require('playwright');

// NVR-ul asculta pe 8079 (8080 e ocupat de stack-ul fotovoltaic Multi-Bus Gateway);
// suprascrie cu NVR_BASE daca portul difera pe alt sistem
const BASE = process.env.NVR_BASE || 'http://127.0.0.1:8079';
// contul cu care ruleaza testul (admin); se poate suprascrie din mediu
const USER = process.env.NVR_TEST_USER || 'admin';
const PASS = process.env.NVR_TEST_PASS || '';
if (!PASS) { console.error('seteaza NVR_TEST_USER/NVR_TEST_PASS (contul de admin al instalatiei de test)'); process.exit(2); }
const pass = [], fail = [], warn = [];
const ok = (m) => { pass.push(m); console.log('  ✓ ' + m); };
const bad = (m, d) => { fail.push(m + (d ? ' — ' + d : '')); console.log('  ✗ ' + m + (d ? ' — ' + d : '')); };
const note = (m) => { warn.push(m); console.log('  ⚠ ' + m); };

(async () => {
  const browser = await chromium.launch();
  // bypassCSP: page.evaluate foloseste eval, pe care CSP-ul paginii il refuza
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 }, bypassCSP: true });
  const page = await ctx.newPage();
  // dialogurile native (alert/confirm) sunt inchise, dar textul lor ajunge in jurnalul rularii
  // (inchiderea e amanata un tick: handler-ele `page.once('dialog', accept)` ale pasilor ruleaza intai)
  page.on('dialog', (d) => { console.log(`  [dialog ${d.type()}] ${d.message().slice(0, 120)}`); setTimeout(() => d.dismiss().catch(() => {}), 0); });

  // strangem erorile din consola si cererile esuate
  const errors = [], streamErrors = [], failedReq = [];
  // Erorile care vin de la go2rtc (prin releu, portul 8076) se tin separat: un flux cerut inainte sa
  // porneasca ffmpeg-ul da 502, iar playerul reincearca si merge. Nu e o defectiune a
  // aplicatiei, dar nici nu o ascundem — apare ca observatie, nu ca esec.
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    // URL-ul resursei care a picat — fara el, un "502" generic nu se poate ancheta
    const url = ((m.location() || {}).url || '').replace(BASE, '');
    const t = m.text().slice(0, 140) + (url ? ` [${url.slice(0, 80)}]` : '');
    (/:8076|go2rtc/.test(t) ? streamErrors : errors).push(t);
  });
  page.on('pageerror', (e) => errors.push('EXCEPTIE: ' + String(e).slice(0, 140)));
  page.on('requestfailed', (r) => {
    const u = r.url();
    // ERR_ABORTED = cerere anulata de o navigare a testului insusi, nu o defectiune
    if (!u.includes(':8076') && r.failure()?.errorText !== 'net::ERR_ABORTED') {
      failedReq.push(`${r.method()} ${u.replace(BASE, '')} (${r.failure()?.errorText})`);
    }
  });

  console.log('\n== 0. Autentificare ==');
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 });
  page.url().includes('/login') ? ok('neautentificat -> pagina de login')
                                : bad('login', 'nu se cere autentificare: ' + page.url());
  if (page.url().includes('/login')) {
    await page.fill('input[name="user"]', USER);
    await page.fill('input[name="password"]', PASS);
    await Promise.all([page.waitForNavigation(), page.click('button[type="submit"]')]);
    !page.url().includes('/login') ? ok(`login reusit ca "${USER}"`)
                                   : bad('login', 'a ramas pe pagina de login (parola?)');
  }

  console.log('\n== 1. Incarcarea paginii ==');
  // 'networkidle' e nesigur cu 18 stream-uri live (reteaua nu tace niciodata);
  // asteptam DOM-ul si apoi explicit tile-urile din grila.
  const resp = await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 });
  resp.status() === 200 ? ok('pagina raspunde 200') : bad('pagina', 'status ' + resp.status());
  // grila vine din /api/cameras, care sub incarcare (detectie + 18 stream-uri pe un
  // singur proces) poate raspunde in ~20-25s; asteptam generos ca sa nu fie fals-negativ.
  await page.waitForSelector('#grid .tile', { timeout: 35000 }).catch(() => {});
  await page.waitForTimeout(1500);
  const camCount = await page.locator('#grid .tile').count();
  camCount === 18 ? ok(`grila afiseaza toate cele ${camCount} camere`)
                  : bad('grila', `${camCount} tile-uri in loc de 18`);
  ok(`titlu: "${await page.title()}"`);

  console.log('\n== 2. Navigarea intre taburi ==');
  const TABS = [['tab-live', 'live'], ['tab-pb', 'pb'], ['tab-ev', 'ev'],
                ['tab-ai', 'ai'], ['tab-cams', 'cams'], ['tab-set', 'set']];
  for (const [btn, sec] of TABS) {
    await page.click('#' + btn);
    await page.waitForTimeout(900);
    const vis = await page.locator('#' + sec).isVisible();
    // esential: doar sectiunea curenta trebuie sa fie vizibila
    const others = [];
    for (const [, s] of TABS) {
      if (s !== sec && await page.locator('#' + s).isVisible()) others.push(s);
    }
    if (!vis) bad(`tab ${sec}`, 'sectiunea nu devine vizibila');
    else if (others.length) bad(`tab ${sec}`, 'raman vizibile si: ' + others.join(', '));
    else ok(`tab ${sec}: se afiseaza singur, corect`);
  }

  console.log('\n== 3. Setari: navigarea pe sectiuni ==');
  await page.click('#tab-set');
  await page.waitForTimeout(1200);
  const navItems = await page.locator('.snav-item').count();
  navItems === 9 ? ok('9 sectiuni in meniul lateral') : bad('meniu setari', navItems + ' sectiuni');
  for (let i = 0; i < navItems; i++) {
    const item = page.locator('.snav-item').nth(i);
    const name = (await item.textContent()).trim();
    await item.click();
    await page.waitForTimeout(300);
    const paneId = 'pane-' + await item.getAttribute('data-pane');
    const visible = await page.locator('#' + paneId).isVisible();
    const openPanes = await page.locator('.set-pane:visible').count();
    if (visible && openPanes === 1) ok(`sectiunea "${name}" se deschide singura`);
    else bad(`sectiunea "${name}"`, `vizibila=${visible}, panouri deschise=${openPanes}`);
  }

  // cautarea semantica a ramas in Detectie; Face ID si LPR s-au mutat in tabul AI.
  // Sectiunile cu 2+ carduri au acum tab-uri interne — un card vizibil o data.
  await page.locator('.snav-item[data-pane="detectie"]').click();
  await page.waitForTimeout(600);
  const cardTabs = await page.locator('#pane-detectie .card-tabs button').count();
  cardTabs >= 4 ? ok(`Detectie are ${cardTabs} tab-uri interne (un card vizibil o data)`)
                : bad('card-tabs', cardTabs + ' tab-uri in Detectie');
  await page.locator('#pane-detectie .card-tabs button',
                     { hasText: 'semantic' }).click();
  await page.waitForTimeout(300);
  (await page.locator('#sem-on').isVisible())
    ? ok('tab-ul "Cautare semantica" arata cardul lui')
    : bad('semantic', 'lipseste comutatorul #sem-on dupa comutarea tab-ului');
  (await page.locator('#pane-detectie .set-card:visible').count()) === 1
    ? ok('un singur card vizibil o data in sectiunile cu tab-uri')
    : bad('card-tabs', 'mai multe carduri vizibile simultan');

  console.log('\n== 3b. Tabul AI: Persoane / Masini / Reguli / Evenimente ==');
  await page.click('#tab-ai');
  await page.waitForTimeout(1500);
  (await page.locator('#fc-on').isVisible())
    ? ok('Persoane: cardul Face ID e prezent')
    : bad('ai', 'lipseste #fc-on in sectiunea Persoane');
  (await page.locator('#fc-spoof').isVisible())
    ? ok('Persoane: comutatorul de anti-spoofing e prezent')
    : bad('ai', 'lipseste #fc-spoof');
  (await page.locator('#fc-desc').isVisible())
    ? ok('Persoane: comutatorul de semnalmente (VLM) e prezent')
    : bad('ai', 'lipseste #fc-desc');
  await page.locator('.ainav-item[data-pane="auto"]').click();
  await page.waitForTimeout(500);
  (await page.locator('#lp-on').isVisible())
    ? ok('Masini: cardul LPR e prezent')
    : bad('ai', 'lipseste #lp-on in sectiunea Masini');
  await page.locator('.ainav-item[data-pane="reguli"]').click();
  await page.waitForTimeout(500);
  (await page.locator('#ru-save').isVisible())
    ? ok('Reguli: formularul de regula e prezent')
    : bad('ai', 'lipseste formularul de reguli');
  (await page.locator('#ru-groups option').count()) > 0
    ? ok('Reguli: zonele (grupurile de camere) sunt disponibile')
    : bad('reguli', 'lipsesc grupurile din formular');
  (await page.locator('#ru-days input').count()) === 7
    ? ok('Reguli: programul are cele 7 zile ale saptamanii')
    : bad('reguli', 'lipsesc zilele saptamanii');
  (await page.locator('.ru-actions input[data-act="mqtt"]').count()) === 1
    ? ok('Reguli: actiunea MQTT e disponibila')
    : bad('reguli', 'lipseste actiunea MQTT');
  // creare regula din UI, apoi stergere
  await page.fill('#ru-name', 'regula-test-ui');
  await page.selectOption('#ru-trigger', 'detectie');
  await page.fill('#ru-value', 'dog');
  await page.locator('.ru-actions input[data-act="notificare"]').check();
  await page.click('#ru-save');
  await page.waitForTimeout(800);
  (await page.locator('#ru-list .u-row', { hasText: 'regula-test-ui' }).count()) >= 1
    ? ok('regula creata din UI apare in lista')
    : bad('reguli', 'regula creata nu apare');
  // sterge TOATE regulile de test (o rulare picata anterior poate lasa duplicate);
  // altfel filtrul de mai jos vede 2 randuri si pica pe strict-mode
  let guard = 0;
  while (await page.locator('#ru-list .u-row', { hasText: 'regula-test-ui' }).count() > 0 && guard++ < 10) {
    page.once('dialog', (d) => d.accept());
    await page.locator('#ru-list .u-row', { hasText: 'regula-test-ui' })
      .first().locator('.ru-del').click();
    await page.waitForTimeout(600);
  }
  (await page.locator('#ru-list .u-row', { hasText: 'regula-test-ui' }).count()) === 0
    ? ok('regula de test s-a sters')
    : bad('reguli', 'regula de test nu s-a sters');
  // jurnalul de evenimente sta acum in tabul Evenimente (sub-panoul Jurnal)
  await page.click('#tab-ev');
  await page.click('#ev-nav button[data-evpane="jurnal"]');
  await page.waitForSelector('#ev-list .ev-row', { timeout: 8000 }).catch(() => {});   // se incarca la deschidere
  await page.waitForTimeout(300);
  (await page.locator('#evjurnal').isVisible() && await page.locator('#ev-grup').isChecked())
    ? ok('Evenimente: vederea implicita e pe episoade cu durata')
    : bad('jurnal', 'comutatorul de episoade nu e activ implicit');
  const evRows = await page.locator('#ev-list .ev-row').count();
  if (evRows > 0) {
    ok(`Evenimente: ${evRows} episoade afisate`);
    (await page.locator('#ev-list .ev-dur').count()) > 0
      ? ok('episoadele au durata afisata')
      : bad('jurnal', 'lipseste durata pe episoade');
    (await page.locator('#ev-list a[href*="/api/export"]').count()) > 0
      ? ok('episoadele au legatura de descarcare MP4')
      : bad('jurnal', 'lipseste descarcarea MP4');
    // exportul trece prin coada de joburi: click pe MP4 -> job in tava -> gata (fisier descarcat)
    const mp4 = page.locator('#ev-list a.job-link[href*="/api/export?"]').first();
    if (await mp4.count()) {
      const dl = page.waitForEvent('download', { timeout: 40000 }).catch(() => null);
      await mp4.click();
      await page.waitForSelector('#jobs-tray .job', { timeout: 5000 }).catch(() => {});
      (await page.locator('#jobs-tray .job').count()) > 0 ? ok('exportul apare in tava de joburi') : bad('joburi', 'tava goala dupa click pe MP4');
      await page.waitForSelector('#jobs-tray .job.gata, #jobs-tray .job.eroare', { timeout: 40000 }).catch(() => {});
      const st = (await page.locator('#jobs-tray .job.gata').count()) ? 'gata'
        : (await page.locator('#jobs-tray .job.eroare .job-m').textContent().catch(() => 'necunoscut'));
      const d = await dl;
      st === 'gata' && d ? ok(`jobul de export s-a terminat si fisierul s-a descarcat (${d.suggestedFilename()})`)
        : st === 'gata' ? ok('jobul de export s-a terminat (fara eveniment de descarcare in headless)')
        : bad('joburi', 'exportul a esuat: ' + st);
      // un job esuat ramane in tava si acopera coltul din dreapta-jos (bara de salvare din
      // Setari); il inchidem ca restul rularii sa nu depinda de el
      await page.evaluate(() => document.querySelectorAll('#jobs-tray .job-hide').forEach((b) => b.click()));
    }
  } else note('jurnalul de evenimente e inca gol');
  await page.locator('#ev-grup').uncheck();
  await page.waitForTimeout(700);
  (await page.locator('#ev-list .ev-row').count()) >= evRows
    ? ok('vederea bruta arata evenimentele individuale')
    : bad('jurnal', 'vederea bruta are mai putine randuri decat episoadele');
  await page.locator('#ev-grup').check();
  await page.waitForTimeout(800);
  (await page.locator('#ev-csv').getAttribute('href')).includes('journal.csv')
    ? ok('butonul de descarcare CSV e legat corect')
    : bad('jurnal', 'lipseste legatura CSV');
  (await page.locator('#ev-sev').isVisible())
    ? ok('jurnalul are filtru de severitate')
    : bad('severitate', 'lipseste filtrul #ev-sev');
  (await page.locator('#ev-list .sev-dot').count()) > 0
    ? ok('evenimentele au punct de severitate colorat')
    : note('niciun eveniment de afisat pentru severitate');
  const clipBtns = await page.locator('#ev-list .clip-btn').count();
  if (clipBtns > 0) {
    ok(`evenimentele au buton de clip video (${clipBtns})`);
    await page.locator('#ev-list .clip-btn').first().click();
    await page.waitForTimeout(500);
    (await page.locator('#clip-modal:not(.hidden)').count()) === 1
      ? ok('player-ul de clip se deschide la click')
      : bad('clip', 'player-ul nu se deschide');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    (await page.locator('#clip-modal.hidden').count()) === 1
      ? ok('player-ul se inchide cu Escape')
      : bad('clip', 'player-ul nu se inchide');
  } else note('niciun eveniment cu clip (jurnal gol?)');
  await page.waitForSelector('#ev-list a[href*="/api/export/report"]',
                             { timeout: 8000 }).catch(() => {});
  (await page.locator('#ev-list a[href*="/api/export/report"]').count()) > 0
    ? ok('episoadele au fisa probatorie (raport cu SHA-256)')
    : note('niciun episod cu raport (jurnal gol?)');
  await page.click('#tab-ai');
  await page.locator('.ainav-item[data-pane="auto"]').click();
  await page.waitForTimeout(400);
  (await page.locator('#lp-q').isVisible())
    ? ok('istoricul LPR are camp de cautare dupa numar/nume')
    : bad('lpr', 'lipseste cautarea in istoric');

  console.log('\n== 3c. Utilizatori: sectiune proprie, creare si stergere din UI ==');
  await page.click('#tab-set');
  await page.waitForTimeout(600);
  await page.locator('.snav-item[data-pane="utilizatori"]').click();
  await page.waitForTimeout(800);
  (await page.locator('#u-list .u-row').count()) >= 1
    ? ok('lista de conturi se incarca (macar adminul exista)')
    : bad('utilizatori', 'lista e goala');
  await page.fill('#u-name', 'test-ui-user');
  await page.fill('#u-pass', 'parola-ui-123');
  await page.selectOption('#u-role', 'viewer');
  const uResp = page.waitForResponse((r) => r.url().includes('/api/users') && r.request().method() === 'POST',
                                     { timeout: 8000 }).catch(() => null);
  await page.click('#u-save');
  // asteptare pe SELECTOR, nu pe timp fix: 900ms nu ajungeau sub incarcare
  // (flake vazut de 3 ori: 2026-09-02, 2x 2026-09-10)
  await page.waitForSelector('#u-list .u-row[data-user="test-ui-user"]',
                             { timeout: 8000 }).catch(() => {});
  if ((await page.locator('#u-list .u-row[data-user="test-ui-user"]').count()) === 1) {
    ok('utilizator creat din formular apare in lista');
  } else {
    const rp = await uResp;
    bad('utilizatori', 'contul creat nu apare; POST /api/users: '
      + (rp ? `${rp.status()} ${(await rp.text().catch(() => '')).slice(0, 80)}` : 'fara raspuns in 8 s'));
  }
  await page.locator('#u-list .u-row[data-user="test-ui-user"] .u-edit').click({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(300);
  (await page.locator('#u-form-title').textContent()).includes('Modifici: test-ui-user')
    ? ok('"modifica" incarca formularul cu titlul contextual')
    : bad('utilizatori', 'titlul de modificare lipseste');
  await page.click('#u-cancel');
  page.once('dialog', (d) => d.accept());
  await page.locator('#u-list .u-row[data-user="test-ui-user"] .u-del').click();
  await page.waitForTimeout(900);
  (await page.locator('#u-list .u-row[data-user="test-ui-user"]').count()) === 0
    ? ok('utilizatorul de test s-a sters din UI')
    : bad('utilizatori', 'contul de test nu s-a sters');

  console.log('\n== 3d. Panou de incidente (triaj) ==');
  await page.click('#tab-ev');
  await page.click('#ev-nav button[data-evpane="inc"]');
  await page.waitForTimeout(600);
  (await page.locator('#inc').isVisible())
    ? ok('Evenimente → De tratat se deschide')
    : bad('incidente', 'tabul nu se deschide');
  // filtrul "Toate" arata si incidentele tratate -> robust indiferent de starea live
  await page.locator('#inc-filter button', { hasText: 'Toate' }).click();
  await page.waitForSelector('#inc-list .inc-card', { timeout: 8000 }).catch(() => {});
  const incCards = await page.locator('#inc-list .inc-card').count();
  if (incCards > 0) {
    ok(`${incCards} incidente in coada`);
    (await page.locator('#inc-list .inc-card .sev-dot').count()) > 0
      ? ok('incidentele au severitate vizuala')
      : bad('incidente', 'lipseste severitatea');
    (await page.locator('#inc-list .inc-ok').count()) > 0
      ? ok('incidentele au buton de confirmare')
      : note('niciun incident netratat de confirmat');
    // confirma UN incident anume si verifica pe cardul lui ca a trecut la "tratat"
    // (numaratoarea globala e fragila: evenimente live pot sosi intre click si re-render)
    if (await page.locator('#inc-list .inc-ok').count() > 0) {
      const eid = await page.locator('#inc-list .inc-ok').first().getAttribute('data-id');
      await page.locator(`#inc-list .inc-card[data-id="${eid}"] .inc-ok`).click();
      // acelasi card trebuie sa arate butonul de redeschidere (= tratat); asteptam
      // re-render-ul (sub incarcare poate dura), nu un timeout fix
      await page.waitForSelector(`#inc-list .inc-card[data-id="${eid}"] .inc-reopen`,
        { timeout: 10000 })
        .then(() => ok('confirmarea marcheaza incidentul ca tratat'))
        .catch(() => bad('incidente', 'confirmarea nu a avut efect'));
      await page.locator(`#inc-list .inc-card[data-id="${eid}"] .inc-reopen`).click()
        .catch(() => {});   // redeschide, ca sa nu lase starea modificata pe disc
      await page.waitForTimeout(500);
    }
  } else note('niciun incident in coada (liniste)');

  console.log('\n== 3e. Dosare de caz (pachet de probe + export ZIP) ==');
  await page.click('#tab-ev');
  await page.click('#ev-nav button[data-evpane="inc"]');
  await page.waitForTimeout(400);
  await page.locator('#inc-cases').click();
  await page.waitForTimeout(500);
  (await page.locator('#cases-modal').isVisible())
    ? ok('modalul de dosare se deschide')
    : bad('dosare', 'modalul nu se deschide');
  // creeaza un dosar (prin dialogul prompt)
  const titluCaz = 'Test UI ' + Date.now();
  page.once('dialog', (d) => d.accept(titluCaz));
  await page.locator('#cases-new').click();
  await page.waitForTimeout(700);
  const cazVis = await page.locator('.caz-row', { hasText: titluCaz }).count();
  cazVis > 0 ? ok('dosarul nou apare in lista')
             : bad('dosare', 'dosarul creat nu apare');
  // inchide modalul, pune un incident "la dosar" daca exista buton
  await page.locator('#cases-close').click();
  await page.waitForTimeout(300);
  await page.locator('#inc-filter button', { hasText: 'Toate' }).click();
  await page.waitForTimeout(600);
  const dosarBtns = await page.locator('#inc-list .dosar-btn').count();
  if (dosarBtns > 0) {
    ok(`incidentele au buton „la dosar" (${dosarBtns})`);
    await page.locator('#inc-list .dosar-btn').first().click();
    await page.waitForTimeout(500);
    (await page.locator('#cases-modal').isVisible()
      && await page.locator('.caz-add').count() > 0)
      ? ok('„la dosar" deschide alegerea dosarului')
      : bad('dosare', 'nu apare alegerea dosarului');
    await page.locator('.caz-row', { hasText: titluCaz })
      .locator('.caz-add').first().click();
    await page.waitForTimeout(800);
    // redeschide dosarul si verifica ca proba a intrat
    await page.locator('#inc-cases').click();
    await page.waitForTimeout(500);
    await page.locator('.caz-row', { hasText: titluCaz }).locator('.caz-view').click();
    await page.waitForTimeout(600);
    (await page.locator('.caz-row', { hasText: titluCaz })
      .locator('.proba-row').count()) > 0
      ? ok('proba a fost adaugata la dosar')
      : bad('dosare', 'proba nu apare in dosar');
    // butonul ZIP trebuie sa existe acum (dosar cu probe)
    (await page.locator('.caz-row', { hasText: titluCaz })
      .locator('a[href*="/export"]').count()) > 0
      ? ok('dosarul cu probe are buton de export ZIP')
      : bad('dosare', 'lipseste exportul ZIP');
  } else note('niciun incident cu buton „la dosar"');
  // curatare: sterge TOATE dosarele de test (o rulare picata poate lasa orfani);
  // non-fatala — o eroare de UI la stergere nu trebuie sa opreasca suita
  try {
    let g = 0;
    while (await page.locator('.caz-row', { hasText: 'Test UI' }).count() > 0 && g++ < 10) {
      page.once('dialog', (d) => d.accept());
      await page.locator('.caz-row', { hasText: 'Test UI' }).first()
        .locator('.caz-del').click({ timeout: 8000 });
      await page.waitForTimeout(500);
    }
    (await page.locator('.caz-row', { hasText: titluCaz }).count()) === 0
      ? ok('dosarul de test a fost sters')
      : note('dosarul de test a ramas (curata manual)');
  } catch { note('stergerea dosarului de test a esuat (curata manual)'); }
  if (await page.locator('#cases-modal').isVisible())
    await page.locator('#cases-close').click().catch(() => {});

  console.log('\n== 3f. Rezumatul zilei ==');
  await page.locator('#inc-digest').click();
  await page.waitForTimeout(400);
  (await page.locator('#digest-modal').isVisible())
    ? ok('modalul de rezumat se deschide')
    : bad('rezumat', 'modalul nu se deschide');
  // ziua de azi are date; /api/digest proceseaza tot jurnalul zilei, sub incarcare
  // poate dura -> asteptam pana cifrele chiar se randeaza (nu doar "Se calculeaza...")
  await page.waitForFunction(
    () => document.querySelectorAll('#digest-body .dig-num').length >= 4,
    { timeout: 20000 }).catch(() => {});
  (await page.locator('#digest-body .dig-num').count()) >= 4
    ? ok('rezumatul arata cifrele (incidente/critice/atentie/evenimente)')
    : bad('rezumat', 'lipsesc cifrele');
  (await page.locator('#digest-body .dig-when').count()) > 0
    ? ok('rezumatul arata fereastra de activitate')
    : bad('rezumat', 'lipseste fereastra de activitate');
  // schimbarea zilei reincarca rezumatul
  await page.fill('#digest-day', '2020-01-01');
  await page.waitForTimeout(800);
  (await page.locator('#digest-body').textContent()).includes('0')
    ? ok('o zi fara evenimente se afiseaza corect (zero)')
    : note('ziua goala nu s-a putut verifica');
  await page.locator('#digest-close').click();
  await page.waitForTimeout(200);

  console.log('\n== 4. Bara de salvare (apare doar la modificari) ==');
  await page.click('#tab-set');
  await page.waitForTimeout(600);
  await page.locator('.snav-item[data-pane="sistem"]').click();
  await page.waitForTimeout(400);
  await page.check('#set-adv');   // MQTT e card „avansat”: ascuns pana bifezi
  await page.locator('#pane-sistem .card-tabs button', { hasText: 'NVR Hikvision' }).click();
  (await page.locator('#s-nvr-retras').isVisible())
    ? ok('Sistem (avansat): comutatorul „NVR retras” e prezent')
    : bad('nvr retras', 'lipseste comutatorul din Sistem');
  await page.waitForTimeout(300);
  await page.locator('#pane-sistem .card-tabs button', { hasText: 'MQTT' }).click();   // sub-tabul lui
  await page.waitForTimeout(300);
  (await page.locator('#mq-test').isVisible())
    ? ok('cardul MQTT e prezent in Sistem (broker + test)')
    : bad('mqtt', 'lipseste cardul MQTT');
  await page.locator('.snav-item[data-pane="stare"]').click();
  await page.waitForTimeout(1200);
  (await page.locator('#sys-stats .ai-row').count()) === 1
    ? ok('pagina Stare arata starea AI (Face ID, LPR, cautare, MQTT, reguli)')
    : bad('stare', 'lipseste randul AI din starea sistemului');
  await page.locator('.snav-item[data-pane="inreg"]').click();
  await page.waitForTimeout(500);
  let barVisible = await page.locator('#set-savebar').isVisible();
  !barVisible ? ok('bara de salvare ascunsa cand nimic nu s-a schimbat')
              : bad('bara de salvare', 'apare fara modificari');
  await page.fill('#ret-days', '17');
  await page.dispatchEvent('#ret-days', 'input');
  await page.waitForTimeout(400);
  barVisible = await page.locator('#set-savebar').isVisible();
  const dirtyTxt = barVisible ? (await page.textContent('#set-dirty')).trim() : '';
  barVisible ? ok(`bara apare la modificare: "${dirtyTxt}"`)
             : bad('bara de salvare', 'nu apare dupa modificare');
  await page.click('#s-revert');
  // asteptare conditionata: sub sarcina (backfill pe GPU), reincarcarea ia si 3-4s
  await page.waitForFunction(
    () => document.querySelector('#set-savebar').classList.contains('hidden'),
    { timeout: 10000 }).catch(() => {});
  !(await page.locator('#set-savebar').isVisible())
    ? ok('"Renunta" ascunde bara si reincarca valorile')
    : bad('renunta', 'bara ramane vizibila');

  console.log('\n== 5. Tabul Camere: lista, panou, filtre ==');
  await page.click('#tab-cams');
  await page.waitForTimeout(1500);
  const rows = await page.locator('#cams-table .cam-row:not(.cam-row-head)').count();
  rows === 18 ? ok(`lista are ${rows} randuri`) : bad('lista camere', rows + ' randuri');
  await page.locator('#cams-table .cam-row:not(.cam-row-head)').first().click();
  await page.waitForTimeout(2000);
  (await page.locator('#cam-panel').isVisible())
    ? ok('panoul de detaliu se deschide la click pe rand') : bad('panou camera', 'nu se deschide');
  const secs = await page.locator('#cam-panel-body .panel-sec h3').allTextContents();
  secs.length >= 4 ? ok('panoul are sectiunile: ' + secs.join(', '))
                   : bad('panou camera', 'doar ' + secs.length + ' sectiuni');
  const narrowed = await page.evaluate(() =>
    getComputedStyle(document.querySelector('#cams')).marginRight);
  narrowed !== '0px' ? ok('tabelul se ingusteaza cand panoul e deschis (' + narrowed + ')')
                     : note('tabelul nu se ingusteaza (verifica latimea ecranului)');
  await page.click('#cam-panel-close');
  await page.waitForTimeout(500);
  !(await page.locator('#cam-panel').isVisible()) ? ok('panoul se inchide') : bad('panou', 'ramane deschis');

  // filtrare
  await page.fill('#cams-search', 'curte');
  await page.waitForTimeout(600);
  const filtered = await page.locator('#cams-table .cam-row:not(.cam-row-head)').count();
  filtered > 0 && filtered < 18 ? ok(`cautarea "curte" filtreaza la ${filtered} camere`)
                                : bad('cautare', filtered + ' rezultate');
  await page.fill('#cams-search', '');
  await page.waitForTimeout(500);

  // selectie in masa
  await page.locator('#cams-table .cam-pick').first().check();
  await page.waitForTimeout(400);
  (await page.locator('#bulk-bar').isVisible())
    ? ok('bara de operatii in masa apare la selectie') : bad('operatii in masa', 'bara nu apare');
  await page.click('#bulk-clear');
  await page.waitForTimeout(300);

  console.log('\n== 6. Evenimente → Poze: filtre si vizualizator ==');
  await page.click('#tab-ev');
  await page.click('#ev-nav button[data-evpane="poze"]');
  await page.waitForTimeout(2500);
  (await page.locator('#al').isVisible() && await page.evaluate(() => location.hash) === '#ev/poze')
    ? ok('sub-panoul Poze se deschide, adresa #ev/poze') : bad('evenimente', 'Poze nu se deschide sau hash gresit');
  (await page.locator('#sem-q').isVisible())
    ? ok('campul de cautare semantica e prezent in Poze')
    : bad('semantic', 'lipseste campul #sem-q');
  const semR = await page.request.get(BASE + '/api/search?q=test&days=1&limit=3');
  [200, 409].includes(semR.status())
    ? ok(`/api/search raspunde (${semR.status() === 200 ? 'index activ' : 'inca nepornit'})`)
    : bad('semantic', '/api/search -> ' + semR.status());
  // intrebare libera in aceeasi caseta -> banner cu raspuns
  await page.fill('#sem-q', 'cand a venit o masina alba?');
  await page.click('#sem-go');
  await page.waitForSelector('.ask-answer', { timeout: 20000 }).catch(() => {});
  (await page.locator('.ask-answer').count()) === 1
    ? ok('intrebarea libera primeste raspuns in banner: "'
         + (await page.locator('.ask-answer').textContent()).slice(0, 60) + '..."')
    : bad('ask', 'lipseste bannerul de raspuns');
  await page.fill('#sem-q', '');
  const pills = await page.locator('#al-pills .pill').count();
  pills > 0 ? ok(`${pills} filtre de tip (pill-uri)`) : note('niciun eveniment azi de filtrat');
  const cards = await page.locator('#al-grid .al-card').count();
  if (cards > 0) {
    ok(`${cards} poze in galerie`);
    await page.locator('#al-grid .al-card').first().click();
    await page.waitForTimeout(1200);
    (await page.locator('#ph-overlay').isVisible())
      ? ok('vizualizatorul de poze se deschide') : bad('vizualizator', 'nu se deschide');
    (await page.locator('#ph-fix button').count()) > 3
      ? ok('bara de recatalogare are butoane') : bad('recatalogare', 'lipsesc butoanele');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  } else note('galeria e goala (fara evenimente azi)');

  console.log('\n== 7. Inregistrari: comenzi si timeline ==');
  await page.click('#tab-pb');
  await page.waitForTimeout(800);
  const camOpts = await page.locator('#pb-cam option').count();
  camOpts === 18 ? ok('selectorul de camera are 18 optiuni') : bad('selector camera', camOpts + ' optiuni');
  await page.click('#pb-search');
  await page.waitForTimeout(3500);
  const segs = await page.locator('#timeline .seg').count();
  segs > 0 ? ok(`timeline-ul afiseaza ${segs} segmente`) : note('nicio inregistrare pentru ziua selectata');
  const navBtns = await page.locator('#pb-nav button').count();
  navBtns >= 6 ? ok(`${navBtns} butoane de navigare in inregistrari`) : bad('navigare', navBtns + ' butoane');
  // marcaje (bookmarks): pus prin API (fetch-ul paginii adauga X-CSRF), vizibil pe timeline
  // si in lista laterala, apoi sters
  const pbCam = await page.inputValue('#pb-cam');
  const pbDay = await page.inputValue('#pb-date');
  const mk = await page.evaluate(async ({ cam, day }) => {
    const r = await fetch('/api/marcaje', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cam: +cam, start: `${day} 12:34:56`, eticheta: 'Test UI marcaj <b>x</b>' }) });
    return r.json();
  }, { cam: pbCam, day: pbDay });
  mk && mk.id ? ok(`marcaj creat prin API (${mk.id})`) : bad('marcaj', JSON.stringify(mk).slice(0, 80));
  await page.click('#pb-search');
  await page.waitForTimeout(2500);
  const flags = await page.locator('#timeline .marcaj').count();
  flags > 0 ? ok(`${flags} marcaj(e) pe timeline`) : bad('marcaj timeline', 'niciun steag');
  const mkRow = page.locator(`#pb-marks .mk-row[data-id="${mk.id}"]`);
  (await mkRow.count()) === 1 && (await mkRow.textContent()).includes('Test UI marcaj <b>x</b>')
    ? ok('marcajul apare in lista laterala, cu eticheta scapata') : bad('marcaj lista', 'lipseste sau nescapat');
  const gone = await page.evaluate(async (id) => (await fetch(`/api/marcaje/${id}`, { method: 'DELETE' })).ok, mk.id);
  gone ? ok('marcajul de test a fost sters') : bad('marcaj stergere', 'DELETE a esuat');
  // Etapa 2: zoom pe timeline, pauza/cadru/link, cinci viteze, legatura #pb/...
  await page.click('#tl-zoom button[data-span="3600"]');
  await page.waitForTimeout(300);
  const ticks1h = await page.locator('#ticks span').count();
  const rng = (await page.locator('#tl-range').textContent()) || '';
  ticks1h >= 6 && rng.includes('–') ? ok(`zoom 1h pe timeline: ${ticks1h} gradatii, fereastra ${rng}`)
    : bad('zoom timeline', `${ticks1h} gradatii, fereastra '${rng}'`);
  await page.click('#tl-zoom button[data-span="86400"]');
  await page.waitForTimeout(200);
  (await page.locator('#ticks span').count()) === 9 ? ok('inapoi la 24h: 9 gradatii din 3 in 3 ore') : bad('zoom timeline', 'gradatiile la 24h');
  (await page.locator('#pb-speed button').count()) === 5 ? ok('cinci viteze de redare (½x–8x)') : bad('viteze', 'nu sunt 5 butoane');
  const ctrlIds = ['pb-play', 'pb-f-prev', 'pb-f-next', 'pb-link'];
  const ctrlOk = [];
  for (const id of ctrlIds) if (await page.locator('#' + id).isVisible()) ctrlOk.push(id);
  ctrlOk.length === ctrlIds.length ? ok('pauza, cadru inapoi/inainte si link sunt pe bara') : bad('comenzi redare', 'lipsesc: ' + ctrlIds.filter((x) => !ctrlOk.includes(x)).join(','));
  await page.goto(`${BASE}/#pb/${pbCam}/${pbDay}/43200`);
  await page.waitForTimeout(4000);
  (await page.locator('#pb:not(.hidden)').count()) === 1 && (await page.inputValue('#pb-cam')) === pbCam
    ? ok(`legatura #pb/${pbCam}/${pbDay}/43200 deschide Inregistrarile pe camera ceruta`)
    : bad('rutare hash', 'tabul sau camera nu corespund');
  const posTxt = (await page.locator('#pb-pos').textContent()) || '';
  posTxt.startsWith('12:0') ? ok(`redarea a pornit la ${posTxt} (din legatura)`) : note(`redarea nu a pornit din legatura (${posTxt || 'fara pozitie'} — lipseste material la 12:00?)`);

  console.log('\n== 8. Live: densitate, vizualizari, cautare ==');
  await page.click('#tab-live');
  await page.waitForTimeout(1200);
  const views = await page.locator('#views .view-chip').count();
  views > 0 ? ok(`${views} vizualizari salvate`) : note('nicio vizualizare salvata');
  await page.locator('#density button[data-cols="3"]').click();
  await page.waitForTimeout(600);
  const cols = await page.getAttribute('#grid', 'data-cols');
  cols === '3' ? ok('selectorul de densitate schimba coloanele') : bad('densitate', 'data-cols=' + cols);
  await page.locator('#density button[data-cols="auto"]').click();
  await page.fill('#live-search', 'spate');
  await page.waitForTimeout(700);
  const liveFiltered = await page.locator('#grid .tile').count();
  liveFiltered > 0 && liveFiltered < 18 ? ok(`cautarea in Live filtreaza la ${liveFiltered} camere`)
                                        : bad('cautare live', liveFiltered + ' rezultate');
  await page.fill('#live-search', '');
  await page.waitForTimeout(500);
  // overlay-ul HD intra in istoric: Inapoi il inchide (telefon), adresa e #hd/<cam>
  await page.locator('#grid .tile').first().click();
  await page.waitForSelector('#hd-overlay:not(.hidden)', { timeout: 5000 }).catch(() => {});
  const hdHash = await page.evaluate(() => location.hash);
  hdHash.startsWith('#hd/') ? ok(`overlay-ul HD pune adresa ${hdHash}`) : bad('rutare HD', 'hash=' + hdHash);
  await page.goBack();
  await page.waitForTimeout(800);
  (await page.locator('#hd-overlay.hidden').count()) === 1 && (await page.evaluate(() => location.hash)) === '#live'
    ? ok('Inapoi inchide overlay-ul HD si revine la #live') : bad('rutare HD', 'overlay-ul nu s-a inchis cu Inapoi');

  console.log('\n== 9. Legaturi si formulare ==');
  const links = await page.locator('#quick-links a').count();
  const nvrRetras = await page.evaluate(() => fetch('/api/system').then((r) => r.json()).then((s) => !!(s.nvr && s.nvr.retras)));
  links === (nvrRetras ? 1 : 2) ? ok(`${links} legaturi rapide in antet (go2rtc${nvrRetras ? '; NVR-ul retras nu apare' : ', NVR'})`)
                               : bad('legaturi', `${links} (NVR retras: ${nvrRetras})`);
  for (const a of await page.locator('#quick-links a').all()) {
    const href = await a.getAttribute('href');
    if (!href || !href.startsWith('http')) bad('legatura invalida', href);
  }
  ok('toate legaturile au adrese valide');
  await page.click('#add-cam');
  await page.waitForTimeout(500);
  (await page.locator('#addcam-form').isVisible()) ? ok('formularul de adaugare camera se deschide')
                                                   : bad('adaugare camera', 'nu se deschide');
  await page.click('#addcam-cancel');
  await page.waitForTimeout(300);

  console.log('\n== 10. Accesibilitate si aspect ==');
  await page.keyboard.press('Tab');   // focus real de la tastatura (nu programatic)
  await page.waitForTimeout(200);
  const hasFocusRing = await page.evaluate(() => {
    const s = getComputedStyle(document.activeElement);
    return s.boxShadow !== 'none' || s.outlineStyle !== 'none';
  });
  hasFocusRing ? ok('controalele au inel de focus vizibil la navigarea cu Tab')
               : bad('accesibilitate', 'fara stare de focus');
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  overflow <= 0 ? ok('pagina nu are derulare orizontala') : bad('layout', overflow + 'px in afara ecranului');
  // mobil
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(800);
  const mOverflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  mOverflow <= 2 ? ok('pe telefon (390px) nu depaseste ecranul')
                 : bad('mobil', mOverflow + 'px in afara ecranului');
  await page.setViewportSize({ width: 1600, height: 950 });

  console.log('\n== 11. Erori de consola / retea ==');
  const realErrors = errors.filter((e) =>
    !/contentscript|ObjectMultiplex|MaxListeners|Eternl/i.test(e));
  realErrors.length === 0 ? ok('nicio eroare de consola din aplicatie')
                          : bad(`${realErrors.length} erori de consola`, realErrors.slice(0, 3).join(' | '));
  if (streamErrors.length) {
    note(`${streamErrors.length} erori tranzitorii de flux (go2rtc): ${streamErrors[0]}`);
  } else {
    ok('niciun flux nu a dat eroare');
  }
  failedReq.length === 0 ? ok('nicio cerere esuata') : bad(`${failedReq.length} cereri esuate`,
                                                          failedReq.slice(0, 3).join(' | '));

  console.log('\n== 12. Roluri: viewer restrans (2 camere, fara modificari) ==');
  // cream un cont de test prin API (cu sesiunea de admin + jetonul CSRF al ei)
  const me = await (await page.request.get(BASE + '/api/me')).json();
  const csrf = { 'X-CSRF': me.csrf };
  const TESTU = 'test-viewer-ui';
  let r = await page.request.post(BASE + '/api/users', { headers: csrf,
    data: { user: TESTU, pass: 'parola-test-ui', role: 'viewer', cameras: [101, 102] } });
  r.ok() ? ok('cont viewer de test creat (camerele 101+102)')
         : bad('creare viewer', 'status ' + r.status());

  const vctx = await browser.newContext({ viewport: { width: 1600, height: 950 }, bypassCSP: true });
  const vpage = await vctx.newPage();
  await vpage.goto(BASE, { waitUntil: 'domcontentloaded' });
  await vpage.fill('input[name="user"]', TESTU);
  await vpage.fill('input[name="password"]', 'parola-test-ui');
  await Promise.all([vpage.waitForNavigation(), vpage.click('button[type="submit"]')]);
  // asteptare robusta: /api/cameras sub incarcare (detectie + stream-uri concurente pe
  // un singur proces) poate raspunde in ~20-25s; asteptam pana apar exact cele 2 tile-uri
  await vpage.waitForFunction(
    () => document.querySelectorAll('#grid .tile').length >= 2, { timeout: 40000 }
  ).catch(() => {});
  await vpage.waitForTimeout(600);

  const vTiles = await vpage.locator('#grid .tile').count();
  vTiles === 2 ? ok('viewerul vede doar cele 2 camere permise')
               : bad('scope viewer', vTiles + ' tile-uri in loc de 2');
  for (const [id, nume] of [['#tab-cams', 'tab Camere'], ['#tab-set', 'tab Setari'],
                            ['#add-cam', 'buton Adauga camera'], ['#view-save', 'salvare vizualizare']]) {
    (await vpage.locator(id).isVisible())
      ? bad('viewer UI', nume + ' e vizibil desi nu ar trebui')
      : ok(`viewer: ${nume} e ascuns`);
  }
  // butoanele de actiune (sirena/vorbeste/strobo) nu exista pe tile-uri
  const actBtns = await vpage.locator('#grid .ctrl button').count();
  const listenOnly = await vpage.evaluate(() =>
    [...document.querySelectorAll('#grid .ctrl button')]
      .every((b) => /microfon|imagine|captura/i.test(b.title || '')));
  (actBtns === 0 || listenOnly)
    ? ok('viewer: pe tile-uri raman doar ascultatul si captura')
    : bad('viewer UI', actBtns + ' butoane de actiune vizibile');

  // serverul refuza efectiv, nu doar ascunde: POST cu jetonul CSRF valid al viewerului
  const vme = await (await vpage.request.get(BASE + '/api/me')).json();
  const denied = [];
  for (const [met, cale, body] of [
    ['post', '/api/cam/101/siren', {}],
    ['post', '/api/settings', { retention_days: 1 }],
    ['post', '/api/users', { user: 'x', pass: 'yyyyyy', role: 'admin' }],
    ['get', '/api/settings', null],
    ['get', '/api/backup', null],
    ['get', '/api/thumb/103', null],
    ['get', '/api/faces', null],
    ['get', '/api/semantic/status', null],
    ['get', '/api/rules', null],
  ]) {
    const rr = met === 'get'
      ? await vpage.request.get(BASE + cale)
      : await vpage.request.post(BASE + cale, { headers: { 'X-CSRF': vme.csrf }, data: body });
    if (rr.status() !== 403) denied.push(`${met.toUpperCase()} ${cale} -> ${rr.status()}`);
  }
  denied.length === 0 ? ok('serverul intoarce 403 la toate incercarile viewerului (9/9)')
                      : bad('enforcement server', denied.join(' | '));
  // jurnalul si citirile LPR sunt permise viewerului, dar DOAR pe camerele lui (cautarea unificata)
  const vj = await (await vpage.request.get(BASE + '/api/journal?limit=300')).json();
  const camStraine = (vj.events || []).filter((e) => e.cam && ![101, 102].includes(+e.cam));
  camStraine.length === 0 ? ok(`viewer: jurnalul e filtrat pe camerele lui (${(vj.events || []).length} evenimente, 0 straine)`) : bad('viewer jurnal', camStraine.length + ' evenimente de pe alte camere');
  const vp = await (await vpage.request.get(BASE + '/api/plates')).json();
  (vp.reads || []).every((r) => [101, 102].includes(+r.cam)) ? ok('viewer: citirile LPR sunt filtrate pe camerele lui') : bad('viewer plates', 'citiri de pe alte camere');
  (await vpage.locator('#tab-ai').isVisible())
    ? bad('viewer UI', 'tabul AI e vizibil desi nu ar trebui')
    : ok('viewer: tabul AI e ascuns');
  await vpage.click('#tab-ev');
  await vpage.waitForTimeout(600);
  (await vpage.locator('#ev-nav button[data-evpane="inc"]').isVisible())
    ? bad('viewer UI', 'sub-panoul De tratat e vizibil pentru viewer')
    : (await vpage.locator('#evjurnal').isVisible()) ? ok('viewer: Evenimente se deschide pe Jurnal, fara triaj') : bad('viewer UI', 'Evenimente nu se deschide pe Jurnal');
  await vctx.close();

  r = await page.request.delete(BASE + '/api/users/' + TESTU, { headers: csrf });
  r.ok() ? ok('contul de test a fost sters') : bad('stergere cont test', 'status ' + r.status());

  console.log('\n== 13. Rolul operator (paznic): actioneaza, nu configureaza ==');
  const OPU = 'test-operator-ui';
  r = await page.request.post(BASE + '/api/users', { headers: csrf,
    data: { user: OPU, pass: 'parola-op-ui', role: 'operator', cameras: [101, 102] } });
  r.ok() ? ok('cont operator de test creat') : bad('creare operator', 'status ' + r.status());
  const octx = await browser.newContext({ viewport: { width: 1600, height: 950 }, bypassCSP: true });
  const opage = await octx.newPage();
  await opage.goto(BASE, { waitUntil: 'domcontentloaded' });
  await opage.fill('input[name="user"]', OPU);
  await opage.fill('input[name="password"]', 'parola-op-ui');
  await Promise.all([opage.waitForNavigation(), opage.click('button[type="submit"]')]);
  await opage.waitForFunction(
    () => document.querySelectorAll('#grid .tile').length >= 2, { timeout: 40000 }
  ).catch(() => {});
  await opage.waitForTimeout(600);
  (await opage.locator('#grid .tile').count()) === 2
    ? ok('operatorul vede doar camerele lui')
    : bad('operator', 'numar gresit de camere');
  (await opage.locator('#grid .ctrl button[title*="apasat"], #grid .ctrl button[title*="alarma"]').count()) > 0
    ? ok('operatorul ARE butoane de actiune (vorbit/sirena)')
    : bad('operator', 'lipsesc butoanele de actiune');
  (await opage.locator('#tab-set').isVisible())
    ? bad('operator', 'tabul Setari e vizibil')
    : ok('operator: taburile de configurare raman ascunse');
  const ome = await (await opage.request.get(BASE + '/api/me')).json();
  const oh = { 'X-CSRF': ome.csrf };
  const r1 = await opage.request.post(BASE + '/api/cam/102/volume',
    { headers: oh, data: { speaker: 50 } });
  ![401, 403].includes(r1.status())
    ? ok('serverul permite operatorului actiunea pe camera lui')
    : bad('operator', 'actiune permisa refuzata: ' + r1.status());
  const opDenied = [];
  for (const [cale, body] of [['/api/cam/3/siren', {}], ['/api/cam/102/config', { name: 'x' }],
                              ['/api/settings', {}], ['/api/rules', {}]]) {
    const rr = await opage.request.post(BASE + cale, { headers: oh, data: body });
    if (rr.status() !== 403) opDenied.push(`${cale} -> ${rr.status()}`);
  }
  opDenied.length === 0 ? ok('serverul refuza operatorului configurarea si camerele straine (4/4)')
                        : bad('operator enforcement', opDenied.join(' | '));
  await octx.close();
  r = await page.request.delete(BASE + '/api/users/' + OPU, { headers: csrf });
  r.ok() ? ok('contul operator de test a fost sters') : bad('stergere operator', 'status ' + r.status());

  console.log('\n== 12. Etapa 3: Contul meu (2FA, push, dispozitive), audit cu utilizator ==');
  // dialogul „Contul meu" din numele utilizatorului (orice rol)
  await page.click('#user-chip .me-open');
  await page.waitForTimeout(800);
  (await page.evaluate(() => document.querySelector('#me-dlg').open))
    ? ok('„Contul meu" se deschide din numele utilizatorului') : bad('Contul meu', 'dialogul nu s-a deschis');
  const tfaSt = await page.locator('#tfa-stare').innerText();
  /Inactiv|Activ/.test(tfaSt) ? ok('2FA: starea e afisata (' + tfaSt.slice(0, 30) + '…)') : bad('2FA stare', tfaSt);
  const pushSt = await page.locator('#push-stare').innerText();
  pushSt.length > 10 ? ok('push: starea e afisata (' + pushSt.slice(0, 50) + '…)') : bad('push stare', pushSt);
  await page.click('#tfa-on');
  await page.waitForSelector('#tfa-pas1:not(.hidden)', { timeout: 8000 }).catch(() => {});
  const qr = await page.evaluate(() => (document.querySelector('#tfa-qr').src || '').slice(0, 18));
  qr === 'data:image/svg+xml' ? ok('2FA: QR-ul SVG apare') : bad('2FA QR', qr);
  (await page.locator('#tfa-secret').innerText()).length >= 32
    ? ok('2FA: secretul de tastat manual e afisat') : bad('2FA secret', 'lipseste');
  await page.click('#tfa-cancel');
  await page.click('#me-close');
  await page.waitForTimeout(300);
  !(await page.evaluate(() => document.querySelector('#me-dlg').open))
    ? ok('„Contul meu" se inchide') : bad('Contul meu', 'nu s-a inchis');

  // 2FA cap-coada pe un cont de test: activare prin API (cod TOTP calculat aici), login in doi pasi
  const totpNode = (secret) => {
    const b32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
    for (const ch of secret.replace(/=+$/, '')) bits += b32.indexOf(ch).toString(2).padStart(5, '0');
    const key = Buffer.from((bits.match(/.{8}/g) || []).map((b) => parseInt(b, 2)));
    const c = Buffer.alloc(8); c.writeUInt32BE(Math.floor(Date.now() / 30000), 4);
    const h = require('crypto').createHmac('sha1', key).update(c).digest();
    const o = h[19] & 15;
    return String((h.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, '0');
  };
  const TFAU = 'test-2fa-ui';
  await page.request.delete(BASE + '/api/users/' + TFAU, { headers: csrf });
  r = await page.request.post(BASE + '/api/users', { headers: csrf,
    data: { user: TFAU, pass: 'parola-2fa-ui', role: 'viewer' } });
  r.ok() ? ok('cont de test pentru 2FA creat') : bad('creare cont 2fa', 'status ' + r.status());
  const tctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, bypassCSP: true });
  const tpage = await tctx.newPage();
  await tpage.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await tpage.fill('input[name="user"]', TFAU);
  await tpage.fill('input[name="password"]', 'parola-2fa-ui');
  await Promise.all([tpage.waitForNavigation(), tpage.click('button[type="submit"]')]);
  const tme = await (await tpage.request.get(BASE + '/api/me')).json();
  const th = { 'X-CSRF': tme.csrf };
  const pre = await (await tpage.request.post(BASE + '/api/me/2fa/pregateste', { headers: th })).json();
  const act = await (await tpage.request.post(BASE + '/api/me/2fa/activeaza',
    { headers: th, data: { cod: totpNode(pre.secret) } })).json();
  act.ok && (act.recuperare || []).length === 8
    ? ok('2FA activat prin API cu un cod TOTP calculat local (8 coduri de recuperare)')
    : bad('2FA activare', JSON.stringify(act).slice(0, 100));
  await tpage.goto(BASE + '/logout', { waitUntil: 'domcontentloaded' });
  await tpage.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await tpage.fill('input[name="user"]', TFAU);
  await tpage.fill('input[name="password"]', 'parola-2fa-ui');
  await tpage.click('button[type="submit"]');
  await tpage.waitForSelector('input[name="cod"]', { timeout: 8000 }).catch(() => {});
  (await tpage.locator('input[name="cod"]').count()) === 1
    ? ok('login cu 2FA: dupa parola se cere codul') : bad('login 2FA', 'nu a aparut formularul de cod');
  (await tpage.request.get(BASE + '/api/me')).status() === 401
    ? ok('inainte de cod, API-ul raspunde 401') : bad('login 2FA', 'API accesibil fara cod');
  await tpage.fill('input[name="cod"]', '000000');
  await tpage.click('button[type="submit"]');
  await tpage.waitForTimeout(1500);
  (await tpage.content()).match(/gre[sș]it/) ? ok('cod gresit: refuzat, formularul ramane')
                                               : bad('login 2FA', 'codul gresit nu a fost refuzat');
  await tpage.fill('input[name="cod"]', act.recuperare[0]);
  await Promise.all([tpage.waitForNavigation(), tpage.click('button[type="submit"]')]);
  !tpage.url().includes('/login') ? ok('cod de recuperare acceptat -> logat') : bad('login 2FA', 'recuperarea nu a mers');
  await tctx.close();
  r = await page.request.delete(BASE + '/api/users/' + TFAU + '/2fa', { headers: csrf });
  r.ok() ? ok('adminul scoate 2FA de pe cont') : bad('scoate 2FA', 'status ' + r.status());
  r = await page.request.delete(BASE + '/api/users/' + TFAU, { headers: csrf });
  r.ok() ? ok('contul de test 2FA a fost sters') : bad('stergere cont 2fa', 'status ' + r.status());

  // push: cheia VAPID + abonare/dezabonare prin API (browserul headless nu are serviciu de push)
  const pk = await (await page.request.get(BASE + '/api/push/cheie')).json();
  pk.activ && (pk.cheie || '').length > 80 ? ok('push: serverul are cheie VAPID') : bad('push cheie', JSON.stringify(pk).slice(0, 80));
  const fakeEp = 'https://push.example.org/ui-test-' + Date.now();
  r = await page.request.post(BASE + '/api/push/abonare', { headers: csrf,
    data: { abonament: { endpoint: fakeEp, keys: { p256dh: 'B' + 'x'.repeat(86), auth: 'y'.repeat(22) } }, nivel: 'atentie' } });
  r.ok() ? ok('push: abonare acceptata') : bad('push abonare', 'status ' + r.status());
  const pl = await (await page.request.get(BASE + '/api/push/abonamente')).json();
  (pl.abonamente || []).some((a) => a.endpoint === fakeEp && a.nivel === 'atentie')
    ? ok('push: abonamentul apare in lista adminului') : bad('push lista', JSON.stringify(pl).slice(0, 80));
  r = await page.request.delete(BASE + '/api/push/abonare', { headers: csrf, data: { endpoint: fakeEp } });
  r.ok() ? ok('push: dezabonare') : bad('push dezabonare', 'status ' + r.status());

  // Setari: butoanele de admin pe conturi si lista push; jurnalul arata utilizatorul
  await page.click('#tab-set');
  await page.click('.snav-item[data-pane="utilizatori"]');
  await page.waitForTimeout(800);
  (await page.locator('#u-list .u-revoke').count()) > 0
    ? ok('Utilizatori: fiecare cont are „deconecteaza"') : bad('Utilizatori', 'lipseste „deconecteaza"');
  await page.click('.snav-item[data-pane="notif"]');
  await page.waitForTimeout(500);
  (await page.locator('#push-lista').count()) === 1 ? ok('Notificari: cardul push exista') : bad('Notificari', 'lipseste cardul push');
  await page.click('.snav-item[data-pane="jurnal"]');
  await page.waitForSelector('#log-list .log-row', { timeout: 8000 }).catch(() => {});
  const whoTxt = await page.locator('#log-list .log-row .log-w').first().innerText().catch(() => '');
  whoTxt === USER ? ok(`Jurnal: intrarile poarta utilizatorul („${whoTxt}"), nu IP-ul`) : bad('Jurnal who', whoTxt);

  console.log('\n== 13. Etapa 4: mod acasa/plecat/noapte, harta, pop-up, panou I/O ==');
  await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#mod-chip button.on', { timeout: 8000 }).catch(() => {});
  const modOn = await page.locator('#mod-chip button.on').count();
  modOn === 1 ? ok('comutatorul de mod din antet arata modul curent') : bad('mod chip', modOn + ' butoane active');
  const modInit = await page.evaluate(() => document.querySelector('#mod-chip button.on')?.dataset.mod);
  const modTinta = modInit === 'plecat' ? 'acasa' : 'plecat';
  await page.click(`#mod-chip button[data-mod="${modTinta}"]`);
  await page.waitForTimeout(800);
  (await page.evaluate(() => document.querySelector('#mod-chip button.on')?.dataset.mod)) === modTinta
    ? ok(`modul comutat din antet pe „${modTinta}"`) : bad('mod', 'nu s-a comutat');
  const modApi = await (await page.request.get(BASE + '/api/mod')).json();
  modApi.mod === modTinta ? ok('serverul confirma modul nou') : bad('mod api', modApi.mod);
  await page.request.post(BASE + '/api/mod', { headers: csrf, data: { mod: modInit || 'acasa' } });
  // pop-up: comutatorul exista si se tine minte
  (await page.locator('#popup-toggle').isVisible()) ? ok('butonul „Pop-up la critic" e in bara Live') : bad('popup', 'lipseste');
  // panoul I/O
  await page.click('#io-toggle');
  await page.waitForTimeout(400);
  const ioRows = await page.locator('#io-list .io-row').count();
  (!(await page.locator('#io-panel').evaluate((el) => el.classList.contains('hidden'))) && ioRows > 0)
    ? ok(`panoul I/O se deschide cu ${ioRows} camere actionabile`) : bad('I/O', 'panoul nu s-a deschis sau e gol');
  await page.click('#io-close');
  // harta: tabul, planul de test, editarea
  // planuri ramase de la o rulare intrerupta: le stergem, altfel interfata ar deschide planul vechi
  for (const pl of ((await (await page.request.get(BASE + '/api/harta')).json()).planuri || [])) {
    if (pl.nume === 'Plan test UI') await page.request.delete(BASE + '/api/harta/' + pl.id, { headers: csrf });
  }
  const hp = await (await page.request.post(BASE + '/api/harta', { headers: csrf, data: { plan_nou: 'Plan test UI' } })).json();
  hp.ok ? ok('plan de harta creat prin API') : bad('harta plan', JSON.stringify(hp).slice(0, 80));
  // o imagine PNG 2x2 valida
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAC0lEQVQI12NgQAcAABIAAe+JVKQAAAAASUVORK5CYII=', 'base64');
  const up = await page.request.post(BASE + `/api/harta/${hp.plan.id}/imagine`, { headers: { ...csrf, 'Content-Type': 'image/png' }, data: png });
  up.ok() ? ok('imaginea planului incarcata') : bad('harta imagine', 'status ' + up.status());
  await page.request.post(BASE + '/api/harta', { headers: csrf, data: { id: hp.plan.id, camere: { 101: { x: 0.3, y: 0.3, dir: 45, fov: 90 }, 102: { x: 0.7, y: 0.6, dir: 200, fov: 60 } } } });
  await page.click('#tab-harta');
  // planul de test, nu altul salvat de utilizator: modulul citeste localStorage doar la incarcare,
  // asa ca alegem planul ca un om, din select (vizibil doar cand exista cel putin 2 planuri)
  await page.waitForSelector('#harta-plan option', { timeout: 8000 }).catch(() => {});
  if (await page.locator('#harta-plan').isVisible()) await page.selectOption('#harta-plan', hp.plan.id);
  await page.waitForTimeout(400);
  await page.waitForSelector('#harta-svg g.hm', { timeout: 8000 }).catch(() => {});
  const hmk = await page.locator('#harta-svg g.hm').count();
  hmk === 2 ? ok('harta arata cele 2 camere asezate (marcaj + con)') : bad('harta', hmk + ' marcaje');
  (await page.evaluate(() => location.hash)) === '#harta' ? ok('adresa #harta') : bad('harta hash', await page.evaluate(() => location.hash));
  await page.click('#harta-edit');
  await page.waitForTimeout(500);
  (await page.locator('#harta-side').isVisible()) ? ok('modul de editare arata panoul lateral') : bad('harta edit', 'panoul lateral lipseste');
  const unplaced = await page.locator('#harta-unplaced .u-row').count();
  unplaced > 0 ? ok(`${unplaced} camere neasezate in lista`) : bad('harta edit', 'lista de camere neasezate e goala');
  await page.click('#harta-svg g.hm[data-id="101"] circle', { force: true });  // marcajul pulseaza (alarma activa) => "not stable"
  await page.waitForTimeout(300);
  (await page.locator('#harta-sel').isVisible()) ? ok('click pe un marcaj il selecteaza (directie/unghi)') : bad('harta sel', 'panoul de selectie lipseste');
  await page.click('#harta-edit');
  await page.waitForTimeout(300);
  // click pe marcaj in modul normal deschide HD-ul
  await page.click('#harta-svg g.hm[data-id="101"] circle', { force: true });  // marcajul pulseaza (alarma activa) => "not stable"
  await page.waitForTimeout(1200);
  const hdDeschis = await page.evaluate(() => !document.querySelector('#hd-overlay').classList.contains('hidden'));
  hdDeschis ? ok('click pe camera de pe harta deschide HD-ul') : bad('harta HD', 'HD-ul nu s-a deschis');
  if (hdDeschis) { await page.click('#hd-close'); await page.waitForTimeout(500); }
  const del = await page.request.delete(BASE + `/api/harta/${hp.plan.id}`, { headers: csrf });
  del.ok() ? ok('planul de test a fost sters') : bad('harta del', 'status ' + del.status());
  // formularul de reguli are modurile si actiunea „mod"
  await page.click('#tab-ai');
  await page.waitForTimeout(800);
  (await page.locator('#ru-moduri input').count()) === 3 ? ok('regula: pasul 3 are cele 3 moduri') : bad('reguli moduri', 'lipsesc bifele');
  (await page.locator('.ru-actions input[data-act="mod"]').count()) === 1 ? ok('regula: actiunea „trece in modul"') : bad('reguli', 'lipseste actiunea mod');

  console.log('\n== 14. Etapa 5: cautare pe regiune, harta de caldura, time-lapse, masca ==');
  await page.goto(BASE + '/#pb', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  for (const [id, nume] of [['#pb-regiune', 'Cauta pe regiune'], ['#pb-heat', 'Harta de caldura'], ['#pb-timelapse', 'Time-lapse']]) {
    (await page.locator(id).isVisible()) ? ok(`Inregistrari: butonul „${nume}"`) : bad('pb butoane', nume + ' lipseste');
  }
  // regiune: overlay-ul se deschide, API-ul raspunde la un dreptunghi
  await page.click('#pb-regiune');
  await page.waitForTimeout(1500);
  !(await page.locator('#reg-overlay').evaluate((el) => el.classList.contains('hidden')))
    ? ok('cautarea pe regiune deschide overlay-ul cu cadrul') : bad('regiune', 'overlay ascuns');
  const cvBox = await page.locator('#reg-canvas').boundingBox();
  if (cvBox) {
    await page.mouse.move(cvBox.x + cvBox.width * 0.2, cvBox.y + cvBox.height * 0.2);
    await page.mouse.down();
    await page.mouse.move(cvBox.x + cvBox.width * 0.6, cvBox.y + cvBox.height * 0.6, { steps: 5 });
    await page.mouse.up();
    await page.waitForTimeout(1500);
    const info = await page.locator('#reg-info').innerText();
    /detec/.test(info) ? ok('regiune: dreptunghiul tras intreaba serverul (' + info.slice(0, 40) + '…)') : bad('regiune', info);
  } else bad('regiune', 'canvas fara dimensiuni');
  await page.click('#reg-close');
  // harta de caldura
  await page.click('#pb-heat');
  await page.waitForFunction(() => /episoade/.test(document.querySelector('#heat-info')?.innerText || ''), { timeout: 8000 }).catch(() => {});
  const heatInfo = await page.locator('#heat-info').innerText();
  /episoade/.test(heatInfo) ? ok('harta de caldura: ' + heatInfo.slice(0, 60)) : bad('heatmap', heatInfo);
  await page.click('#heat-zile button[data-zile="7"]');
  await page.waitForTimeout(2000);
  /7 zile/.test(await page.locator('#heat-info').innerText()) ? ok('harta de caldura pe 7 zile') : bad('heatmap 7', await page.locator('#heat-info').innerText());
  await page.click('#heat-close');
  // API-urile direct: regiune/heatmap cu parametri invalizi -> 400; timelapse -> 202 (job anulat imediat)
  (await page.request.get(BASE + '/api/regiune?channel=101&day=2026-13-99&x=0&y=0&w=1&h=1')).status() === 400
    ? ok('API regiune: zi invalida -> 400') : bad('regiune api', 'zi invalida acceptata');
  const tl = await page.request.get(BASE + '/api/timelapse?channel=101&day=' + new Date().toISOString().slice(0, 10) + '&pas=600');
  if (tl.status() === 202) {
    const jid = (await tl.json()).id;
    await page.request.delete(BASE + '/api/jobs/' + jid, { headers: csrf });
    ok('API time-lapse: 202 + job (anulat imediat in test)');
  } else bad('timelapse api', 'status ' + tl.status());
  // masca: salvare + scoatere prin API (fluxurile se regenereaza), butonul in HD
  let mr = await page.request.post(BASE + '/api/cam/101/masca', { headers: csrf, data: { rects: [[0.8, 0.8, 0.2, 0.2]] } });
  mr.ok() && (await mr.json()).mask.length === 1 ? ok('masca de confidentialitate salvata pe cam 101') : bad('masca', 'status ' + mr.status());
  const camsM = await (await page.request.get(BASE + '/api/cameras')).json();
  (camsM.find((c) => c.id === 101) || {}).mask ? ok('masca apare in /api/cameras') : bad('masca', 'lipseste din /api/cameras');
  mr = await page.request.post(BASE + '/api/cam/101/masca', { headers: csrf, data: { rects: null } });
  mr.ok() ? ok('masca scoasa') : bad('masca', 'nu s-a scos');

  console.log('\n== 15. Etapa 6: arhiva, copie nocturna criptata, lant de custodie ==');
  await page.goto(BASE + '/#set', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  await page.click('.snav-item[data-pane="inreg"]');
  await page.waitForTimeout(800);
  (await page.locator('#arh-dir').count()) === 1 && /Oprit|arhiv|Director/i.test(await page.locator('#arh-report').innerText())
    ? ok('Inregistrare: cardul „Nivel de arhiva" cu starea lui') : bad('arhiva', 'cardul lipseste');
  await page.click('.snav-item[data-pane="unelte"]');
  await page.waitForTimeout(1200);
  // panoul arata un singur card o data (tab-uri automate din titluri): activam cardul copiei
  await page.locator('#pane-unelte .card-tabs button', { hasText: 'Copie nocturn' }).click();
  await page.waitForTimeout(500);
  const bnSt = await page.locator('#bn-stare').innerText();
  /copie|Nicio/i.test(bnSt) ? ok('Unelte: cardul copiei nocturne arata starea (' + bnSt.slice(0, 40) + '…)') : bad('backup card', bnSt);
  await page.click('#bn-cheie');
  await page.waitForTimeout(800);
  const cheie = await page.locator('#bn-cheie-val').innerText();
  cheie.length >= 44 ? ok('cheia de criptare se poate afisa (de notat separat)') : bad('cheie backup', cheie.slice(0, 40));
  await page.click('#bn-cheie');
  // API: stare, lista, segmente cu hash, verificare
  const bs = await (await page.request.get(BASE + '/api/backup/stare')).json();
  bs.cheie_amprenta && Array.isArray(bs.copii) ? ok(`API backup: ${bs.copii.length} copii locale, cheie ${bs.cheie_amprenta}`) : bad('backup api', JSON.stringify(bs).slice(0, 80));
  const zi = new Date().toLocaleDateString('sv-SE');
  let seg = null;
  const camsSeg = await (await page.request.get(BASE + '/api/cameras')).json();
  for (const c of camsSeg.slice(0, 6)) {
    const d = await (await page.request.get(BASE + `/api/segmente?channel=${c.id}&day=${zi}`)).json();
    if ((d.segmente || []).length) { seg = { cam: c.id, ...d.segmente[d.segmente.length - 1] }; break; }
  }
  seg ? ok(`lant de custodie: cam${seg.cam} are amprente azi (${seg.nume} ${seg.sha256.slice(0, 10)}…)`) : note('lant de custodie: nicio amprenta inca (nvr-record abia a pornit?)');
  if (seg) {
    const v = await (await page.request.get(BASE + `/api/segmente/verifica?channel=${seg.cam}&nume=${encodeURIComponent(seg.nume)}`)).json();
    v.ok === true ? ok('verificarea amprentei: fisierul e neatins') : bad('verificare amprenta', JSON.stringify(v).slice(0, 80));
  }
  (await page.request.get(BASE + '/api/segmente?channel=101&day=xx')).status() === 400 ? ok('API segmente: zi invalida -> 400') : bad('segmente api', 'zi invalida acceptata');

  console.log('\n== 16. Etapa 7: multi-vendor (drivere hik/onvif) ==');
  const camsV = await (await page.request.get(BASE + '/api/cameras')).json();
  camsV.length && camsV.every((c) => c.vendor === 'hik') ? ok(`/api/cameras: fiecare camera are vendor (${camsV.length} x hik)`) : bad('vendor', 'lipseste campul vendor');
  await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  await page.click('#add-cam');
  await page.waitForTimeout(400);
  (await page.locator('#addcam-vendor option').count()) === 2 ? ok('formularul de adaugare are selectorul de producator (Hikvision / ONVIF)') : bad('addcam vendor', 'selectorul lipseste');
  await page.selectOption('#addcam-vendor', 'onvif');
  await page.waitForTimeout(200);
  (await page.locator('#addcam-http').isVisible()) ? ok('ONVIF: apare portul HTTP') : bad('addcam http', 'portul HTTP nu apare');
  await page.selectOption('#addcam-vendor', 'hik');
  await page.click('#addcam-cancel');
  const bad1 = await page.request.post(BASE + '/api/cameras', { headers: csrf, data: { name: 'x', ip: '10.255.255.2', user: 'u', pass: 'p', vendor: 'onvif' } });
  if (bad1.status() === 202) {
    const jid = (await bad1.json()).id; let j = null;
    for (let i = 0; i < 20; i++) { j = await (await page.request.get(BASE + '/api/jobs/' + jid)).json(); if (['gata', 'eroare', 'anulat'].includes(j.stare)) break; await page.waitForTimeout(1000); }
    j && j.stare === 'eroare' && /ONVIF/.test(j.mesaj || '') ? ok('camera ONVIF inexistenta: jobul esueaza curat (' + (j.mesaj || '').slice(0, 40) + '…)') : bad('onvif job', JSON.stringify(j).slice(0, 80));
  } else bad('onvif add', 'status ' + bad1.status());

  console.log('\n== 17. Frontend: grila oprita sub HD, Esc global, toast anuntat, tinte 44 px ==');
  await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#grid .tile').length > 0, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1500);
  const tileCnt = await page.locator('#grid .tile').count();
  if (tileCnt) {
    await page.locator('#grid .tile').first().click();
    await page.waitForTimeout(1500);
    const pauzate = await page.evaluate(() => [...document.querySelectorAll('#grid video-stream')].filter((v) => v._pauzat).length);
    const total = await page.evaluate(() => document.querySelectorAll('#grid video-stream').length);
    total === 0 ? note('grila e in previzualizare (fara video-stream), pauza nu se aplica')
      : pauzate === total ? ok(`sub HD, toate cele ${total} fluxuri din grila sunt oprite`) : bad('grila sub HD', `${pauzate}/${total} oprite`);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(800);
    (await page.evaluate(() => document.querySelector('#hd-overlay').classList.contains('hidden'))) ? ok('Esc inchide HD-ul') : bad('Esc HD', 'overlay-ul a ramas');
    const reluate = await page.evaluate(() => [...document.querySelectorAll('#grid video-stream')].filter((v) => v._pauzat).length);
    reluate === 0 ? ok('dupa inchidere, fluxurile din grila repornesc') : bad('grila dupa HD', reluate + ' inca oprite');
  } else note('grila goala, sar peste testul HD/Esc');
  await page.click('#add-cam');
  await page.waitForTimeout(300);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  (await page.evaluate(() => document.querySelector('#addcam-overlay').classList.contains('hidden'))) ? ok('Esc inchide formularul de adaugare') : bad('Esc overlay', 'formularul a ramas');
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  const rolToast = await page.evaluate(() => { const t = document.querySelector('#toast'); return t ? t.getAttribute('role') : 'fara toast inca'; });
  ['status', 'alert', 'fara toast inca'].includes(rolToast) ? ok('toast-ul poarta role=' + rolToast) : bad('toast aria', rolToast);
  const tctx2 = await browser.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true, isMobile: true, bypassCSP: true });
  const mpage = await tctx2.newPage();
  await mpage.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await mpage.fill('input[name="user"]', USER); await mpage.fill('input[name="password"]', PASS);
  await Promise.all([mpage.waitForNavigation(), mpage.click('button[type="submit"]')]);
  await mpage.waitForTimeout(1500);
  const hNav = await mpage.locator('nav button').first().boundingBox();
  hNav && hNav.height >= 44 ? ok(`telefon: butoanele de navigare au ${Math.round(hNav.height)} px inaltime`) : bad('tinte 44px', JSON.stringify(hNav));
  await tctx2.close();

  console.log('\n== 18. Directorul de inregistrari nescriibil: banner ==');
  {
    const h = await (await page.request.get(BASE + '/healthz')).json();
    'blocat' in h ? ok('healthz raporteaza daca directorul de inregistrari e scriibil') : bad('healthz', 'lipseste campul blocat');
    const ascuns = await page.evaluate(() => document.querySelector('#blocat').classList.contains('hidden'));
    ascuns === !h.blocat ? ok(`banner-ul de deblocare ${h.blocat ? 'apare' : 'e ascuns'} cand directorul ${h.blocat ? 'e' : 'nu e'} blocat`)
      : bad('banner blocat', `ascuns=${ascuns}, blocat=${h.blocat}`);
  }

  console.log('\n== 19. LPR: alerta la masini necunoscute, citiri confirmate; fete grupate pe persoana ==');
  {
    const lp = await (await page.request.get(BASE + '/api/plates')).json();
    ('notify_unknown' in lp && 'sure_conf' in lp && Array.isArray(lp.alert_cams) && lp.stats && 'total7' in lp.stats)
      ? ok(`LPR: configurarea noua + statistici (${lp.stats.total7} citiri in 7 zile)`) : bad('lpr api', JSON.stringify(lp).slice(0, 100));
    const r = await page.request.post(BASE + '/api/plates/config', { headers: csrf, data: { alert_cams: ['x'] } });
    r.status() === 400 ? ok('LPR: lista de camere invalida e refuzata') : bad('lpr config', 'status ' + r.status());
    await page.click('#tab-ai'); await page.waitForTimeout(800);
    (await page.locator('#lp-nu').count()) === 1 && (await page.locator('#lp-cams option').count()) > 0
      ? ok('LPR: bifa „masini necunoscute” si lista de camere sunt in ecranul AI') : bad('lpr ui', 'controalele lipsesc');
    (await page.locator('#lp-stats').innerText()).includes('Citiri') ? ok('LPR: statisticile de citiri apar') : bad('lpr ui', 'fara statistici');
    const g = await (await page.request.get(BASE + '/api/faces/groups')).json();
    Array.isArray(g.grupuri) && Array.isArray(g.persoane)
      ? ok(`fete: gruparea raspunde (${g.grupuri.length} grupuri, ${g.persoane.length} persoane in biblioteca)`) : bad('fete grupuri', JSON.stringify(g).slice(0, 100));
    const bad1 = await (await page.request.post(BASE + '/api/faces/groups/assign', { headers: csrf, data: { poze: ['unknown/x.jpg'], actiune: 'persoana', nume: '' } })).json();
    bad1.error ? ok('fete: inrolarea unui grup fara nume e refuzata') : bad('fete assign', JSON.stringify(bad1));
    (await page.locator('#fc-group').count()) === 1 && (await page.locator('#fc-auto').count()) === 0
      ? ok('fete: butonul „Grupeaza pe persoana” a inlocuit auto-inrolarea') : bad('fete ui', 'butonul lipseste');
  }

  console.log('\n== 21. Antrenament local: stare, butoane, joburi in nvr-ai ==');
  {
    const t = await (await page.request.get(BASE + '/api/train/status')).json();
    ('finetune' in t && Array.isArray(t.runs) && 'job' in t)
      ? ok(`antrenament: starea (${t.finetune} imagini etichetate, ${t.runs.length} antrenamente, job ${t.job ? (t.job.activ ? 'activ' : 'inactiv') : 'nvr-ai oprit'})`)
      : bad('train api', JSON.stringify(t).slice(0, 120));
    await page.click('#tab-set'); await page.waitForTimeout(400);
    await page.locator('.snav-item[data-pane="unelte"]').click(); await page.waitForTimeout(800);
    (await page.locator('#s-train-dataset').count()) === 1 && (await page.locator('#s-train-run').count()) === 1
      ? ok('antrenament: butoanele de dataset si antrenare sunt in Unelte, fara unelte externe') : bad('train ui', 'butoane lipsa');
    const r = await page.request.post(BASE + '/api/train/run', { headers: csrf, data: { epoci: 'x' } }).catch(() => null);
    r && [200, 400, 409, 503].includes(r.status()) ? ok('antrenament: pornirea raspunde (' + r.status() + ')') : bad('train run', r ? 'status ' + r.status() : 'fara raspuns');
    if (r && r.status() === 200) { await page.request.post(BASE + '/api/train/stop', { headers: csrf }); note('un antrenament real a pornit din test si a fost oprit'); }
  }

  console.log('\n== 22. Adnotare: datasetul etichetat, cadru cu chenare, navigare ==');
  {
    const l = await (await page.request.get(BASE + '/api/adnotare?limit=5')).json();
    (Array.isArray(l.cadre) && 'total' in l && Array.isArray(l.clase))
      ? ok(`adnotare: lista datasetului (${l.total} cadre, ${l.clase.length} clase, ${l.verificate} verificate)`) : bad('adnotare api', JSON.stringify(l).slice(0, 100));
    if (l.cadre.length) {
      const c = l.cadre[0];
      const d = await (await page.request.get(BASE + `/api/adnotare/cadru/${c.split}/${encodeURIComponent(c.fn)}`)).json();
      d.w > 0 && Array.isArray(d.boxes) ? ok(`adnotare: cadrul ${c.fn} (${d.w}x${d.h}, ${d.boxes.length} chenare)`) : bad('adnotare cadru', JSON.stringify(d).slice(0, 100));
      const im = await page.request.get(BASE + `/api/adnotare/img/${c.split}/${encodeURIComponent(c.fn)}`);
      im.status() === 200 && (im.headers()['content-type'] || '').includes('image/jpeg') ? ok('adnotare: imaginea cadrului se serveste') : bad('adnotare img', 'status ' + im.status());
      const rau = await page.request.post(BASE + `/api/adnotare/cadru/${c.split}/${encodeURIComponent(c.fn)}`, { headers: csrf, data: { boxes: [{ cls: 99, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 }] } });
      rau.status() === 400 ? ok('adnotare: un chenar cu clasa necunoscuta e refuzat (400)') : bad('adnotare validare', 'status ' + rau.status());
    }
    (await page.request.get(BASE + '/api/adnotare/img/train/..%2F..%2Fcameras.json')).status() === 404 ? ok('adnotare: caile cu .. sunt refuzate') : bad('adnotare cale', 'a servit ceva');
    await page.click('#tab-ai'); await page.waitForTimeout(300);
    await page.click('.ainav-item[data-pane="adnot"]');
    await page.waitForFunction(() => document.querySelectorAll('#ad-list .ad-row').length > 0, { timeout: 15000 }).catch(() => {});
    const rows = await page.locator('#ad-list .ad-row').count();
    rows > 0 ? ok(`adnotare: lista din UI are ${rows} cadre si canvas-ul e prezent`) : note('adnotare: dataset gol in UI');
    if (rows > 1) {
      const info1 = await page.locator('#ad-info').innerText();
      await page.keyboard.press('ArrowRight'); await page.waitForTimeout(700);
      (await page.locator('#ad-info').innerText()) !== info1 ? ok('adnotare: sageata dreapta trece la cadrul urmator') : bad('adnotare taste', 'cadrul nu s-a schimbat');
      // editare pe canvas, fara salvare: chenar nou prin tragere, clasa din tasta, undo, stergere
      const leg = async () => (await page.locator('#ad-legend').innerText()).replace(/\s+/g, ' ');
      const l0 = await leg();
      const bb = await page.locator('#ad-canvas').boundingBox();
      await page.keyboard.press('3');
      await page.mouse.move(bb.x + bb.width * 0.3, bb.y + bb.height * 0.3);
      await page.mouse.down(); await page.mouse.move(bb.x + bb.width * 0.5, bb.y + bb.height * 0.6, { steps: 6 }); await page.mouse.up();
      await page.waitForTimeout(200);
      const l1 = await leg();
      l1 !== l0 && (await page.locator('#ad-confirm').innerText()) === 'Salvează' ? ok('adnotare: un chenar nou prin tragere (butonul devine „Salveaza”)') : bad('adnotare editare', l1);
      await page.keyboard.press('1'); await page.waitForTimeout(100);
      (await leg()).includes('person ×') ? ok('adnotare: tasta 1 schimba clasa chenarului selectat') : bad('adnotare clasa', await leg());
      await page.keyboard.press('Delete'); await page.waitForTimeout(100);
      (await leg()) === l0 ? ok('adnotare: Delete sterge chenarul selectat') : bad('adnotare stergere', await leg());
      await page.keyboard.press('Control+z'); await page.waitForTimeout(100);
      (await leg()) !== l0 ? ok('adnotare: Ctrl+Z aduce chenarul inapoi') : bad('adnotare undo', await leg());
      await page.keyboard.press('Control+z'); await page.keyboard.press('Control+z'); await page.keyboard.press('Control+z');
      await page.waitForTimeout(100);
      (await leg()) === l0 ? ok('adnotare: undo pana la starea initiala') : bad('adnotare undo-tot', await leg());
      // nimic nu s-a salvat: cadrul ramane neverificat pe server
      const l2 = await (await page.request.get(BASE + '/api/adnotare?limit=1')).json();
      l2.verificate === l.verificate ? ok('adnotare: editarea fara salvare nu a atins datasetul') : bad('adnotare salvare', `${l2.verificate} vs ${l.verificate}`);
      // coada cadrelor nesigure: scanare mica in nvr-ai, lista cu scor, cadru cu incredere
      await page.keyboard.press('Control+z');   // curat: fara modificari nesalvate
      const sc = await (await page.request.post(BASE + '/api/adnotare/nesigure/scaneaza', { headers: csrf, data: { n: 20 } })).json();
      if (sc.error) note('nesigure: ' + sc.error);
      else {
        let st = null;
        for (let k = 0; k < 30; k++) { await page.waitForTimeout(1000); st = await (await page.request.get(BASE + '/api/adnotare/nesigure/stare')).json(); if (st.scan && !st.scan.activ) break; }
        st && st.scan && !st.scan.activ && !st.scan.eroare ? ok(`nesigure: scanarea a trecut prin ${st.scan.total} cadre, ${st.scan.gasite} nesigure (in coada ${st.in_coada})`) : bad('nesigure scan', JSON.stringify(st).slice(0, 120));
        const q = await (await page.request.get(BASE + '/api/adnotare?split=nesigure&limit=3')).json();
        Array.isArray(q.cadre) ? ok(`nesigure: coada se listeaza (${q.total})`) : bad('nesigure lista', JSON.stringify(q).slice(0, 100));
        if (q.cadre.length) {
          const d = await (await page.request.get(BASE + `/api/adnotare/cadru/nesigure/${encodeURIComponent(q.cadre[0].fn)}`)).json();
          d.boxes && d.boxes.length && 'conf' in d.boxes[0] ? ok('nesigure: cadrul vine pre-etichetat, cu incredere pe chenare') : bad('nesigure cadru', JSON.stringify(d).slice(0, 100));
          await page.selectOption('#ad-split', 'nesigure');
          await page.waitForFunction(() => (document.querySelector('#ad-count')?.textContent || '').includes('nesigure în coadă'), { timeout: 10000 }).catch(() => {});
          (await page.locator('#ad-ignore').isVisible()) ? ok('nesigure: in UI apare butonul „Ignora” pentru coada') : bad('nesigure ui', 'fara Ignora');
          await page.selectOption('#ad-split', '');
        }
      }
    }
  }

  console.log('\n== 23. Fluxul de evenimente de langa Live ==');
  {
    const f = await (await page.request.get(BASE + '/api/flux?limit=5')).json();
    Array.isArray(f.cadre) && 'zi' in f ? ok(`flux: API-ul da cadrele de azi (${f.cadre.length} din ultimele 5)`) : bad('flux api', JSON.stringify(f).slice(0, 100));
    if (f.cadre.length) {
      const c = f.cadre[0];
      ('cat' in c && 'cam_nume' in c && c.thumb) ? ok(`flux: cadrul are categorie (${c.cat}), camera si miniatura`) : bad('flux cadru', JSON.stringify(c).slice(0, 100));
      const th = await page.request.get(BASE + c.thumb);
      th.status() === 200 && (th.headers()['content-type'] || '').includes('image/jpeg') ? ok('flux: miniatura se serveste') : bad('flux thumb', 'status ' + th.status());
      const dupa = await (await page.request.get(BASE + `/api/flux?dupa=${encodeURIComponent(c.time)}`)).json();
      dupa.cadre.every((x) => x.time > c.time) ? ok('flux: ?dupa= intoarce doar cadrele mai noi') : bad('flux dupa', 'a intors si cadre vechi');
    }
    (await page.request.get(BASE + '/api/flux/thumb/2026-10-24/..%2Fcameras.json')).status() === 404 ? ok('flux: caile cu .. sunt refuzate la miniaturi') : bad('flux thumb cale', 'a servit ceva');
    await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => { try { localStorage.setItem('fluxOn', '1'); } catch {} });
    await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#flux-list .flux-item').length > 0, { timeout: 15000 }).catch(() => {});
    const n = await page.locator('#flux-list .flux-item').count();
    (await page.locator('#flux').isVisible()) ? ok(`flux: panoul e langa grila (${n} cadre fara miscarea simpla)`) : bad('flux ui', 'panoul nu e vizibil');
    await page.click('#flux-chips button[data-cat="miscare"]'); await page.waitForTimeout(300);
    const nm = await page.locator('#flux-list .flux-item').count();
    await page.click('#flux-chips button[data-cat=""]'); await page.waitForTimeout(200);
    if (n || nm) {
      if (!n) await page.click('#flux-chips button[data-cat="miscare"]');
      await page.click('#flux-chips button[data-cat="miscare"]'); await page.waitForTimeout(300);
      const toate = await page.evaluate(() => [...document.querySelectorAll('#flux-list .flux-item')].every((b) => b.dataset.cat === 'miscare'));
      toate ? ok('flux: chip-ul „Miscare” filtreaza lista') : bad('flux filtru', 'au ramas alte categorii');
      await page.click('#flux-chips button[data-cat=""]'); await page.waitForTimeout(200);
      await page.locator('#flux-list .flux-item').first().click(); await page.waitForTimeout(600);
      (await page.evaluate(() => document.querySelector('#fisa').open)) ? ok('flux: click pe un cadru deschide fisa evenimentului') : bad('flux click', 'fisa nu s-a deschis');
      await page.click('#fisa-play'); await page.waitForTimeout(800);
      (await page.evaluate(() => location.hash)).startsWith('#pb/') ? ok('flux: „Reda momentul” din fisa deschide redarea la moment') : bad('flux reda', await page.evaluate(() => location.hash));
      await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(800);
    }
    await page.click('#flux-toggle'); await page.waitForTimeout(200);
    !(await page.locator('#flux').isVisible()) ? ok('flux: butonul „Flux” il ascunde') : bad('flux toggle', 'a ramas vizibil');
    await page.click('#flux-toggle'); await page.waitForTimeout(200);
  }

  console.log('\n== 24. Fisa evenimentului: din flux, jurnal si incidente ==');
  {
    await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#flux-list .flux-item').length > 0, { timeout: 15000 }).catch(() => {});
    if (await page.locator('#flux-list .flux-item').count()) {
      await page.locator('#flux-list .flux-item').first().click(); await page.waitForTimeout(500);
      (await page.evaluate(() => document.querySelector('#fisa').open)) ? ok('fisa: se deschide din flux (' + (await page.locator('#fisa-title').innerText()) + ')') : bad('fisa flux', 'dialogul nu s-a deschis');
      (await page.locator('#fisa-img').isVisible()) ? ok('fisa: poza evenimentului e in fisa') : bad('fisa poza', 'fara poza');
      (await page.locator('#fisa-export').getAttribute('href') || '').includes('/api/export?channel=') ? ok('fisa: exportul de 30 s are intervalul calculat') : bad('fisa export', 'href gresit');
      await page.click('#fisa-play'); await page.waitForTimeout(700);
      (await page.evaluate(() => location.hash)).startsWith('#pb/') && !(await page.evaluate(() => document.querySelector('#fisa').open)) ? ok('fisa: „Reda momentul” inchide fisa si deschide redarea') : bad('fisa reda', await page.evaluate(() => location.hash));
    } else note('fisa: fluxul e gol, nu pot deschide din el');
    await page.goto(BASE + '/#ev/jurnal', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#ev-list .fisa-btn').length > 0, { timeout: 15000 }).catch(() => {});
    if (await page.locator('#ev-list .fisa-btn').count()) {
      await page.locator('#ev-list .fisa-btn').first().click(); await page.waitForTimeout(400);
      (await page.evaluate(() => document.querySelector('#fisa').open)) ? ok('fisa: se deschide din jurnal') : bad('fisa jurnal', 'nu s-a deschis');
      await page.keyboard.press('Escape'); await page.waitForTimeout(200);
      !(await page.evaluate(() => document.querySelector('#fisa').open)) ? ok('fisa: Esc o inchide') : bad('fisa esc', 'a ramas deschisa');
    }
    await page.goto(BASE + '/#ev/inc', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#inc-list .fisa-btn').length > 0, { timeout: 15000 }).catch(() => {});
    if (await page.locator('#inc-list .fisa-btn').count()) {
      await page.locator('#inc-list .fisa-btn').first().click(); await page.waitForTimeout(400);
      (await page.locator('#fisa-tratare').isVisible()) ? ok('fisa: din incidente are confirmat / fals') : bad('fisa tratare', 'butoanele lipsesc');
      await page.click('#fisa-regula'); await page.waitForTimeout(700);
      (await page.locator('#aipane-reguli').isVisible()) && (await page.inputValue('#ru-name')).length > 0 ? ok('fisa: „Regula din asta” precompleteaza formularul de reguli') : bad('fisa regula', await page.inputValue('#ru-name'));
    }
  }

  console.log('\n== 25. Setari: cautare + avansate; AI pe intrebari; scurtatura Stare ==');
  {
    await page.click('#tab-stare'); await page.waitForTimeout(500);
    (await page.locator('#pane-stare').isVisible()) ? ok('„Stare” din antet deschide Setari -> Stare') : bad('tab stare', 'pane-ul nu e vizibil');
    // intr-o pagina proaspata (Setarile nu au fost deschise niciodata): scurtatura trebuie sa
    // incarce si starea, nu doar sa arate panoul (ramanea pe „Se încarcă...”, 2026-10-25)
    {
      const p2 = await ctx.newPage();
      await p2.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await p2.waitForSelector('#tab-stare', { timeout: 15000 });
      await p2.click('#tab-stare');
      const incarcat = await p2.waitForSelector('#sys-stats .sys-item', { timeout: 12000 }).then(() => true).catch(() => false);
      incarcat ? ok('scurtatura Stare incarca starea sistemului si intr-o pagina proaspata')
               : bad('tab stare', 'starea sistemului ramane pe „Se încarcă...”');
      const nvrRow = (await p2.locator('#sys-stats .sys-item').allInnerTexts()).find((t) => t.includes('NVR Hikvision')) || '';
      if (nvrRetras) {
        !nvrRow ? ok('Stare: NVR-ul retras nu mai are rand') : bad('stare nvr', 'randul NVR apare desi e retras');
      } else {
        /online|OFFLINE|se verific/.test(nvrRow) ? ok(`Stare arata NVR-ul Hikvision: ${nvrRow.replace(/\s+/g, ' ').slice(0, 60)}`)
                                               : bad('stare nvr', 'lipseste randul NVR Hikvision');
      }
      await p2.close();
    }
    await page.uncheck('#set-adv'); await page.waitForTimeout(200);
    await page.locator('.snav-item[data-pane="sistem"]').click(); await page.waitForTimeout(300);
    !(await page.locator('#mq-test').isVisible()) ? ok('setari: fara „avansate”, MQTT e ascuns') : bad('avansate', 'MQTT vizibil fara bifa');
    await page.check('#set-adv'); await page.waitForTimeout(300);
    (await page.evaluate(() => [...document.querySelectorAll('#pane-sistem .card-tabs button')].some((b) => b.textContent.includes('MQTT') && getComputedStyle(b).display !== 'none')))
      ? ok('setari: cu „avansate”, tab-ul MQTT apare') : bad('avansate', 'tab-ul MQTT lipseste');
    await page.fill('#set-q', 'telegram'); await page.waitForTimeout(300);
    const hits = await page.locator('#set .set-card.q-hit').count();
    hits > 0 && (await page.evaluate(() => [...document.querySelectorAll('#set .set-card.q-hit h2')].some((h) => /Telegram/.test(h.textContent))))
      ? ok(`setari: cautarea „telegram” gaseste ${hits} carduri, inclusiv Notificari Telegram`) : bad('cautare setari', 'hits=' + hits);
    await page.fill('#set-q', ''); await page.waitForTimeout(200);
    !(await page.evaluate(() => document.querySelector('#set').classList.contains('cautare'))) ? ok('setari: golirea cautarii revine la sectiuni') : bad('cautare setari', 'a ramas in cautare');
    await page.click('#tab-ai'); await page.waitForTimeout(2500);
    /persoane cunoscute/.test(await page.locator('#ai-stare-pers').innerText()) ? ok('AI: linia de stare „cine intra?” vine din date (' + (await page.locator('#ai-stare-pers').innerText()).slice(0, 50) + '…)') : bad('ai stare', await page.locator('#ai-stare-pers').innerText());
    (await page.locator('.ainav-item[data-pane="pers"] small').count()) === 1 ? ok('AI: sectiunile au intrebarea sub nume') : bad('ai nav', 'fara intrebari');
  }

  console.log('\n== 26. Necunoscutii ca persoane: grupuri permanente, id-uri stabile, regula „plecat” ==');
  {
    const f1 = await (await page.request.get(BASE + '/api/faces')).json();
    Array.isArray(f1.grupuri) && Array.isArray(f1.perechi) ? ok(`fete: /api/faces are grupurile permanente (${f1.grupuri.length}) si perechile (${f1.perechi.length})`) : bad('grupuri api', JSON.stringify(f1).slice(0, 100));
    const rg = await (await page.request.post(BASE + '/api/faces/regrupeaza', { headers: csrf, data: {} })).json();
    rg.ok ? ok(`fete: regruparea la cerere raspunde (${rg.grupuri} grupuri)`) : bad('regrupeaza', JSON.stringify(rg).slice(0, 100));
    const f2 = await (await page.request.get(BASE + '/api/faces')).json();
    const ids1 = f1.grupuri.map((g) => g.id).join(','), ids2 = f2.grupuri.map((g) => g.id).join(',');
    ids1 === ids2 ? ok('fete: id-urile grupurilor raman aceleasi dupa regrupare') : bad('id-uri stabile', `${ids1} vs ${ids2}`);
    if (f2.grupuri.length) {
      const g = f2.grupuri[0];
      g.id.startsWith('nec-') && g.n >= 2 && g.mostre.length && (g.ultima === null || 'cam' in g.ultima) ? ok(`fete: grupul ${g.id} are poze, mostre si ultima aparitie`) : bad('grup', JSON.stringify(g).slice(0, 120));
    }
    await page.click('#tab-ai'); await page.locator('.ainav-item[data-pane="pers"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#fc-groups .fc-group').length > 0 || /Niciun grup/.test(document.querySelector('#fc-groups')?.textContent || ''), { timeout: 15000 }).catch(() => {});
    const n = await page.locator('#fc-groups .fc-group').count();
    n === f2.grupuri.length ? ok(`fete: UI arata ${n} carduri „Necunoscut N”`) : bad('grupuri ui', `${n} vs ${f2.grupuri.length}`);
    // regula preset: creata prin API ca din buton, apoi stearsa
    const r = await (await page.request.post(BASE + '/api/rules', { headers: csrf, data: { nume: 'Fata necunoscuta cand sunt plecat (test UI)', declansator: { tip: 'persoana_necunoscuta', valoare: '' }, moduri: ['plecat', 'noapte'], cooldown: 120, actiuni: [{ tip: 'notificare' }] } })).json();
    r.ok && r.rule && r.rule.moduri.includes('plecat') ? ok('fete: regula „fata necunoscuta cand sunt plecat” se creeaza cu modurile plecat + noapte') : bad('regula plecat', JSON.stringify(r).slice(0, 120));
    if (r.ok) await page.request.delete(BASE + '/api/rules/' + r.rule.id, { headers: csrf });
    (await page.locator('#fc-regula-plecat').count()) === 1 ? ok('fete: butonul presetului e in Persoane') : bad('preset ui', 'butonul lipseste');
  }

  console.log('\n== 27. O singura cautare: antet, tasta /, surse combinate, fisa din rezultat ==');
  {
    await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(800);
    await page.keyboard.press('/'); await page.waitForTimeout(200);
    (await page.evaluate(() => document.activeElement && document.activeElement.id)) === 'q-global' ? ok('cautare: tasta / duce in campul din antet') : bad('cautare /', 'focusul nu e in camp');
    await page.fill('#q-global', 'om'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#cauta')?.open && !/caut…/.test(document.querySelector('#cauta-info')?.textContent || ''), { timeout: 30000 }).catch(() => {});
    (await page.evaluate(() => document.querySelector('#cauta').open)) ? ok('cautare: dialogul cu rezultate se deschide') : bad('cautare dialog', 'nu s-a deschis');
    const info = await page.locator('#cauta-info').innerText();
    const n = await page.locator('#cauta-grid .q-item').count();
    n > 0 ? ok(`cautare: „om” da ${n} rezultate (${info.slice(0, 40)}…)`) : note('cautare: niciun rezultat pentru „om” (' + info + ')');
    const chips = await page.evaluate(() => [...document.querySelectorAll('#cauta-chips button')].map((b) => b.textContent.trim()));
    chips.length >= 5 && chips.every((c) => /\d+$/.test(c)) ? ok('cautare: chips-urile arata numarul pe fiecare sursa (' + chips.join(', ') + ')') : bad('cautare chips', chips.join(', '));
    if (n) {
      await page.click('#cauta-chips button[data-cat="jurnal"]'); await page.waitForTimeout(200);
      const nj = await page.locator('#cauta-grid .q-item').count();
      (await page.evaluate(() => [...document.querySelectorAll('#cauta-grid .q-item')].every((b) => b.dataset.cat === 'jurnal'))) ? ok(`cautare: filtrul „Jurnal” lasa doar evenimentele (${nj})`) : bad('cautare filtru', 'au ramas alte surse');
      await page.click('#cauta-chips button[data-cat=""]'); await page.waitForTimeout(200);
      await page.locator('#cauta-grid .q-item').first().click(); await page.waitForTimeout(500);
      (await page.evaluate(() => document.querySelector('#fisa').open && !document.querySelector('#cauta').open)) ? ok('cautare: click pe un rezultat inchide cautarea si deschide fisa') : bad('cautare fisa', 'fisa nu s-a deschis');
      await page.keyboard.press('Escape');
    } else { await page.keyboard.press('Escape'); }
  }

  console.log('\n== 28. Camere: sanatatea fiecarei camere in lista (ultimul segment, goluri azi) ==');
  {
    await page.click('#tab-cams'); await page.waitForTimeout(500);
    await page.waitForFunction(() => document.querySelectorAll('#cams-table .row-state small').length > 0, { timeout: 15000 }).catch(() => {});
    const n = await page.locator('#cams-table .row-state small').count(), tot = await page.locator('#cams-table .row-state').count();
    n > 0 ? ok(`camere: ${n}/${tot} randuri au detaliul de sanatate (ultimul segment / goluri)`) : bad('camere sanatate', 'niciun detaliu');
    // tabelul se poate reconstrui intre timp (pulsul); asteptam textul, nu doar elementele
    const gasit = await page.waitForFunction(() => [...document.querySelectorAll('#cams-table .row-state small')].some((s) => /ultimul segment acum|nu scrie de|la mișcare|înregistrare oprită/.test(s.textContent)), { timeout: 8000 }).then(() => true).catch(() => false);
    gasit ? ok('camere: detaliul spune cand a scris ultimul segment') : bad('camere detaliu', 'textul nu a aparut in 8 s');
  }

  console.log('\n== 29. Reguli „smart”: trecere de linie cu sens, zona, intrus; regula din linie ==');
  {
    const r = await (await page.request.get(BASE + '/api/rules')).json();
    const trig = r.triggers || r.capabilities || {};
    ['trecere', 'intrare_zona', 'intrus', 'obiect_nou'].every((k) => k in trig) ? ok('reguli: declansatoarele noi sunt in API') : bad('reguli triggers', Object.keys(trig).join(','));
    const nou = await (await page.request.post(BASE + '/api/rules', { headers: csrf, data: { nume: 'Trecere poarta intra (test UI)', declansator: { tip: 'trecere', valoare: 'poarta:intra@person' }, actiuni: [{ tip: 'notificare' }], moduri: ['plecat'] } })).json();
    nou.ok && nou.rule.declansator.valoare === 'poarta:intra@person' ? ok('reguli: o regula pe linie cu sens si eticheta se salveaza') : bad('reguli trecere', JSON.stringify(nou).slice(0, 120));
    if (nou.ok) await page.request.delete(BASE + '/api/rules/' + nou.rule.id, { headers: csrf });
    await page.click('#tab-ai'); await page.locator('.ainav-item[data-pane="reguli"]').click(); await page.waitForTimeout(600);
    (await page.evaluate(() => [...document.querySelectorAll('#ru-trigger option')].some((o) => o.value === 'trecere'))) ? ok('reguli: formularul are „linie de numarare trecuta” in lista de declansatoare') : bad('reguli ui', 'optiunea lipseste');
    await page.selectOption('#ru-trigger', 'trecere'); await page.waitForTimeout(300);
    (await page.evaluate(() => [...document.querySelectorAll('#ru-value-list option')].some((o) => o.value === 'intra'))) ? ok('reguli: sugestiile pentru trecere includ sensul (intra / iese)') : bad('reguli sugestii', 'fara sugestii');
    await page.evaluate(() => import('/js/70-ai.js').then((m) => m.regulaDin({ tip: 'intrare_zona', valoare: 'curte', cam: 101, nume: 'Intrare in curte — test' })));
    await page.waitForTimeout(500);
    (await page.inputValue('#ru-trigger')) === 'intrare_zona' && (await page.inputValue('#ru-value')) === 'curte' && (await page.inputValue('#ru-name')).includes('curte')
      ? ok('reguli: regulaDin() precompleteaza declansatorul, valoarea, camera si numele') : bad('regulaDin', (await page.inputValue('#ru-trigger')) + ' / ' + (await page.inputValue('#ru-value')));
  }

  console.log('\n== 30. „Gaseste-l pe toate camerele”: arhiva re-ID, cautare din poza, dialogul ==');
  {
    const f = await (await page.request.get(BASE + '/api/flux?limit=60')).json();
    const om = (f.cadre || []).find((c) => c.cat === 'om');
    if (!om) note('reid: niciun cadru cu om in flux azi, sar cautarea din poza');
    else {
      const r = await page.request.get(BASE + '/api/reid/cauta?poza=' + encodeURIComponent(om.url) + '&zile=7');
      const d = await r.json();
      if (r.status() === 200) ok(`reid: cautarea din poza raspunde (${(d.aparitii || []).length} aparitii, arhiva ${(d.arhiva || {}).esantioane_7_zile || 0} esantioane)`);
      else if (r.status() === 400 && /om|prea mic/.test(d.error || '')) ok('reid: poza fara om utilizabil e refuzata curat (' + d.error + ')');
      else bad('reid cauta', r.status() + ' ' + JSON.stringify(d).slice(0, 100));
    }
    const rau = await page.request.get(BASE + '/api/reid/cauta?poza=' + encodeURIComponent('../../cameras.json'));
    rau.status() === 400 ? ok('reid: o cale invalida e refuzata') : bad('reid cale', 'status ' + rau.status());
    (await page.request.get(BASE + '/api/reid/crop/2026-01-01/..%2Fx.jpg')).status() === 404 ? ok('reid: crop-urile cu .. sunt refuzate') : bad('reid crop', 'a servit ceva');
    await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#flux-list .flux-item').length > 0, { timeout: 15000 }).catch(() => {});
    if (await page.locator('#flux-list .flux-item').count()) {
      await page.locator('#flux-list .flux-item').first().click(); await page.waitForTimeout(500);
      (await page.locator('#fisa-reid').isVisible()) ? ok('reid: fisa unui cadru cu poza are butonul „Gaseste-l pe toate camerele”') : bad('reid buton', 'butonul lipseste');
      await page.click('#fisa-reid');
      await page.waitForFunction(() => document.querySelector('#reid')?.open && !/caut…/.test(document.querySelector('#reid-info')?.textContent || ''), { timeout: 60000 }).catch(() => {});
      (await page.evaluate(() => document.querySelector('#reid').open)) ? ok('reid: dialogul cu aparitii se deschide (' + (await page.locator('#reid-info').innerText()).slice(0, 60) + '…)') : bad('reid dialog', 'nu s-a deschis');
      await page.keyboard.press('Escape');
    }
  }

  console.log('\n== 31. Sabotaj camera: categorie in flux, contor pe camera, sugestie in reguli ==');
  {
    const sb = await (await page.request.get(BASE + '/api/sabotaj')).json();
    sb && typeof sb.sabotaj === 'object' ? ok(`sabotaj: contorul pe camere raspunde (${Object.keys(sb.sabotaj).length} camere azi)`) : bad('sabotaj api', JSON.stringify(sb).slice(0, 80));
    await page.goto(BASE + '/#live', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(800);
    (await page.locator('#flux-chips button[data-cat="sabotaj"]').count()) === 1 ? ok('sabotaj: fluxul are chip-ul „Sabotaj”') : bad('sabotaj chip', 'lipseste');
    await page.click('#tab-ai'); await page.locator('.ainav-item[data-pane="reguli"]').click(); await page.waitForTimeout(500);
    await page.selectOption('#ru-trigger', 'alarma_camera'); await page.waitForTimeout(200);
    (await page.evaluate(() => [...document.querySelectorAll('#ru-value-list option')].some((o) => o.value === 'shelteralarm'))) ? ok('sabotaj: „obiectiv acoperit” e in sugestiile regulilor') : bad('sabotaj reguli', 'fara sugestie');
  }

  console.log('\n== 32. Rezumatul zilei pe Telegram: text, setari, buton ==');
  {
    const t = await (await page.request.get(BASE + '/api/digest/text?day=2026-10-24')).json();
    (t.text || '').startsWith('📋 Rezumatul zilei 2026-10-24') ? ok('rezumat: textul zilei se construieste (' + t.text.split('\n')[1].slice(0, 50) + ')') : bad('rezumat text', JSON.stringify(t).slice(0, 100));
    const s0 = await (await page.request.get(BASE + '/api/settings')).json();
    const r = await (await page.request.post(BASE + '/api/settings', { headers: csrf, data: { digest_telegram: false, digest_ora: '20:45' } })).json();
    const s1 = await (await page.request.get(BASE + '/api/settings')).json();
    s1.digest_ora === '20:45' && s1.digest_telegram === false ? ok('rezumat: ora si comutatorul se salveaza in setari') : bad('rezumat setari', JSON.stringify({ r, ora: s1.digest_ora }).slice(0, 100));
    await page.request.post(BASE + '/api/settings', { headers: csrf, data: { digest_telegram: !!s0.digest_telegram, digest_ora: s0.digest_ora || '21:00' } });
    await page.click('#tab-set'); await page.locator('.snav-item[data-pane="notif"]').click(); await page.waitForTimeout(400);
    (await page.locator('#s-digest-on').count()) === 1 && (await page.locator('#s-digest-now').count()) === 1 ? ok('rezumat: comutatorul, ora si „Trimite acum” sunt in Notificari') : bad('rezumat ui', 'controalele lipsesc');
  }

  console.log('\n== 33. Canalele de notificare in Stare ==');
  {
    const sy = await (await page.request.get(BASE + '/api/system')).json();
    sy.notificari && 'canal' in sy.notificari && 'push' in sy.notificari ? ok(`stare: canalele de notificare sunt raportate (telegram=${sy.notificari.telegram}, push=${sy.notificari.push}, canal=${sy.notificari.canal})`) : bad('notificari api', JSON.stringify(sy.notificari));
    await page.click('#tab-stare'); await page.waitForTimeout(1500);
    (await page.locator('#sys-stats').innerText()).includes('Notificări') ? ok('stare: randul „Notificari” apare pe pagina Stare') : bad('notificari ui', 'randul lipseste');
  }

  console.log('\n== 34. Verificarea publica a probelor (/verifica, fara login) ==');
  {
    const pctx = await browser.newContext();
    const pp = await pctx.newPage();
    const g = await pp.goto(BASE + '/verifica', { waitUntil: 'domcontentloaded' });
    g.status() === 200 && (await pp.locator('input[name="sha256"]').count()) === 1 ? ok('verifica: pagina publica se deschide fara login') : bad('verifica', 'status ' + g.status());
    await pp.fill('input[name="sha256"]', 'b'.repeat(64)); await pp.click('button[type="submit"]'); await pp.waitForTimeout(800);
    (await pp.locator('.rez').innerText()).includes('necunoscut') ? ok('verifica: o amprenta necunoscuta e raportata ca atare') : bad('verifica', await pp.locator('.rez').innerText());
    const r2 = await pp.request.post(BASE + '/verifica', { form: { sha256: 'nu-e-hash' } });
    r2.status() === 200 && (await r2.text()).includes('necunoscut') ? ok('verifica: un hash invalid nu rupe pagina') : bad('verifica invalid', 'status ' + r2.status());
    await pctx.close();
  }

  await page.screenshot({ path: 'final.png', fullPage: false });
  await browser.close();

  console.log(`\n${'='.repeat(56)}`);
  console.log(`REZULTAT: ${pass.length} trecute, ${fail.length} esuate, ${warn.length} observatii`);
  if (fail.length) { console.log('\nESECURI:'); fail.forEach((f) => console.log('  ✗ ' + f)); }
  if (warn.length) { console.log('\nOBSERVATII:'); warn.forEach((w) => console.log('  ⚠ ' + w)); }
  process.exit(fail.length ? 1 : 0);
})();
