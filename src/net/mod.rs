//! 对外取数据：拉 CDN 直链、SSRF 防护、请求头、链接签名。
//!
//! 解析平台接口的请求全在 alcedo 里；这里只管解析结果里那些直链（视频、图片），
//! 它们要被代理给浏览器，或者下载到本地做转换。

mod edge;
mod fetch;
mod names;
mod referer;
mod sign;

pub use edge::EdgeImages;
pub use fetch::{describe as describe_error, Download, MediaClient};
pub use names::{attachment_header, safe_filename, scrub_paths};
pub use sign::Signer;

/// 只放行公网 http(s)：字面 IP 直接判，域名在 DNS 层判（解析到内网地址的也拒绝）。
///
/// 检查结果按域名缓存 5 分钟（见 alcedo 的 `SafeResolver`），一次解析里重复查的
/// 那几个平台域名不会每次都打 DNS。
pub async fn is_public_url(raw: &str, check_dns: bool) -> bool {
    let Ok(url) = url::Url::parse(raw) else {
        return false;
    };
    if !alcedo::http::ssrf::url_allowed(&url) {
        return false;
    }
    match url.host() {
        Some(url::Host::Domain(host)) if check_dns => alcedo::http::ssrf::check_domain(host).await.is_ok(),
        _ => true,
    }
}

/// 主域名的最后两段，统计下载来源用：`v5-dy.douyinvod.com` -> `douyinvod.com`。
pub fn registrable_host(raw: &str) -> String {
    let host = url::Url::parse(raw)
        .ok()
        .and_then(|u| u.host_str().map(str::to_owned))
        .unwrap_or_default();
    let parts: Vec<&str> = host.rsplitn(3, '.').collect();
    match parts.as_slice() {
        [tld, name, ..] => format!("{name}.{tld}"),
        _ => host,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn private_and_odd_urls_are_rejected_without_dns() {
        for bad in [
            "http://127.0.0.1/x",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/",
            "http://localhost:8000/",
            "file:///etc/passwd",
            "ftp://example.com/",
            "not a url",
        ] {
            assert!(!is_public_url(bad, false).await, "{bad}");
        }
        assert!(is_public_url("https://1.1.1.1/", false).await);
    }

    #[test]
    fn registrable_host_keeps_last_two_labels() {
        assert_eq!(registrable_host("https://v5-dy.douyinvod.com/a.mp4"), "douyinvod.com");
        assert_eq!(registrable_host("https://example.com/"), "example.com");
        assert_eq!(registrable_host("garbage"), "");
    }
}
