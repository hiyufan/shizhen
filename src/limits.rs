//! 公网部署用的限流：按客户端 IP 的令牌桶 + 同时进行数上限。
//!
//! 单进程内存实现，够一台机器用；多机部署时每台各自限流。

use std::collections::HashMap;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use crate::config::RateLimits;
use crate::error::{ApiError, ApiResult};

/// 每 `per` 补满 `burst` 个令牌；不够就 429。
#[derive(Debug)]
pub struct RateLimit {
    burst: f64,
    per: Duration,
    buckets: Mutex<Buckets>,
}

#[derive(Debug, Default)]
struct Buckets {
    by_ip: HashMap<String, (f64, Instant)>,
    last_sweep: Option<Instant>,
}

impl RateLimit {
    pub fn new(burst: u32, per: Duration) -> Self {
        Self {
            burst: f64::from(burst.max(1)),
            per,
            buckets: Mutex::default(),
        }
    }

    pub fn hit(&self, ip: &str) -> ApiResult<()> {
        let now = Instant::now();
        let mut b = self.buckets.lock().unwrap_or_else(PoisonError::into_inner);
        self.sweep(&mut b, now);

        let refill = self.burst / self.per.as_secs_f64();
        let (tokens, updated) = b.by_ip.entry(ip.to_owned()).or_insert((self.burst, now));
        *tokens = (*tokens + now.duration_since(*updated).as_secs_f64() * refill).min(self.burst);
        *updated = now;
        if *tokens < 1.0 {
            let wait = Duration::from_secs_f64((1.0 - *tokens) / refill).as_secs() + 1;
            return Err(ApiError::too_many(format!("请求太频繁了，{wait} 秒后再试"), Duration::from_secs(wait)));
        }
        *tokens -= 1.0;
        Ok(())
    }

    /// 每 5 分钟清一次早就补满了的桶，免得 IP 越攒越多。
    fn sweep(&self, b: &mut Buckets, now: Instant) {
        if b.last_sweep.is_some_and(|t| now.duration_since(t) < Duration::from_secs(300)) {
            return;
        }
        b.last_sweep = Some(now);
        b.by_ip.retain(|_, (_, updated)| now.duration_since(*updated) < self.per * 2);
    }
}

/// 同一 IP 同时进行的数量上限（代理流、后台任务）。
#[derive(Debug, Clone)]
pub struct Concurrency {
    what: &'static str,
    limit: usize,
    active: Arc<Mutex<HashMap<String, usize>>>,
}

/// 一个名额；drop 时归还。
#[derive(Debug)]
pub struct Slot {
    ip: String,
    active: Arc<Mutex<HashMap<String, usize>>>,
}

impl Concurrency {
    pub fn new(what: &'static str, limit: usize) -> Self {
        Self { what, limit: limit.max(1), active: Arc::default() }
    }

    pub fn acquire(&self, ip: &str) -> ApiResult<Slot> {
        let mut active = self.active.lock().unwrap_or_else(PoisonError::into_inner);
        let n = active.entry(ip.to_owned()).or_insert(0);
        if *n >= self.limit {
            let msg = format!("你有 {} 个{}还在进行，等一下再来", self.limit, self.what);
            return Err(ApiError::too_many(msg, Duration::from_secs(10)));
        }
        *n += 1;
        Ok(Slot { ip: ip.to_owned(), active: Arc::clone(&self.active) })
    }
}

impl Drop for Slot {
    fn drop(&mut self) {
        let mut active = self.active.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(n) = active.get_mut(&self.ip) {
            *n = n.saturating_sub(1);
            if *n == 0 {
                active.remove(&self.ip);
            }
        }
    }
}

#[derive(Debug)]
pub struct Limits {
    pub parse: RateLimit,
    pub job: RateLimit,
    pub upload: RateLimit,
    pub proxy: RateLimit,
    pub streams: Concurrency,
    pub jobs: Concurrency,
}

impl Limits {
    pub fn new(cfg: &RateLimits) -> Self {
        Self {
            parse: RateLimit::new(cfg.parse_per_min, Duration::from_secs(60)),
            job: RateLimit::new(cfg.job_per_10min, Duration::from_secs(600)),
            upload: RateLimit::new(cfg.upload_per_hour, Duration::from_secs(3600)),
            proxy: RateLimit::new(cfg.proxy_per_min, Duration::from_secs(60)),
            streams: Concurrency::new("下载", cfg.streams_per_ip),
            jobs: Concurrency::new("任务", cfg.jobs_per_ip),
        }
    }
}

const IMAGE_EXT: &[&str] = &[".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp", ".avif"];
/// 各家图片 CDN 的处理参数，带上就肯定是图片而不是视频
const IMAGE_HINT: &[&str] = &["imageview2", "image_process", "x-oss-process", "format/jpg", "format/webp", "/format/png"];

/// 按地址猜是不是图片。
///
/// 图文笔记一次十几张图，让它们跟视频抢同一个并发池的话，用户自己就把自己限流了。
/// 猜错的代价很小：当成图片就少一层并发保护（令牌桶还在），当成视频最多是并发紧一点。
pub fn looks_like_image(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    let (path, query) = lower.split_once('?').unwrap_or((&lower, ""));
    IMAGE_EXT.iter().any(|e| path.ends_with(e)) || IMAGE_HINT.iter().any(|h| query.contains(h))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bucket_allows_burst_then_limits() {
        let rl = RateLimit::new(3, Duration::from_secs(60));
        for _ in 0..3 {
            rl.hit("a").unwrap();
        }
        let err = rl.hit("a").unwrap_err();
        assert_eq!(err.status.as_u16(), 429);
        assert!(err.retry_after.is_some());
        rl.hit("b").unwrap();
    }

    #[test]
    fn slots_are_returned_on_drop() {
        let c = Concurrency::new("任务", 1);
        let s = c.acquire("a").unwrap();
        assert!(c.acquire("a").is_err());
        drop(s);
        assert!(c.acquire("a").is_ok());
    }

    #[test]
    fn image_guess() {
        assert!(looks_like_image("https://x/a.JPG?x=1"));
        assert!(looks_like_image("https://ci.xiaohongshu.com/k?imageView2/2/w/0/format/jpg"));
        assert!(!looks_like_image("https://x/a.mp4?name=b.jpg"));
    }
}
