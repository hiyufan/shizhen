/**
 * 阿里云 ESA 边缘函数 / 边缘 Pages 函数：给拾帧当"国内出口"。
 *
 * 一个文件两个用途：
 *   /probe?xhs=<小红书分享链接>   探测：这个边缘节点的出口 IP、B站 API 状态、小红书页面是否有笔记数据
 *   /relay?url=<目标地址>          中继：拾帧把国内平台的解析请求发到这里，由边缘节点代为访问
 *   /img?url=&e=&s=                图片：浏览器直接从国内边缘节点取国内平台的图（拾帧设 PARSE_VIDEO_EDGE_IMG=1 才会用）
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
  if (url.pathname.endsWith("/img")) return img(url);
  // 其它路径（含根路径）都当探测用，直接打开函数地址就能看结果
  return probe(url);
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

// /img?url=&e=&s=  浏览器直接从这里取国内平台的图片，不用绕海外服务器跨两次太平洋。
// 只接受拾帧签过名、没过期的地址，只转白名单里的图片 CDN，只回 image/*，免得被当成通用代理 / 视频带宽。
// 白名单和服务器端 convert/relay.py 的 _EDGE_IMG_HOSTS 保持一致
const IMG_REFERERS = [
  ["xhscdn.com", "https://www.xiaohongshu.com/"],
  ["xiaohongshu.com", "https://www.xiaohongshu.com/"],
  ["douyinpic.com", "https://www.douyin.com/"],
  ["yximgs.com", "https://www.kuaishou.com/"],
  ["hdslb.com", "https://www.bilibili.com/"],
  ["sinaimg.cn", "https://weibo.com/"],
];

const imgReferer = (host) => (IMG_REFERERS.find(([s]) => host === s || host.endsWith("." + s)) || [])[1];

async function hmacHex(message) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(TOKEN), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
  return Array.from(mac, (b) => b.toString(16).padStart(2, "0")).join("");
}

function sameString(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function img(url) {
  const target = url.searchParams.get("url") || "";
  const exp = Number(url.searchParams.get("e") || 0);
  const sig = url.searchParams.get("s") || "";
  if (!(exp > Date.now() / 1000)) return new Response("expired", { status: 403 });
  if (!sameString((await hmacHex(`img\n${exp}\n${target}`)).slice(0, 32), sig)) return new Response("forbidden", { status: 403 });
  let ref;
  try { ref = imgReferer(new URL(target).hostname); } catch (e) {}
  if (!ref || !/^https?:\/\//i.test(target)) return new Response("bad url", { status: 400 });

  let resp;
  try {
    resp = await fetch(target, { headers: { "User-Agent": UA, Referer: ref, Accept: "image/avif,image/webp,image/*,*/*;q=0.8" } });
  } catch (e) {
    return new Response("fetch failed", { status: 502 });
  }
  const ctype = resp.headers.get("content-type") || "";
  // 跳转之后也得还在白名单里
  let finalOk = true;
  try { finalOk = !resp.url || !!imgReferer(new URL(resp.url).hostname); } catch (e) {}
  if (!resp.ok || !ctype.startsWith("image/") || !finalOk) return new Response("upstream " + resp.status, { status: 502 });
  const headers = { "content-type": ctype, "cache-control": "public, max-age=86400", "x-content-type-options": "nosniff" };
  if (resp.headers.get("content-length")) headers["content-length"] = resp.headers.get("content-length");
  return new Response(resp.body, { headers });
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
    // 边缘 / 机房 IP 常被小红书要求登录；带 ?cookie=<登录后的 Cookie 串（encodeURIComponent 过）> 再测一次
    const cookie = url.searchParams.get("cookie") || "";
    try {
      const headers = { "User-Agent": UA, Accept: "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9" };
      if (cookie) headers["Cookie"] = cookie;
      const r = await fetch(xhs, { headers, redirect: "follow" });
      const html = await r.text();
      const title = ((html.match(/<title>(.*?)<\/title>/s) || [])[1] || "").trim().slice(0, 60);
      const loginWall = r.url.includes("/login");
      out.xhs = { status: r.status, finalUrl: r.url.slice(0, 120), title, withCookie: !!cookie, loginWall,
                  hasNote: html.includes("noteDetailMap") && !r.url.includes("/404") && !loginWall };
      if (loginWall && !cookie) out.xhs.hint = "桌面页要求登录；拾帧实际用的是下面的手机分享页，看 xhsMobile";
    } catch (e) { out.xhs = { error: String(e) }; }
    // 拾帧在机房 / 边缘出口上走的是手机分享页（不要求登录），这个才是关键
    try {
      const headers = { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                        Accept: "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9" };
      if (cookie) headers["Cookie"] = cookie;
      const r = await fetch(xhs, { headers, redirect: "follow" });
      const html = await r.text();
      const title = ((html.match(/<title>(.*?)<\/title>/s) || [])[1] || "").trim().slice(0, 60);
      out.xhsMobile = { status: r.status, finalUrl: r.url.slice(0, 120), title, loginWall: r.url.includes("/login"),
                        hasNote: html.includes('"noteData"') && html.includes("imageList") && !r.url.includes("/404") && !r.url.includes("/login") };
    } catch (e) { out.xhsMobile = { error: String(e) }; }
  } else {
    out.xhs = "加 ?xhs=<小红书分享链接> 一起测";
  }
  out.verdict = out.bilibili?.ok ? "B站可用" : "B站不可用";
  if (typeof out.xhsMobile === "object" && !out.xhsMobile.error) out.verdict += out.xhsMobile.hasNote ? "，小红书可用（手机分享页）" : (out.xhs?.hasNote ? "，小红书可用（桌面页）" : "，小红书不可用");
  return new Response(JSON.stringify(out, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
}
