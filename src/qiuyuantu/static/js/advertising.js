import { el } from './dom.js';

const FORMATS = { desktop: { width: 728, height: 90 }, mobile: { width: 320, height: 50 } };

/** 同一工具页仅保留一个适合当前屏幕的横幅，不随解析重复加载。 */
export function setupAdvertising() {
  const slot = document.querySelector('[data-banner]');
  if (!slot) return;
  const viewport = matchMedia('(min-width: 860px)');
  const supported = matchMedia('(min-width: 320px)');
  let frame = null;
  let timeout = null;
  let placement = '';
  const cancelTimeout = () => { clearTimeout(timeout); timeout = null; };
  const update = () => {
    const next = supported.matches ? (viewport.matches ? 'desktop' : 'mobile') : '';
    if (next === placement) return;
    cancelTimeout();
    frame?.remove();
    frame = null;
    placement = next;
    slot.dataset.state = 'waiting';
    if (placement) {
      const size = FORMATS[placement];
      frame = el('iframe', {
        src: slot.dataset.banner + placement, title: slot.dataset.title, ...size, loading: 'lazy',
        sandbox: 'allow-scripts allow-popups allow-popups-to-escape-sandbox', referrerpolicy: 'no-referrer',
      });
      const current = frame;
      frame.addEventListener('load', () => {
        if (frame !== current || slot.dataset.state !== 'waiting') return;
        cancelTimeout();
        timeout = setTimeout(() => {
          if (frame === current && slot.dataset.state === 'waiting') slot.dataset.state = 'unavailable';
        }, 14000);
      });
      slot.querySelector('.banner-body').append(frame);
    }
  };
  window.addEventListener('message', (event) => {
    if (!frame || event.source !== frame.contentWindow || event.data?.type !== 'qyt-banner') return;
    if (!['loaded', 'unavailable'].includes(event.data.state)) return;
    cancelTimeout();
    slot.dataset.state = event.data.state;
  });
  viewport.addEventListener('change', update);
  supported.addEventListener('change', update);
  update();
}
