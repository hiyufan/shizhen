"""执行真实页面适配脚本，验证账号、来源、过期围栏及输出白名单。"""

import json
import shutil
import subprocess

import pytest

from qiuyuantu_inbox.follow_browser import FOLLOW_JS

FIXTURE = r"""
const scenario=JSON.parse(require('fs').readFileSync(0,'utf8'));
const window={}, document={querySelector:()=>({})};
const symbol=Symbol.for('qiuyuantu.inbox.follow.v1');
const target={uid:'200',sec_uid:'sec_200',follow_status:0,follower_status:1,token:'must-not-export'};
const login={uid:'100',secUid:'sec_100'};
const calls=[];
let posts=0;
const args={account:'100',owner:'one',operation:'install',cursor:1000,
  user:{uid:'200',sec_uid:'sec_200'},token:'intent',deadline:Date.now()+5000};
const app={store:{usersInfoStore:{curLoginUserInfo:login}},requestManager:{request:async options=>{
  calls.push({method:options.method,url:options.url,data:options.data});
  if(options.url.endsWith('/follower/list/'))return {
    status_code:0,myself_user_id:scenario==='other_account'?'999':'100',
    followers:[target],has_more:true,max_time:995,min_time:scenario==='stuck_cursor'?1000:900,
    cookie:'must-not-export'};
  if(options.method==='POST'){
    posts++;
    if(scenario==='rejected')return {status_code:8,secret:'must-not-export'};
    if(scenario==='empty_response')return {};
    if(scenario!=='unconfirmed')target.follow_status=scenario==='approval'?4:2;
    return {status_code:0,follow_status:target.follow_status,auth:'must-not-export'};
  }
  if(scenario==='account_changed')login.uid='999';
  if(scenario==='expired')args.deadline=0;
  if(scenario==='closed')window[symbol].closed=true;
  return {status_code:0,user:{...target,uid:scenario==='wrong_target'?'300':target.uid}};
}}};
const pageClient=()=>app;
"""


@pytest.mark.parametrize(
    ("scenario", "expected", "posts"),
    [
        ("success", "mutual", 1),
        ("approval", "requested", 1),
        ("unconfirmed", "uncertain", 1),
        ("empty_response", "uncertain", 1),
        ("rejected", "rejected", 1),
        ("already", "mutual", 0),
        ("withdrawn", "skipped", 0),
        ("blocked", "skipped", 0),
        ("requested", "requested", 0),
        ("account_changed", "follow_account_changed", 0),
        ("wrong_target", "follow_target_changed", 0),
        ("expired", "follow_submission_expired", 0),
        ("closed", "follow_owner_changed", 0),
        ("owner_changed", "follow_owner_changed", 0),
    ],
)
def test_follow_submission_contract(scenario, expected, posts):
    script = (
        FIXTURE + "const call=async()=>{" + FOLLOW_JS + "};(async()=>{await call();args.operation='follow';"
        "if(scenario==='already')target.follow_status=2;"
        "if(scenario==='withdrawn')target.follower_status=0;"
        "if(scenario==='blocked')target.is_block=true;"
        "if(scenario==='requested')target.follow_status=4;"
        "if(scenario==='owner_changed')args.owner='other';"
        "let result;try{result=await call();}catch(e){result={error:e.message};}"
        "console.log(JSON.stringify({result,posts,calls,active:window[symbol]?.active}));})();"
    )
    output = run_script(script, scenario)
    assert output["posts"] == posts
    assert (output["result"].get("state") or output["result"].get("error")) == expected
    assert not output.get("active")
    for call in output["calls"]:
        if call["method"] == "POST":
            assert call["data"] == {"user_id": "200", "type": "1"}


@pytest.mark.parametrize("scenario", ["page", "other_account", "stuck_cursor", "invalid_user"])
def test_follow_list_exports_only_bound_user_fields(scenario):
    script = (
        FIXTURE
        + "const call=async()=>{"
        + FOLLOW_JS
        + "};(async()=>{args.operation='page';if(scenario==='invalid_user')target.uid='bad';"
        "try{console.log(JSON.stringify({result:await call()}));}"
        "catch(e){console.log(JSON.stringify({error:e.message}));}})();"
    )
    output = run_script(script, scenario)
    if scenario == "page":
        assert output["result"] == {
            "users": [{"uid": "200", "sec_uid": "sec_200", "following": 0, "follower": 1, "blocked": False}],
            "cursor": 900,
            "more": True,
        }
    else:
        assert "error" in output


def test_inflight_previous_request_blocks_new_owner_and_expired_write():
    script = (
        FIXTURE
        + "const call=async()=>{"
        + FOLLOW_JS
        + "};(async()=>{await call();window[symbol].active='old';args.owner='two';"
        "try{await call();}catch(e){console.log(JSON.stringify({error:e.message,posts}));}})();"
    )
    assert run_script(script, "success") == {"error": "follow_previous_request_active", "posts": 0}


def run_script(script, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), capture_output=True, text=True, timeout=10, check=True
    )
    assert "must-not-export" not in result.stdout
    return json.loads(result.stdout)
