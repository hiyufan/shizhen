//! 拉 CDN 直链时要带的请求头。各家 CDN 校验 Referer，缺了一律 403。

/// 域名后缀 -> Referer。
const REFERERS: &[(&str, &str)] = &[
    ("bilivideo.com", "https://www.bilibili.com/"),
    ("hdslb.com", "https://www.bilibili.com/"),
    ("acfun.cn", "https://www.acfun.cn/"),
    ("xhscdn.com", "https://www.xiaohongshu.com/"),
    ("xiaohongshu.com", "https://www.xiaohongshu.com/"),
    ("douyinvod.com", "https://www.douyin.com/"),
    ("douyinpic.com", "https://www.douyin.com/"),
    ("365yg.com", "https://www.douyin.com/"),
    ("snssdk.com", "https://www.douyin.com/"),
    ("douyinstatic.com", "https://www.douyin.com/"),
    ("zjcdn.com", "https://www.douyin.com/"),
    ("kuaishou.com", "https://www.kuaishou.com/"),
    ("kwaicdn.com", "https://www.kuaishou.com/"),
    ("yximgs.com", "https://www.kuaishou.com/"),
    ("twimg.com", "https://x.com/"),
    ("sinaimg.cn", "https://weibo.com/"),
    ("weibocdn.com", "https://weibo.com/"),
    ("miaopai.com", "https://weibo.com/"),
    ("pipix.com", "https://h5.pipix.com/"),
    ("ixigua.com", "https://www.ixigua.com/"),
    ("tiktokcdn.com", "https://www.tiktok.com/"),
    ("cdninstagram.com", "https://www.instagram.com/"),
    ("pinimg.com", "https://www.pinterest.com/"),
];

pub const DESKTOP_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
                              (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36";

/// 调用方不能覆盖的头：Host / Content-Length 由 HTTP 库决定，Cookie 不许从前端透传。
const BLOCKED: &[&str] = &["host", "content-length", "cookie"];

fn referer_for(url: &str) -> Option<&'static str> {
    let host = url::Url::parse(url).ok()?.host_str()?.to_ascii_lowercase();
    REFERERS
        .iter()
        .find(|(suffix, _)| host == *suffix || host.ends_with(&format!(".{suffix}")))
        .map(|(_, r)| *r)
}

/// 默认 UA + 按域名补的 Referer，再叠上解析器给的头（`video_headers`）。
pub fn headers_for(url: &str, extra: &[(String, String)]) -> Vec<(String, String)> {
    let mut headers = vec![
        ("User-Agent".to_owned(), DESKTOP_UA.to_owned()),
        ("Accept".to_owned(), "*/*".to_owned()),
    ];
    if let Some(r) = referer_for(url) {
        headers.push(("Referer".to_owned(), r.to_owned()));
    }
    for (k, v) in extra {
        if BLOCKED.contains(&k.to_ascii_lowercase().as_str()) {
            continue;
        }
        headers.retain(|(ek, _)| !ek.eq_ignore_ascii_case(k));
        headers.push((k.clone(), v.clone()));
    }
    headers
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn referer_matches_suffix_not_substring() {
        assert_eq!(
            referer_for("https://upos-sz.bilivideo.com/x"),
            Some("https://www.bilibili.com/")
        );
        assert_eq!(
            referer_for("https://bilivideo.com/x"),
            Some("https://www.bilibili.com/")
        );
        assert_eq!(referer_for("https://evilbilivideo.com/x"), None);
    }

    #[test]
    fn extra_headers_override_but_cookie_is_dropped() {
        let h = headers_for(
            "https://a.xhscdn.com/x",
            &[
                ("referer".into(), "https://custom/".into()),
                ("Cookie".into(), "a=1".into()),
            ],
        );
        let get = |k: &str| {
            h.iter()
                .find(|(ek, _)| ek.eq_ignore_ascii_case(k))
                .map(|(_, v)| v.as_str())
        };
        assert_eq!(get("referer"), Some("https://custom/"));
        assert_eq!(get("cookie"), None);
        assert_eq!(
            h.iter()
                .filter(|(k, _)| k.eq_ignore_ascii_case("referer"))
                .count(),
            1
        );
    }
}
