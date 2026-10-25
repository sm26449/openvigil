// Set de iconite pentru interfata. Un singur stil: contur de 1.7px, colturi rotunjite,
// caseta 24x24, culoare mostenita (currentColor) — deci se coloreaza singure dupa stare.
// Inlocuiesc emoji-urile, care se randau diferit pe fiecare sistem si nu se aliniau.

const P = {
  // audio
  listen: '<path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/><path d="M19 6a9 9 0 0 1 0 12"/>',
  mute:   '<path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="m17 9 5 6M22 9l-5 6"/>',
  talk:   '<rect x="9" y="3" width="6" height="10" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  siren:  '<path d="M3 10v4h3l7 4V6l-7 4H3Z"/><path d="M17 7a7 7 0 0 1 0 10"/><path d="M20 4a11 11 0 0 1 0 16"/>',
  // lumina / alarma
  strobe: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>',
  alarm:  '<path d="M12 4a6 6 0 0 0-6 6c0 4-1.5 5-2 6h16c-.5-1-2-2-2-6a6 6 0 0 0-6-6Z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  relay:  '<path d="M7 3v7H4l6 11v-7h3L7 3Z"/>',
  // detectie / zone
  paw:    '<circle cx="7" cy="8" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="17" cy="8" r="2"/><path d="M12 11c-3 0-5 2.5-5 5 0 2 1.5 3 3 3h4c1.5 0 3-1 3-3 0-2.5-2-5-5-5Z"/>',
  zone:   '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><circle cx="12" cy="12" r="2.5"/>',
  // optica
  zoomin:  '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8 11h6M11 8v6"/>',
  zoomout: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8 11h6"/>',
  camera:  '<path d="M3 8a2 2 0 0 1 2-2h2l1.5-2h7L17 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8Z"/><circle cx="12" cy="12.5" r="3.5"/>',
  // navigare / timp
  play:    '<path d="M7 4v16l13-8L7 4Z"/>',
  live:    '<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="9"/>',
  rewind:  '<path d="M11 5 3 12l8 7V5ZM21 5l-8 7 8 7V5Z"/>',
  prev:    '<path d="M18 5 8 12l10 7V5ZM6 5v14"/>',
  next:    '<path d="M6 5l10 7L6 19V5ZM18 5v14"/>',
  back10:  '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v4h4"/>',
  fwd10:   '<path d="M21 12a9 9 0 1 1-3-6.7M21 4v4h-4"/>',
  // sistem
  gear:    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7.5 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 15H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 8.5l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 4.6V4a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1.3Z"/>',
  disk:    '<path d="M5 3h11l3 3v15H5V3Z"/><path d="M8 3v6h8V3M8 21v-6h8v6"/>',
  link:    '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
  external:'<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  trash:   '<path d="M4 6h16M9 6V4h6v2M6 6l1 14h10l1-14"/><path d="M10 10v7M14 10v7"/>',
  plus:    '<path d="M12 5v14M5 12h14"/>',
  search:  '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  upload:  '<path d="M12 19V5M6 11l6-6 6 6"/><path d="M4 21h16"/>',
  download:'<path d="M12 5v14M6 13l6 6 6-6"/><path d="M4 21h16"/>',
  undo:    '<path d="M3 10h11a5 5 0 0 1 0 10h-3M3 10l5-5M3 10l5 5"/>',
  image:   '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4 18 5-5 3 3 3-3 5 5"/>',
  lock:    '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  key:     '<circle cx="8" cy="14" r="4"/><path d="m11 11 9-9M17 5l2 2M15 7l2 2"/>',
  exit:    '<path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M9 16l-4-4 4-4M5 12h10"/>',
  warn:    '<path d="M12 4 2 20h20L12 4Z"/><path d="M12 10v5M12 18h.01"/>',
  brain:   '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V15a3 3 0 0 0 3 3h1a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8V15a3 3 0 0 1-3 3h-1a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/>',
  server:  '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  tools:   '<path d="M14.5 5.5a4 4 0 0 0 5.3 5.3L21 9.5 14.5 3 13 4.2a4 4 0 0 0 1.5 1.3Z"/><path d="m13 9-9 9 2 2 9-9"/>',
  bolt:    '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>',
};

/** Iconita ca sir HTML (pentru innerHTML). size = latura in px. */
export function icon(name, size = 16) {
  const d = P[name];
  if (!d) return '';
  return `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" ` +
    `stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" ` +
    `aria-hidden="true">${d}</svg>`;
}

/** Iconita ca element DOM. */
export function iconEl(name, size = 16) {
  const s = document.createElement('span');
  s.className = 'ic-wrap';
  s.innerHTML = icon(name, size);
  return s;
}
