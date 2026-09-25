//! HTTP 层：路由、中间件、请求提取器。
//!
//! 处理函数只做三件事：取参数、调业务、拼响应。业务逻辑在 `parse` / `jobs` / `site` 里。

mod admin;
mod files;
mod jobs;
mod media;
mod pages;
mod parse;
mod proxy;

use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::{ConnectInfo, DefaultBodyLimit, FromRequestParts, Request, State};
use axum::http::request::Parts;
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use tower_http::compression::predicate::{NotForContentType, Predicate, SizeAbove};
use tower_http::compression::CompressionLayer;

use crate::config::Config;
use crate::jobs::tasks::Toolkit;
use crate::jobs::Jobs;
use crate::limits::Limits;
use crate::net::{EdgeImages, Signer};
use crate::parse::ParseService;
use crate::site::Site;
use crate::stats::{Event, Stats};
use crate::store::Store;

/// 所有处理函数共享的状态。
#[derive(Debug)]
pub struct AppState {
    pub cfg: Config,
    pub site: Site,
    pub parse: ParseService,
    pub jobs: Jobs,
    pub store: Arc<Store>,
    pub kit: Toolkit,
    pub limits: Limits,
    pub stats: Arc<Stats>,
    pub signer: Signer,
    pub edge: Option<EdgeImages>,
}

pub type Shared = Arc<AppState>;

pub fn router(state: Shared) -> Router {
    let upload_limit = usize::try_from(state.cfg.max_upload_bytes)
        .unwrap_or(usize::MAX)
        .saturating_add(1 << 20);

    // 可选 Basic Auth 管的路由：页面和业务接口
    let protected = Router::new()
        .route("/", get(pages::home))
        .route("/guides", get(pages::guides))
        .route("/guide/{slug}", get(pages::guide))
        .route("/{slug}", get(pages::landing))
        .route("/api/parse", get(parse::api_parse))
        .route("/video/share/url/parse", get(parse::legacy_share))
        .route("/video/id/parse", get(parse::legacy_id))
        .route("/api/proxy", get(proxy::api_proxy))
        .route("/api/prepare", post(media::prepare))
        .route(
            "/api/upload",
            post(media::upload).layer(DefaultBodyLimit::max(upload_limit)),
        )
        .route("/api/source/{id}", get(media::source_file))
        .route("/api/source/{id}/strip", get(media::source_strip))
        .route("/api/convert", post(media::convert))
        .route("/api/download", post(media::download))
        .route("/api/live", post(media::live))
        .route("/api/jobs/{id}", get(jobs::status).delete(jobs::cancel))
        .route("/api/jobs/{id}/file", get(jobs::file))
        .route("/api/jobs/{id}/preview", get(jobs::cover))
        .route("/api/jobs/{id}/video", get(jobs::motion))
        .route_layer(middleware::from_fn_with_state(
            Arc::clone(&state),
            basic_auth,
        ));

    // 不需要登录的：健康检查、爬虫要看的、统计页（自带令牌）、静态资源
    let open = Router::new()
        .route("/api/health", get(admin::health))
        .route("/robots.txt", get(pages::robots))
        .route("/sitemap.xml", get(pages::sitemap))
        .route("/api/stats", get(admin::stats_api))
        .route("/stats", get(admin::stats_page))
        .route("/static/{name}", get(pages::static_file));

    protected
        .merge(open)
        .fallback(pages::fallback)
        .layer(middleware::from_fn_with_state(
            Arc::clone(&state),
            site_headers,
        ))
        .layer(CompressionLayer::new().compress_when(compressible()))
        .with_state(state)
}

/// 只压缩文本类响应：视频 / 图片本来就压缩过，压了还会破坏 Range 请求。
fn compressible() -> impl Predicate {
    SizeAbove::new(1024)
        .and(NotForContentType::IMAGES)
        .and(NotForContentType::const_new("video/"))
        .and(NotForContentType::const_new("audio/"))
        .and(NotForContentType::const_new("application/zip"))
        .and(NotForContentType::const_new("application/octet-stream"))
        .and(NotForContentType::const_new("font/"))
}

// ------------------------------------------------------------------ 客户端 IP

/// 客户端 IP。反代后面（`PARSE_VIDEO_TRUST_PROXY=1`）取 `X-Forwarded-For` 的第一个。
#[derive(Debug, Clone)]
pub struct ClientIp(pub String);

impl FromRequestParts<Shared> for ClientIp {
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &Shared,
    ) -> Result<Self, Self::Rejection> {
        let peer = parts
            .extensions
            .get::<ConnectInfo<SocketAddr>>()
            .map(|c| c.0.ip().to_string());
        Ok(Self(client_ip(&parts.headers, peer, state.cfg.trust_proxy)))
    }
}

fn client_ip(headers: &HeaderMap, peer: Option<String>, trust_proxy: bool) -> String {
    let header = |name: &str| {
        headers
            .get(name)
            .and_then(|v| v.to_str().ok())
            .map(str::trim)
    };
    let forwarded = trust_proxy
        .then(|| {
            header("x-forwarded-for")
                .and_then(|v| v.split(',').next())
                .map(str::trim)
                .or_else(|| header("x-real-ip"))
        })
        .flatten()
        .filter(|v| !v.is_empty());
    forwarded
        .map(str::to_owned)
        .or(peer)
        .unwrap_or_else(|| "unknown".into())
}

// ------------------------------------------------------------------ 中间件

async fn basic_auth(State(state): State<Shared>, req: Request, next: Next) -> Response {
    let Some(auth) = &state.cfg.auth else {
        return next.run(req).await;
    };
    let given = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Basic "))
        .and_then(|b| base64::Engine::decode(&base64::engine::general_purpose::STANDARD, b).ok())
        .and_then(|raw| String::from_utf8(raw).ok());
    let expected = format!("{}:{}", auth.username, auth.password);
    if given.is_some_and(|g| constant_time_eq(g.as_bytes(), expected.as_bytes())) {
        return next.run(req).await;
    }
    let mut resp = (StatusCode::UNAUTHORIZED, "Incorrect username or password").into_response();
    resp.headers_mut()
        .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Basic"));
    resp
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// 爬虫 / 脚本的浏览不计入统计。
fn is_bot(ua: &str) -> bool {
    let ua = ua.to_ascii_lowercase();
    [
        "bot", "spider", "crawl", "slurp", "fetch", "curl", "wget", "python", "http",
    ]
    .iter()
    .any(|k| ua.contains(k))
}

/// 安全响应头、缓存策略，顺带记页面浏览量。
async fn site_headers(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    req: Request,
    next: Next,
) -> Response {
    let path = req.uri().path().to_owned();
    let is_get = req.method() == axum::http::Method::GET;
    let bot = req
        .headers()
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .is_some_and(is_bot);
    let mut resp = next.run(req).await;

    let is_html = resp
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|c| c.starts_with("text/html"));
    let h = resp.headers_mut();
    if path.starts_with("/static/") {
        // 静态资源带内容版本号，可以长期缓存
        h.insert(
            header::CACHE_CONTROL,
            HeaderValue::from_static("public, max-age=31536000, immutable"),
        );
    } else if is_html && is_get && !path.starts_with("/api") {
        h.entry(header::CACHE_CONTROL)
            .or_insert(HeaderValue::from_static("public, max-age=600"));
    }
    h.entry("x-content-type-options")
        .or_insert(HeaderValue::from_static("nosniff"));
    h.entry("referrer-policy")
        .or_insert(HeaderValue::from_static("strict-origin-when-cross-origin"));
    h.entry("x-frame-options")
        .or_insert(HeaderValue::from_static("DENY"));
    h.entry("permissions-policy")
        .or_insert(HeaderValue::from_static(
            "camera=(), microphone=(), geolocation=()",
        ));
    if is_html {
        if let Ok(csp) = HeaderValue::from_str(&csp(state.edge.as_ref())) {
            h.entry("content-security-policy").or_insert(csp);
        }
        if resp.status() == StatusCode::OK && is_get && path != "/stats" && !bot {
            state.stats.record(Event::View {
                ip: &ip,
                path: &path,
            });
        }
    }
    resp
}

fn csp(edge: Option<&EdgeImages>) -> String {
    let edge_origin = edge.map(|e| format!(" {}", e.origin())).unwrap_or_default();
    format!(
        "default-src 'self'; img-src 'self' data: blob:{edge_origin}; media-src 'self' blob:; \
         style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self'; \
         connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
}

/// 请求的站点根地址（没配 `PARSE_VIDEO_SITE_URL` 时用）。
fn request_base(headers: &HeaderMap, trust_proxy: bool) -> String {
    let host = headers
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("localhost");
    let proto = trust_proxy
        .then(|| {
            headers
                .get("x-forwarded-proto")
                .and_then(|v| v.to_str().ok())
        })
        .flatten()
        .unwrap_or("http");
    format!("{proto}://{host}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forwarded_ip_only_when_trusted() {
        let mut h = HeaderMap::new();
        h.insert(
            "x-forwarded-for",
            HeaderValue::from_static("9.9.9.9, 10.0.0.1"),
        );
        assert_eq!(client_ip(&h, Some("1.1.1.1".into()), false), "1.1.1.1");
        assert_eq!(client_ip(&h, Some("1.1.1.1".into()), true), "9.9.9.9");
        assert_eq!(client_ip(&HeaderMap::new(), None, true), "unknown");
    }

    #[test]
    fn bots() {
        assert!(is_bot("Mozilla/5.0 (compatible; Googlebot/2.1)"));
        assert!(is_bot("curl/8.0"));
        assert!(!is_bot(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X)"
        ));
    }
}
