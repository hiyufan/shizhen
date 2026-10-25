"""账号工具不能与匿名解析共享会话，控制端口不能对外开放。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox import account as account


def test_account_directory_uses_isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("PARSE_VIDEO_ACCOUNT_DIR", raising=False)
    monkeypatch.setenv("PARSE_VIDEO_DATA_DIR", str(tmp_path))
    assert account.account_dir() == tmp_path / "accounts" / "douyin"


def test_account_directory_is_private_and_overridable(monkeypatch, tmp_path):
    path = tmp_path / "account"
    monkeypatch.setenv("PARSE_VIDEO_ACCOUNT_DIR", str(path))
    account.prepare_directory(account.account_dir())
    assert path.stat().st_mode & 0o777 == 0o700
    path.chmod(0o755)
    account.prepare_directory(path)
    assert path.stat().st_mode & 0o777 == 0o700


def test_browser_uses_separate_profile_and_local_control(monkeypatch, tmp_path):
    monkeypatch.setenv("PARSE_VIDEO_ACCOUNT_BROWSER", "/test/chromium")
    options = account.browser_options(tmp_path, headed=True)
    assert options["user_data_dir"] == str(tmp_path / "profile")
    assert options["executable_path"] == "/test/chromium"
    assert options["headless"] is False
    assert options["accept_downloads"] is False
    assert "--remote-debugging-address=127.0.0.1" in options["args"]
    assert account.CDP_URL == "http://127.0.0.1:9223"


@pytest.mark.parametrize("visible", [True, False])
async def test_login_leaves_verification_page_for_owner(visible):
    login = SimpleNamespace(is_visible=AsyncMock(return_value=visible), click=AsyncMock())
    page = SimpleNamespace(get_by_text=lambda *args, **kwargs: SimpleNamespace(first=login))
    await account.show_login(page)
    assert login.click.await_count == int(visible)


def test_account_and_queue_use_same_explicit_directory(monkeypatch, tmp_path):
    from qiuyuantu_inbox.__main__ import account_root

    monkeypatch.setenv("PARSE_VIDEO_ACCOUNT_DIR", str(tmp_path / "existing"))
    monkeypatch.setenv("PARSE_VIDEO_DATA_DIR", str(tmp_path / "web-data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert account.account_dir() == account_root() == tmp_path / "existing"


def test_new_install_uses_private_xdg_directory_independent_of_cwd(monkeypatch, tmp_path):
    from qiuyuantu_inbox.__main__ import account_root

    monkeypatch.delenv("PARSE_VIDEO_ACCOUNT_DIR", raising=False)
    monkeypatch.delenv("PARSE_VIDEO_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    expected = tmp_path / "xdg" / "qiuyuantu-inbox"
    assert account.account_dir() == account_root() == expected
    monkeypatch.chdir(tmp_path)
    assert account.account_dir() == account_root() == expected
