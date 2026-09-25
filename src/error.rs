//! 接口错误：状态码 + 给用户看的一句话。
//!
//! 响应体是 `{"detail": "..."}`，和 Python 版（FastAPI 的 HTTPException）一致，
//! 前端按 `body.detail` 取文案。

use std::time::Duration;

use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;

#[derive(Debug)]
pub struct ApiError {
    pub status: StatusCode,
    pub detail: String,
    /// 429 / 503 时告诉客户端多久后再来
    pub retry_after: Option<Duration>,
}

pub type ApiResult<T> = Result<T, ApiError>;

impl ApiError {
    pub fn new(status: StatusCode, detail: impl Into<String>) -> Self {
        Self {
            status,
            detail: detail.into(),
            retry_after: None,
        }
    }

    pub fn bad_request(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, detail)
    }
    pub fn forbidden(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::FORBIDDEN, detail)
    }
    pub fn not_found(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, detail)
    }
    pub fn too_many(detail: impl Into<String>, retry_after: Duration) -> Self {
        Self {
            retry_after: Some(retry_after),
            ..Self::new(StatusCode::TOO_MANY_REQUESTS, detail)
        }
    }
    pub fn unavailable(detail: impl Into<String>, retry_after: Duration) -> Self {
        Self {
            retry_after: Some(retry_after),
            ..Self::new(StatusCode::SERVICE_UNAVAILABLE, detail)
        }
    }
    pub fn internal(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, detail)
    }

    /// 带签名的地址才转发 / 下载：不是我们解析出来的一律拒绝。
    pub fn not_ours() -> Self {
        Self::forbidden("这个地址不是解析结果里的")
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let mut resp = (self.status, Json(serde_json::json!({ "detail": self.detail }))).into_response();
        if let Some(wait) = self.retry_after {
            if let Ok(v) = HeaderValue::from_str(&wait.as_secs().max(1).to_string()) {
                resp.headers_mut().insert(header::RETRY_AFTER, v);
            }
        }
        resp
    }
}

/// 后台任务 / 内部步骤的错误：给用户看的一句话，已经去掉了服务器路径。
#[derive(Debug)]
pub struct TaskError(pub String);

impl TaskError {
    pub fn new(msg: impl Into<String>) -> Self {
        Self(msg.into())
    }
}

impl std::fmt::Display for TaskError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for TaskError {}

impl From<std::io::Error> for TaskError {
    fn from(e: std::io::Error) -> Self {
        Self(format!("读写文件失败: {}", e.kind()))
    }
}

impl From<TaskError> for ApiError {
    fn from(e: TaskError) -> Self {
        Self::internal(e.0)
    }
}

pub type TaskResult<T> = Result<T, TaskError>;
