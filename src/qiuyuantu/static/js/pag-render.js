// 固定本地运行库，只读取已传入的 PAG 数据；所有页面网络请求由宿主拦截。
function metadata(file) {
  const width = file.width();
  const height = file.height();
  const rate = file.frameRate();
  const duration = file.duration() / 1000000;
  const count = Math.ceil(duration * rate - 0.0001);
  const valid = [width, height, rate, duration].every(value => Number.isFinite(value) && value > 0);
  if (!valid || Math.max(width, height) > 1024 || rate > 60 || count < 2 || count > 600
      || duration > 30 || width * height * count > 100000000) {
    throw new Error('PAG 超过尺寸、帧数或时长限制');
  }
  return { width, height, rate, duration, count };
}

window.qytLoadPAG = async function () {
  const PAG = await window.libpag.PAGInit({ locateFile: () => '/libpag.wasm' });
  const raw = await (await fetch('/source.pag')).arrayBuffer();
  const file = await PAG.PAGFile.load(raw);
  if (!file) throw new Error('PAG 文件无效');
  const { width, height, rate, duration, count } = metadata(file);
  if (file.numTexts() > 0) throw new Error('这份 PAG 含字体图层，暂时只支持无字体动画');
  const canvas = document.getElementById('animation');
  canvas.width = width;
  canvas.height = height;
  window.qytPAGView = await PAG.PAGView.init(file, canvas, { firstFrame: false, useScale: false });
  window.qytPAGFile = file;
  return { width, height, rate, duration, count };
};
window.qytPAGFrame = async function (progress) {
  window.qytPAGView.setProgress(progress);
  await window.qytPAGView.flush();
  return document.getElementById('animation').toDataURL('image/png').split(',')[1];
};
