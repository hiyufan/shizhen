//! 运行时配置：所有环境变量在启动时读一次，之后只读。
//!
//! 变量名沿用 Python 版的 `PARSE_VIDEO_*`，线上部署脚本不用改。平台 cookie、
//! 国内代理 / 中转这些解析相关的配置由 alcedo 自己读（它同样认 `PARSE_VIDEO_*`）。

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

/// 按客户端 IP 的限流参数。
#[derive(Debug, Clone)]
pub struct RateLimits {
    /// 每分钟解析次数
    pub parse_per_min: u32,
    /// 每 10 分钟后台任务数
    pub job_per_10min: u32,
    /// 每小时上传次数
    pub upload_per_hour: u32,
    /// 每分钟代理请求数（含 Range 分段、图文笔记的十几张图）
    pub proxy_per_min: u32,
    /// 同一 IP 同时进行的视频代理流
    pub streams_per_ip: usize,
    /// 同一 IP 同时进行的后台任务
    pub jobs_per_ip: usize,
}

/// 可选的 Basic Auth。
#[derive(Debug, Clone)]
pub struct BasicAuth {
    pub username: String,
    pub password: String,
}

/// 国内出口中转（边缘函数）。解析请求由 alcedo 自己走中转，这里只用来签边缘取图地址。
#[derive(Debug, Clone)]
pub struct Relay {
    pub url: String,
    pub token: String,
    /// 图片由国内边缘节点直接送给浏览器（需要部署带 /img 的 esa-relay.js）
    pub edge_img: bool,
}

#[derive(Debug, Clone)]
pub struct Config {
    pub listen: SocketAddr,
    /// 反代后面才信任 `X-Forwarded-For`
    pub trust_proxy: bool,
    pub data_dir: PathBuf,
    /// 代理链接的签名密钥；不配就生成到 data/secret.key
    pub secret: Option<String>,
    pub auth: Option<BasicAuth>,

    /// 解析结果缓存秒数；0 关闭
    pub parse_cache: Duration,
    pub job_ttl: Duration,
    pub source_ttl: Duration,
    pub job_timeout: Duration,
    pub max_concurrent_jobs: usize,
    pub max_queued_jobs: usize,
    pub max_upload_bytes: u64,
    pub max_source_bytes: u64,
    pub disk_quota_bytes: u64,
    /// 每个 ffmpeg 的线程数；0 = 按并发数平分核心
    pub ffmpeg_threads: usize,

    /// 拉 CDN 直链时的代理
    pub media_proxy: Option<String>,
    pub ssrf_dns: bool,
    pub relay: Option<Relay>,
    pub limits: RateLimits,

    /// 站点绝对地址，如 `https://example.com`；不填按请求推断
    pub site_url: String,
    pub site_verification_html: String,
    pub analytics_html: String,

    /// 统计页令牌；不配就不记录
    pub stats_token: Option<String>,
    pub stats_retention_days: u32,
}

impl Config {
    pub fn from_env() -> Self {
        let cpus = std::thread::available_parallelism().map_or(2, usize::from);
        let host = env_str("PARSE_VIDEO_HOST").unwrap_or_else(|| "0.0.0.0".into());
        let port: u16 = env_num("PARSE_VIDEO_PORT", 8000);
        let listen = format!("{host}:{port}")
            .parse()
            .unwrap_or_else(|_| SocketAddr::from(([0, 0, 0, 0], port)));

        Self {
            listen,
            trust_proxy: env_flag("PARSE_VIDEO_TRUST_PROXY", false),
            data_dir: env_str("PARSE_VIDEO_DATA_DIR").map_or_else(|| "data".into(), PathBuf::from),
            secret: env_str("PARSE_VIDEO_SECRET"),
            auth: basic_auth(),

            parse_cache: secs("PARSE_VIDEO_PARSE_CACHE", 600),
            job_ttl: secs("PARSE_VIDEO_JOB_TTL", 3600),
            source_ttl: secs("PARSE_VIDEO_SOURCE_TTL", 2 * 3600),
            job_timeout: secs("PARSE_VIDEO_JOB_TIMEOUT", 300),
            max_concurrent_jobs: env_num("PARSE_VIDEO_MAX_JOBS", cpus.saturating_sub(1).max(1)),
            max_queued_jobs: env_num("PARSE_VIDEO_MAX_QUEUE", 50),
            max_upload_bytes: env_num("PARSE_VIDEO_MAX_UPLOAD", 300 << 20),
            max_source_bytes: env_num("PARSE_VIDEO_MAX_SOURCE", 300 << 20),
            disk_quota_bytes: env_num("PARSE_VIDEO_DISK_QUOTA", 8 << 30),
            ffmpeg_threads: env_num("PARSE_VIDEO_FFMPEG_THREADS", 0),

            media_proxy: media_proxy(),
            ssrf_dns: env_flag("PARSE_VIDEO_SSRF_DNS", true),
            relay: relay(),
            limits: RateLimits {
                parse_per_min: env_num("PARSE_VIDEO_RL_PARSE", 30),
                job_per_10min: env_num("PARSE_VIDEO_RL_JOB", 20),
                upload_per_hour: env_num("PARSE_VIDEO_RL_UPLOAD", 10),
                proxy_per_min: env_num("PARSE_VIDEO_RL_PROXY", 240),
                streams_per_ip: env_num("PARSE_VIDEO_MAX_STREAMS_PER_IP", 4),
                jobs_per_ip: env_num("PARSE_VIDEO_MAX_JOBS_PER_IP", 2),
            },

            site_url: env_str("PARSE_VIDEO_SITE_URL")
                .map(|s| s.trim_end_matches('/').to_owned())
                .unwrap_or_default(),
            site_verification_html: env_str("PARSE_VIDEO_SITE_VERIFICATION").unwrap_or_default(),
            analytics_html: env_str("PARSE_VIDEO_ANALYTICS").unwrap_or_default(),

            stats_token: env_str("PARSE_VIDEO_STATS_TOKEN"),
            stats_retention_days: env_num("PARSE_VIDEO_STATS_DAYS", 90),
        }
    }

    pub fn sources_dir(&self) -> PathBuf {
        self.data_dir.join("sources")
    }
    pub fn outputs_dir(&self) -> PathBuf {
        self.data_dir.join("outputs")
    }
    pub fn uploads_dir(&self) -> PathBuf {
        self.data_dir.join("uploads")
    }
    pub fn bin_dir(&self) -> PathBuf {
        self.data_dir.join("bin")
    }

    /// 放临时文件的三个目录，清理和配额都只管它们。
    pub fn work_dirs(&self) -> [PathBuf; 3] {
        [self.sources_dir(), self.outputs_dir(), self.uploads_dir()]
    }

    pub fn ensure_dirs(&self) -> std::io::Result<()> {
        for d in self.work_dirs().iter().chain([&self.bin_dir()]) {
            std::fs::create_dir_all(d)?;
        }
        Ok(())
    }
}

/// 转换的硬性上限，不开放配置：放宽它们的代价是别人的等待时间。
pub mod limits {
    pub const GIF_MAX_SECONDS: f64 = 30.0;
    pub const LIVE_MAX_SECONDS: f64 = 10.0;
    /// 为转换拉取原视频时，从高清档里挑的最高分辨率（短边）
    pub const SOURCE_MAX_SHORT_SIDE: u32 = 1080;
    /// 实况原样打包一次最多几张
    pub const LIVE_MAX_ITEMS: usize = 30;
    /// 实况打包时单个文件（图片 / 短视频）的上限
    pub const LIVE_ITEM_MAX_BYTES: u64 = 100 << 20;
}

fn basic_auth() -> Option<BasicAuth> {
    Some(BasicAuth {
        username: env_str("PARSE_VIDEO_USERNAME")?,
        password: env_str("PARSE_VIDEO_PASSWORD")?,
    })
}

/// 拉 CDN 直链选哪个代理。
///
/// 默认不走国内代理：国内平台的 CDN 对海外 IP 一般放行，视频流量又大，别把国内
/// 出口占满。确实被 CDN 403 时设 `PARSE_VIDEO_PROXY_CN_MEDIA=1`。
fn media_proxy() -> Option<String> {
    if env_flag("PARSE_VIDEO_PROXY_CN_MEDIA", false) {
        if let Some(cn) = env_str("PARSE_VIDEO_PROXY_CN") {
            return Some(cn);
        }
    }
    env_str("PARSE_VIDEO_PROXY")
}

fn relay() -> Option<Relay> {
    Some(Relay {
        url: env_str("PARSE_VIDEO_RELAY_CN")?,
        token: env_str("PARSE_VIDEO_RELAY_TOKEN")?,
        edge_img: env_flag("PARSE_VIDEO_EDGE_IMG", false),
    })
}

fn env_str(key: &str) -> Option<String> {
    std::env::var(key)
        .ok()
        .map(|v| v.trim().to_owned())
        .filter(|v| !v.is_empty())
}

fn env_num<T: std::str::FromStr>(key: &str, default: T) -> T {
    env_str(key).and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn env_flag(key: &str, default: bool) -> bool {
    env_str(key).map_or(default, |v| v == "1")
}

fn secs(key: &str, default: u64) -> Duration {
    Duration::from_secs(env_num(key, default))
}
