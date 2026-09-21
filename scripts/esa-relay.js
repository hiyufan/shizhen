/**
 * 阿里云 ESA 边缘函数 / 边缘 Pages 函数：给拾帧当"国内出口"。
 *
 * 一个文件两个用途：
 *   /probe?xhs=<小红书分享链接>   探测：这个边缘节点的出口 IP、B站 API 状态、小红书页面是否有笔记数据
 *   /relay?url=<目标地址>          中继：拾帧把国内平台的解析请求发到这里，由边缘节点代为访问
 *
 * 部署（边缘函数）：ESA 控制台 → 边缘函数 → 新建 → 把本文件贴进去 → 改 TOKEN → 发布，绑定一个域名或用默认地址。
 * 部署（边缘 Pages）：把本文件放到项目的 functions/[[path]].js，把末尾的 export default 换成
 *     export function onRequest({ request }) { return handle(request); }
 *
 * 拾帧侧配置（服务器环境变量）：
 *     PARSE_VIDEO_RELAY_CN=https://你的函数域名/relay
 *     PARSE_VIDEO_RELAY_TOKEN=和下面 TOKEN 一样的字符串
 *
 * 中继只用于解析请求（网页 / API，几十 KB），视频本体仍由服务器直连 CDN。
 * Cloudflare Workers 也能原样跑这份代码（同样是 export default { fetch }），但它的出口在海外，B站 会 412，别用。
 */

const TOKEN = "change-me-to-a-long-random-string";

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36";
const DROP_RESP = new Set(["content-encoding", "content-length", "transfer-encoding", "connection", "set-cookie"]);
const DROP_REQ = new Set(["host", "content-length", "connection", "accept-encoding", "x-relay-token", "x-relay-method", "x-relay-headers"]);

// ESA 要求 ES module：默认导出一个带 fetch 的对象
export default {
  async fetch(request, env, ctx) {
    return handle(request);
  },
};

async function handle(request) {
  const url = new URL(request.url);
  if (url.pathname.endsWith("/relay")) return relay(request, url);
  if (url.pathname.endsWith("/probe")) return probe(url);
  return new Response("shizhen relay: use /probe or /relay", { status: 404 });
}

// UTF-8 安全的 base64
const b64e = (s) => btoa(unescape(encodeURIComponent(s)));
const b64d = (s) => decodeURIComponent(escape(atob(s)));

async function relay(request, url) {
  if (request.headers.get("x-relay-token") !== TOKEN) return new Response("forbidden", { status: 403 });
  const target = url.searchParams.get("url") || "";
  if (!/^https?:\/\//i.test(target)) return new Response("bad url", { status: 400 });

  const method = (request.headers.get("x-relay-method") || "GET").toUpperCase();
  let headers = {};
  try { headers = JSON.parse(b64d(request.headers.get("x-relay-headers") || "e30=")); } catch (e) {}
  for (const k of Object.keys(headers)) if (DROP_REQ.has(k.toLowerCase())) delete headers[k];
  if (!headers["user-agent"] && !headers["User-Agent"]) headers["User-Agent"] = UA;

  const init = { method, headers, redirect: "manual" };   // 跳转交回给拾帧那边的 httpx 处理，每一跳都过它的 SSRF 检查
  if (!["GET", "HEAD"].includes(method)) init.body = await request.arrayBuffer();

  let resp;
  try {
    resp = await fetch(target, init);
  } catch (e) {
    return new Response(String(e), { status: 200, headers: { "x-relay-status": "599", "x-relay-error": String(e).slice(0, 200) } });
  }
  const pairs = [];
  resp.headers.forEach((v, k) => { if (!DROP_RESP.has(k.toLowerCase())) pairs.push([k, v]); });
  if (typeof resp.headers.getSetCookie === "function") {
    for (const c of resp.headers.getSetCookie()) pairs.push(["set-cookie", c]);
  } else if (resp.headers.get("set-cookie")) {
    pairs.push(["set-cookie", resp.headers.get("set-cookie")]);
  }
  const body = await resp.arrayBuffer();
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "application/octet-stream",
      "x-relay-status": String(resp.status),
      "x-relay-headers": b64e(JSON.stringify(pairs)),
    },
  });
}

async function probe(url) {
  const xhs = url.searchParams.get("xhs") || "";
  const out = {};

  // 出口 IP：先问国内的 ipip，不行再问 ip.sb
  for (const [name, u, pick] of [
    ["ipip", "https://myip.ipip.net/json", (j) => ({ ip: j.data?.ip, location: (j.data?.location || []).join(" ") })],
    ["ip.sb", "https://api.ip.sb/geoip", (j) => ({ ip: j.ip, location: `${j.country || ""} ${j.region || ""} ${j.isp || j.organization || ""}` })],
  ]) {
    try {
      const j = await (await fetch(u, { headers: { "User-Agent": "curl/8" } })).json();
      out.egress = { via: name, ...pick(j) };
      break;
    } catch (e) { out.egress = { error: String(e) }; }
  }

  try {
    const r = await fetch("https://api.bilibili.com/x/web-interface/view?bvid=BV1GJ411x7h7", {
      headers: { "User-Agent": UA, Referer: "https://www.bilibili.com/", Accept: "application/json" },
    });
    const text = await r.text();
    let code = null; try { code = JSON.parse(text).code; } catch (e) {}
    out.bilibili = { status: r.status, code, ok: r.status === 200 && code === 0, sample: text.slice(0, 80) };
  } catch (e) { out.bilibili = { error: String(e) }; }

  if (xhs) {
    try {
      const r = await fetch(xhs, {
        headers: { "User-Agent": UA, Accept: "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9" },
        redirect: "follow",
      });
      const html = await r.text();
      const title = ((html.match(/<title>(.*?)<\/title>/s) || [])[1] || "").trim().slice(0, 60);
      out.xhs = { status: r.status, finalUrl: r.url, title, hasNote: html.includes("noteDetailMap") && !r.url.includes("/404") };
    } catch (e) { out.xhs = { error: String(e) }; }
  } else {
    out.xhs = "加 ?xhs=<小红书分享链接> 一起测";
  }
  out.verdict = out.bilibili?.ok ? "B站可用" : "B站不可用";
  if (typeof out.xhs === "object" && !out.xhs.error) out.verdict += out.xhs.hasNote ? "，小红书可用" : "，小红书不可用";
  return new Response(JSON.stringify(out, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
}
