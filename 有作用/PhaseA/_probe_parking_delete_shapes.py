# -*- coding: utf-8 -*-
"""Round 3 探针：删除通道 `codes` 形状实证（2026-09-10，一次性 aux）。

用途：Round 2 实收发显示 `carWhiteBlack/delete` 与 `parkingCouponIssue/deleteByCodes`
收到 `{"codes": [...]}` 时返回「参数类型错误」，但 api_defs 的 desc 未写明形状。
按「接口事实一律维护数据、不猜」约束，此处以**假编码**非破坏性地逐一试形状：
  - 形状错 → 类型检查层拦截（「参数类型错误」）
  - 形状对 → 通过类型检查，返回业务层结果（「请求失败」/「不存在」/ success）

对照组：`parkingDevice/deleteByCodes`（Round 0 已实证形状为逗号分隔字符串）一并复测，
以确认判别方法本身可靠。

不入系统运行（有作用/ 惯例，aux）。产出证据 JSON 供人工与文档比对。
"""
import argparse
import json
import sys
import time
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = "https://dev.damaiiot.com:40443"
WEB = HOST + "/park-access-parking-web"
LOGIN = HOST + "/park-base-auth/login"
YQ = "test"

_login_payload = {
    "identityCode": "", "name": "admin",
    "password": "aa60fab6276567c09dcdffe80df5f0f1",
    "randomStr": "", "yqAppCode": YQ,
}

# 明显不存在的编码（非破坏性）
FAKE = "__PROBE_NOT_EXIST__"

# (标签, 请求体) —— 覆盖四种候选形状
SHAPES = [
    ("obj_str", {"codes": FAKE}),                 # {"codes": "a"}
    ("obj_list", {"codes": [FAKE]}),              # {"codes": ["a"]}
    ("bare_list", [FAKE]),                        # ["a"]
    ("obj_csv", {"codes": f"{FAKE},{FAKE}_2"}),   # {"codes": "a,b"}
]

TARGETS = [
    ("carWhiteBlack/delete", "黑白名单-删除"),
    ("parkingCouponIssue/deleteByCodes", "优惠券-删除"),
    ("parkingDevice/deleteByCodes", "设备-删除(对照组)"),
]

# 类型检查层的拒绝话术（出现即判「形状错」）
TYPE_REJECT = "参数类型错误"


def _headers(token):
    return {"Content-Type": "application/json", "token": token, "yq-app-code": YQ}


def _post(url, token, body, timeout=20):
    t0 = time.time()
    try:
        r = requests.post(url, json=body, headers=_headers(token),
                          verify=False, timeout=timeout)
        out = {"http": r.status_code, "elapsed_ms": int((time.time() - t0) * 1000)}
        try:
            out["response"] = r.json()
        except Exception:  # noqa: BLE001
            out["response"] = r.text[:500]
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e), "elapsed_ms": int((time.time() - t0) * 1000)}


def main():
    ap = argparse.ArgumentParser(description="智慧停车删除通道形状探针")
    ap.add_argument("--out", required=True, help="证据 JSON 输出路径")
    args = ap.parse_args()

    ev = {"run_at": datetime.now().isoformat(timespec="seconds"), "host": HOST,
          "fake_code": FAKE, "type_reject_marker": TYPE_REJECT, "results": []}

    r = requests.post(LOGIN, json=_login_payload,
                      headers={"Content-Type": "application/json;charset=UTF-8"},
                      verify=False, timeout=20)
    token = r.json()["data"]["token"]
    print("[probe] login ok", flush=True)

    verdicts = {}
    for suffix, label in TARGETS:
        url = f"{WEB}/{suffix}"
        print(f"\n=== {label}  {url}", flush=True)
        per = []
        accepted = []
        for tag, body in SHAPES:
            res = _post(url, token, body)
            resp = res.get("response")
            data = resp.get("data") if isinstance(resp, dict) else None
            msg = resp.get("msg") if isinstance(resp, dict) else None
            rejected = (data == TYPE_REJECT) or (msg == TYPE_REJECT) or (
                isinstance(data, str) and TYPE_REJECT in data)
            verdict = "形状错(type-reject)" if rejected else "形状通过"
            if not rejected:
                accepted.append(tag)
            per.append({"shape": tag, "request": body, "verdict": verdict,
                        "http": res.get("http"), "response": resp})
            print(f"   {tag:10} -> {verdict:20} http={res.get('http')} resp={str(resp)[:110]}",
                  flush=True)
        verdicts[suffix] = accepted
        ev["results"].append({"url": url, "label": label, "trials": per,
                              "accepted_shapes": accepted})

    ev["summary"] = {"accepted_shapes": verdicts}
    print("\n[probe] 通过的形状:", json.dumps(verdicts, ensure_ascii=False), flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print("[probe] written:", args.out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
