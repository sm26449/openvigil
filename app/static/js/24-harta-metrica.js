/* ====================================================================
   Harta: masura planului (pixeli <-> metri), pentru interfata
   Modul ES al interfetei (app/static/js/); intrarea e app/static/app.js, care le importa
   pe toate; importurile/exporturile sunt verificate de scripts/module_check.py (si in pytest).
   ==================================================================== */
/* Masura o calculeaza serverul (app/plan_metrica.py) din limita proprietatii sau din scara:
   p.metrica = {G: [a, b, c], A, Ainv, mod, w, h, ...}. Intre doua puncte la (dx, dy) pixeli
   de imagine, distanta reala e sqrt(a*dx^2 + 2b*dx*dy + c*dy^2) — aceeasi formula pentru un
   plan la scara (izotrop) si pentru o schita intinsa pe o directie (anizotrop). Aici doar
   functii pure, folosite de harta (zona de sol, bara de scara, limita) si de calibrare. */

// metri intre doua puncte la (dx, dy) pixeli de imagine
function lungime(met, dx, dy) {
  const [a, b, c] = met.G;
  return Math.sqrt(Math.max(0, a * dx * dx + 2 * b * dx * dy + c * dy * dy));
}
// un vector de pixeli -> metri (A) si invers (Ainv)
const pxLaMetri = (met, dx, dy) => [met.A[0][0] * dx + met.A[0][1] * dy, met.A[1][0] * dx + met.A[1][1] * dy];
const metriLaPx = (met, mx, my) => [met.Ainv[0][0] * mx + met.Ainv[0][1] * my, met.Ainv[1][0] * mx + met.Ainv[1][1] * my];
// cati metri inseamna un pixel pe orizontala (bara de scara e orizontala)
const metriPePxX = (met) => Math.sqrt(met.G[0]);

// omografia cadru -> plan (fractii); null dincolo de orizont
function proiect(h, x, y) {
  const w = h[6] * x + h[7] * y + h[8];
  if (w <= 1e-9) return null;
  return [(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w];
}
/* Eroarea fiecarei perechi a unei calibrari: unde cade punctul din cadru fata de unde l-ai pus
   pe plan. In metri cand planul are masura (met), altfel in fractii din plan. */
function eroriCalibrare(perechi, h, met) {
  return perechi.map(([cx, cy, px, py]) => {
    const q = proiect(h, cx, cy);
    if (!q) return Infinity;
    return met ? lungime(met, (q[0] - px) * met.w, (q[1] - py) * met.h) : Math.hypot(q[0] - px, q[1] - py);
  });
}

const SOL_MAX_M = 60;   // dincolo de atat (sau peste orizont) zona de sol se opreste

/* Zona de sol vazuta DIN MONTAJ, pentru o camera pinhole: razele de pe conturul cadrului (16:9,
   unghiul orizontal = deschiderea conului) intersectate cu solul, de la inaltimea data,
   inclinata in jos cu `inclinare`. Razele care nu ating solul (peste orizont) sau il ating
   prea departe se taie la SOL_MAX_M. Directia camerei e desenata pe plan (pixeli); pe teren o
   ducem prin masura planului (A), ca zona sa fie corecta si pe o schita intinsa pe o directie.
   Intoarce puncte in pixeli de imagine (camera la cx, cy) sau null fara montaj. */
function amprentaMontaj(pos, met, cx, cy) {
  if (!met || !(pos.inaltime > 0) || pos.inclinare == null) return null;
  const rad = Math.PI / 180, h = pos.inaltime, th = pos.inclinare * rad;
  const tu = Math.tan(Math.min(pos.fov, 170) / 2 * rad), tv = tu * 9 / 16;
  const fp = pxLaMetri(met, Math.sin(pos.dir * rad), -Math.cos(pos.dir * rad)), nf = Math.hypot(...fp) || 1;
  const fw = [fp[0] / nf, fp[1] / nf], dr = [-fw[1], fw[0]];   // inainte / dreapta, in metri pe teren
  const N = 12, raze = [];
  for (let i = 0; i <= N; i++) raze.push([-tu + 2 * tu * i / N, -tv]);   // marginea de jos
  for (let i = 1; i <= N; i++) raze.push([tu, -tv + 2 * tv * i / N]);    // dreapta
  for (let i = 1; i <= N; i++) raze.push([tu - 2 * tu * i / N, tv]);     // sus
  for (let i = 1; i < N; i++) raze.push([-tu, tv - 2 * tv * i / N]);     // stanga
  return raze.map(([u, w]) => {
    const fx = Math.cos(th) + w * Math.sin(th), fz = -Math.sin(th) + w * Math.cos(th);
    let F, L;
    if (fz < -1e-6) { const tt = h / -fz; F = tt * fx; L = tt * u; } else { F = fx * 1e6; L = u * 1e6; }
    const d = Math.hypot(F, L);
    if (d > SOL_MAX_M) { F *= SOL_MAX_M / d; L *= SOL_MAX_M / d; }
    const [dx, dy] = metriLaPx(met, F * fw[0] + L * dr[0], F * fw[1] + L * dr[1]);
    return [cx + dx, cy + dy];
  });
}

/* Zona de sol vazuta DIN CALIBRARE: conturul cadrului proiectat pe plan prin omografia
   camerei. Calibrarea e masuratoarea (perechi puse de om pe imagine si pe plan), deci pentru o
   camera calibrata are prioritate fata de montaj. Pe fiecare coloana a cadrului urcam de jos
   pana unde punctul mai cade in fata camerei si la cel mult SOL_MAX_M de ea (orizontul si
   departarile mari ies din omografie cu erori mari); conturul = marginea de jos, dreapta pana
   la taietura, taietura spre stanga, stanga in jos. Pixeli de imagine (W x H) sau null. */
function amprentaCalibrare(h, met, cam, W, H) {
  const N = 16, PAS = 40;
  const bun = (x, y) => {
    const q = proiect(h, x, y);
    if (!q || Math.abs(q[0] - 0.5) > 2 || Math.abs(q[1] - 0.5) > 2) return null;
    if (met && lungime(met, (q[0] - cam[0]) * W, (q[1] - cam[1]) * H) > SOL_MAX_M) return null;
    return [q[0] * W, q[1] * H];
  };
  const taietura = (x) => {          // cel mai sus punct valid pe coloana x (urcand de jos)
    let ultim = bun(x, 1);
    if (!ultim) return null;
    for (let k = 1; k <= PAS; k++) {
      const p = bun(x, 1 - k / PAS);
      if (!p) break;
      ultim = p;
    }
    return ultim;
  };
  const jos = [], sus = [];
  for (let i = 0; i <= N; i++) {
    const x = i / N, a = bun(x, 1), b = taietura(x);
    if (!a || !b) continue;
    jos.push(a); sus.push(b);
  }
  if (jos.length < 2) return null;
  return [...jos, ...sus.reverse()];
}

export { SOL_MAX_M, amprentaCalibrare, amprentaMontaj, eroriCalibrare, lungime, metriLaPx, metriPePxX, proiect, pxLaMetri };
