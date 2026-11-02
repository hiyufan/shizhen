// 推送广告已停用。访客浏览器里装过的旧 Worker 会定期重新拉取本文件，
// 这里只负责退订推送并注销自身；若改成 404，浏览器会继续运行旧 Worker。
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    self.registration.pushManager.getSubscription()
      .then((subscription) => subscription && subscription.unsubscribe())
      .catch(() => {})
      .then(() => self.registration.unregister()),
  );
});
