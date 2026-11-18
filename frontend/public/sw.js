/* Browser push worker: keep alert text brief and never include order secrets. */
self.addEventListener("push", (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { title: "Trading platform alert", body: "Open the app to review a new alert." };
  }

  const title = typeof message.title === "string" ? message.title : "Trading platform alert";
  const options = {
    body: typeof message.body === "string" ? message.body : "Open the app to review a new alert.",
    icon: "/favicon.svg",
    badge: "/favicon.svg",
    tag: typeof message.tag === "string" ? message.tag : undefined,
    data: { url: typeof message.url === "string" ? message.url : "/" },
    renotify: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const raw = event.notification.data && event.notification.data.url;
  let target = self.location.origin + "/";
  try {
    const parsed = new URL(typeof raw === "string" ? raw : "/", self.location.origin);
    if (parsed.origin === self.location.origin) target = parsed.href;
  } catch {
    // Keep the same-origin root as the safe fallback.
  }
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (client.url.startsWith(self.location.origin) && "focus" in client) {
        await client.navigate(target);
        return client.focus();
      }
    }
    return self.clients.openWindow(target);
  })());
});
