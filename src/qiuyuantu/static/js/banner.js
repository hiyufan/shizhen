// 每次打开工具页只请求一个尺寸；改变窗口大小不刷新广告、不重复计曝光。
export function initBanner() {
  const slot = document.querySelector('[data-banner]');
  if (!slot) return;
  slot.dataset.bannerState = 'waiting';
  slot.hidden = false;
  const width = slot.clientWidth;
  if (width < 320) {
    slot.hidden = true;
    return;
  }
  const desktop = width >= 728;
  slot.dataset.bannerSize = desktop ? 'desktop' : 'mobile';
  const frame = document.createElement('iframe');
  frame.width = desktop ? '728' : '320';
  frame.height = desktop ? '90' : '50';
  frame.title = slot.dataset.bannerTitle;
  frame.sandbox = 'allow-scripts allow-popups allow-popups-to-escape-sandbox';
  frame.referrerPolicy = 'origin';
  // 先以正常尺寸保留可见位置；隐藏容器会干扰供应商自己的可见性检测。
  slot.append(frame);
  const load = () => {
    slot.dataset.bannerState = 'requested';
    frame.src = '/ads/banner/' + (desktop ? 'desktop' : 'mobile');
  };
  if (!window.IntersectionObserver) {
    load();
    return;
  }
  // 观察广告本身的完整矩形，不提前在屏幕外请求，也不自行补报曝光。
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= 0.5)) return;
    observer.disconnect();
    load();
  }, { threshold: 0.5 });
  observer.observe(frame);
}
