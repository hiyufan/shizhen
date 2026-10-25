"""包名迁移：新旧导入共享状态，旧入口不额外加载 Web / CLI 依赖。"""

import subprocess
import sys
from importlib import metadata

import pytest
from typer.testing import CliRunner

from qiuyuantu.cli import app


@pytest.mark.parametrize("first", ["parse_video_py", "qiuyuantu"])
def test_import_order_preserves_module_identity_and_state(first):
    # 使用新进程，避免测试收集时已加载的新模块掩盖「先导入旧包」的问题。
    script = """
import importlib
import sys

first = sys.argv[1]
importlib.import_module(first)
assert 'fastapi' not in sys.modules and 'typer' not in sys.modules
modules = ('', '.parser', '.parser.base', '.parser.errors', '.utils', '.convert',
           '.convert.config', '.convert.jobs', '.convert.store', '.web', '.web.limits', '.cli')
for suffix in modules:
    importlib.import_module(first + suffix)
    old = importlib.import_module('parse_video_py' + suffix)
    new = importlib.import_module('qiuyuantu' + suffix)
    assert old is new, suffix
    assert new.__name__ == 'qiuyuantu' + suffix, suffix
    assert new.__spec__.name == 'qiuyuantu' + suffix, suffix

from parse_video_py import VideoInfo
from parse_video_py.convert import jobs as old_jobs
from parse_video_py.web import app as old_app
from qiuyuantu import parser, web
from qiuyuantu.convert import config, jobs

assert VideoInfo is parser.VideoInfo
job = old_jobs.Job(id='compat', type='gif')
old_jobs._jobs[job.id] = job
assert jobs.get(job.id) is job and isinstance(job, jobs.Job)
assert old_app is web.app
assert config.DATA_DIR.name == 'isolated-data'
"""
    result = subprocess.run(
        [sys.executable, "-c", script, first], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(autouse=True)
def isolated_subprocess_data(monkeypatch, tmp_path):
    monkeypatch.setenv("PARSE_VIDEO_DATA_DIR", str(tmp_path / "isolated-data"))


@pytest.mark.parametrize("package", ["qiuyuantu", "parse_video_py"])
def test_diagnostic_module_entry_point(package):
    # 无参数只打印用法，不请求平台，也不运行出口诊断。
    result = subprocess.run(
        [sys.executable, "-m", f"{package}.diag"], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "python -m qiuyuantu.diag" in result.stdout


@pytest.mark.parametrize("command", ["qiuyuantu", "parse-video-py"])
def test_both_console_entry_points_use_the_new_distribution(command):
    entries = metadata.distribution("qiuyuantu").entry_points
    entry = next(item for item in entries if item.group == "console_scripts" and item.name == command)
    assert entry.load() is app
    result = CliRunner().invoke(entry.load(), ["version"], prog_name=command)
    assert result.exit_code == 0
    assert result.stdout.strip() == f"qiuyuantu {metadata.version('qiuyuantu')}"
