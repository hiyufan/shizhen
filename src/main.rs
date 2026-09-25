//! 拾帧：粘贴链接，取出无水印视频和原图，做成 GIF、iPhone 实况或安卓动态照片。
//!
//! ```text
//! shizhen                  启动网站（默认）
//! shizhen diag <链接>      站长诊断：出口 IP、解析结果
//! shizhen paths            站内所有页面路径（给主动推送脚本用）
//! ```
//!
//! 模块分层：`web`（HTTP）→ `parse` / `jobs` / `site`（业务）→ `net` / `media` / `store`（能力）。
//! 上层依赖下层，反过来不行。

mod app;
mod config;
mod diag;
mod error;
mod fsutil;
mod jobs;
mod limits;
mod maintenance;
mod media;
mod net;
mod parse;
mod site;
mod stats;
mod store;
mod web;

use std::process::ExitCode;

fn main() -> ExitCode {
    init_logging();
    // reqwest 用的是 rustls 的"不带加密后端"版本，必须自己装一个，否则第一次握手就 panic
    let _ = rustls::crypto::ring::default_provider().install_default();

    let runtime = match tokio::runtime::Builder::new_multi_thread().enable_all().build() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("启动运行时失败: {e}");
            return ExitCode::FAILURE;
        }
    };
    match runtime.block_on(run(std::env::args().skip(1).collect())) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}

async fn run(args: Vec<String>) -> Result<(), String> {
    let cfg = config::Config::from_env();
    match args.first().map(String::as_str) {
        None | Some("serve") => app::serve(app::build(cfg)?).await,
        Some("diag") => {
            let input = args[1..].join(" ");
            if input.trim().is_empty() {
                return Err("用法: shizhen diag <分享链接>".into());
            }
            diag::run(&cfg, &input).await
        }
        Some("paths") => {
            for p in site::Content::embedded()?.paths() {
                println!("{p}");
            }
            Ok(())
        }
        Some("-h" | "--help") => {
            println!("用法: shizhen [serve | diag <链接> | paths]\n配置见 README 的「配置」一节（环境变量 PARSE_VIDEO_*）");
            Ok(())
        }
        Some(other) => Err(format!("不认识的命令: {other}（shizhen --help 看用法）")),
    }
}

fn init_logging() {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info,alcedo=warn"));
    tracing_subscriber::fmt().with_env_filter(filter).with_target(false).init();
}
