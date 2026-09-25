//! 准备原视频：拉到本地、探测时长尺寸，登记到 [`Store`] 供反复转换。

use std::path::PathBuf;

use serde_json::json;

use super::{materialize, MediaSource, Toolkit};
use crate::error::{TaskError, TaskResult};
use crate::fsutil::PartialFile;
use crate::jobs::{JobOutput, Progress};
use crate::store::{Source, Store};

pub async fn fetch_source(
    kit: &Toolkit,
    store: &Store,
    progress: &Progress,
    source_id: &str,
    media: &MediaSource,
    title: &str,
) -> TaskResult<JobOutput> {
    if let Some(existing) = store.get(source_id) {
        progress.step(1.0, "已就绪");
        return Ok(output(&existing));
    }

    progress.step(0.02, "正在下载原视频");
    let path = materialize(kit, media, &kit.sources_dir, source_id, &progress.span(0.02, 0.92)).await?;
    let path = PartialFile::new(path); // 探测失败时也别留下

    progress.step(0.95, "正在读取视频信息");
    let probe = kit.ffmpeg.probe(path.path()).await?;
    if !probe.is_video() {
        return Err(TaskError::new("下载到的文件不是可用的视频"));
    }
    let source = Source {
        id: source_id.to_owned(),
        path: path.keep(),
        title: title.to_owned(),
        duration: probe.duration,
        width: probe.width,
        height: probe.height,
        fps: probe.fps,
        strip: None,
    };
    store.put(source.clone());
    Ok(output(&source))
}

/// 前端从 `extra` 里读原视频信息。
fn output(source: &Source) -> JobOutput {
    JobOutput {
        extra: json!(source.view()),
        ..JobOutput::default()
    }
}

/// 裁剪器背景用的缩略图条；生成过就直接用。
pub async fn make_strip(kit: &Toolkit, store: &Store, source: &Source, frames: u32) -> TaskResult<PathBuf> {
    if let Some(strip) = source.strip.as_ref().filter(|p| p.exists()) {
        return Ok(strip.clone());
    }
    let dest = PartialFile::new(kit.sources_dir.join(format!("{}_strip.jpg", source.id)));
    kit.ffmpeg.filmstrip(&source.path, dest.path(), source.duration, frames).await?;
    let strip = dest.keep();
    store.set_strip(&source.id, strip.clone());
    Ok(strip)
}
