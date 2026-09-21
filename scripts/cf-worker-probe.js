/**
 * 用 Cloudflare Worker 探测：从 Cloudflare 的出口访问 B站 / 小红书 会被怎么对待。
 *
 * 用法：Cloudflare 控制台 → Workers & Pages → Create → Hello World → Edit code，
 * 把这个文件整个贴进去，Deploy，然后打开
 *   https://<worker 名>.<账号>.workers.dev/?xhs=<一条新鲜的小红书分享链接>
 * 返回 JSON：Worker 所在机房（colo）、出口 IP 及归属地、B站 API 状态码、小红书页面是否含笔记数据。
 *
 * 结论怎么看：
 *   bilibili.status = 412        → B站 把这个出口当机器人，不可用
 *   xhs.hasNote = false          → 小红书给了验证页 / 拦截页，不可用
 *   两者都正常                    → 这个 colo 的出口能用（但换个 colo 可能就不行，Worker 不能指定出口）
 */
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const xhs = url.searchParams.get("xhs") || "";
    const out = { colo: request.cf?.colo, country: request.cf?.country };

    try {
      const ip = await (await fetch("https://api.ip.sb/geoip", { headers: { "User-Agent": "curl/8" } })).json();
      out.egress = { ip: ip.ip, country: ip.country, isp: ip.isp || ip.organization };
    } catch (e) { out.egress = String(e); }

    try {
      const r = await fetch("https://api.bilibili.com/x/web-interface/view?bvid=BV1GJ411x7h7", {
        headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                   "Referer": "https://www.bilibili.com/", "Accept": "application/json" },
      });
      const text = await r.text();
      out.bilibili = { status: r.status, code: (() => { try { return JSON.parse(text).code; } catch { return null; } })(), sample: text.slice(0, 80) };
    } catch (e) { out.bilibili = String(e); }

    if (xhs) {
      try {
        const r = await fetch(xhs, {
          headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                     "Accept": "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9" },
          redirect: "follow",
        });
        const html = await r.text();
        const title = (html.match(/<title>(.*?)<\/title>/s) || [])[1] || "";
        out.xhs = { status: r.status, finalUrl: r.url, title: title.trim().slice(0, 60),
                    hasState: html.includes("__INITIAL_STATE__"), hasNote: html.includes("noteDetailMap") && !r.url.includes("/404") };
      } catch (e) { out.xhs = String(e); }
    } else {
      out.xhs = "加 ?xhs=<小红书分享链接> 参数一起测";
    }
    return new Response(JSON.stringify(out, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
  },
};
