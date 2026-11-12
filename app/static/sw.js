// Service worker: necesar pentru instalarea PWA (Add to Home Screen) si pentru
// notificarile push. Nu face cache: totul e live (camere), offline nu are sens.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));
self.addEventListener('fetch', () => {});

// Un mesaj push (push.py): {titlu, corp, url, tag, sev, img, act}. Il aratam ca notificare
// nativa; `tag` = id-ul evenimentului, ca acelasi incident sa nu apara de doua ori.
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { corp: e.data && e.data.text() }; }
  const opt = {
    body: d.corp || '', tag: d.tag || undefined, icon: '/icon-192.png', badge: '/icon-192.png',
    data: { url: d.url || '/' }, renotify: !!d.tag, requireInteraction: d.sev === 'critic',
    timestamp: d.t ? Date.parse(d.t.replace(' ', 'T')) || Date.now() : Date.now(),
  };
  // imagine mare (miniatura camerei) — Android o arata extinsa; iOS o ignora, benign
  if (d.img) opt.image = d.img;
  // buton de actiune: duce la acelasi moment ca si corpul (util pe Android)
  opt.actions = [{ action: 'vezi', title: d.act || 'Vezi' }];
  e.waitUntil((async () => {
    await self.registration.showNotification(d.titlu || 'OpenVigil', opt);
    // badge numeric pe icon = cate notificari sunt active (unde API-ul exista)
    if (self.navigator && 'setAppBadge' in self.navigator) {
      try { const n = await self.registration.getNotifications(); await self.navigator.setAppBadge(n.length); } catch (_) {}
    }
  })());
});

// Click pe notificare (sau pe butonul „Vezi"): fereastra deschisa a aplicatiei sare direct
// la moment (#pb/cam/zi/sec), altfel deschidem una noua. Badge-ul se recalculeaza din cele ramase.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    // nu golim orbeste: daca mai sunt notificari in tava, pastram numarul lor pe icon
    if (self.navigator && 'setAppBadge' in self.navigator) {
      try { const n = await self.registration.getNotifications();
        n.length ? await self.navigator.setAppBadge(n.length) : await self.navigator.clearAppBadge(); } catch (_) {}
    }
    const wins = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if ('focus' in w) { await w.focus(); if ('navigate' in w) return w.navigate(url); return; }
    }
    return clients.openWindow(url);
  })());
});
