//! 解析服务：alcedo + 结果缓存 + 转成前端的 JSON。
//!
//! 平台解析全在 alcedo 里；这里只做站点层面的事：从分享文案里抠链接、缓存
//! （爆款链接短时间被很多人贴）、签名、统计要用的来源和耗时。

mod dto;
mod token;

use std::collections::HashMap;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Serialize;

pub use dto::{ParsedDto, VideoDto};
pub use token::MediaToken;

use crate::net::{EdgeImages, Signer};

/// 解析失败的结果也缓存一会儿，免得有人反复贴一条坏链接把平台打到限流。
const ERROR_TTL: Duration = Duration::from_secs(60);
/// 直链过期前留的余量：别把马上要失效的地址发给用户。
const EXPIRY_MARGIN: u64 = 120;
const CACHE_MAX: usize = 500;
/// 整次解析的上限，和 Python 版一致。
const PARSE_TIMEOUT: Duration = Duration::from_secs(90);

/// `/api/parse` 的响应体：`{code, msg, data?, reason?}`。
#[derive(Debug, Clone, Serialize)]
pub struct ParseReply {
    pub code: u16,
    pub msg: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<ParsedDto>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

impl ParseReply {
    fn failed(code: u16, msg: impl Into<String>, reason: &str) -> Self {
        Self { code, msg: msg.into(), data: None, reason: Some(reason.to_owned()) }
    }

    pub fn ok(&self) -> bool {
        self.code == 200
    }
}

/// 一次解析的结果和统计要用的信息。
#[derive(Debug)]
pub struct Outcome {
    pub reply: Arc<ParseReply>,
    /// 平台标识；认不出平台时为空
    pub source: String,
    pub from_cache: bool,
    pub elapsed: Duration,
}

struct Cached {
    reply: Arc<ParseReply>,
    stored: Instant,
    ttl: Duration,
}

pub struct ParseService {
    client: alcedo::Client,
    signer: Signer,
    edge: Option<EdgeImages>,
    ttl: Duration,
    cache: Mutex<HashMap<String, Cached>>,
}

impl std::fmt::Debug for ParseService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ParseService").field("ttl", &self.ttl).finish_non_exhaustive()
    }
}

impl ParseService {
    pub fn new(client: alcedo::Client, signer: Signer, edge: Option<EdgeImages>, ttl: Duration) -> Self {
        Self { client, signer, edge, ttl, cache: Mutex::default() }
    }

    pub fn client(&self) -> &alcedo::Client {
        &self.client
    }

    /// 从分享文案里抠出链接。
    pub fn extract_url(input: &str) -> Option<String> {
        alcedo::util::extract_url(input).map(str::to_owned).or_else(|| {
            let t = input.trim();
            t.starts_with("http").then(|| t.to_owned())
        })
    }

    /// 平台标识（统计用）；认不出返回空串。
    pub fn platform_of(url: &str) -> String {
        alcedo::registry::detect(url).map(|s| s.as_str().to_owned()).unwrap_or_default()
    }

    pub async fn parse(&self, share_url: &str) -> Outcome {
        let source = Self::platform_of(share_url);
        if let Some(reply) = self.cached(share_url) {
            let source = reply.data.as_ref().map_or(source, |d| d.video.source.clone());
            return Outcome { reply, source, from_cache: true, elapsed: Duration::ZERO };
        }

        let started = Instant::now();
        let reply = match tokio::time::timeout(PARSE_TIMEOUT, self.client.parse(share_url)).await {
            Err(_) => ParseReply::failed(504, "解析超时，请稍后再试", "timeout"),
            Ok(Err(e)) => ParseReply::failed(500, e.to_string(), e.reason.as_str()),
            Ok(Ok(info)) => self.success(&info, share_url),
        };
        let ttl = self.ttl_for(&reply);
        let reply = Arc::new(reply);
        self.store(share_url, &reply, ttl);

        let source = reply.data.as_ref().map_or(source, |d| d.video.source.clone());
        Outcome { reply, source, from_cache: false, elapsed: started.elapsed() }
    }

    /// 给服务端自己用：拿到原始解析结果（准备原视频时挑一档来合并）。
    pub async fn info(&self, share_url: &str) -> alcedo::Result<alcedo::VideoInfo> {
        self.client.parse(share_url).await
    }

    fn success(&self, info: &alcedo::VideoInfo, share_url: &str) -> ParseReply {
        let video = VideoDto::from_info(info, share_url, &self.signer);
        // 边缘取图地址要比结果缓存活得久，缓存里拿出来的也还能用
        let edge = self.edge.as_ref().map(|e| (e, unix_now() + self.ttl.as_secs() + 3600));
        ParseReply {
            code: 200,
            msg: "解析成功".into(),
            data: Some(ParsedDto::new(video, share_url, &self.signer, edge)),
            reason: None,
        }
    }

    /// 成功的按配置缓存，但不超过结果里最早过期的那个直链；失败的只缓存一分钟。
    fn ttl_for(&self, reply: &ParseReply) -> Duration {
        let Some(data) = &reply.data else { return ERROR_TTL.min(self.ttl) };
        let earliest = data.sig.keys().filter_map(|u| alcedo::cache::url_expiry(u)).min();
        match earliest {
            Some(exp) => self.ttl.min(Duration::from_secs(exp.saturating_sub(unix_now() + EXPIRY_MARGIN))),
            None => self.ttl,
        }
    }

    fn cached(&self, key: &str) -> Option<Arc<ParseReply>> {
        let mut cache = self.lock();
        let hit = cache.get(key)?;
        if hit.stored.elapsed() < hit.ttl {
            return Some(Arc::clone(&hit.reply));
        }
        cache.remove(key);
        None
    }

    fn store(&self, key: &str, reply: &Arc<ParseReply>, ttl: Duration) {
        if ttl.is_zero() {
            return;
        }
        let mut cache = self.lock();
        if cache.len() >= CACHE_MAX {
            cache.retain(|_, c| c.stored.elapsed() < c.ttl);
        }
        if cache.len() >= CACHE_MAX {
            let oldest = cache.iter().min_by_key(|(_, c)| c.stored).map(|(k, _)| k.clone());
            oldest.map(|k| cache.remove(&k));
        }
        cache.insert(key.to_owned(), Cached { reply: Arc::clone(reply), stored: Instant::now(), ttl });
    }

    pub fn cache_len(&self) -> usize {
        self.lock().len()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Cached>> {
        self.cache.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

fn unix_now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}
