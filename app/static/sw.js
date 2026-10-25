// Service worker: necesar pentru instalarea PWA (Add to Home Screen) si pentru
// notificarile push. Nu face cache: totul e live (camere), offline nu are sens.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));
self.addEventListener('fetch', () => {});

// Un mesaj push (push.py): {titlu, corp, url, tag, sev}. Il aratam ca notificare nativa;
// `tag` = id-ul evenimentului, ca acelasi incident sa nu apara de doua ori.
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { corp: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.titlu || 'OpenVigil', {
    body: d.corp || '', tag: d.tag || undefined, icon: '/icon-192.png', badge: '/icon-192.png',
    data: { url: d.url || '/' }, renotify: !!d.tag, requireInteraction: d.sev === 'critic',
    timestamp: d.t ? Date.parse(d.t.replace(' ', 'T')) || Date.now() : Date.now(),
  }));
});

// Click pe notificare: fereastra deschisa a aplicatiei sare direct la moment
// (#pb/cam/zi/sec), altfel deschidem una noua.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
    for (const w of wins) {
      if ('focus' in w) {
        w.focus();
        if ('navigate' in w) return w.navigate(url);
        return;
      }
    }
    return clients.openWindow(url);
  }));
});
