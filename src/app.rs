//! 组装：读配置、建各个组件、起后台任务、监听。

use std::sync::Arc;
use std::time::Duration;

use crate::config::Config;
use crate::jobs::tasks::Toolkit;
use crate::jobs::Jobs;
use crate::limits::Limits;
use crate::maintenance::{self, Cleanup};
use crate::media::Ffmpeg;
use crate::net::{EdgeImages, MediaClient, Signer};
use crate::parse::ParseService;
use crate::site::{Content, Site, SiteConfig};
use crate::stats::{self, Stats};
use crate::store::Store;
use crate::web::{self, AppState, Shared};

/// 常用的国内平台：启动时预热连接，之后定期保温。
const WARM: &[alcedo::Source] = &[
    alcedo::Source::DouYin,
    alcedo::Source::BiliBili,
    alcedo::Source::RedBook,
];

pub fn build(cfg: Config) -> Result<Shared, String> {
    cfg.ensure_dirs()
        .map_err(|e| format!("创建 {} 失败: {e}", cfg.data_dir.display()))?;
    let signer = Signer::load(cfg.secret.as_deref(), &cfg.data_dir)
        .map_err(|e| format!("读写 secret.key 失败: {e}"))?;
    let stats = Arc::new(Stats::new(
        cfg.stats_token.clone(),
        cfg.data_dir.join("stats.db"),
        cfg.stats_retention_days,
        signer.clone(),
    ));

    let cpus = std::thread::available_parallelism().map_or(2, usize::from);
    let threads = if cfg.ffmpeg_threads > 0 {
        cfg.ffmpeg_threads
    } else {
        (cpus / cfg.max_concurrent_jobs.max(1)).max(1)
    };
    let kit = Toolkit {
        ffmpeg: Ffmpeg::locate(&cfg.bin_dir(), threads)?,
        media: MediaClient::new(cfg.media_proxy.as_deref(), cfg.ssrf_dns)?,
        sources_dir: cfg.sources_dir(),
        outputs_dir: cfg.outputs_dir(),
        max_source_bytes: cfg.max_source_bytes,
    };

    let edge = EdgeImages::from_config(cfg.relay.as_ref());
    let client = alcedo::Client::new().map_err(|e| format!("解析器初始化失败: {e}"))?;
    let parse = ParseService::new(client, signer.clone(), edge.clone(), cfg.parse_cache);

    let site_cfg = SiteConfig {
        site_url: cfg.site_url.clone(),
        site_verification_html: cfg.site_verification_html.clone(),
        analytics_html: cfg.analytics_html.clone(),
    };
    let site =
        Site::new(Content::embedded()?, site_cfg).map_err(|e| format!("模板加载失败: {e}"))?;

    Ok(Arc::new(AppState {
        jobs: Jobs::new(
            cfg.max_concurrent_jobs,
            cfg.max_queued_jobs,
            cfg.job_timeout,
            Arc::clone(&stats),
            cfg.outputs_dir(),
        ),
        limits: Limits::new(&cfg.limits),
        store: Arc::new(Store::default()),
        site,
        parse,
        kit,
        stats,
        signer,
        edge,
        cfg,
    }))
}

/// 后台任务：定期清理、统计落盘、连接保温。
pub fn spawn_background(state: &Shared) {
    let s = Arc::clone(state);
    tokio::spawn(async move {
        loop {
            let st = Arc::clone(&s);
            let _ = tokio::task::spawn_blocking(move || {
                Cleanup {
                    cfg: &st.cfg,
                    jobs: &st.jobs,
                    store: &st.store,
                    stats: &st.stats,
                }
                .run();
            })
            .await;
            tokio::time::sleep(maintenance::INTERVAL).await;
        }
    });

    tokio::spawn(stats::flusher(Arc::clone(&state.stats)));

    // 走国内中转时，中转那端空闲一两分钟就断，重连要多一次跨洋握手（实测 1.3s）；
    // 直连时连接池 5 分钟回收，赶在那之前碰一次就行
    let every = if state.cfg.relay.is_some() {
        Duration::from_secs(60)
    } else {
        Duration::from_secs(240)
    };
    let s = Arc::clone(state);
    tokio::spawn(async move {
        loop {
            s.parse.client().prewarm(WARM).await;
            tokio::time::sleep(every).await;
        }
    });
}

pub async fn serve(state: Shared) -> Result<(), String> {
    let addr = state.cfg.listen;
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .map_err(|e| format!("监听 {addr} 失败: {e}"))?;
    tracing::info!("拾帧已启动: http://{addr}");
    spawn_background(&state);

    let stats = Arc::clone(&state.stats);
    let app = web::router(state).into_make_service_with_connect_info::<std::net::SocketAddr>();
    let result = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await;

    // 退出前把攒着的统计写掉
    let _ = tokio::task::spawn_blocking(move || stats.flush()).await;
    result.map_err(|e| format!("服务异常退出: {e}"))
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let term = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let term = std::future::pending::<()>();
    tokio::select! {
        () = ctrl_c => {}
        () = term => {}
    }
    tracing::info!("收到退出信号，停止接收新请求");
}
