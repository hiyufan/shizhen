//! 发送本地文件：支持 Range（`<video>` 拖进度条要用），可选作为附件下载。

use std::path::Path;

use axum::body::Body;
use axum::extract::Request;
use axum::http::{header, HeaderValue};
use axum::response::{IntoResponse, Response};
use tower::ServiceExt;
use tower_http::services::ServeFile;

/// `download_name` 给了就加 `Content-Disposition: attachment`。
pub async fn send(
    path: &Path,
    content_type: &str,
    download_name: Option<&str>,
    req: Request,
) -> Response {
    let (parts, _) = req.into_parts();
    let req = Request::from_parts(parts, Body::empty());
    let mut resp = match ServeFile::new(path).oneshot(req).await {
        Ok(r) => r.into_response(),
        Err(never) => match never {},
    };
    // 类型由调用方决定，不按扩展名猜（.MOV 之类猜出来的不一定对）
    if resp.status().is_success() {
        if let Ok(v) = HeaderValue::from_str(content_type) {
            resp.headers_mut().insert(header::CONTENT_TYPE, v);
        }
    }
    if let Some(name) = download_name {
        if let Ok(v) = HeaderValue::from_str(&crate::net::attachment_header(name)) {
            resp.headers_mut().insert(header::CONTENT_DISPOSITION, v);
        }
    }
    resp
}

/// 按扩展名给结果文件的 Content-Type。
pub fn content_type_for(path: &Path) -> &'static str {
    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_ascii_lowercase);
    match ext.as_deref() {
        Some("gif") => "image/gif",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("zip") => "application/zip",
        Some("mp4") => "video/mp4",
        Some("m4a") => "audio/mp4",
        Some("webm") => "video/webm",
        Some("mov") => "video/quicktime",
        _ => "application/octet-stream",
    }
}
