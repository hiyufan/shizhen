//! 边缘取图：图片由国内边缘节点直接送给浏览器。
//!
//! 服务器在海外时，图片走 /api/proxy 要 国内 CDN -> 海外 -> 国内 跨两次太平洋。
//! 中转的边缘节点本来就在国内，让它直接给浏览器送图，两段都在国内。
//! 协议和 scripts/esa-relay.js 的 `/img` 对应：地址带过期时间和 HMAC，过期后边缘拒绝。

use hmac::{Hmac, Mac};
use sha2::Sha256;

use crate::config::Relay;

/// 和 esa-relay.js 的 IMG_REFERERS 保持一致。
const HOSTS: &[&str] = &[
    "xhscdn.com",
    "xiaohongshu.com",
    "douyinpic.com",
    "yximgs.com",
    "hdslb.com",
    "sinaimg.cn",
];

#[derive(Debug, Clone)]
pub struct EdgeImages {
    /// 中转地址去掉末尾的 `/relay`
    base: String,
    token: String,
}

impl EdgeImages {
    /// 没配中转、没开开关、或者中转地址不是 `.../relay` 形式时返回 `None`。
    pub fn from_config(relay: Option<&Relay>) -> Option<Self> {
        let relay = relay.filter(|r| r.edge_img)?;
        let base = relay.url.strip_suffix("/relay")?;
        Some(Self {
            base: base.to_owned(),
            token: relay.token.clone(),
        })
    }

    /// 给 CSP 的 img-src 用。
    pub fn origin(&self) -> String {
        url::Url::parse(&self.base)
            .map(|u| u.origin().ascii_serialization())
            .unwrap_or_default()
    }

    /// 白名单里的图片 CDN 才给边缘地址。`expires_at` 是 Unix 秒。
    pub fn url_for(&self, image: &str, expires_at: u64) -> Option<String> {
        let host = url::Url::parse(image)
            .ok()?
            .host_str()?
            .to_ascii_lowercase();
        if !HOSTS
            .iter()
            .any(|s| host == *s || host.ends_with(&format!(".{s}")))
        {
            return None;
        }
        let mut mac = Hmac::<Sha256>::new_from_slice(self.token.as_bytes()).ok()?;
        mac.update(format!("img\n{expires_at}\n{image}").as_bytes());
        let mut sig = hex::encode(mac.finalize().into_bytes());
        sig.truncate(32);

        let mut url = url::Url::parse(&format!("{}/img", self.base)).ok()?;
        url.query_pairs_mut()
            .append_pair("url", image)
            .append_pair("e", &expires_at.to_string())
            .append_pair("s", &sig);
        Some(url.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edge() -> EdgeImages {
        EdgeImages::from_config(Some(&Relay {
            url: "https://relay.example.cn/relay".into(),
            token: "t".into(),
            edge_img: true,
        }))
        .unwrap()
    }

    #[test]
    fn only_whitelisted_hosts_get_edge_urls() {
        assert!(edge()
            .url_for("https://sns-img.xhscdn.com/a.jpg", 100)
            .is_some());
        assert!(edge().url_for("https://example.com/a.jpg", 100).is_none());
        assert_eq!(edge().origin(), "https://relay.example.cn");
    }

    #[test]
    fn signature_matches_python() {
        // Python: hmac(token, f"img\n{exp}\n{url}", sha256).hexdigest()[:32]
        let u = edge()
            .url_for("https://a.xhscdn.com/x.jpg", 1_700_000_000)
            .unwrap();
        assert!(u.contains("s=49dec95604c180913a0cda01e51517e5"), "{u}");
    }

    #[test]
    fn disabled_when_not_configured() {
        assert!(EdgeImages::from_config(None).is_none());
        let off = Relay {
            url: "https://r/relay".into(),
            token: "t".into(),
            edge_img: false,
        };
        assert!(EdgeImages::from_config(Some(&off)).is_none());
    }
}
