"""原生实况的解码留在页面，来源、体积及原始视频摘要必须匹配。"""

import base64
import hashlib
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.native_video import DOWNLOAD_JS, read_native_video

FIXTURE = r"""
const scenario = JSON.parse(require('fs').readFileSync(0,'utf8'));
const counts = {requests:0, workers:0, terminated:0};
const args={conversation:'chat',sender:'sender',account:'account',message:'source',md5:'image',
  live_vid:'pair',live_md5:'video',token:'token',deadline:Date.now()+5000,max_bytes:100};
const content={md5:'image',live_photo_video:{vid:'pair',md5:'video',skey:'private-key',tkey:'private-tos'}};
const source={serverId:'source',type:27,sender:'sender',conversationId:'chat',content:JSON.stringify(content)};
if(scenario==='sender')source.sender='other';
if(scenario==='conversation')source.conversationId='other';
if(scenario==='digest')args.live_md5='other';
if(scenario==='expired')args.deadline=0;
let active=true;
const sdk={getConversationMessages:()=>[source]};
const target={id:'chat'};
const app={requestManager:{request:async request=>{
  counts.requests++;
  if(request.data.req_infos[0].tos_key!=='private-tos')throw Error('wrong_resource');
  if(scenario==='cancelled')active=false;
  const url=scenario==='host'?'https://127.0.0.1/private':'https://v5.zjcdn.com/original';
  return {data:{play_infos:[{encrypted_url:{main_url:'https://v5.zjcdn.com/transcoded'},
    original_encrypted_url:scenario==='no_original'?undefined:{main_url:url,skey:'private-key'}}]}};
}}};
const nativeContext=()=>({app,sdk,target});
const element={};
const document={querySelector:()=>active?{}:null};
const load={p:'https://www.douyin.com/',u:()=>'/worker.js',b:'https://www.douyin.com/',
  m:{4623:function(){return 'proxy.decryptVideo';}}};
const chunks=[];chunks.push=entry=>{entry[2](load);return Array.prototype.push.call(chunks,entry);};
const window={'@pc-im/im:test':chunks};
const fetch=async url=>{
  if(!url.endsWith('/original'))throw Error('must_use_original');
  let done=false;
  return {ok:true,headers:{get:()=>null},body:{getReader:()=>({
    read:async()=>{if(done)return{done:true};done=true;return {done:false,
      value:new Uint8Array(scenario==='oversized'?101:20)};},cancel:async()=>{}
  })}};
};
class Worker {
  constructor(){counts.workers++;this.listeners=[];}
  addEventListener(type,listener){this.listeners.push(listener);}
  removeEventListener(type,listener){this.listeners=this.listeners.filter(x=>x!==listener);}
  postMessage(payload){
    if(payload.type==='request'&&(payload.handler!=='decryptVideo'||payload.arguments[0]!=='private-key'))
      throw Error('wrong_decoder');
    queueMicrotask(()=>this.listeners.slice().forEach(fn=>fn({data:{____CORS_WORKER____:true,id:payload.id,
      status:scenario==='worker_error'?'error':'success',body:new Blob(['decoded-video'])}})));
  }
  terminate(){counts.terminated++;}
}
"""


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("success", None),
        ("sender", "native_video_source_changed"),
        ("conversation", "native_video_source_changed"),
        ("digest", "native_video_source_changed"),
        ("expired", "native_video_cancelled"),
        ("cancelled", "native_video_cancelled"),
        ("host", "native_video_resource_host_changed"),
        ("no_original", "Invalid URL"),
        ("oversized", "native_video_too_large"),
        ("worker_error", "native_video_decode_failed"),
    ],
)
def test_page_decoder_checks_original_source_and_cleans_worker(scenario, error):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = (
        FIXTURE
        + "const execute=async()=>{"
        + DOWNLOAD_JS
        + "};execute().then(result=>console.log(JSON.stringify({result,counts})))"
        + ".catch(error=>console.log(JSON.stringify({error:error.message,counts})));"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "private-key" not in result.stdout and "private-tos" not in result.stdout
    output = json.loads(result.stdout)
    assert output.get("error") == error
    if error is None:
        assert base64.b64decode(output["result"]["data"]) == b"decoded-video"
        assert len(base64.b64decode(output["result"]["encrypted"])) == 20
    assert output["counts"]["terminated"] == output["counts"]["workers"]


@pytest.mark.parametrize("scenario", ["success", "digest", "size", "header", "exception"])
async def test_python_decoder_checks_bytes_and_removes_cancel_marker(scenario):
    data = b"\0\0\0\x18ftypisom" if scenario != "header" else b"not-video"
    encrypted = b"encrypted-resource"
    result = {"data": base64.b64encode(data).decode(), "encrypted": base64.b64encode(encrypted).decode()}
    locator = SimpleNamespace(evaluate=AsyncMock(return_value=result), evaluate_all=AsyncMock())
    locator.first = locator
    page = SimpleNamespace(evaluate=AsyncMock(return_value=1000), locator=lambda selector: locator)
    request = {"max_bytes": 100, "live_md5": hashlib.md5(encrypted, usedforsecurity=False).hexdigest()}
    if scenario == "digest":
        request["live_md5"] = "wrong"
    if scenario == "size":
        request["max_bytes"] = 1
    if scenario == "exception":
        locator.evaluate.side_effect = TimeoutError()
    if scenario == "success":
        assert await read_native_video(page, request, "") == data
    else:
        with pytest.raises((ValueError, TimeoutError)):
            await read_native_video(page, request, "")
    locator.evaluate_all.assert_awaited_once()
