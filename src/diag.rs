//! 站长诊断：在服务器上看看各平台到底返回了什么。
//!
//! ```text
//! shizhen diag <分享链接>
//! docker compose exec app shizhen diag <分享链接>
//! ```

use std::time::Instant;

use crate::config::Config;

pub async fn run(cfg: &Config, input: &str) -> Result<(), String> {
    let url = alcedo::util::extract_url(input).unwrap_or(input).to_owned();
    println!("链接: {url}\n");

    let route = match (&cfg.relay, std::env::var("PARSE_VIDEO_PROXY_CN").ok().filter(|s| !s.is_empty())) {
        (Some(r), _) => format!("中转 {}", r.url),
        (None, Some(p)) => format!("代理 {p}"),
        (None, None) => "服务器直连".into(),
    };
    println!("国内平台走: {route}");
    egress(cfg).await;
    println!();

    let client = alcedo::Client::new().map_err(|e| e.to_string())?;
    let started = Instant::now();
    match client.parse(&url).await {
        Ok(info) => {
            let labels: Vec<&str> = info.formats.iter().map(|f| f.label.as_str()).collect();
            println!(
                "[解析成功] {:.0}ms 平台={} 标题={:?} 视频={} 图片={} 清晰度={labels:?}",
                started.elapsed().as_secs_f64() * 1000.0,
                info.source.map_or("?", alcedo::Source::as_str),
                info.title.chars().take(30).collect::<String>(),
                if info.video_url.is_empty() { "无" } else { "有" },
                info.images.len(),
            );
        }
        Err(e) => println!("[解析失败] {:.0}ms 原因={}  {e}", started.elapsed().as_secs_f64() * 1000.0, e.reason),
    }
    Ok(())
}

/// 服务器出口 IP 和归属地（拉直链走的那个出口）。
async fn egress(cfg: &Config) {
    match egress_ip(cfg).await {
        Ok((ip, location)) => println!("[出口] {ip}  {location}"),
        Err(e) => println!("[出口] 探测失败: {e}"),
    }
}

async fn egress_ip(cfg: &Config) -> Result<(String, String), String> {
    let mut builder = reqwest::Client::builder().timeout(std::time::Duration::from_secs(20));
    if let Some(p) = cfg.media_proxy.as_deref() {
        builder = builder.proxy(reqwest::Proxy::all(p).map_err(|e| e.to_string())?);
    }
    let client = builder.build().map_err(|e| e.to_string())?;
    let body = client
        .get("https://myip.ipip.net/json")
        .send()
        .await
        .map_err(|e| e.to_string())?
        .text()
        .await
        .map_err(|e| e.to_string())?;
    let v: serde_json::Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let d = &v["data"];
    let location: Vec<&str> = d["location"].as_array().into_iter().flatten().filter_map(|x| x.as_str()).collect();
    Ok((d["ip"].as_str().unwrap_or("?").to_owned(), location.join(" ")))
}
