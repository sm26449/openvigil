// Starea partajata intre ecrane: variabilele scrise din mai multe module (ES nu lasa un modul
// sa atribuie un import). Fiecare ecran citeste/scrie S.<nume>; restul starii sta in modulul ei
// si se exporta ca legatura vie (import-urile vad valoarea curenta).
//
// CONTRACTUL (audit #20): orice proprietate noua de aici se declara mai jos, cu valoarea
// initiala, sensul si cine o SCRIE. Un modul care doar o citeste nu trebuie listat.
export const S = {
  // [{id, name, group, ip, features, …}] — camerele contului, din /api/cameras.
  // Scrie: 10-live.js (loadCameras). Citit de aproape toate ecranele.
  cameras: [],
  // numele vizualizarii salvate active pe Live ('' = nicio vizualizare / filtru manual).
  // Scrie: 10-live.js (alegere/salvare/stergere), 50-camere.js (cautarea in Live o anuleaza).
  currentView: '',
  // camerele suplimentare din redarea pe mai multe camere (id-uri), fara camera principala.
  // Scrie: 30-redare.js (+ camere), 40-evenimente.js (scoate camera aleasa ca principala).
  pbExtra: [],
  // proba in asteptarea alegerii dosarului: {cam, start, end, nota, event_id} sau null.
  // Scrie: 45-fisa.js, 30-redare.js (marcaj), 40-evenimente.js (alegerea dosarului / renuntare).
  pendingProba: null,
  // rezolvatorul promisiunii din closeHD cat asteptam popstate-ul de la history.back(), sau null.
  // Scrie: 20-hd.js (il pune), 05-navigare.js (il consuma in popstate).
  popIgnora: null,
};
