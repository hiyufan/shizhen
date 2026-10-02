// 修剪条：缩略图背景上拖两个把手选一段；点选区外面整段平移过去，点里面跳到那一帧。
// 起止时间写在共享的 state.start / state.end 上，时长上限按 state.format 取 LIMITS

import { el, fmtTime } from './dom.js';

export const LIMITS = { gif: 30, livephoto: 10, motionphoto: 10 };
const MIN_LENGTH = 0.2;

export class Trimmer {
  /**
   * @param src      原视频信息（id、duration）
   * @param video    预览用的 <video>，拖动时跟着跳帧、播放时在选区里循环
   * @param state    和转换参数共用的状态对象
   * @param onLayout 选区变了之后调（封面帧要跟着收进选区）
   * 用完调 destroy() 摘掉挂在 window 上的监听：换一个转换器时不调的话，旧的会一直跟着窗口大小重排
   */
  constructor(src, video, state, onLayout) {
    this.src = src;
    this.video = video;
    this.state = state;
    this.onLayout = onLayout;
    this.layout = this.layout.bind(this);
    this.destroy = () => window.removeEventListener('resize', this.layout);
    this.element = el('div', { class: 'trim' }, this.buildStrip(), this.buildTimes());
    this.bindHandle(this.hStart, 'start');
    this.bindHandle(this.hEnd, 'end');
    this.bindStrip();
    this.bindVideo();
    window.addEventListener('resize', this.layout);
  }

  buildStrip() {
    const stripSrc = '/api/source/' + this.src.id + '/strip?n=20';
    const stripImg = el('img', { alt: '', draggable: 'false', src: stripSrc });
    this.selImg = el('img', { alt: '', draggable: 'false', src: stripSrc });
    this.selImgWrap = el('div', { class: 'sel-img' }, this.selImg);
    this.sel = el('div', { class: 'sel' });
    this.cursor = el('div', { class: 'cursor' });
    this.hStart = el('div', { class: 'handle', role: 'slider', tabindex: '0', 'aria-label': '起点' });
    this.hEnd = el('div', { class: 'handle', role: 'slider', tabindex: '0', 'aria-label': '终点' });
    this.strip = el('div', { class: 'strip loading', tabindex: '-1' },
      stripImg, this.selImgWrap, this.sel, this.cursor, this.hStart, this.hEnd);
    const loaded = () => this.strip.classList.remove('loading');
    stripImg.addEventListener('load', loaded);
    stripImg.addEventListener('error', loaded);
    return this.strip;
  }

  buildTimes() {
    this.tStart = el('b');
    this.tEnd = el('b');
    this.tLen = el('b');
    return el('div', { class: 'times' },
      el('span', {}, '起点 ', this.tStart), el('span', {}, '选了 ', this.tLen), el('span', {}, '终点 ', this.tEnd));
  }

  width() {
    return this.strip.getBoundingClientRect().width;
  }

  pxToTime(px) {
    return Math.max(0, Math.min(this.src.duration, (px / this.width()) * this.src.duration));
  }

  pct(t) {
    return (t / this.src.duration) * 100 + '%';
  }

  layout() {
    const { start, end } = this.state;
    const handleLeft = (t) => 'clamp(10px, ' + this.pct(t) + ', calc(100% - 10px))';
    this.sel.style.left = this.selImgWrap.style.left = this.pct(start);
    this.sel.style.width = this.selImgWrap.style.width = this.pct(end - start);
    this.selImg.style.marginLeft = '-' + (start / this.src.duration) * this.width() + 'px';
    this.selImg.style.width = this.width() + 'px';
    this.hStart.style.left = handleLeft(start);
    this.hEnd.style.left = handleLeft(end);
    this.hStart.setAttribute('aria-valuenow', start.toFixed(1));
    this.hEnd.setAttribute('aria-valuenow', end.toFixed(1));
    this.tStart.textContent = fmtTime(start);
    this.tEnd.textContent = fmtTime(end);
    this.tLen.textContent = (end - start).toFixed(1) + ' 秒';
    this.onLayout();
  }

  /** 选区至少 MIN_LENGTH 秒、最多这个格式的上限；超了就以 anchor 那一端为准收另一端 */
  setRange(start, end, anchor) {
    const max = LIMITS[this.state.format];
    const duration = this.src.duration;
    start = Math.max(0, start);
    end = Math.min(duration, end);
    if (end - start < MIN_LENGTH) {
      if (anchor === 'start') end = Math.min(duration, start + MIN_LENGTH);
      else start = Math.max(0, end - MIN_LENGTH);
    }
    if (end - start > max) {
      if (anchor === 'start') end = start + max;
      else start = end - max;
    }
    this.state.start = start;
    this.state.end = end;
    this.layout();
  }

  moveHandle(which, t) {
    if (which === 'start') this.setRange(t, this.state.end, 'start');
    else this.setRange(this.state.start, t, 'end');
    this.video.currentTime = which === 'start' ? this.state.start : this.state.end;
  }

  bindHandle(handle, which) {
    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const left = this.strip.getBoundingClientRect().left;
      const move = (ev) => this.moveHandle(which, this.pxToTime(ev.clientX - left));
      const stop = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', stop);
        handle.removeEventListener('pointercancel', stop);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', stop);
      handle.addEventListener('pointercancel', stop);
    });
    handle.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 1 : 0.1;
      const delta = { ArrowLeft: -step, ArrowRight: step }[e.key];
      if (!delta) return;
      e.preventDefault();
      this.moveHandle(which, (which === 'start' ? this.state.start : this.state.end) + delta);
    });
  }

  /** 点在选区外：把整段选区平移过去；点在选区内：跳到那一帧 */
  bindStrip() {
    this.strip.addEventListener('pointerdown', (e) => {
      if (e.target === this.hStart || e.target === this.hEnd) return;
      const { start, end } = this.state;
      const t = this.pxToTime(e.clientX - this.strip.getBoundingClientRect().left);
      if (t < start || t > end) {
        const len = end - start;
        const s = Math.max(0, Math.min(this.src.duration - len, t - len / 2));
        this.setRange(s, s + len, 'start');
      }
      this.video.currentTime = Math.max(this.state.start, Math.min(this.state.end, t));
    });
  }

  /** 播放头跟着走；播到选区末尾就回到起点，只循环选中的那段 */
  bindVideo() {
    const video = this.video;
    video.addEventListener('timeupdate', () => {
      this.cursor.style.left = this.pct(video.currentTime);
      if (!video.paused && video.currentTime >= this.state.end) video.currentTime = this.state.start;
    });
    video.addEventListener('loadedmetadata', () => { video.currentTime = this.state.start; });
  }
}
