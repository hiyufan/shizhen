// 经典同步脚本：供应商脚本读取全局 atOptions，并可能通过 document.write 插入 iframe。
function configureBanner() {
  const options = document.body.dataset;
  window.atOptions = {
    key: options.bannerKey,
    format: 'iframe',
    height: Number(options.bannerHeight),
    width: Number(options.bannerWidth),
    params: {},
  };
}

configureBanner();
