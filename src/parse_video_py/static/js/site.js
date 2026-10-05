// 每个页面都有的：顶部阅读进度条、背景光晕视差、入场动画、数字滚动（都尊重 reduce-motion），测试模式标记

const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

// 站长开了 /test 的浏览器：左下角标一下，免得忘了自己在测试模式里（判断在服务端，这个 cookie 只管显示）
if (/(^|;\s*)sz_t=1/.test(document.cookie)) {
  const badge = document.getElementById('test-badge');
  badge.href = '/test';
  badge.hidden = false;
}

function trackScroll() {
  const bar = document.getElementById('progress');
  const shapes = [...document.querySelectorAll('.shape[data-parallax]')];
  let ticking = false;
  const update = () => {
    const y = window.scrollY;
    const max = document.documentElement.scrollHeight - innerHeight;
    bar.style.width = (max > 0 ? Math.min(100, (y / max) * 100) : 0) + '%';
    if (!reduce) for (const s of shapes) s.style.transform = 'translate3d(0,' + y * parseFloat(s.dataset.parallax) + 'px,0)';
    ticking = false;
  };
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(update);
  };
  addEventListener('scroll', onScroll, { passive: true });
  // 双 rAF 后再跑首帧：页面刚写完 DOM 时同步读 scrollHeight 会强迫整页重排
  // （Lighthouse 的 forced reflow 217ms 就是它）。刷新落在页中时也能正确恢复进度条。
  requestAnimationFrame(() => requestAnimationFrame(onScroll));
}

function countUp(node) {
  const target = +node.dataset.count;
  if (reduce) {
    node.textContent = target;
    return;
  }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / 1000);
    node.textContent = Math.round(target * (1 - Math.pow(1 - p, 2)));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function revealOnScroll() {
  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add('in');
      entry.target.querySelectorAll('[data-count]').forEach(countUp);
      if (entry.target.dataset.count != null) countUp(entry.target);
      io.unobserve(entry.target);
    }
  }, { threshold: 0.15, rootMargin: '0px 0px -8% 0px' });
  document.querySelectorAll('[data-reveal]').forEach((node) => io.observe(node));
}

trackScroll();
revealOnScroll();
