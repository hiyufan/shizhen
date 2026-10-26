"""网页作品详情的白名单读取与匿名解析预热。"""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.__main__ import run_with_warm_parser
from qiuyuantu_inbox.browser import JS
from qiuyuantu_inbox.media import Asset, assets_from_snapshot
from qiuyuantu_inbox.models import Settings
from qiuyuantu_inbox.work_media import RESOLVE_JS, SOURCE_JS, read_work_media


@pytest.mark.parametrize(
    "row",
    [
        {"url": "http://127.0.0.1/private"},
        {"url": "https://p9.douyinpic.com/a", "live": True},
        {"url": "https://p9.douyinpic.com/a", "video": "http://v.douyinvod.com/a.mp4"},
        {"url": "https://p9.douyinpic.com/a", "video": "https://douyinvod.com.evil.test/a.mp4"},
    ],
)
def test_invalid_cached_urls_fall_back_to_parser(row):
    assert assets_from_snapshot([row], Settings()) is None


def test_cached_media_retains_pairing_and_file_limit():
    row = {"url": "https://p9.douyinpic.com/a", "video": "https://v3.douyinvod.com/a.mp4", "live": True}
    assert assets_from_snapshot([row], Settings()) == [Asset(row["url"]), Asset(row["video"], video=True, live=True)]
    assert assets_from_snapshot([row], Settings(max_files=1)) is None


SOURCE_FIXTURE = r"""
const scenario = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const cid = '0:1:100:200';
const args = {message: 'message', conversation: cid, sender: '200', account: '100', work_id: '123'};
const card = {itemId: '123', image_count: 1, is_live_photo: 1};
const image = {url_list: ['https://p9.douyinpic.com/image'], live_photo_type: 1,
  download_url_list: ['https://p9.douyinpic.com/watermarked'],
  video: {play_addr: {url_list: ['https://v3.douyinvod.com/live.mp4']}}};
const source = {serverId: 'message', conversationId: cid, sender: '200', type: 77};
const detail = {aweme_id: '123', images: [image], authentication_token: 'must-stay-in-page'};
const cached = {detail, filted: false};
if (scenario === 'sender') source.sender = 'other';
if (scenario === 'conversation') source.conversationId = 'other';
if (scenario === 'wrong_work') detail.aweme_id = '456';
if (scenario === 'partial') card.image_count = 2;
if (scenario === 'missing_video') delete image.video;
if (scenario === 'filtered') cached.filted = true;
if (scenario === 'message') source.serverId = 'other';
if (scenario === 'own') {source.sender = '100';args.sender = '100';}
if (scenario === 'card_work') card.itemId = '456';
if (scenario === 'actor') args.conversation = source.conversationId = '0:1:100:300';
if (scenario === 'malformed') args.conversation = source.conversationId = 'malformed';
if (scenario === 'formats') image.url_list = ['https://p9.douyinpic.com/image.webp',
  'https://p9.douyinpic.com/image.jpg?signature=kept'];
if (scenario.startsWith('video')) {
  source.type = 8;
  card.is_live_photo = scenario === 'video_live_card' ? 1 : 0;
  delete detail.images;
  detail.desc = '视频标题';
  detail.video = {play_addr: {url_list: ['https://v3.douyinvod.com/source.mp4']}};
  if (scenario === 'video_slides') card.is_slides = true;
  if (scenario === 'video_missing') delete detail.video;
}
source.content = JSON.stringify(card);
const sdk = {getConversationMessages: () => [source], getConversation: () => ({
  id: scenario === 'target' ? 'other' : args.conversation, type: scenario === 'group' ? 2 : 1})};
const app = {store: {awemeStore: {awemeDetailMap: new Map([['123', cached]])},
  usersInfoStore: {curLoginUserInfo: {uid: scenario === 'account' ? 'other' : '100'}}},
  imSdkService: {imSdkManager: {getImSdkInstance: () => sdk}}};
const pageClient = () => app;
const nativeContext = () => {throw Error('must_not_depend_on_active_chat_or_draft');};
const element = scenario === 'missing_element' ? null : {};
"""


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "formats",
        "sender",
        "conversation",
        "wrong_work",
        "partial",
        "missing_video",
        "filtered",
        "message",
        "own",
        "card_work",
        "actor",
        "malformed",
        "target",
        "group",
        "missing_element",
    ],
)
def test_cached_work_read_is_bound_to_the_received_source(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("网页 JS 契约检查需要 Node.js，CI 提供 Node.js 22")
    script = SOURCE_FIXTURE + "console.log(JSON.stringify((()=>{" + SOURCE_JS + "})()));"
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "must-stay-in-page" not in result.stdout
    if scenario in {"success", "formats"}:
        image = (
            "https://p9.douyinpic.com/image.jpg?signature=kept"
            if scenario == "formats"
            else "https://p9.douyinpic.com/image"
        )
        assert json.loads(result.stdout) == [{"url": image, "video": "https://v3.douyinvod.com/live.mp4", "live": True}]
    else:
        assert json.loads(result.stdout) is None


def test_passive_cache_read_rejects_changed_login_account():
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = SOURCE_FIXTURE + "try {(()=>{" + SOURCE_JS + "})();} catch(error) {console.log(error.message);}"
    result = subprocess.run(
        [node, "-e", script], input='"account"', text=True, capture_output=True, check=True, timeout=10
    )
    assert result.stdout.strip() == "work_account_changed"


RESOLVE_FIXTURE = (
    SOURCE_FIXTURE
    + r"""
const requests = [];
const target = {id: args.conversation, type: scenario === 'group' ? 2 : 1, shortId: '900'};
sdk.getConversation = () => target;
source.conversationShortId = scenario === 'short_id' ? '901' : '900';
if (scenario !== 'cached' && scenario !== 'filtered') app.store.awemeStore.awemeDetailMap.clear();
if (scenario === 'target') target.id = 'other';
const response = {status_code: 0, aweme_details: [detail], filter_list: []};
if (scenario === 'rejected') response.status_code = 1;
if (scenario === 'unavailable') response.filter_list = [{aweme_id: '123'}];
if (scenario === 'extra') response.aweme_details = [detail, detail];
if (scenario === 'static') {delete image.video; image.live_photo_type = card.is_live_photo = 0;}
source.content = JSON.stringify(card);
app.requestManager = {request: async options => {
  requests.push(options);
  if (scenario === 'read_error') throw Error('internal-details-must-stay-in-page');
  if (scenario === 'changed_account') app.store.usersInfoStore.curLoginUserInfo.uid = 'other';
  if (scenario === 'changed_sender') source.sender = 'other';
  if (scenario === 'changed_work') {card.itemId = '456'; source.content = JSON.stringify(card);}
  if (scenario === 'changed_short_id') target.shortId = '901';
  return scenario === 'wrapped' ? {data: response} : response;
}};
"""
)


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "wrapped",
        "static",
        "video_fetch",
        "cached",
        "filtered",
        "sender",
        "group",
        "target",
        "actor",
        "message",
        "own",
        "card_work",
        "short_id",
        "rejected",
        "unavailable",
        "extra",
        "read_error",
        "wrong_work",
        "partial",
        "missing_video",
        "changed_account",
        "changed_sender",
        "changed_work",
        "changed_short_id",
    ],
)
def test_background_detail_fetch_validates_source_before_and_after_read(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = RESOLVE_FIXTURE + "(async()=>{" + RESOLVE_JS + "})()"
    script += ".then(rows=>console.log(JSON.stringify({rows,requests})))"
    script += ".catch(error=>console.log(JSON.stringify({error:error.message,requests})));"
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "must-stay-in-page" not in result.stdout and "watermarked" not in result.stdout
    data = json.loads(result.stdout)
    if scenario == "changed_account":
        assert data["error"] == "work_account_changed"
    else:
        assert bool(data["rows"]) == (scenario in {"success", "wrapped", "static", "video_fetch", "cached"})
    if scenario in {
        "cached",
        "filtered",
        "sender",
        "group",
        "target",
        "actor",
        "message",
        "own",
        "card_work",
        "short_id",
    }:
        assert data["requests"] == []
    else:
        assert data["requests"] == [
            {
                "method": "POST",
                "url": "/aweme/v1/web/multi/aweme/detail/",
                "noStatusCode": True,
                "params": {
                    "aweme_ids": "[123]",
                    "origin_type": "chat",
                    "request_source": "3",
                    "conversation_short_id": "900",
                },
            }
        ]


async def test_background_detail_timeout_returns_to_existing_fallback():
    cancelled = asyncio.Event()

    async def stalled(*_):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    page = SimpleNamespace(evaluate=AsyncMock(side_effect=stalled))
    assert await read_work_media(page, {}, "", 0.01, resolve=True) is None
    assert cancelled.is_set()


@pytest.mark.parametrize("scenario", ["video", "video_slides", "video_missing", "video_live_card"])
def test_cached_ordinary_video_never_substitutes_for_missing_slides(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = SOURCE_FIXTURE + "console.log(JSON.stringify((()=>{" + SOURCE_JS + "})()));"
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    rows = json.loads(result.stdout)
    if scenario == "video":
        assert assets_from_snapshot(rows, Settings()) == [
            Asset("https://v3.douyinvod.com/source.mp4", video=True, title="视频标题")
        ]
    else:
        assert rows is None


async def test_parser_warmup_does_not_block_receiver_and_is_cancelled_on_exit(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def warmup():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def run():
        await asyncio.wait_for(started.wait(), 1)
        raise RuntimeError("receiver stopped")

    monkeypatch.setattr("qiuyuantu.parser.douyin.warmup_browser", warmup)
    with pytest.raises(RuntimeError, match="receiver stopped"):
        await run_with_warm_parser(SimpleNamespace(send_enabled=True, run=run))
    assert cancelled.is_set()


async def test_receive_only_does_not_start_parser_browser(monkeypatch):
    warmup = AsyncMock()
    monkeypatch.setattr("qiuyuantu.parser.douyin.warmup_browser", warmup)
    engine = SimpleNamespace(send_enabled=False, run=AsyncMock())
    await run_with_warm_parser(engine)
    warmup.assert_not_awaited()
    engine.run.assert_awaited_once()


@pytest.mark.parametrize(("kind", "expected"), [(8, "work"), (77, "work"), (30, None)])
def test_received_video_card_is_recognized_without_reading_authentication_fields(kind, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = (
        "const type = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const m={type, serverId:'123456',conversationId:'chat',sender:'sender',createdAt:1001000,"
        "parsedContent:{itemId:'123',content_title:'视频标题',is_slides:false,authentication_token:'private'}};"
        "const element={__reactFiber$test:{memoizedProps:{message:m}},querySelector:()=>null,setAttribute:()=>{}};"
        + JS
        + "console.log(JSON.stringify(message(element)));"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(kind), text=True, capture_output=True, check=True, timeout=10
    )
    row = json.loads(result.stdout)
    assert row["kind"] == expected
    assert "private" not in result.stdout
    if expected:
        assert row["data"] == {"work_id": "123", "title": "视频标题", "photos": False, "live": False}


@pytest.mark.parametrize(
    ("comments", "verified"), [(None, False), ([], False), ([{"cid": "other"}], False), ([{"cid": "456"}], True)]
)
def test_missing_comment_metadata_is_unverified(comments, verified):
    node = shutil.which("node")
    if not node:
        pytest.skip("网页 JS 契约检查需要 Node.js，CI 提供 Node.js 22")
    script = (
        "const comments = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const element = {__reactFiber$test: {memoizedProps: {value: {store: {awemeStore: {"
        "commentMap: new Map([['123456', comments]])}}}}}};"
        + JS
        + "console.log(JSON.stringify(commentVerified(element, {itemId: '123', comment_id: '456'})));"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(comments), text=True, capture_output=True, check=True, timeout=10
    )
    assert json.loads(result.stdout) is verified
