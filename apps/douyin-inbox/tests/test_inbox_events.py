"""客户端事件覆盖已读聊天，有界缓冲先落盘再确认，崩溃重放不重复入队。"""

import asyncio
import json
import shutil
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.browser import JS
from qiuyuantu_inbox.engine import Engine
from qiuyuantu_inbox.events import EVENT_JS
from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import Store

FIXTURE = r"""
const scenario=JSON.parse(require('fs').readFileSync(0,'utf8'));
const crypto=require('crypto').webcrypto;
const cid='0:1:100:200';
const args={operation:'install',account:'100',scope:scenario==='public'?[]:[cid],owner:'one',limit:1};
const callbacks={ReceiveNewMessage:[]};
const manager={listenerObj:callbacks,registerEvent:(type,fn)=>(callbacks[type]??=[]).push(fn),
  unRegisterEvent:(type,fn)=>callbacks[type]=callbacks[type].filter(v=>v!==fn)};
const sdk={getConversation:({conversationId})=>({id:conversationId,type:scenario==='group'?2:1})};
manager.getImSdkInstance=()=>sdk;
const app={store:{usersInfoStore:{curLoginUserInfo:{uid:'100'}}},imSdkService:{imSdkManager:manager}};
const pageClient=()=>app;
const document={querySelector:()=>({})};
const window={};
class Source {
  get serverId(){return '123';}
  get conversationId(){return scenario==='scope'?'0:1:100:300':cid;}
  get sender(){return scenario==='own'?'100':'200';}
  get type(){return 8;}
  get createdAt(){return 1001000;}
  get content(){return JSON.stringify({itemId:'1234',content_title:'video',token:'must-not-export'});}
}
"""


@pytest.mark.parametrize(
    "scenario", ["success", "public", "own", "scope", "group", "overflow", "restart", "identity", "ack"]
)
def test_event_buffer_contract(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = (
        FIXTURE + JS + "const call=()=>{" + EVENT_JS + "};let batch=call();"
        "callbacks.ReceiveNewMessage.forEach(fn=>fn(new Source()));"
        "if(scenario==='overflow')callbacks.ReceiveNewMessage.forEach(fn=>fn(new Source()));"
        "args.operation='read';const before=call();"
        "try {if(scenario==='restart'){args.operation='install';args.owner='two';}"
        "if(scenario==='identity')app.store.usersInfoStore.curLoginUserInfo.uid='other';"
        "if(scenario==='ack'){args.operation='ack';args.generation=before.generation;args.sequence=1;}"
        "const after=call();console.log(JSON.stringify({before,after,"
        "listeners:callbacks.ReceiveNewMessage.length}));}"
        "catch(error){console.log(JSON.stringify({error:error.message}));}"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "must-not-export" not in result.stdout
    output = json.loads(result.stdout)
    if scenario == "identity":
        assert output["error"] == "event_account_changed"
        return
    expected = 0 if scenario in {"own", "scope", "group"} else 1
    assert len(output["before"]["rows"]) == expected
    assert len(output["after"]["rows"]) == (0 if scenario == "ack" else expected)
    assert output["listeners"] == 1
    assert output["after"]["gaps"] == (2 if scenario == "overflow" else 1)
    if expected:
        assert output["before"]["rows"][0]["row"]["kind"] == "work"
    if scenario == "restart":
        assert output["before"]["generation"] == output["after"]["generation"]


def test_capture_and_application_recover_independently(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    message = Message("source", "chat", "sender", 1001, "work", {"work_id": "123"})
    store.capture([message, message], 1002)
    assert len(store.inbound()) == 1
    # 在业务事务提交后、事件处理标记之前退出。
    store.ingest(message, 1003)
    recovered = Store(store.root, store.settings)
    assert recovered.handle_inbound(recovered.inbound()[0], 1004) == "duplicate"
    assert recovered.inbound() == []
    assert recovered.status(1004)["counts"] == {"queued": 1}


def test_reply_before_prompt_ack_is_replayed_after_confirmation_and_restart(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    source = Message("source", "chat", "sender", 1001, "work", {"work_id": "123"})
    store.ingest(source, 1002)
    working = store.claim("queued", 1003)
    store.ready(working, "", [{"offer_video": {"files": ["video.mp4"], "md5": ["video"]}}], 1004)
    sending = store.claim("ready", 1005)
    store.start_part(sending, 0, 1005)
    reply = Message("reply", "chat", "sender", 1006, "selection", {"mode": "both", "code": ""})
    store.capture([reply], 1007)
    assert store.handle_inbound(store.inbound()[0], 1007) == "deferred"
    store.finish_part(sending, 0, ["prompt-receipt"])
    # 回执已落盘、任务状态提交前退出：恢复不能重复提示，也不能丢掉早到的选择。
    recovered = Store(store.root, store.settings)
    recovered.recover(1008)
    assert recovered.handle_inbound(recovered.inbound()[0], 1009) == "selected"
    assert recovered.inbound() == []
    assert recovered.selection(working)["mode"] == "both"
    assert recovered.parts(working)[0]["state"] == "sent"


def test_delayed_selection_does_not_block_new_requests(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    first = Message("first", "chat", "sender", 1001, "selection", {"mode": "both", "code": ""})
    second = Message("second", "other", "other", 1002, "work", {"work_id": "123"})
    store.capture([first, second], 1003)
    delayed = store.inbound()[0]
    with store.transaction() as db:
        db.execute("UPDATE inbound_events SET available=? WHERE id=?", (time.time() + 1, delayed["id"]))
    event = store.inbound()[0]
    assert json.loads(event["payload"])["id"] == "second"
    assert store.handle_inbound(event, 1004) == "queued"


async def test_event_intake_continues_while_browser_is_busy_and_never_acks_failed_commit(tmp_path, monkeypatch):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    engine = Engine(store, SimpleNamespace(), {"chat"}, False)
    row = {
        "id": "1",
        "conversation": "chat",
        "sender": "sender",
        "created": time.time(),
        "kind": "work",
        "data": {"work_id": "123"},
    }
    events = SimpleNamespace(
        read=AsyncMock(return_value={"generation": "g", "gaps": 0, "rows": [{"sequence": 1, "row": row}]}),
        ack=AsyncMock(),
    )
    engine.events = events
    await engine.ui.acquire()
    try:
        original = store.capture
        monkeypatch.setattr(store, "capture", lambda *args: (_ for _ in ()).throw(OSError()))
        with pytest.raises(OSError):
            await engine.event_once()
        events.ack.assert_not_awaited()
        monkeypatch.setattr(store, "capture", original)
        await asyncio.wait_for(engine.event_once(), 1)
    finally:
        engine.ui.release()
    events.ack.assert_awaited_once()
    assert store.status()["counts"] == {"queued": 1}
