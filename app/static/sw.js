// Service worker: a small script the browser keeps running in the background for
// this app. Phones need one before they'll treat a website as an installable app,
// and it's what receives push notifications, even when the app is closed.
//
// It deliberately caches nothing: your log must always be fresh from the server.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// Pass every request straight to the network.
self.addEventListener("fetch", () => {});

// A reminder arrived from the server: show it.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data?.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Fit Agent", {
    body: data.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: data.url || "/" },
  }));
});

// Tapping the notification opens the app (or switches to it if it's already open).
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of windows) {
      if ("focus" in w) { w.navigate(url); return w.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});
