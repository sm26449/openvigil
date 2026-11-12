// Audit MOBIL izolat: emuleaza un iPhone, deschide fiecare ecran principal si (1) salveaza
// capturi (viewport „above-the-fold" + pagina intreaga), (2) masoara ce strica UX-ul pe
// telefon: debordare orizontala, tinte de atingere prea mici (<44px, HIG) SAU prea mari
// (inaltime exagerata = spatiu irosit), inaltimea antetului fata de continut, fonturi <12px.
// Nu modifica nimic — doar dovezi pentru optimizare. Ruleaza in container (Node >=20):
//   node tests/ui-audit-mobil.js
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { startStatic, installApi } = require('./ui-fixtures');

const OUT = path.join(__dirname, '_audit');
fs.mkdirSync(OUT, { recursive: true });
const eStream = (t) => /:8076|go2rtc|videortc|video-stream|ws(e)?\b|websocket/i.test(t);

// iPhone 13 (390x844, DPR3, tactil) — reper comun pentru „telefon"
const IPHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true };

// [nume, tabClick, selectorPrimar] — primarul = continutul principal care trebuie vazut
// fara derulare (top < inaltimea ecranului)
const ECRANE = [
  ['live', null, '#grid .tile'], ['harta', '#tab-harta', '#harta-wrap'], ['evenimente', '#tab-ev', '#ev-list, #evjurnal'],
  ['inregistrari', '#tab-pb', '#timeline'], ['ai', '#tab-ai', '#ai .ai-body, #ai section'],
  ['camere', '#tab-cams', '#cams-table, .row-state'], ['setari', '#tab-set', '#set .set-card, #set section'],
];

const METRICI = (primarSel) => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && e.offsetParent !== null; };
  const inter = [...document.querySelectorAll('button, a[href], [role="button"], input:not([type="hidden"]), select, label')].filter(vis);
  const small = [], big = [];
  for (const e of inter) {
    const r = e.getBoundingClientRect();
    const m = Math.min(r.width, r.height);
    const txt = (e.textContent || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 24);
    if (m > 0 && m < 44) small.push({ sel: e.id ? '#' + e.id : (e.className || e.tagName).toString().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), txt });
    if (r.height > 60) big.push({ sel: e.id ? '#' + e.id : (e.className || e.tagName).toString().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), txt });
  }
  // fonturi mici (text vizibil sub 12px)
  let tiny = 0;
  for (const e of document.querySelectorAll('body *')) {
    if (!e.children.length && e.textContent.trim() && vis(e)) { const fs = parseFloat(getComputedStyle(e).fontSize); if (fs && fs < 12) tiny++; }
  }
  const header = document.querySelector('header');
  const hr = header ? header.getBoundingClientRect() : { height: 0, bottom: 0 };
  // continutul principal: cel mai de sus element care se potriveste selectorului primar
  let primar = null;
  if (primarSel) {
    const els = [...document.querySelectorAll(primarSel)].filter(vis);
    if (els.length) { const top = Math.min(...els.map((e) => e.getBoundingClientRect().top));
      primar = { top: Math.round(top), sub_fold: top < innerHeight }; }
  }
  // cate butoane vizibile sunt „above the fold" (aglomerarea de care se plange pe mobil)
  const afBtns = inter.filter((e) => { const r = e.getBoundingClientRect(); return (e.tagName === 'BUTTON' || e.getAttribute('role') === 'button') && r.top >= 0 && r.top < innerHeight; }).length;
  // uneltele de export/analiza din Inregistrari: pliate implicit pe mobil?
  const unelte = document.querySelector('#pb-unelte');
  const pbUneltePliat = unelte ? (getComputedStyle(unelte).display === 'none' || unelte.offsetParent === null) : null;
  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    vw: innerWidth, vh: innerHeight, coarse: matchMedia('(pointer: coarse)').matches,
    antet_h: Math.round(hr.height), continut_incepe_la: Math.round(hr.bottom),
    primar, butoane_above_fold: afBtns, pb_unelte_pliat: pbUneltePliat,
    tinte_mici: small, tinte_mari: big.slice(0, 12), nr_tinte_mari: big.length,
    fonturi_mici: tiny,
  };
};

// criteriile de acceptare (vezi promptul de optimizare). Hard = poarta; soft = informativ.
function verdict(nume, m) {
  const c = [];
  c.push(['overflow X == 0', m.overflow === 0, true]);
  c.push(['fonturi <12px == 0', m.fonturi_mici === 0, false]);
  if (m.primar) c.push(['primar vizibil fara derulare', m.primar.sub_fold, nume === 'inregistrari' || nume === 'live']);
  if (nume === 'inregistrari') c.push(['unelte export/analiza pliate implicit (mobil)', m.pb_unelte_pliat === true, true]);
  return c;
}

(async () => {
  const srv = await startStatic();
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ locale: 'ro-RO', bypassCSP: true, ...IPHONE });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error' && !eStream(m.text())) errors.push(m.text().slice(0, 120)); });
  await installApi(page, {});
  await page.goto(srv.origin, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForSelector('html[data-gata="2"]', { timeout: 15000 });

  const raport = {};
  for (const [nume, tab, primarSel] of ECRANE) {
    // pe mobil, ai/setari stau sub „Mai mult" (butonul din antet e ascuns) — apelam handlerul
    // direct, ca .click() programatic ruleaza si pe un element ascuns
    if (tab) { await page.evaluate((s) => document.querySelector(s)?.click(), tab); await page.waitForTimeout(600); }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(OUT, `${nume}-fold.png`) });                    // ce vezi prima data
    await page.screenshot({ path: path.join(OUT, `${nume}-full.png`), fullPage: true });     // tot ecranul
    raport[nume] = await page.evaluate(METRICI, primarSel || null);
  }
  fs.writeFileSync(path.join(OUT, 'metrici.json'), JSON.stringify(raport, null, 2));

  // rezumat + verdict pe criterii
  let hardFail = 0;
  for (const [nume, m] of Object.entries(raport)) {
    console.log(`\n== ${nume} ==`);
    console.log(`  debordare X: ${m.overflow}px | antet: ${m.antet_h}px | coarse: ${m.coarse} | butoane above-fold: ${m.butoane_above_fold}` +
      (m.primar ? ` | primar sus la ${m.primar.top}px (${m.primar.sub_fold ? 'VIZIBIL' : 'SUB fold'})` : ''));
    for (const [et, ok2, hard] of verdict(nume, m)) {
      if (!ok2 && hard) hardFail++;
      console.log(`   ${ok2 ? '✓' : (hard ? '✗' : '⚠')} ${et}${hard ? '' : ' (soft)'}`);
    }
  }
  console.log(`\nCapturi in tests/_audit/*.png (fold = prima vedere, full = tot). errors de consola: ${errors.length}`);
  console.log(hardFail ? `\n==== ${hardFail} criterii HARD picate ====` : '\n==== toate criteriile HARD trec ====');
  await browser.close();
  await srv.close();
  process.exit(hardFail ? 1 : 0);
})();
