//! 转换相关接口：准备原视频、上传、转换、服务端合并下载、实况打包。

use std::collections::BTreeMap;
use std::future::Future;
use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Multipart, Path, Query, Request, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;

use super::files::send;
use super::{ClientIp, Shared};
use crate::config::limits::{LIVE_MAX_ITEMS, SOURCE_MAX_SHORT_SIDE};
use crate::error::{ApiError, ApiResult, TaskError, TaskResult};
use crate::fsutil::PartialFile;
use crate::jobs::tasks::{self, ConvertFormat, ConvertSpec, LiveFormat, LiveItem, MediaSource};
use crate::jobs::{JobOutput, JobSpec, Progress};
use crate::media::Dither;
use crate::net::is_public_url;
use crate::parse::MediaToken;
use crate::store::{source_id_for, Source};

/// 入队：先占这个 IP 的任务名额，名额随任务一起结束。
fn start_job<F, Fut>(
    state: &Shared,
    ip: &str,
    kind: &'static str,
    source_id: Option<String>,
    task: F,
) -> ApiResult<Value>
where
    F: FnOnce(Progress) -> Fut + Send + 'static,
    Fut: Future<Output = TaskResult<JobOutput>> + Send + 'static,
{
    let slot = state.limits.jobs.acquire(ip)?;
    let spec = JobSpec {
        kind,
        source_id,
        owner: ip.to_owned(),
    };
    let job = state.jobs.start(spec, slot, task).map_err(|_| {
        ApiError::unavailable(
            "服务器正忙，排队的任务太多了，请稍后再试",
            std::time::Duration::from_secs(30),
        )
    })?;
    Ok(json!(job.view()))
}

fn source_or_404(state: &Shared, id: &str) -> ApiResult<Source> {
    state
        .store
        .get(id)
        .ok_or_else(|| ApiError::not_found("原视频已过期，请重新解析"))
}

async fn ensure_ours(state: &Shared, url: &str, sig: &str) -> ApiResult<()> {
    if !state.signer.verify(url, sig) {
        return Err(ApiError::not_ours());
    }
    if !is_public_url(url, state.cfg.ssrf_dns).await {
        return Err(ApiError::bad_request("不支持的地址"));
    }
    Ok(())
}

// ------------------------------------------------------------------ 准备原视频

#[derive(Deserialize)]
pub struct PrepareRequest {
    /// 直链
    #[serde(default)]
    url: String,
    /// 页面地址：没有合适的直链时，服务端重新解析它、挑一档高清来合并
    #[serde(default)]
    page_url: String,
    /// 某一档的媒体令牌（可选）
    #[serde(default)]
    format_spec: String,
    #[serde(default)]
    headers: BTreeMap<String, String>,
    #[serde(default)]
    title: String,
    #[serde(default)]
    sig: String,
}

/// 要准备的东西从哪来。
enum Origin {
    Ready(MediaSource),
    /// 要先解析页面才知道拉哪条
    Page(String),
}

/// 把原视频缓存到服务端，供转换预览 / 裁剪使用。已缓存则立即返回。
pub async fn prepare(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    Json(req): Json<PrepareRequest>,
) -> ApiResult<Json<Value>> {
    state.limits.job.hit(&ip)?;
    let (origin, sid) = prepare_origin(&state, &req).await?;

    if let Some(src) = state.store.get(&sid) {
        return Ok(Json(json!({ "ready": true, "source": src.view() })));
    }
    // 同一个来源已经有人在准备了（爆款链接常见），跟着等那个任务
    if let Some(pending) = state.jobs.find_pending("prepare", &sid) {
        return Ok(Json(
            json!({ "ready": false, "job": pending.view(), "source_id": sid }),
        ));
    }

    let st = Arc::clone(&state);
    let (id, title) = (sid.clone(), req.title);
    let job = start_job(
        &state,
        &ip,
        "prepare",
        Some(sid.clone()),
        move |progress| async move {
            let media = match origin {
                Origin::Ready(m) => m,
                Origin::Page(page) => media_for_page(&st, &page).await?,
            };
            tasks::fetch_source(&st.kit, &st.store, &progress, &id, &media, &title).await
        },
    )?;
    Ok(Json(
        json!({ "ready": false, "job": job, "source_id": sid }),
    ))
}

async fn prepare_origin(state: &Shared, req: &PrepareRequest) -> ApiResult<(Origin, String)> {
    if !req.format_spec.is_empty() {
        let token =
            MediaToken::decode(&req.format_spec, &state.signer).ok_or_else(ApiError::not_ours)?;
        let sid = source_id_for(&[&token.video, &token.audio]);
        return Ok((Origin::Ready(MediaSource::Tracks(token)), sid));
    }
    if !req.page_url.is_empty() {
        ensure_ours(state, &req.page_url, &req.sig).await?;
        return Ok((
            Origin::Page(req.page_url.clone()),
            source_id_for(&[&req.page_url, "default"]),
        ));
    }
    if req.url.is_empty() {
        return Err(ApiError::bad_request("缺少视频地址"));
    }
    ensure_ours(state, &req.url, &req.sig).await?;
    let headers = req
        .headers
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    Ok((
        Origin::Ready(MediaSource::from_url(req.url.clone(), headers)),
        source_id_for(&[&req.url]),
    ))
}

/// 页面地址 -> 适合转换的一条：高清分轨里挑不超过 1080p 的最高一档，没有就用直链。
async fn media_for_page(state: &Shared, page: &str) -> TaskResult<MediaSource> {
    let info = state
        .parse
        .info(page)
        .await
        .map_err(|e| TaskError::new(e.to_string()))?;
    let headers: Vec<(String, String)> = info
        .video_headers
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let short = |f: &alcedo::Format| f.height;
    let best = info
        .formats
        .iter()
        .filter(|f| f.needs_merge() && short(f) <= SOURCE_MAX_SHORT_SIDE)
        // 同高度优先 H.264：转换时重编码更快，老设备也放得了
        .max_by_key(|f| (short(f), f.codec.is_empty()));
    if let Some(f) = best {
        let token = MediaToken {
            video: f.video_url.clone(),
            audio: f.audio_url.clone(),
            headers,
        };
        return Ok(MediaSource::Tracks(token));
    }
    if info.video_url.is_empty() {
        return Err(TaskError::new("这条内容没有可以转换的视频"));
    }
    Ok(MediaSource::from_url(info.video_url, headers))
}

// ------------------------------------------------------------------ 上传

/// 本地视频也能转 GIF / 实况。
pub async fn upload(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    mut form: Multipart,
) -> ApiResult<Json<Value>> {
    state.limits.upload.hit(&ip)?;
    let field = loop {
        match form
            .next_field()
            .await
            .map_err(|_| ApiError::bad_request("上传中断了"))?
        {
            Some(f) if f.name() == Some("file") => break f,
            Some(_) => continue,
            None => return Err(ApiError::bad_request("没有收到文件")),
        }
    };
    let original = field.file_name().unwrap_or("video").to_owned();
    let original_path = std::path::Path::new(&original);
    let suffix = original_path
        .extension()
        .and_then(|e| e.to_str())
        .filter(|e| e.len() <= 5 && e.chars().all(char::is_alphanumeric))
        .map_or_else(|| "mp4".to_owned(), str::to_ascii_lowercase);
    let sid = hex::encode(rand::random::<[u8; 8]>());
    let dest = PartialFile::new(state.cfg.uploads_dir().join(format!("{sid}.{suffix}")));

    receive(field, dest.path(), state.cfg.max_upload_bytes).await?;
    let probe = state.kit.ffmpeg.probe(dest.path()).await?;
    if !probe.is_video() {
        return Err(ApiError::bad_request("这个文件不是可识别的视频"));
    }
    let title = original_path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let source = Source {
        id: sid,
        path: dest.keep(),
        title,
        duration: probe.duration,
        width: probe.width,
        height: probe.height,
        fps: probe.fps,
        strip: None,
    };
    state.store.put(source.clone());
    Ok(Json(json!({ "ready": true, "source": source.view() })))
}

async fn receive(
    mut field: axum::extract::multipart::Field<'_>,
    dest: &std::path::Path,
    max: u64,
) -> ApiResult<()> {
    let mut file = tokio::fs::File::create(dest)
        .await
        .map_err(|_| ApiError::internal("写文件失败"))?;
    let mut size = 0u64;
    while let Some(chunk) = field
        .chunk()
        .await
        .map_err(|_| ApiError::bad_request("上传中断了"))?
    {
        size += chunk.len() as u64;
        if size > max {
            return Err(ApiError::new(StatusCode::PAYLOAD_TOO_LARGE, "文件太大"));
        }
        file.write_all(&chunk)
            .await
            .map_err(|_| ApiError::internal("写文件失败"))?;
    }
    file.flush()
        .await
        .map_err(|_| ApiError::internal("写文件失败"))
}

// ------------------------------------------------------------------ 原视频文件与缩略图条

pub async fn source_file(
    State(state): State<Shared>,
    Path(id): Path<String>,
    req: Request,
) -> ApiResult<Response> {
    let src = source_or_404(&state, &id)?;
    Ok(send(&src.path, "video/mp4", None, req).await)
}

#[derive(Deserialize)]
pub struct StripQuery {
    #[serde(default = "default_frames")]
    n: u32,
}

fn default_frames() -> u32 {
    16
}

pub async fn source_strip(
    State(state): State<Shared>,
    Path(id): Path<String>,
    Query(q): Query<StripQuery>,
    req: Request,
) -> ApiResult<Response> {
    let src = source_or_404(&state, &id)?;
    let path: PathBuf = tasks::make_strip(&state.kit, &state.store, &src, q.n).await?;
    let mut resp = send(&path, "image/jpeg", None, req).await;
    resp.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, max-age=3600"),
    );
    Ok(resp)
}

// ------------------------------------------------------------------ 转换

#[derive(Deserialize)]
pub struct ConvertRequest {
    source_id: String,
    format: String,
    #[serde(default)]
    start: f64,
    end: Option<f64>,
    #[serde(default = "default_fps")]
    fps: u32,
    #[serde(default = "default_width")]
    width: u32,
    #[serde(default = "default_dither")]
    dither: String,
    #[serde(default = "default_speed")]
    speed: f64,
    key_time: Option<f64>,
}

fn default_fps() -> u32 {
    12
}
fn default_width() -> u32 {
    480
}
fn default_dither() -> String {
    "bayer".into()
}
fn default_speed() -> f64 {
    1.0
}

impl ConvertRequest {
    fn spec(&self) -> ApiResult<ConvertSpec> {
        let format = match self.format.as_str() {
            "gif" => ConvertFormat::Gif,
            "livephoto" => ConvertFormat::LivePhoto,
            "motionphoto" => ConvertFormat::MotionPhoto,
            _ => return Err(ApiError::bad_request("不支持的格式")),
        };
        let dither = match self.dither.as_str() {
            "bayer" => Dither::Bayer,
            "sierra2_4a" => Dither::Sierra,
            "none" => Dither::None,
            _ => return Err(ApiError::bad_request("不支持的抖动算法")),
        };
        Ok(ConvertSpec {
            format,
            start: self.start,
            end: self.end,
            fps: self.fps,
            width: self.width,
            dither,
            speed: self.speed,
            key_time: self.key_time,
        })
    }
}

pub async fn convert(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    Json(req): Json<ConvertRequest>,
) -> ApiResult<Json<Value>> {
    state.limits.job.hit(&ip)?;
    let spec = req.spec()?;
    let src = source_or_404(&state, &req.source_id)?;
    let kind = match spec.format {
        ConvertFormat::Gif => "gif",
        ConvertFormat::LivePhoto => "livephoto",
        ConvertFormat::MotionPhoto => "motionphoto",
    };
    let st = Arc::clone(&state);
    let sid = src.id.clone();
    let job = start_job(&state, &ip, kind, Some(sid), move |progress| async move {
        tasks::convert(&st.kit, &progress, &src, &spec).await
    })?;
    Ok(Json(job))
}

// ------------------------------------------------------------------ 服务端合并下载

#[derive(Deserialize)]
pub struct DownloadRequest {
    format_spec: String,
    #[serde(default)]
    title: String,
}

/// 需要服务端合并的清晰度：先下载合并，再给文件。
pub async fn download(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    Json(req): Json<DownloadRequest>,
) -> ApiResult<Json<Value>> {
    state.limits.job.hit(&ip)?;
    let token =
        MediaToken::decode(&req.format_spec, &state.signer).ok_or_else(ApiError::not_ours)?;
    let st = Arc::clone(&state);
    let job = start_job(&state, &ip, "download", None, move |progress| async move {
        tasks::download_for_user(&st.kit, &progress, &MediaSource::Tracks(token), &req.title).await
    })?;
    Ok(Json(job))
}

// ------------------------------------------------------------------ 平台实况原样打包

#[derive(Deserialize)]
pub struct LiveRequestItem {
    image_url: String,
    video_url: String,
    #[serde(default)]
    image_sig: String,
    #[serde(default)]
    video_sig: String,
}

#[derive(Deserialize)]
pub struct LiveRequest {
    items: Vec<LiveRequestItem>,
    #[serde(default = "default_live_format")]
    format: String,
    #[serde(default)]
    title: String,
}

fn default_live_format() -> String {
    "livephoto".into()
}

pub async fn live(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    Json(req): Json<LiveRequest>,
) -> ApiResult<Json<Value>> {
    state.limits.job.hit(&ip)?;
    if req.items.is_empty() || req.items.len() > LIVE_MAX_ITEMS {
        return Err(ApiError::bad_request(format!(
            "一次打包 1 到 {LIVE_MAX_ITEMS} 张"
        )));
    }
    let fmt = match req.format.as_str() {
        "livephoto" => LiveFormat::LivePhoto,
        "motionphoto" => LiveFormat::MotionPhoto,
        _ => return Err(ApiError::bad_request("不支持的格式")),
    };
    for it in &req.items {
        ensure_ours(&state, &it.image_url, &it.image_sig).await?;
        ensure_ours(&state, &it.video_url, &it.video_sig).await?;
    }
    let items: Vec<LiveItem> = req
        .items
        .into_iter()
        .map(|i| LiveItem {
            image_url: i.image_url,
            video_url: i.video_url,
        })
        .collect();
    let st = Arc::clone(&state);
    let job = start_job(&state, &ip, "live", None, move |progress| async move {
        tasks::pair_live(&st.kit, &progress, &items, fmt, &req.title).await
    })?;
    Ok(Json(job))
}
