// Intrarea interfetei: importa modulele pe ecrane (app/static/js/) si porneste aplicatia.
// Nucleul (00-core) nu depinde de niciun ecran si se evalueaza primul; restul se pot referi
// incrucisat (legaturi vii), starea scrisa din mai multe module sta in js/state.js.
import './js/00-core.js';
import './js/05-navigare.js';
import './js/10-live.js';
import './js/15-flux.js';
import './js/20-hd.js';
import './js/25-harta.js';
import './js/30-redare.js';
import './js/40-evenimente.js';
import './js/45-fisa.js';
import './js/46-reid.js';
import './js/48-cauta.js';
import './js/50-camere.js';
import './js/60-setari.js';
import './js/70-ai.js';
import './js/75-adnotare.js';
import { loadCameras } from './js/10-live.js';
import { routeHash } from './js/05-navigare.js';

// toate modulele si-au pus handlerele cand ajungem aici: marcam documentul ca „gata” (testele
// si orice script extern asteapta atributul in loc sa ghiceasca dupa timp)
document.documentElement.dataset.gata = '1';
loadCameras().then(routeHash).finally(() => { document.documentElement.dataset.gata = '2'; });
