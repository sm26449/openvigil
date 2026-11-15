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

export { eroriCalibrare, lungime, metriLaPx, metriPePxX, proiect, pxLaMetri };
