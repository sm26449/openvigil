// Starea partajata intre ecrane: variabilele scrise din mai multe module (ES nu lasa un modul
// sa atribuie un import). Fiecare ecran citeste/scrie S.<nume>; restul starii sta in modulul ei
// si se exporta ca legatura vie (import-urile vad valoarea curenta).
export const S = {};
