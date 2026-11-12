"""独立下载域名：只放行下载接口、不记统计、出错回说明页；主站行为不变。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from qiuyuantu import seo, stats, web
from qiuyuantu.convert import jobs
from qiuyuantu.web import download_host, limits

MAIN = "https://example.test"
DOWNLOAD = {"host": "download.example.test"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(download_host, "ORIGIN", "https://download.example.test")
    monkeypatch.setattr(download_host, "HOST", "download.example.test")
    monkeypatch.setattr(seo, "SITE_URL", MAIN)
    monkeypatch.setattr(limits.proxy_limit, "_buckets", {})
    return TestClient(web.app, base_url=MAIN, follow_redirects=False)


@pytest.fixture
def recorded(monkeypatch):
    rows = []
    monkeypatch.setattr(stats, "TOKEN", "tok")
    monkeypatch.setattr(stats, "_buf", rows)
    monkeypatch.setattr(stats, "_traffic_buf", [])
    monkeypatch.setattr(stats, "_recent_downloads", {})
    return rows


def _finished_job(tmp_path) -> jobs.Job:
    async def run():
        async def fn(job):
            path = tmp_path / "out.gif"
            path.write_bytes(b"GIF89a")
            job.result_path, job.filename = str(path), "clip.gif"

        job = jobs.start("gif", fn, owner="203.0.113.9")
        await job.task
        return job

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("value", "site", "expected"),
    [
        ("https://download.example.test/", MAIN, "https://download.example.test"),
        ("download.example.test", MAIN, ""),  # 要带协议
        ("https://download.example.test/files", MAIN, ""),  # 只要域名，不带路径
        ("https://example.test", MAIN, ""),  # 和主站同名等于没换连接
        ("", MAIN, ""),
    ],
)
def test_origin_setting_is_validated(monkeypatch, value, site, expected):
    monkeypatch.setenv("PARSE_VIDEO_DOWNLOAD_ORIGIN", value)
    monkeypatch.setattr(seo, "SITE_URL", site)
    assert download_host._origin() == expected


def test_pages_tell_the_frontend_where_downloads_go(client, monkeypatch):
    assert 'data-download-origin="https://download.example.test"' in client.get("/").text
    monkeypatch.setattr(download_host, "ORIGIN", "")
    assert "data-download-origin" not in client.get("/").text


def test_only_download_endpoints_are_served_on_the_download_host(client):
    for path in ("/", "/douyin?url=x", "/api/parse?url=https://v.douyin.com/a/", "/stats", "/static/site.css"):
        response = client.get(path, headers=DOWNLOAD)
        assert response.status_code == 301 and response.headers["location"] == MAIN + path, path
        assert response.headers["x-robots-tag"] == "noindex"
    assert client.post("/api/parse", json={"url": "x"}, headers=DOWNLOAD).status_code == 404
    assert client.delete("/api/jobs/abc", headers=DOWNLOAD).status_code == 404
    # 主站不受影响
    assert client.get("/").status_code == 200


def test_downloads_work_there_but_are_counted_only_through_the_main_site(client, recorded, tmp_path):
    job = _finished_job(tmp_path)
    recorded.clear()
    response = client.get(f"/api/jobs/{job.id}/file", headers=DOWNLOAD)
    assert response.status_code == 200 and response.content == b"GIF89a"
    assert "clip.gif" in response.headers["content-disposition"]
    assert recorded == []  # 这里收不到本站 Cookie，不记
    assert client.post(f"/api/jobs/{job.id}/download-hit").json() == {"ok": True}
    assert [(row[1], row[3]) for row in recorded] == [("download", "gif")]
    assert client.post("/api/jobs/missing/download-hit").status_code == 404


def test_errors_on_the_download_host_are_a_readable_page(client):
    params = {"url": "https://example.com/a.mp4", "sig": "0" * 32, "download": 1}
    page = client.get("/api/proxy", params=params, headers=DOWNLOAD)
    assert page.status_code == 403 and page.headers["content-type"].startswith("text/html")
    assert "这个地址不是解析结果里的，拒绝转发" in page.text and f'href="{MAIN}/"' in page.text
    assert page.headers["cache-control"] == "no-store" and "<script" not in page.text
    english = client.get("/api/proxy", params=params, headers={**DOWNLOAD, "accept-language": "en"})
    assert "This address is not from a parse result" in english.text and 'lang="en"' in english.text
    # 主站上的接口照旧回 JSON，页面脚本靠它显示原因
    assert client.get("/api/proxy", params=params).json() == {"detail": "这个地址不是解析结果里的，拒绝转发"}


def test_untranslated_reason_falls_back_to_a_generic_english_message():
    assert download_host._message("请求太频繁了，12 秒后再试", "zh") == "请求太频繁了，12 秒后再试"
    assert "try again" in download_host._message("请求太频繁了，12 秒后再试", "en").lower()
    assert download_host._message("Not Found", "zh") == download_host.GENERIC_ERROR
