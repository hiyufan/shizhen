//! 使用统计：什么时候有多少人在用、用的哪个平台、成功率多少。
//!
//! SQLite 落在 data/stats.db，表结构和 Python 版一致，线上已有的数据直接接着用。
//! 只记事件不记内容：链接不存，IP 用签名密钥做 HMAC 后只留 12 位——能数出
//! "多少个人"，还原不出是谁。请求路径上只往内存里攒，后台每几秒批量落盘。

use std::path::PathBuf;
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::net::Signer;
use rusqlite::{params, Connection};

/// 一次要记下的事。
#[derive(Debug)]
pub enum Event<'a> {
    View {
        ip: &'a str,
        path: &'a str,
    },
    Parse {
        ip: &'a str,
        source: &'a str,
        ok: bool,
        reason: &'a str,
        elapsed: Duration,
    },
    Job {
        ip: &'a str,
        kind: &'a str,
        ok: bool,
        reason: &'a str,
        elapsed: Duration,
    },
    Download {
        ip: &'a str,
        source: &'a str,
    },
}

struct Row {
    ts: f64,
    kind: &'static str,
    ip: String,
    source: String,
    ok: bool,
    reason: String,
    ms: i64,
}

struct Enabled {
    db: PathBuf,
    token: String,
    retention: Duration,
    signer: Signer,
    buffer: Mutex<Vec<Row>>,
    conn: Mutex<Option<Connection>>,
}

pub struct Stats(Option<Enabled>);

impl std::fmt::Debug for Stats {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Stats({})", if self.0.is_some() { "on" } else { "off" })
    }
}

impl Stats {
    /// 没配令牌就是关着的：什么都不记，统计页也假装不存在。
    pub fn new(token: Option<String>, db: PathBuf, retention_days: u32, signer: Signer) -> Self {
        Self(token.map(|token| Enabled {
            db,
            token,
            retention: Duration::from_secs(u64::from(retention_days) * 86_400),
            signer,
            buffer: Mutex::default(),
            conn: Mutex::default(),
        }))
    }

    #[cfg(test)]
    pub fn disabled() -> Self {
        Self(None)
    }

    pub fn enabled(&self) -> bool {
        self.0.is_some()
    }

    /// 常量时间比较。
    pub fn check_token(&self, given: &str) -> bool {
        let Some(s) = &self.0 else { return false };
        !given.is_empty()
            && given.len() == s.token.len()
            && given
                .bytes()
                .zip(s.token.bytes())
                .fold(0u8, |acc, (a, b)| acc | (a ^ b))
                == 0
    }

    pub fn record(&self, event: Event<'_>) {
        let Some(s) = &self.0 else { return };
        let row = s.row(event);
        s.buffer
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(row);
    }

    /// 把攒着的写进数据库。阻塞，放在 `spawn_blocking` 里调。
    pub fn flush(&self) -> rusqlite::Result<usize> {
        let Some(s) = &self.0 else { return Ok(0) };
        let rows = std::mem::take(&mut *s.buffer.lock().unwrap_or_else(PoisonError::into_inner));
        if rows.is_empty() {
            return Ok(0);
        }
        s.with_conn(|conn| {
            let tx = conn.transaction()?;
            {
                let mut stmt =
                    tx.prepare_cached("INSERT INTO events VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)")?;
                for r in &rows {
                    stmt.execute(params![
                        r.ts,
                        r.kind,
                        r.ip,
                        r.source,
                        i64::from(r.ok),
                        r.reason,
                        r.ms
                    ])?;
                }
            }
            tx.commit()?;
            Ok(rows.len())
        })
    }

    /// 删掉超过保留期的记录。
    pub fn prune(&self) -> rusqlite::Result<usize> {
        let Some(s) = &self.0 else { return Ok(0) };
        let cutoff = now() - s.retention.as_secs_f64();
        s.with_conn(|conn| conn.execute("DELETE FROM events WHERE ts < ?1", [cutoff]))
    }

    /// `[since, until)` 内按 `step` 秒分桶。`tz_offset` 让"按天"的桶从当地零点开始。
    pub fn summary(&self, q: &SummaryQuery) -> rusqlite::Result<Summary> {
        let Some(s) = &self.0 else {
            return Ok(Summary::default());
        };
        s.with_conn(|conn| summary::query(conn, q))
    }
}

impl Enabled {
    fn row(&self, event: Event<'_>) -> Row {
        let (kind, ip, source, ok, reason, elapsed) = match event {
            Event::View { ip, path } => ("view", ip, path, true, "", Duration::ZERO),
            Event::Parse {
                ip,
                source,
                ok,
                reason,
                elapsed,
            } => ("parse", ip, source, ok, reason, elapsed),
            Event::Job {
                ip,
                kind,
                ok,
                reason,
                elapsed,
            } => ("job", ip, kind, ok, reason, elapsed),
            Event::Download { ip, source } => ("download", ip, source, true, "", Duration::ZERO),
        };
        let mut hashed = hex::encode(self.signer.mac(ip.as_bytes()));
        hashed.truncate(12);
        Row {
            ts: now(),
            kind,
            ip: hashed,
            source: source.chars().take(32).collect(),
            ok,
            reason: reason.chars().take(32).collect(),
            ms: i64::try_from(elapsed.as_millis()).unwrap_or(i64::MAX),
        }
    }

    fn with_conn<T>(
        &self,
        f: impl FnOnce(&mut Connection) -> rusqlite::Result<T>,
    ) -> rusqlite::Result<T> {
        let mut guard = self.conn.lock().unwrap_or_else(PoisonError::into_inner);
        if guard.is_none() {
            *guard = Some(open(&self.db)?);
        }
        let conn = guard.as_mut().unwrap_or_else(|| unreachable!());
        f(conn)
    }
}

fn open(db: &std::path::Path) -> rusqlite::Result<Connection> {
    if let Some(dir) = db.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let conn = Connection::open(db)?;
    conn.busy_timeout(Duration::from_secs(10))?;
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS events (
            ts REAL NOT NULL, kind TEXT NOT NULL, ip TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
            ok INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL DEFAULT '', ms INTEGER NOT NULL DEFAULT 0);
         CREATE INDEX IF NOT EXISTS events_ts ON events (ts);",
    )?;
    Ok(conn)
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub use summary::{Summary, SummaryQuery};

mod summary {
    //! 统计页的查询。口径和 Python 版一致：缓存命中不算一次新的解析。

    use std::collections::BTreeMap;

    use rusqlite::{params, Connection};
    use serde::Serialize;

    #[derive(Debug)]
    pub struct SummaryQuery {
        pub since: f64,
        pub until: f64,
        pub step: i64,
        pub tz_offset: i64,
    }

    #[derive(Debug, Default, Clone, Serialize)]
    pub struct Bucket {
        pub t: f64,
        pub view: i64,
        pub parse: i64,
        pub parse_ok: i64,
        pub job: i64,
        pub job_ok: i64,
        pub download: i64,
        pub users: i64,
    }

    #[derive(Debug, Default, Serialize)]
    pub struct Totals {
        pub view: i64,
        pub parse: i64,
        pub parse_ok: i64,
        pub job: i64,
        pub job_ok: i64,
        pub download: i64,
        pub users: i64,
    }

    #[derive(Debug, Serialize)]
    pub struct SourceRow {
        pub source: String,
        pub n: i64,
        pub ok: i64,
        pub users: i64,
        pub ms: i64,
    }

    #[derive(Debug, Serialize)]
    pub struct ReasonRow {
        pub reason: String,
        pub n: i64,
    }

    #[derive(Debug, Serialize)]
    pub struct JobRow {
        #[serde(rename = "type")]
        pub kind: String,
        pub n: i64,
        pub ok: i64,
        pub ms: i64,
    }

    #[derive(Debug, Default, Serialize)]
    pub struct Summary {
        pub since: f64,
        pub until: f64,
        pub step: i64,
        pub first: Option<f64>,
        pub totals: Totals,
        pub series: Vec<Bucket>,
        pub sources: Vec<SourceRow>,
        pub reasons: Vec<ReasonRow>,
        pub jobs: Vec<JobRow>,
    }

    pub fn query(conn: &Connection, q: &SummaryQuery) -> rusqlite::Result<Summary> {
        let step = q.step.max(60);
        let bucket = format!(
            "(CAST((ts + {tz}) / {step} AS INTEGER) * {step} - {tz})",
            tz = q.tz_offset
        );
        let range = params![q.since, q.until];

        let mut series = empty_buckets(q, step);
        let mut stmt = conn.prepare(&format!(
            "SELECT {bucket} AS b, kind, COUNT(*), SUM(ok) FROM events
             WHERE ts >= ?1 AND ts < ?2 AND NOT (kind = 'parse' AND reason = 'cache') GROUP BY b, kind"
        ))?;
        let rows = stmt.query_map(range, |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, Option<i64>>(3)?,
            ))
        })?;
        for row in rows {
            let (b, kind, n, ok) = row?;
            fill(
                series.entry(b).or_insert_with(|| bucket_at(b)),
                &kind,
                n,
                ok.unwrap_or(0),
            );
        }

        let mut stmt = conn.prepare(&format!(
            "SELECT {bucket} AS b, COUNT(DISTINCT ip) FROM events
             WHERE ts >= ?1 AND ts < ?2 AND kind IN ('parse', 'job') GROUP BY b"
        ))?;
        for row in stmt.query_map(range, |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)))? {
            let (b, n) = row?;
            if let Some(cell) = series.get_mut(&b) {
                cell.users = n;
            }
        }

        let series: Vec<Bucket> = series.into_values().collect();
        let mut totals = Totals::default();
        for c in &series {
            totals.view += c.view;
            totals.parse += c.parse;
            totals.parse_ok += c.parse_ok;
            totals.job += c.job;
            totals.job_ok += c.job_ok;
            totals.download += c.download;
        }
        totals.users = conn.query_row(
            "SELECT COUNT(DISTINCT ip) FROM events WHERE ts >= ?1 AND ts < ?2 AND kind IN ('parse', 'job')",
            range,
            |r| r.get(0),
        )?;

        Ok(Summary {
            since: q.since,
            until: q.until,
            step,
            first: conn.query_row("SELECT MIN(ts) FROM events", [], |r| r.get(0))?,
            totals,
            series,
            sources: sources(conn, q)?,
            reasons: reasons(conn, q)?,
            jobs: jobs(conn, q)?,
        })
    }

    fn sources(conn: &Connection, q: &SummaryQuery) -> rusqlite::Result<Vec<SourceRow>> {
        let mut stmt = conn.prepare(
            "SELECT source, COUNT(*), SUM(ok), COUNT(DISTINCT ip), CAST(AVG(ms) AS INTEGER) FROM events
             WHERE ts >= ?1 AND ts < ?2 AND kind = 'parse' AND reason != 'cache'
             GROUP BY source ORDER BY 2 DESC LIMIT 20",
        )?;
        let rows = stmt.query_map(params![q.since, q.until], |r| {
            Ok(SourceRow {
                source: r.get(0)?,
                n: r.get(1)?,
                ok: r.get::<_, Option<i64>>(2)?.unwrap_or(0),
                users: r.get(3)?,
                ms: r.get::<_, Option<i64>>(4)?.unwrap_or(0),
            })
        })?;
        rows.collect()
    }

    fn reasons(conn: &Connection, q: &SummaryQuery) -> rusqlite::Result<Vec<ReasonRow>> {
        let mut stmt = conn.prepare(
            "SELECT reason, COUNT(*) FROM events WHERE ts >= ?1 AND ts < ?2 AND kind = 'parse' AND ok = 0
             AND reason != 'cache' GROUP BY reason ORDER BY 2 DESC LIMIT 8",
        )?;
        let rows = stmt.query_map(params![q.since, q.until], |r| {
            Ok(ReasonRow {
                reason: r.get(0)?,
                n: r.get(1)?,
            })
        })?;
        rows.collect()
    }

    fn jobs(conn: &Connection, q: &SummaryQuery) -> rusqlite::Result<Vec<JobRow>> {
        let mut stmt = conn.prepare(
            "SELECT source, COUNT(*), SUM(ok), CAST(AVG(ms) AS INTEGER) FROM events
             WHERE ts >= ?1 AND ts < ?2 AND kind = 'job' GROUP BY source ORDER BY 2 DESC",
        )?;
        let rows = stmt.query_map(params![q.since, q.until], |r| {
            Ok(JobRow {
                kind: r.get(0)?,
                n: r.get(1)?,
                ok: r.get::<_, Option<i64>>(2)?.unwrap_or(0),
                ms: r.get::<_, Option<i64>>(3)?.unwrap_or(0),
            })
        })?;
        rows.collect()
    }

    /// 区间内每个桶先占好位置，没有数据的时段也要显示成 0。
    fn empty_buckets(q: &SummaryQuery, step: i64) -> BTreeMap<i64, Bucket> {
        #[allow(clippy::cast_possible_truncation)] // 时间戳秒数，远在 i64 范围内
        let since = q.since as i64;
        let mut t = since - (since + q.tz_offset).rem_euclid(step);
        let mut out = BTreeMap::new();
        #[allow(clippy::cast_precision_loss)]
        while (t as f64) < q.until {
            out.insert(t, bucket_at(t));
            t += step;
        }
        out
    }

    #[allow(clippy::cast_precision_loss)]
    fn bucket_at(t: i64) -> Bucket {
        Bucket {
            t: t as f64,
            ..Bucket::default()
        }
    }

    fn fill(cell: &mut Bucket, kind: &str, n: i64, ok: i64) {
        match kind {
            "view" => cell.view = n,
            "parse" => (cell.parse, cell.parse_ok) = (n, ok),
            "job" => (cell.job, cell.job_ok) = (n, ok),
            "download" => cell.download = n,
            _ => {}
        }
    }
}

/// 后台落盘：每 5 秒一次；进程退出前调用方再 flush 一次。
pub async fn flusher(stats: std::sync::Arc<Stats>) {
    if !stats.enabled() {
        return;
    }
    let mut tick = tokio::time::interval(Duration::from_secs(5));
    loop {
        tick.tick().await;
        let s = std::sync::Arc::clone(&stats);
        if let Ok(Err(e)) = tokio::task::spawn_blocking(move || s.flush()).await {
            tracing::warn!(error = %e, "统计落盘失败");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stats() -> (Stats, PathBuf) {
        let db = std::env::temp_dir().join(format!("shizhen-stats-{}.db", rand::random::<u32>()));
        (
            Stats::new(
                Some("tok".into()),
                db.clone(),
                90,
                Signer::new(b"k".to_vec()),
            ),
            db,
        )
    }

    #[test]
    fn disabled_records_nothing() {
        let s = Stats::disabled();
        s.record(Event::View { ip: "1", path: "/" });
        assert_eq!(s.flush().unwrap(), 0);
        assert!(!s.check_token("anything"));
    }

    #[test]
    fn record_flush_and_summarize() {
        let (s, db) = stats();
        assert!(s.check_token("tok"));
        assert!(!s.check_token("tok2"));
        s.record(Event::View {
            ip: "1.1.1.1",
            path: "/",
        });
        s.record(Event::Parse {
            ip: "1.1.1.1",
            source: "douyin",
            ok: true,
            reason: "",
            elapsed: Duration::from_millis(200),
        });
        s.record(Event::Parse {
            ip: "2.2.2.2",
            source: "douyin",
            ok: false,
            reason: "deleted",
            elapsed: Duration::ZERO,
        });
        s.record(Event::Parse {
            ip: "2.2.2.2",
            source: "douyin",
            ok: true,
            reason: "cache",
            elapsed: Duration::ZERO,
        });
        assert_eq!(s.flush().unwrap(), 4);

        let t = now();
        let sum = s
            .summary(&SummaryQuery {
                since: t - 3600.0,
                until: t + 60.0,
                step: 3600,
                tz_offset: 0,
            })
            .unwrap();
        assert_eq!(sum.totals.view, 1);
        assert_eq!(sum.totals.parse, 2, "缓存命中不算");
        assert_eq!(sum.totals.parse_ok, 1);
        assert_eq!(sum.totals.users, 2);
        assert_eq!(sum.sources[0].source, "douyin");
        assert_eq!(sum.reasons[0].reason, "deleted");
        drop(s);
        let _ = std::fs::remove_file(&db);
    }
}
