//! 页面、sitemap、robots、静态资源。

use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, StatusCode, Uri};
use axum::response::{Html, IntoResponse, Response};

use super::{request_base, Shared};
use crate::site::{self, seo, Rendered};

fn base(state: &Shared, headers: &HeaderMap) -> String {
    state.site.base_url(&request_base(headers, state.cfg.trust_proxy))
}

/// 渲染结果 -> 响应。模板出错是我们自己的 bug，记日志、给 500。
fn html(rendered: Rendered, status: StatusCode) -> Response {
    match rendered {
        Ok(body) => (status, Html(body)).into_response(),
        Err(e) => {
            tracing::error!(error = %e, "模板渲染失败");
            (StatusCode::INTERNAL_SERVER_ERROR, "页面渲染失败").into_response()
        }
    }
}

fn not_found(state: &Shared, path: &str, base: &str) -> Response {
    html(state.site.not_found(path, base), StatusCode::NOT_FOUND)
}

pub async fn home(State(state): State<Shared>, headers: HeaderMap) -> Response {
    landing(State(state), headers, Path(String::new())).await
}

/// SEO 落地页：/douyin /xiaohongshu /gif ... 同一个工具，不同的标题和文案。
pub async fn landing(State(state): State<Shared>, headers: HeaderMap, Path(slug): Path<String>) -> Response {
    let base = base(&state, &headers);
    match state.site.landing(&slug, &base) {
        Some(r) => html(r, StatusCode::OK),
        None => not_found(&state, &format!("/{slug}"), &base),
    }
}

pub async fn guides(State(state): State<Shared>, headers: HeaderMap) -> Response {
    let base = base(&state, &headers);
    html(state.site.guides(&base), StatusCode::OK)
}

pub async fn guide(State(state): State<Shared>, headers: HeaderMap, Path(slug): Path<String>) -> Response {
    let base = base(&state, &headers);
    match state.site.guide(&slug, &base) {
        Some(r) => html(r, StatusCode::OK),
        None => not_found(&state, &format!("/guide/{slug}"), &base),
    }
}

pub async fn fallback(State(state): State<Shared>, headers: HeaderMap, uri: Uri) -> Response {
    not_found(&state, uri.path(), &base(&state, &headers))
}

pub async fn sitemap(State(state): State<Shared>, headers: HeaderMap) -> Response {
    let xml = seo::sitemap(&state.site.content, &base(&state, &headers));
    ([(header::CONTENT_TYPE, "application/xml")], xml).into_response()
}

pub async fn robots(State(state): State<Shared>, headers: HeaderMap) -> Response {
    let txt = seo::robots(&base(&state, &headers));
    ([(header::CONTENT_TYPE, "text/plain; charset=utf-8")], txt).into_response()
}

pub async fn static_file(Path(name): Path<String>) -> Response {
    match site::static_file(&name) {
        Some((body, ctype)) => ([(header::CONTENT_TYPE, ctype)], body).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}
