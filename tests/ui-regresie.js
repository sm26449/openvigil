// Suita IZOLATA de regresie a interfetei (audit 5, T6). Fara instanta live, DB, GPU sau
// camere: serveste app/static si intercepteaza /api/* cu date sintetice (tests/ui-fixtures.js).
// Reproduce exact starile pe care auditul le-a semnalat ca periculoase si pe care corecturile
// din 2.0.16→HEAD le-au rezolvat — ca sa ramana rezolvate la fiecare schimbare:
//   A1  salvarea config AI nu confirma „Salvat ✓" dupa HTTP 500
//   A2  editarea unei reguli OPRITE nu o reactiveaza (activ ramane false)
//   S1  daca o sectiune din Setari nu s-a incarcat, salvarea globala e blocata
//   S2  valoarea 0 dBFS se trimite ca 0, nu inlocuita cu -25
//   C2  lipsa telemetriei = „se verifica", niciodata verde „in regula"
//   L2  Enter pe un control din dala NU deschide HD (doar dala insasi, cu focus)
//   T1  „Contul meu" e buton real, focusabil de la tastatura
//   P0  PWA instalabil cu push: meta iOS, manifest (standalone/maskable/scurtaturi), icon-uri
//   M0  mobil (390px): fara debordare pe 6 ecrane; Inregistrari cu unelte export/analiza pliate
// Ruleaza: node tests/ui-regresie.js   (are nevoie de Chromium — npx playwright install chromium)
const { chromium } = require('playwright');
const { startStatic, installApi, err500 } = require('./ui-fixtures');

const pass = [], fail = [];
const ok = (m) => { pass.push(m); console.log('  ✓ ' + m); };
const bad = (m, d) => { fail.push(m + (d ? ' — ' + d : '')); console.log('  ✗ ' + m + (d ? ' — ' + d : '')); };
const note = (m) => console.log('  ⚠ ' + m);

// zgomotul de flux (go2rtc pe :8076 / WebRTC) nu e o defectiune a aplicatiei in izolare
const eStream = (t) => /:8076|go2rtc|videortc|video-stream|ws(e)?\b|websocket/i.test(t);

// deschide aplicatia cu un set de raspunsuri /api si asteapta boot-ul complet (app.js pune
// data-gata='2' dupa loadCameras().then(routeHash)). Intoarce { page, calls, errors, close }.
async function openApp(browser, { api = {}, viewport, mobile = false } = {}) {
  const dev = mobile ? { isMobile: true, hasTouch: true, deviceScaleFactor: 3 } : {};   // declanseaza pointer:coarse
  const ctx = await browser.newContext({ locale: 'ro-RO', viewport: viewport || { width: 1280, height: 900 }, bypassCSP: true, ...dev });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error' && !eStream(m.text())) errors.push(m.text().slice(0, 160)); });
  page.on('pageerror', (e) => { if (!eStream(String(e))) errors.push('EXCEPTIE: ' + String(e).slice(0, 160)); });
  const calls = await installApi(page, api);
  const origin = globalThis.__ORIGIN;
  await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForSelector('html[data-gata="2"]', { timeout: 15000 });
  return { page, calls, errors, close: () => ctx.close() };
}

const sawCall = (calls, path, method = 'POST') => calls.some((c) => c.path === path && c.method === method);
async function waitForCall(calls, path, method, timeout = 4000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const c = calls.find((x) => x.path === path && x.method === method);
    if (c) return c;
    await new Promise((r) => setTimeout(r, 50));
  }
  return null;
}
async function waitEnabled(page, sel, want, timeout = 6000) {
  try { await page.waitForFunction(([s, w]) => { const e = document.querySelector(s); return e && e.disabled === w; }, [sel, want], { timeout }); return true; }
  catch { return false; }
}

async function scenBoot(browser) {
  console.log('\n== 0. Boot izolat (sanity) ==');
  const app = await openApp(browser);
  try {
    const n = await app.page.locator('#grid .tile').count();
    n >= 1 ? ok(`grila randeaza ${n} dale din date sintetice (fara backend)`) : bad('boot', 'nicio dala in #grid');
    app.errors.length ? note(`erori de consola non-flux: ${app.errors.slice(0, 3).join(' | ')}`) : ok('fara erori de consola non-flux la boot');
  } finally { await app.close(); }
}

async function scenA1(browser) {
  console.log('\n== A1. Config AI: fara „Salvat ✓" dupa HTTP 500 ==');
  const app = await openApp(browser, {
    api: { '/api/faces/config': ({ method }) => method === 'POST' ? err500() : { enabled: false, notify: false, unknown_save: false, antispoof: false } },
  });
  const { page, calls } = app;
  try {
    await page.click('#tab-ai');
    await page.click('.ainav-item[data-pane="config"]');
    await page.waitForSelector('#fc-on', { state: 'visible', timeout: 5000 });
    await page.click('#fc-on');              // schimbam ceva ca salvarea sa trimita grupul faces
    await page.click('#ai-save');
    const c = await waitForCall(calls, '/api/faces/config', 'POST', 4000);
    if (!c) { bad('A1', 'UI nu a trimis POST /api/faces/config'); return; }
    ok('UI a trimis POST /api/faces/config');
    await page.waitForTimeout(200);
    const msg = (await page.locator('#ai-msg-save').textContent() || '').trim();
    /salvat/i.test(msg) && !/eroare/i.test(msg)
      ? bad('A1', `confirma fals salvarea dupa 500: „${msg}"`)
      : ok(`nu confirma salvarea dupa 500 (mesaj: „${msg || '(gol)'}")`);
  } finally { await app.close(); }
}

async function scenA2(browser) {
  console.log('\n== A2. Editarea unei reguli OPRITE nu o reactiveaza ==');
  const app = await openApp(browser, {
    api: { '/api/rules': { rules: [require('./ui-fixtures').REGULA({ activ: false })], triggers: { persoana_necunoscuta: 'Persoană necunoscută' }, zile: ['Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sa', 'Du'], groups: [] } },
  });
  const { page, calls } = app;
  try {
    await page.click('#tab-ai');
    await page.click('.ainav-item[data-pane="reguli"]');
    await page.waitForSelector('#ru-list .ru-edit', { timeout: 5000 });
    await page.click('#ru-list .ru-edit');                 // fillRuleForm(rule) — activ:false
    await page.fill('#ru-name', 'Regula redenumita');      // doar corectam numele
    await page.click('#ru-save');
    const c = await waitForCall(calls, '/api/rules', 'POST', 4000);
    if (!c) { bad('A2', 'UI nu a trimis POST /api/rules'); return; }
    c.body && c.body.activ === false
      ? ok('salvarea pastreaza activ:false (regula ramane oprita)')
      : bad('A2', `salvarea trimite activ=${JSON.stringify(c.body && c.body.activ)} — ar reactiva regula`);
  } finally { await app.close(); }
}

async function scenS1(browser) {
  console.log('\n== S1. O sectiune neincarcata blocheaza salvarea globala ==');
  const app = await openApp(browser, { api: { '/api/webhooks': { _abort: true } } });  // loadHooks prinde -> incarcareOk=false
  const { page } = app;
  try {
    await page.click('#tab-set');
    const disabled = await waitEnabled(page, '#s-save', true, 6000);
    disabled ? ok('#s-save e dezactivat cand /api/webhooks nu se incarca')
             : bad('S1', 'salvarea globala a ramas activa desi o sectiune a picat');
    const msg = (await page.locator('#s-msg-save').textContent() || '').trim();
    /blocat|nu s-au încărcat|incarc/i.test(msg) ? ok(`mesaj explicit: „${msg.slice(0, 60)}…"`) : note(`fara mesaj de blocare vizibil (text: „${msg}")`);
  } finally { await app.close(); }
}

async function scenS2(browser) {
  console.log('\n== S2. 0 dBFS se trimite ca 0, nu -25 ==');
  const app = await openApp(browser);
  const { page, calls } = app;
  try {
    await page.click('#tab-set');
    const enabled = await waitEnabled(page, '#s-save', false, 6000);
    if (!enabled) { bad('S2', '#s-save a ramas dezactivat (loaderele nu au terminat?)'); return; }
    await page.$eval('#noise-thr', (el) => { el.value = '0'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.click('#s-save');
    const c = await waitForCall(calls, '/api/settings', 'POST', 4000);
    if (!c) { bad('S2', 'UI nu a trimis POST /api/settings'); return; }
    c.body && c.body.noise_threshold_db === 0
      ? ok('noise_threshold_db trimis ca 0 (zero nu e tratat ca lipsa)')
      : bad('S2', `trimis noise_threshold_db=${JSON.stringify(c.body && c.body.noise_threshold_db)} in loc de 0`);
  } finally { await app.close(); }
}

async function scenC2(browser) {
  console.log('\n== C2. Lipsa telemetriei = „se verifica", nu verde ==');
  const app = await openApp(browser, { api: { '/api/pulse': {} } });   // nicio telemetrie
  const { page } = app;
  try {
    await page.click('#tab-cams');
    await page.waitForSelector('.row-state', { timeout: 5000 });
    const unknown = await page.locator('.row-state.s-unknown').count();
    const green = await page.locator('.row-state.s-ok').count();
    unknown >= 1 ? ok(`${unknown} camere pe „se verifica" fara telemetrie`) : bad('C2', 'nicio stare „se verifica"');
    green === 0 ? ok('niciun verde „in regula" fara telemetrie') : bad('C2', `${green} camere afisate verde desi nu exista telemetrie`);
  } finally { await app.close(); }
}

async function scenL2(browser) {
  console.log('\n== L2. Enter pe un control din dala NU deschide HD ==');
  const app = await openApp(browser);
  const { page } = app;
  try {
    const res = await page.evaluate(() => {
      const tile = document.querySelector('#grid .tile');
      if (!tile) return { err: 'no-tile' };
      const ov = document.querySelector('#hd-overlay');
      const child = tile.querySelector('button, a, input, select, [tabindex]');
      if (!child) return { err: 'no-child' };
      child.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      const afterChild = !ov.classList.contains('hidden');   // s-a deschis HD din gresala?
      tile.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      const afterTile = !ov.classList.contains('hidden');    // dala insasi ar trebui sa deschida
      return { afterChild, afterTile };
    });
    if (res.err) { note(`L2 sarit: ${res.err}`); return; }
    res.afterChild === false ? ok('Enter pe controlul copil nu deschide HD') : bad('L2', 'HD s-a deschis la Enter pe un control copil');
    res.afterTile ? ok('Enter pe dala insasi deschide HD (comportament pastrat)') : note('Enter pe dala nu a deschis HD (verifica separat)');
  } finally { await app.close(); }
}

async function scenT1(browser) {
  console.log('\n== T1. „Contul meu" e buton focusabil ==');
  const app = await openApp(browser);
  const { page } = app;
  try {
    const info = await page.evaluate(() => {
      const el = document.querySelector('#user-chip .me-open');
      if (!el) return { found: false };
      el.focus();
      return { found: true, tag: el.tagName, focused: document.activeElement === el };
    });
    if (!info.found) { bad('T1', 'lipseste #user-chip .me-open'); return; }
    info.tag === 'BUTTON' ? ok('„Contul meu" e <button>') : bad('T1', `este <${info.tag.toLowerCase()}>, nu buton`);
    info.focused ? ok('primeste focus de la tastatura') : bad('T1', 'nu e focusabil');
  } finally { await app.close(); }
}

async function scenC4(browser) {
  console.log('\n== C4. Lista de camere la 390px nu depaseste ecranul ==');
  const app = await openApp(browser, { viewport: { width: 390, height: 780 } });
  const { page } = app;
  try {
    await page.click('#tab-cams');
    await page.waitForSelector('.row-state', { timeout: 5000 }).catch(() => {});
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    over <= 2 ? ok(`fara derulare orizontala la 390px (debordare ${over}px)`) : bad('C4', `debordare orizontala ${over}px la 390px`);
  } finally { await app.close(); }
}

async function scenP0(browser) {
  console.log('\n== P0. PWA instalabil cu push (meta iOS, manifest, icon-uri, bloc instalare) ==');
  const app = await openApp(browser);
  const { page } = app;
  try {
    // meta-tagurile fara care iOS nu porneste PWA-ul pe tot ecranul (si deci nu da push)
    const ios = await page.evaluate(() => ({
      capable: document.querySelector('meta[name="apple-mobile-web-app-capable"]')?.content,
      title: document.querySelector('meta[name="apple-mobile-web-app-title"]')?.content,
      touch: document.querySelector('link[rel="apple-touch-icon"]')?.getAttribute('href'),
      manifest: document.querySelector('link[rel="manifest"]')?.getAttribute('href'),
    }));
    ios.capable === 'yes' ? ok('meta apple-mobile-web-app-capable=yes') : bad('P0', `meta iOS lipsa (capable=${ios.capable})`);
    ios.manifest ? ok(`link manifest prezent (${ios.manifest})`) : bad('P0', 'link manifest lipsa');
    ios.touch ? ok(`apple-touch-icon declarat (${ios.touch})`) : bad('P0', 'apple-touch-icon lipsa');

    // manifestul real: standalone, un icon maskable (Android nu taie din el), scurtaturi
    const man = await page.evaluate(async () => (await fetch('/manifest.json')).json());
    man.display === 'standalone' ? ok('manifest display=standalone') : bad('P0', `display=${man.display}`);
    (man.icons || []).some((i) => /maskable/.test(i.purpose || '')) ? ok('manifest are icon maskable') : bad('P0', 'niciun icon maskable in manifest');
    (man.shortcuts || []).length >= 2 ? ok(`manifest are ${man.shortcuts.length} scurtaturi`) : bad('P0', 'fara scurtaturi in manifest');

    // icon-urile referite chiar exista (altfel instalarea arata rupt)
    const st = await page.evaluate(async () => {
      const g = async (u) => (await fetch(u)).status;
      return { touch: await g('/apple-touch-icon.png'), mask: await g('/icon-maskable-512.png') };
    });
    st.touch === 200 && st.mask === 200 ? ok('apple-touch-icon.png + icon-maskable-512.png se incarca (200)') : bad('P0', `icon-uri lipsa (touch=${st.touch}, mask=${st.mask})`);

    // blocul de instalare exista in DOM (ascuns la boot; apare la deschiderea Setarilor)
    (await page.locator('#instalare-app').count()) === 1 ? ok('blocul de instalare (#instalare-app) e in pagina') : bad('P0', '#instalare-app lipseste');
  } finally { await app.close(); }
}

async function scenM0(browser) {
  console.log('\n== M0. Mobil (390px): fara debordare + Inregistrari cu unelte pliate ==');
  const app = await openApp(browser, { viewport: { width: 390, height: 844 }, mobile: true });
  const { page } = app;
  try {
    // 1) nicio debordare orizontala pe ecranele principale (la latime de telefon)
    const vinovat = [];
    for (const [nume, tab] of [['live', null], ['harta', '#tab-harta'], ['evenimente', '#tab-ev'], ['inregistrari', '#tab-pb'], ['camere', '#tab-cams'], ['setari', '#tab-set']]) {
      if (tab) { await page.evaluate((s) => document.querySelector(s)?.click(), tab); await page.waitForTimeout(250); }
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (over > 2) vinovat.push(`${nume}:${over}px`);
    }
    vinovat.length === 0 ? ok('fara debordare orizontala pe 6 ecrane la 390px') : bad('M0', 'debordare: ' + vinovat.join(', '));

    // 2) Inregistrari: uneltele de export/analiza pliate implicit, timeline deasupra fold-ului
    await page.evaluate(() => document.querySelector('#tab-pb')?.click());
    await page.waitForTimeout(300);
    const pliat = await page.evaluate(() => getComputedStyle(document.querySelector('#pb-unelte')).display === 'none');
    pliat ? ok('Inregistrari: unelte export/analiza pliate implicit pe mobil') : bad('M0', '#pb-unelte nu e pliat la 390px');
    const tlTop = await page.evaluate(() => document.querySelector('#timeline').getBoundingClientRect().top);
    tlTop < 844 ? ok(`timeline vizibil fara derulare (sus la ${Math.round(tlTop)}px)`) : bad('M0', `timeline sub fold (${Math.round(tlTop)}px)`);

    // 3) butonul „Unelte" chiar desface export/analiza
    await page.evaluate(() => document.querySelector('#pb-unelte-tgl')?.click());
    await page.waitForTimeout(100);
    const desfacut = await page.evaluate(() => getComputedStyle(document.querySelector('#pb-unelte')).display !== 'none');
    desfacut ? ok('butonul „Unelte" desface export/analiza') : bad('M0', 'Unelte nu deschide #pb-unelte');

    // 4) bara de transport: controalele secundare (.pb-sec) pliate implicit, „⋯" le desface
    const secAscuns = await page.evaluate(() => getComputedStyle(document.querySelector('#pb-ev-prev')).display === 'none');
    secAscuns ? ok('transport: controale secundare pliate implicit pe mobil') : bad('M0', '.pb-sec vizibile la 390px');
    await page.evaluate(() => document.querySelector('#pb-nav-more')?.click());
    await page.waitForTimeout(100);
    const secVizibil = await page.evaluate(() => getComputedStyle(document.querySelector('#pb-ev-prev')).display !== 'none');
    secVizibil ? ok('butonul „⋯" desface controalele secundare') : bad('M0', '⋯ nu desface .pb-sec');

    // 5) Live: camerele primele — actiuni secundare pliate, bara de vizualizari ascunsa pe mobil
    await page.evaluate(() => document.querySelector('#tab-live')?.click());
    await page.waitForTimeout(250);
    const liveSecAscuns = await page.evaluate(() => getComputedStyle(document.querySelector('#preview')).display === 'none');
    liveSecAscuns ? ok('Live: actiuni secundare pliate implicit pe mobil') : bad('M0', 'Live .live-sec vizibile la 390px');
    const viewsAscuns = await page.evaluate(() => getComputedStyle(document.querySelector('#live .views-bar')).display === 'none');
    viewsAscuns ? ok('Live: bara de vizualizari ascunsa pe mobil') : bad('M0', 'views-bar vizibila la 390px');
    await page.evaluate(() => document.querySelector('#live-more')?.click());
    await page.waitForTimeout(100);
    const liveSecVizibil = await page.evaluate(() => getComputedStyle(document.querySelector('#preview')).display !== 'none');
    liveSecVizibil ? ok('butonul „⋯" (Live) desface actiunile secundare') : bad('M0', '⋯ Live nu desface .live-sec');

    // 6) Setari: toate sub-taburile se vad (se rup pe randuri, niciunul taiat la dreapta)
    await page.evaluate(() => document.querySelector('#tab-set')?.click());
    await page.waitForTimeout(250);
    const taiate = await page.evaluate(() => {
      const w = document.documentElement.clientWidth;
      return [...document.querySelectorAll('#set-nav .snav-item')].filter((e) => e.getBoundingClientRect().right > w + 1).length;
    });
    taiate === 0 ? ok('Setari: toate sub-taburile vizibile (niciunul taiat la dreapta)') : bad('M0', `${taiate} sub-taburi taiate la dreapta`);

    // 7) campuri text >= 16px pe mobil (altfel iOS da zoom la focus)
    const inputMic = await page.evaluate(() => {
      const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && e.offsetParent !== null; };
      return [...document.querySelectorAll('input, select, textarea')].filter((e) => {
        if (!vis(e)) return false;
        const t = (e.getAttribute('type') || '').toLowerCase();
        if (['checkbox', 'radio', 'range', 'color', 'hidden'].includes(t)) return false;
        return parseFloat(getComputedStyle(e).fontSize) < 16;
      }).length;
    });
    inputMic === 0 ? ok('campuri text >=16px pe mobil (fara zoom iOS la focus)') : bad('M0', `${inputMic} campuri <16px`);

    // 8) Camere: actiunile de admin (scanare, selectare in masa) pliate implicit, „⋯" le desface
    await page.evaluate(() => document.querySelector('#tab-cams')?.click());
    await page.waitForTimeout(250);
    const camsAscuns = await page.evaluate(() => getComputedStyle(document.querySelector('#scan-open')).display === 'none');
    camsAscuns ? ok('Camere: actiuni admin pliate implicit pe mobil') : bad('M0', 'Camere .cams-sec vizibile la 390px');
    await page.evaluate(() => document.querySelector('#cams-more')?.click());
    await page.waitForTimeout(100);
    const camsVizibil = await page.evaluate(() => getComputedStyle(document.querySelector('#scan-open')).display !== 'none');
    camsVizibil ? ok('butonul „⋯" (Camere) desface actiunile admin') : bad('M0', '⋯ Camere nu desface .cams-sec');

    // 9) Evenimente: Rezumat + Dosare (secundare) pliate implicit, „⋯" le desface
    await page.evaluate(() => document.querySelector('#tab-ev')?.click());
    await page.waitForTimeout(250);
    const incAscuns = await page.evaluate(() => getComputedStyle(document.querySelector('#inc-digest')).display === 'none');
    incAscuns ? ok('Evenimente: Rezumat/Dosare pliate implicit pe mobil') : bad('M0', 'Evenimente .inc-sec vizibile la 390px');
    await page.evaluate(() => document.querySelector('#inc-more')?.click());
    await page.waitForTimeout(100);
    const incVizibil = await page.evaluate(() => getComputedStyle(document.querySelector('#inc-digest')).display !== 'none');
    incVizibil ? ok('butonul „⋯" (Evenimente) desface Rezumat/Dosare') : bad('M0', '⋯ Evenimente nu desface .inc-sec');
  } finally { await app.close(); }
}

async function scenHD(browser) {
  console.log('\n== HD. Vizualizare camera pe telefon: swipe intre camere + iesire ==');
  const app = await openApp(browser, { viewport: { width: 390, height: 844 }, mobile: true });
  const { page } = app;
  const swipe = (x1, y1, x2, y2) => page.evaluate(([x1, y1, x2, y2]) => {
    const ov = document.querySelector('#hd-overlay');
    const mk = (type, x, y, keep) => { const tch = new Touch({ identifier: 1, target: ov, clientX: x, clientY: y });
      ov.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true, touches: keep ? [tch] : [], changedTouches: [tch] })); };
    mk('touchstart', x1, y1, true); mk('touchend', x2, y2, false);
  }, [x1, y1, x2, y2]);
  try {
    await page.evaluate(() => document.querySelector('#grid .tile')?.click());
    await page.waitForSelector('#hd-overlay:not(.hidden)', { timeout: 5000 });
    const t1 = await page.evaluate(() => document.querySelector('#hd-title').textContent);
    /(^|\s)1\./.test(t1) ? ok(`HD deschis pe camera 1 (${t1})`) : bad('HD', `titlu neasteptat: ${t1}`);

    // „✕ Inchide" vizibil si de 44px (pe iPhone safe-area il scoate de sub notch)
    const close = await page.evaluate(() => { const e = document.querySelector('#hd-close'); const r = e.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), vis: getComputedStyle(e).display !== 'none' }; });
    (close.vis && close.w >= 44 && close.h >= 44) ? ok(`buton Inchide 44px vizibil (${close.w}x${close.h})`) : bad('HD', `Inchide prea mic/ascuns: ${JSON.stringify(close)}`);

    await swipe(320, 400, 50, 410);   // swipe stanga -> camera urmatoare
    await page.waitForTimeout(300);
    const t2 = await page.evaluate(() => document.querySelector('#hd-title').textContent);
    (t2 && t2 !== t1) ? ok(`swipe stanga trece la alta camera (${t2})`) : bad('HD', `swipe nu a schimbat camera (inca ${t2})`);

    await swipe(200, 200, 200, 520);  // swipe jos -> inchide
    await page.waitForTimeout(300);
    const inchis = await page.evaluate(() => document.querySelector('#hd-overlay').classList.contains('hidden'));
    inchis ? ok('swipe jos inchide HD (revine la grila)') : bad('HD', 'swipe jos nu a inchis HD');
  } finally { await app.close(); }
}

(async () => {
  const srv = await startStatic();
  globalThis.__ORIGIN = srv.origin;
  const browser = await chromium.launch();
  try {
    for (const scen of [scenBoot, scenA1, scenA2, scenS1, scenS2, scenC2, scenL2, scenT1, scenC4, scenP0, scenM0, scenHD]) {
      try { await scen(browser); }
      catch (e) { bad(scen.name, 'exceptie in scenariu: ' + String(e).slice(0, 160)); }
    }
  } finally {
    await browser.close();
    await srv.close();
  }
  console.log(`\n==== ${pass.length} trecute, ${fail.length} picate ====`);
  if (fail.length) { console.log('PICATE:\n  - ' + fail.join('\n  - ')); process.exit(1); }
  process.exit(0);
})();
