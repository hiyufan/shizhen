//! 任务本体：拉原视频、服务端合并下载、GIF / 实况转换、平台实况打包。
//!
//! 每个任务是一个普通的 async 函数，拿 [`Toolkit`] 和 [`Progress`]，返回 [`JobOutput`]。
//! 出错就 `?` 往上抛，半截文件由 [`crate::fsutil::PartialFile`] 兜底删除。

mod convert;
mod download;
mod live;
mod source;

use std::path::{Path, PathBuf};

pub use convert::{convert, ConvertSpec, Format as ConvertFormat};
pub use download::download_for_user;
pub use live::{pair_live, LiveFormat, LiveItem};
pub use source::{fetch_source, make_strip};

use crate::error::{TaskError, TaskResult};
use crate::fsutil::PartialFile;
use crate::jobs::Span;
use crate::media::Ffmpeg;
use crate::net::{Download, MediaClient};
use crate::parse::MediaToken;

/// 任务要用到的工具，启动时组装一次，所有任务共用。
#[derive(Debug, Clone)]
pub struct Toolkit {
    pub ffmpeg: Ffmpeg,
    pub media: MediaClient,
    pub sources_dir: PathBuf,
    pub outputs_dir: PathBuf,
    pub max_source_bytes: u64,
}

/// 一个要拉到本地的媒体。
#[derive(Debug, Clone)]
pub enum MediaSource {
    /// 单个文件的直链
    Direct { url: String, headers: Vec<(String, String)> },
    /// HLS 播放列表，交给 ffmpeg 读
    Hls { url: String, headers: Vec<(String, String)> },
    /// 分离的音视频轨（高清档），下载后合并
    Tracks(MediaToken),
}

impl MediaSource {
    /// 直链按扩展名区分是不是 HLS。
    pub fn from_url(url: String, headers: Vec<(String, String)>) -> Self {
        let path = url.split('?').next().unwrap_or("").to_ascii_lowercase();
        if path.ends_with(".m3u8") {
            Self::Hls { url, headers }
        } else {
            Self::Direct { url, headers }
        }
    }

    /// 只有音轨（"仅音频"那一档）。
    fn audio_only(&self) -> bool {
        matches!(self, Self::Tracks(t) if t.video.is_empty())
    }
}

/// 把一个媒体落成 `dir/stem.mp4`（仅音频时是 `.m4a`），返回最终路径。
pub async fn materialize(kit: &Toolkit, media: &MediaSource, dir: &Path, stem: &str, span: &Span) -> TaskResult<PathBuf> {
    let ext = if media.audio_only() { "m4a" } else { "mp4" };
    let dest = PartialFile::new(dir.join(format!("{stem}.{ext}")));
    match media {
        MediaSource::Direct { url, headers } => fetch(kit, url, headers, dest.path(), Some(span)).await?,
        MediaSource::Hls { url, headers } => kit.ffmpeg.fetch_hls(url, headers, dest.path()).await?,
        MediaSource::Tracks(t) => tracks(kit, t, dir, stem, dest.path(), span).await?,
    }
    Ok(dest.keep())
}

/// 分轨：视频、音频各下载一份，再无损合并。只有一条轨的就只下那一条。
async fn tracks(kit: &Toolkit, t: &MediaToken, dir: &Path, stem: &str, dest: &Path, span: &Span) -> TaskResult<()> {
    let video = PartialFile::new(dir.join(format!("{stem}.video")));
    let audio = PartialFile::new(dir.join(format!("{stem}.audio")));
    match (t.video.is_empty(), t.audio.is_empty()) {
        (false, false) => {
            fetch(kit, &t.video, &t.headers, video.path(), Some(&span.sub(0.0, 0.8))).await?;
            fetch(kit, &t.audio, &t.headers, audio.path(), Some(&span.sub(0.8, 0.95))).await?;
            kit.ffmpeg.merge(video.path(), audio.path(), dest).await
        }
        (false, true) => fetch(kit, &t.video, &t.headers, dest, Some(span)).await,
        (true, false) => {
            fetch(kit, &t.audio, &t.headers, audio.path(), Some(&span.sub(0.0, 0.95))).await?;
            kit.ffmpeg.extract_audio(audio.path(), dest).await
        }
        (true, true) => Err(TaskError::new("缺少视频地址")),
    }
}

async fn fetch(kit: &Toolkit, url: &str, headers: &[(String, String)], dest: &Path, span: Option<&Span>) -> TaskResult<()> {
    let d = Download { url, headers, dest, max_bytes: kit.max_source_bytes };
    kit.media.download(d, span).await
}

/// EXIF / MOV 里用的时间：本地时间 `2026:09:25 14:00:00` 和 UTC 的 ISO 8601。
fn timestamps() -> (String, String) {
    let now = chrono::Local::now();
    let local = now.format("%Y:%m:%d %H:%M:%S").to_string();
    let utc = now.with_timezone(&chrono::Utc).format("%Y-%m-%dT%H:%M:%SZ").to_string();
    (local, utc)
}

/// 随机的文件名片段，任务之间的输出文件互不覆盖。
fn unique_stem() -> String {
    hex::encode(rand::random::<[u8; 6]>())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hls_is_detected_by_path_not_query() {
        assert!(matches!(MediaSource::from_url("https://a/x.m3u8?t=1".into(), vec![]), MediaSource::Hls { .. }));
        assert!(matches!(MediaSource::from_url("https://a/x.mp4?f=.m3u8".into(), vec![]), MediaSource::Direct { .. }));
    }
}
