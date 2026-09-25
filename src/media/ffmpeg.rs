//! ffmpeg 封装。
//!
//! 每个操作是一个带参数结构体的方法，参数多了也不会写成一长串位置参数。
//! 子进程设了 `kill_on_drop`：任务超时或被取消时 future 被丢弃，ffmpeg 跟着被杀，
//! 不需要另外的取消标志。

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::LazyLock;

use regex::Regex;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;

use crate::error::{TaskError, TaskResult};
use crate::jobs::Span;

#[derive(Debug, Clone)]
pub struct Ffmpeg {
    exe: PathBuf,
    threads: usize,
}

/// `ffmpeg -i` 读出来的视频信息。
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Probe {
    pub duration: f64,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
}

impl Probe {
    pub fn is_video(&self) -> bool {
        self.duration > 0.0 && self.width > 0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dither {
    Bayer,
    Sierra,
    None,
}

impl Dither {
    fn filter(self) -> &'static str {
        match self {
            Dither::Bayer => "dither=bayer:bayer_scale=4",
            Dither::Sierra => "dither=sierra2_4a",
            Dither::None => "dither=none",
        }
    }
}

#[derive(Debug)]
pub struct Gif<'a> {
    pub src: &'a Path,
    pub dst: &'a Path,
    pub start: f64,
    pub duration: f64,
    pub fps: u32,
    pub width: u32,
    pub dither: Dither,
    pub speed: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Container {
    Mov,
    Mp4,
}

/// 一段 H.264 + AAC，实况 / 动态照片的原料。
#[derive(Debug)]
pub struct Segment<'a> {
    pub src: &'a Path,
    pub dst: &'a Path,
    pub start: f64,
    pub duration: f64,
    pub container: Container,
    /// 额外写进容器的元数据（Apple 的实况配对标识就走这里）
    pub metadata: &'a [(&'a str, String)],
}

const MAX_HEIGHT: u32 = 1080;
const SEGMENT_FPS: u32 = 30;

impl Ffmpeg {
    /// 优先用 PATH 上的 ffmpeg，其次 `data/bin/` 里的。
    pub fn locate(bin_dir: &Path, threads: usize) -> Result<Self, String> {
        let name = if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" };
        let on_path = std::env::var_os("PATH")
            .map(|p| std::env::split_paths(&p).map(|d| d.join(name)).find(|c| c.is_file()))
            .unwrap_or_default();
        let local = bin_dir.join(name);
        let exe = on_path
            .or_else(|| local.is_file().then_some(local))
            .ok_or("没找到 ffmpeg：装到 PATH 上，或者放在 data/bin/ 下")?;
        Ok(Self { exe, threads })
    }

    /// 读 `ffmpeg -i` 的输出：不依赖 ffprobe，少装一个二进制。
    pub async fn probe(&self, path: &Path) -> TaskResult<Probe> {
        let out = Command::new(&self.exe)
            .args(["-hide_banner", "-i"])
            .arg(path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .output()
            .await
            .map_err(|e| TaskError::new(format!("启动 ffmpeg 失败: {}", e.kind())))?;
        Ok(parse_probe(&String::from_utf8_lossy(&out.stderr)))
    }

    /// 两遍调色板的 GIF：先统计颜色生成调色板，再用它抖动编码。
    pub async fn gif(&self, g: &Gif<'_>, progress: &Span) -> TaskResult<()> {
        let speed = g.speed.clamp(0.25, 4.0);
        let pts = if (speed - 1.0).abs() < 1e-3 {
            String::new()
        } else {
            format!("setpts=PTS/{speed},")
        };
        let graph = format!(
            "[0:v]{pts}fps={fps},scale={w}:-2:flags=lanczos,split[a][b];\
             [a]palettegen=max_colors=256:stats_mode=diff[p];\
             [b][p]paletteuse={dither}:diff_mode=rectangle",
            fps = g.fps,
            w = g.width,
            dither = g.dither.filter(),
        );
        let args = Args::new()
            .seek(g.start, g.duration)
            .input(g.src)
            .flags(&["-filter_complex", &graph, "-loop", "0", "-an"])
            .output(g.dst);
        self.run(args, Some((progress, g.duration / speed))).await
    }

    pub async fn segment(&self, s: &Segment<'_>, progress: Option<&Span>) -> TaskResult<()> {
        let vf = format!("scale=-2:'min({MAX_HEIGHT},ih)':flags=lanczos,fps={SEGMENT_FPS}");
        let mut args = Args::new().seek(s.start, s.duration).input(s.src).flags(&[
            "-vf", &vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart+use_metadata_tags", "-map_metadata", "-1",
        ]);
        for (k, v) in s.metadata {
            args = args.flags(&["-metadata", &format!("{k}={v}")]);
        }
        let format = match s.container {
            Container::Mov => "mov",
            Container::Mp4 => "mp4",
        };
        let args = args.flags(&["-f", format]).output(s.dst);
        self.run(args, progress.map(|p| (p, s.duration))).await
    }

    /// 平台自带的实况短视频不重编码，换成 MOV 并写入 Apple 配对标识。
    pub async fn remux_live(&self, src: &Path, dst: &Path, identifier: &str) -> TaskResult<()> {
        let meta = format!("com.apple.quicktime.content.identifier={identifier}");
        let args = Args::new().input(src).flags(&[
            "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart+use_metadata_tags",
            "-metadata", &meta, "-f", "mov",
        ]);
        self.run(args.output(dst), None).await
    }

    /// 高清档的音视频分轨合成一个 mp4，不重编码。
    pub async fn merge(&self, video: &Path, audio: &Path, dst: &Path) -> TaskResult<()> {
        let args = Args::new().input(video).input(audio).flags(&[
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4",
        ]);
        self.run(args.output(dst), None).await
    }

    /// "仅音频"：DASH 的音轨是分片 mp4，换成普通 m4a，各种播放器都认。
    pub async fn extract_audio(&self, src: &Path, dst: &Path) -> TaskResult<()> {
        let args = Args::new().input(src).flags(&["-vn", "-c", "copy", "-movflags", "+faststart", "-f", "ipod"]);
        self.run(args.output(dst), None).await
    }

    /// 读一个 HLS 播放列表存成 mp4（AcFun 这类只给 m3u8 的）。
    pub async fn fetch_hls(&self, url: &str, headers: &[(String, String)], dst: &Path) -> TaskResult<()> {
        let header_blob: String = headers.iter().map(|(k, v)| format!("{k}: {v}\r\n")).collect();
        let args = Args::new()
            .flags(&["-headers", &header_blob, "-i", url, "-c", "copy", "-f", "mp4"])
            .output(dst);
        self.run(args, None).await
    }

    pub async fn frame(&self, src: &Path, dst: &Path, at: f64) -> TaskResult<()> {
        let vf = format!("scale=-2:'min({MAX_HEIGHT},ih)':flags=lanczos");
        let args = Args::new()
            .flags(&["-ss", &format!("{at:.3}")])
            .input(src)
            .flags(&["-frames:v", "1", "-vf", &vf, "-q:v", "2", "-f", "image2"]);
        self.run(args.output(dst), None).await
    }

    /// 任意图片（webp / png / heic）转 JPEG。实况配对只认 JPEG。
    pub async fn to_jpeg(&self, src: &Path, dst: &Path) -> TaskResult<()> {
        let args = Args::new().input(src).flags(&["-frames:v", "1", "-q:v", "2", "-f", "image2"]);
        self.run(args.output(dst), None).await
    }

    /// 一张横排的缩略图条，裁剪器的背景。
    pub async fn filmstrip(&self, src: &Path, dst: &Path, duration: f64, frames: u32) -> TaskResult<()> {
        let frames = frames.clamp(2, 40);
        let step = (duration / f64::from(frames)).max(0.04);
        let vf = format!("fps=1/{step:.4},scale=-2:72,tile={frames}x1");
        let args = Args::new()
            .input(src)
            .flags(&["-vf", &vf, "-frames:v", "1", "-q:v", "5", "-f", "image2"]);
        self.run(args.output(dst), None).await
    }

    /// 跑 ffmpeg；给了 `(span, 输出时长)` 就把 `-progress` 的输出换算成进度。
    async fn run(&self, args: Args, progress: Option<(&Span, f64)>) -> TaskResult<()> {
        let mut child = Command::new(&self.exe)
            .args(["-hide_banner", "-y", "-nostats", "-loglevel", "error"])
            .args(["-threads", &self.threads.to_string(), "-progress", "pipe:1"])
            .args(args.0)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| TaskError::new(format!("启动 ffmpeg 失败: {}", e.kind())))?;

        let stdout = child.stdout.take().map(BufReader::new);
        let mut stderr = child.stderr.take();
        // stdout / stderr 要同时读，任何一边缓冲区写满都会把 ffmpeg 卡死
        let (_, errors) = tokio::join!(report_progress(stdout, progress), async {
            let mut buf = String::new();
            if let Some(e) = stderr.as_mut() {
                let _ = e.read_to_string(&mut buf).await;
            }
            buf
        });
        let status = child.wait().await?;
        if status.success() {
            return Ok(());
        }
        let last = errors.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("");
        Err(TaskError::new(format!("ffmpeg 失败: {}", last.trim())))
    }
}

/// `-progress pipe:1` 每隔一会儿输出一段 `key=value`，其中 `out_time_us` 是已输出的时长。
async fn report_progress(
    stdout: Option<BufReader<tokio::process::ChildStdout>>,
    progress: Option<(&Span, f64)>,
) {
    let Some(mut lines) = stdout.map(AsyncBufReadExt::lines) else {
        return;
    };
    while let Ok(Some(line)) = lines.next_line().await {
        let Some((span, total)) = progress.filter(|(_, t)| *t > 0.0) else {
            continue; // 不报进度也得把输出读完
        };
        let value = line
            .strip_prefix("out_time_us=")
            .or_else(|| line.strip_prefix("out_time_ms=")) // ffmpeg 这两个键都是微秒
            .and_then(|v| v.parse::<f64>().ok());
        if let Some(us) = value {
            span.set((us / 1_000_000.0 / total).min(0.99));
        }
    }
}

/// ffmpeg 参数的拼装器，让各操作读起来像命令行本身。
struct Args(Vec<OsString>);

impl Args {
    fn new() -> Self {
        Self(Vec::new())
    }
    fn flags(mut self, flags: &[&str]) -> Self {
        self.0.extend(flags.iter().map(OsString::from));
        self
    }
    fn seek(self, start: f64, duration: f64) -> Self {
        self.flags(&["-ss", &format!("{start:.3}"), "-t", &format!("{duration:.3}")])
    }
    fn input(mut self, path: &Path) -> Self {
        self.0.push("-i".into());
        self.0.push(path.into());
        self
    }
    fn output(mut self, path: &Path) -> Self {
        self.0.push(path.into());
        self
    }
}

static DURATION: LazyLock<Regex> = LazyLock::new(|| re(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)"));
static SIZE: LazyLock<Regex> = LazyLock::new(|| re(r"Video:.*?\s(\d{2,5})x(\d{2,5})"));
static FPS: LazyLock<Regex> = LazyLock::new(|| re(r"(\d+(?:\.\d+)?)\s*fps"));
static ROTATE: LazyLock<Regex> = LazyLock::new(|| re(r"rotate\s*:\s*(-?\d+)|rotation of (-?\d+(?:\.\d+)?) degrees"));

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|e| unreachable!("正则写错了: {e}"))
}

fn parse_probe(text: &str) -> Probe {
    let num = |caps: &regex::Captures<'_>, i: usize| caps.get(i).and_then(|m| m.as_str().parse::<f64>().ok());
    let mut p = Probe::default();
    if let Some(c) = DURATION.captures(text) {
        p.duration = num(&c, 1).unwrap_or(0.0) * 3600.0 + num(&c, 2).unwrap_or(0.0) * 60.0 + num(&c, 3).unwrap_or(0.0);
    }
    if let Some(c) = SIZE.captures(text) {
        p.width = c[1].parse().unwrap_or(0);
        p.height = c[2].parse().unwrap_or(0);
    }
    if let Some(c) = FPS.captures(text) {
        p.fps = num(&c, 1).unwrap_or(0.0);
    }
    // 手机竖拍的视频常带旋转元数据：存的是横的，播放时转 90 度
    let rotation = ROTATE.captures(text).and_then(|c| num(&c, 1).or_else(|| num(&c, 2)));
    if rotation.is_some_and(|r| (r.abs() - 90.0).abs() < 1.0 || (r.abs() - 270.0).abs() < 1.0) {
        std::mem::swap(&mut p.width, &mut p.height);
    }
    p
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 1200 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 1000 kb/s, 29.97 fps, 29.97 tbr
  Stream #0:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo";

    #[test]
    fn probe_reads_duration_size_fps() {
        let p = parse_probe(SAMPLE);
        assert!((p.duration - 62.5).abs() < 1e-9);
        assert_eq!((p.width, p.height), (1920, 1080));
        assert!((p.fps - 29.97).abs() < 1e-9);
        assert!(p.is_video());
    }

    #[test]
    fn rotated_video_swaps_dimensions() {
        let p = parse_probe(&format!("{SAMPLE}\n    Side data:\n      displaymatrix: rotation of -90.00 degrees"));
        assert_eq!((p.width, p.height), (1080, 1920));
    }

    #[test]
    fn not_a_video() {
        assert!(!parse_probe("a.txt: Invalid data found when processing input").is_video());
    }
}
