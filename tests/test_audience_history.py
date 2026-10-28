"""历史清洗必须可回放、可恢复，混用出口及缺失日志均采用保守分类。"""

import contextlib
import gzip
import json
import sqlite3
import sys

from qiuyuantu import audience, stats, traffic
from qiuyuantu import audience_history as history

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def event(number, kind="parse", stamp=None):
    return (number, stamp or 1000 + number, kind, "shared-ip", "/" if kind == "view" else "douyin", 1, "", 0)


def request(stamp, kind, *, agent=CHROME, path="", same=True):
    return history.LogRequest(stamp, "shared-ip", agent, path or ("/" if kind == "view" else "/api/parse"), kind, same)


def browser_session():
    return [request(1000, "view"), request(1001, "script", path="/js/v/app.js"), request(1003, "parse")]


def test_history_keeps_mixed_ip_requests_separate():
    records = browser_session() + [request(1004, "parse", agent="node")]
    events = [event(1, "view", 1000), event(2, stamp=1003), event(3, stamp=1004)]
    # 两个客户端相隔不足两秒，无法唯一关联的操作不能被认作真人。
    labels = history.classifications(events, records)
    assert labels[1].status == "human"
    assert labels[2].status == labels[3].status == "unknown"
    records[-1].ts = 1010
    events[-1] = event(3, stamp=1010)
    labels = history.classifications(events, records)
    assert labels[2].status == "human"
    assert labels[3].status == "machine"


def test_page_and_scripts_without_action_are_not_human():
    events = [event(1, "view", 1000), event(2, stamp=2000)]
    labels = history.classifications(events, browser_session()[:2])
    assert labels[1].status == "unknown"
    assert labels[2] == audience.Evidence("unknown", "historical-missing")


def test_action_requires_page_scripts_and_same_site_evidence():
    records = browser_session()
    records[-1].same_site = False
    assert history.classifications([event(1, stamp=1003)], records)[1].status == "unknown"
    records[-1].same_site = True
    assert history.classifications([event(1, stamp=1003)], records[1:])[1].status == "unknown"


def test_rate_review_demotes_jobs_in_same_history_session():
    records = browser_session()[:2]
    events = []
    for i in range(20):
        records.append(request(1003 + i, "parse"))
        events.append(event(i + 1, stamp=1003 + i))
    events.append(event(21, "job", 1020))
    labels = history.classifications(events, records)
    assert all(label.status == "unknown" for label in labels.values())


def test_cached_parse_replays_do_not_trigger_rate_review():
    records = browser_session()
    events = [(*event(i, stamp=1003)[:6], "cache", 0) for i in range(1, 30)]
    assert all(label.status == "human" for label in history.classifications(events, records).values())


def test_background_completion_can_use_polling_as_context_not_human_proof():
    records = browser_session() + [request(1100, "poll", path="/api/jobs/test")]
    assert history.classifications([event(1, "job", 1099)], records)[1].status == "human"
    assert history.classifications([event(1, "job", 1099)], records[-1:])[1].status == "unknown"


def test_log_reader_handles_gzip_deduplication_and_drops_sensitive_parameters(tmp_path):
    path = tmp_path / "access.log.gz"
    text = (
        '8.8.8.8 - - [01/Jan/2026:00:00:00 +0000] "GET /?url=PRIVATE HTTP/1.1" 200 12 '
        f'"https://ynvan.test/path?q=PRIVATE" "{CHROME}"\n'
    )
    with gzip.open(path, "wt") as stream:
        stream.write(text)
        stream.write(text)
        stream.write("invalid log entry\n")
    stamp = 1767225600
    events = [(1, stamp, "view", stats.hash_ip("8.8.8.8"), "/", 1, "", 0)]
    rows = history.read_logs([path, path], events, {"ynvan.test"})
    assert len(rows) == 1
    assert rows[0].same_site and rows[0].path == "/"
    assert "PRIVATE" not in repr(rows)
    assert "8.8.8.8" not in repr(rows)


def database(tmp_path):
    path = tmp_path / "stats.db"
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(stats._SCHEMA + traffic.SCHEMA)
        conn.execute("INSERT INTO events VALUES (1000, 'view', 'shared-ip', '/', 1, '', 0)")
        conn.execute("INSERT INTO traffic_visits VALUES ('visit', 1000, 'shared-ip', 'direct', '', '', '', '/')")
        conn.execute("INSERT INTO traffic_events VALUES (1000, 'visit', 'view', '/', 1)")
    return path


def test_backfill_is_idempotent_preserves_events_and_can_restore_backup(tmp_path):
    path = database(tmp_path)
    events, links = history.snapshot(path)
    labels = history.classifications(events, browser_session())
    backup = history.apply(path, events, links, labels)
    history.apply(path, events, links, labels)
    assert backup.stat().st_mode & 0o777 == 0o600
    assert history.snapshot(path) == (events, links)
    assert history.snapshot(backup) == (events, links)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM event_audience").fetchone()[0] == 1
        assert conn.execute("SELECT audience FROM audience_events").fetchone()[0] == "human"
        assert conn.execute("SELECT audience FROM audience_traffic_events").fetchone()[0] == "human"
    with contextlib.closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='event_audience'").fetchone() is None


def test_backfill_does_not_overwrite_live_evidence_or_reused_rowid(tmp_path):
    path = database(tmp_path)
    events, links = history.snapshot(path)
    history.apply(path, events, links, {1: audience.Evidence("unknown", "historical-missing")})
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE event_audience SET status='machine', reason='automation-agent'")
    history.apply(path, events, links, {1: audience.Evidence("human", "historical-browser-action")})
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("SELECT status FROM event_audience").fetchone()[0] == "machine"
        conn.execute("DELETE FROM event_audience")
        conn.execute("UPDATE events SET ts=2000")
    history.apply(path, events, links, {1: audience.Evidence("human", "historical-browser-action")})
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM event_audience").fetchone()[0] == 0


def test_apply_report_uses_preserved_online_evidence(tmp_path, monkeypatch, capsys):
    path = database(tmp_path)
    events, links = history.snapshot(path)
    history.apply(path, events, links, {1: audience.Evidence("human", "browser-interaction")})
    monkeypatch.setattr(sys, "argv", ["clean_stats.py", "--database", str(path), "--apply"])
    history.main()
    result = json.loads(capsys.readouterr().out)
    assert result["processed_events"] == result["events"] == 1
    assert result["classes"]["human"]["events"] == 1
    assert result["classes"]["unknown"]["events"] == 0
