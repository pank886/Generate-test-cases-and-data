# -*- coding: utf-8 -*-
"""Round 0 探针（二）：智慧停车「黄金链路」实证（2026-09-10，一次性 aux）。

背景：B1 早期结论（`parkingPark/saveOrUpdate` 新建路径为接口缺陷）已被用户真实 payload 推翻，
      根因是 **`parkingPark.parkCode` 必须传空串**（服务端生成 code）。故本探针把生成器实际要走的
      链路按 seq 走一遍并逐步取证，用于确认写入 `api_defs.json` / `phase_c_user_context.md` 的
      每一条事实，避免再次写错结论。特别复核 C1（「服务端丢弃客户端传入的 rule code」）是否成立。

链路：规则 add → 规则回读（getByCode / getList）→ 车场 create(parkCode="") → 车场回读 code
      → getDetail → 设备 add(真实 placeCode) → 清理（deleteByCodes）。

不入系统运行（有作用/ 惯例，aux）。
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

TS = datetime.now().strftime("%H%M%S")
_envelope = "raw"
RULE_CODE = f"PROBE_RULE_{TS}"
RULE_NAME = f"PROBE_RULE_NAME_{TS}"
PARK_NAME = f"PROBE_PARK_{TS}"
DEV_CODE = f"PROBE_DEV_{TS}"


def _headers(token):
    return {"Content-Type": "application/json", "token": token, "yq-app-code": YQ}


def call(method, url, token, body, rec, timeout=20):
    t0 = time.time()
    try:
        if method == "GET":
            r = requests.get(url, headers=_headers(token), verify=False, timeout=timeout)
        else:
            r = requests.post(url, json=body, headers=_headers(token), verify=False, timeout=timeout)
        out = {"step": len(rec) + 1, "method": method, "url": url, "request": body,
               "http": r.status_code, "elapsed_ms": int((time.time() - t0) * 1000)}
        try:
            out["response"] = r.json()
        except Exception:
            out["response"] = r.text[:1000]
        rec.append(out)
        return out
    except Exception as e:  # noqa: BLE001
        out = {"step": len(rec) + 1, "method": method, "url": url, "request": body,
               "error": repr(e), "elapsed_ms": int((time.time() - t0) * 1000)}
        rec.append(out)
        return out


def _data(r):
    return (r.get("response") or {}).get("data")


def _wrap(body, envelope):
    """envelope='raw' → 框架实际形态（YAML 的 json: 直接作 body）；'json' → 多包一层 {"json":…}。"""
    return body if envelope == "raw" else {"json": body}


def main():
    ap = argparse.ArgumentParser(description="智慧停车黄金链路实证")
    ap.add_argument("--out", required=True, help="证据 JSON 输出路径")
    ap.add_argument("--keep", action="store_true", help="不执行清理（保留新建数据以便人工核对）")
    ap.add_argument("--envelope", choices=("raw", "json"), default="raw",
                    help="请求体封装：raw=框架实际形态（默认）；json=多包一层 {\"json\":…}")
    ap.add_argument("--mode", choices=("golden", "deletes"), default="golden",
                    help="deletes=只测删除通道编码（json/data/params 三形态）+ getDetail 复测")
    ap.add_argument("--park-code", default=None, help="deletes 模式的目标 parkCode")
    args = ap.parse_args()
    env = args.envelope

    # 名称按封装类型区分，避免两次运行撞「车场名称已存在」
    global RULE_CODE, RULE_NAME, PARK_NAME, DEV_CODE
    RULE_CODE = f"PROBE_{env.upper()}_RULE_{TS}"
    RULE_NAME = f"PROBE_{env.upper()}_RULE_NAME_{TS}"
    PARK_NAME = f"PROBE_{env.upper()}_PARK_{TS}"
    DEV_CODE = f"PROBE_{env.upper()}_DEV_{TS}"

    ev = {"run_at": datetime.now().isoformat(timespec="seconds"), "host": HOST,
          "ids": {"rule_code_sent": RULE_CODE, "rule_name": RULE_NAME,
                  "park_name": PARK_NAME, "dev_code": DEV_CODE},
          "calls": [], "conclusion": {}}
    rec = ev["calls"]

    r = requests.post(LOGIN, json=_login_payload,
                      headers={"Content-Type": "application/json;charset=UTF-8"},
                      verify=False, timeout=20)
    token = r.json()["data"]["token"]
    print("[probe] login ok", flush=True)

    # ================= deletes 模式：删除通道编码 + getDetail 复测 =================
    if args.mode == "deletes":
        pc = args.park_code

        def kw_call(url, **kw):
            t0 = time.time()
            r = requests.post(url, headers=_headers(token), verify=False, timeout=20, **kw)
            try:
                resp = r.json()
            except Exception:
                resp = r.text[:500]
            out = {"url": url, "kwargs": {k: str(v)[:200] for k, v in kw.items()},
                   "http": r.status_code, "response": resp,
                   "elapsed_ms": int((time.time() - t0) * 1000)}
            rec.append(out)
            return out

        if pc:
            g = call("GET", f"{WEB}/parkingPark/getDetail/{pc}", token, None, rec)
            print("[probe] D0 getDetail retry ->",
                  json.dumps(g.get("response"), ensure_ascii=False)[:140], flush=True)

        ev["conclusion"]["delete_matrix"] = {}
        for nm, kw in [
            ("json_codes_array", {"json": {"codes": [pc]}}),
            ("data_codes_array", {"data": {"codes": [pc]}}),
            ("data_codes_str", {"data": {"codes": pc}}),
            ("params_codes_str", {"params": {"codes": pc}}),
            ("params_codes_array", {"params": {"codes": [pc]}}),
        ]:
            rr = kw_call(f"{WEB}/parkingPark/deleteByCodes", **kw)
            ev["conclusion"]["delete_matrix"][nm] = rr["response"]
            print(f"[probe] D1 delete[{nm}] ->",
                  json.dumps(rr["response"], ensure_ascii=False)[:120], flush=True)

        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False, indent=2)
        print("[probe] evidence written:", args.out, flush=True)
        return 0

    # --- S1 规则 add（客户端自带 code） ---
    s1 = call("POST", f"{WEB}/parkingPaymentConfig/add", token, _wrap({
        "code": RULE_CODE, "name": RULE_NAME, "configType": "3", "mainPark": True,
        "parkingLotType": "1", "unitPrice": 10, "freeTime": "45", "maxParkingFee": "240",
        "realFree": True, "minToLeaveAfterAdv": 15, "memo": f"golden-path probe {TS}",
    }, env), rec)
    print("[probe] S1 rule add ->", json.dumps(s1.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S2 规则回读①：用客户端传入 code 直查 ---
    s2 = call("GET", f"{WEB}/parkingPaymentConfig/getByCode/{RULE_CODE}", token, None, rec)
    print("[probe] S2 getByCode(sent) ->", json.dumps(s2.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S3 规则回读②：list（mainPark=true）里按 name 找真实 code ---
    s3 = call("POST", f"{WEB}/parkingPaymentConfig/getParkingPaymentConfigList", token,
              _wrap({"configType": "3", "mainPark": True}, env), rec)
    real_code = None
    lst = _data(s3) or []
    for x in (lst if isinstance(lst, list) else []):
        if x.get("name") == RULE_NAME:
            real_code = x.get("code")
            break
    ev["conclusion"]["rule_code_server_assigned"] = real_code
    print(f"[probe] S3 list n={len(lst) if isinstance(lst, list) else '?'} "
          f"matched_real_code={real_code}", flush=True)

    bind_code = real_code or RULE_CODE

    # --- S4 车场 create：parkCode 空串 ---
    s4 = call("POST", f"{WEB}/parkingPark/saveOrUpdate", token, _wrap({
        "parkingPark": {"parkCode": "", "parkName": PARK_NAME, "mainPark": True,
                        "parentCode": "", "totalSpots": 100, "occupiedSpots": 0,
                        "needCount": 1, "bindPayCode": bind_code, "needPay": 1},
        "carInConfig": {"parkCode": "", "parkName": PARK_NAME, "tempCarIn": True,
                        "carWhiteOtherIsCasual": False, "nobodyPark": False,
                        "specialCarEnable": False, "enableMisMatch": True,
                        "innerOpenWhenNotMatch": True, "otherParkWhiteAccess": False,
                        "otherParkMonthAccess": False},
    }, env), rec)
    print("[probe] S4 park create ->", json.dumps(s4.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S5 车场回读：按 parkName 过滤拿服务端生成的 parkCode ---
    s5 = call("POST", f"{WEB}/parkingPark/getAllWithDevices", token,
              _wrap({"parkName": PARK_NAME}, env), rec)
    new_park_code = None
    for p in (_data(s5) or []):
        pp = p.get("parkingPark") if isinstance(p, dict) else None
        if isinstance(pp, dict) and pp.get("parkName") == PARK_NAME:
            new_park_code = pp.get("parkCode")
            break
    ev["conclusion"]["park_code_server_assigned"] = new_park_code
    print(f"[probe] S5 readback park_code={new_park_code}", flush=True)

    # --- S6 getDetail（新码） ---
    if new_park_code:
        s6 = call("GET", f"{WEB}/parkingPark/getDetail/{new_park_code}", token, None, rec)
        ev["conclusion"]["getDetail_ok"] = isinstance((s6.get("response") or {}).get("data"), dict)
        print("[probe] S6 getDetail ->",
              json.dumps(s6.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S7 设备 add：仅 placeCode/placeName（验证 C2：文档字段名） ---
    dev_base = {
        "deviceType": "1", "deviceStatus": "1", "passType": "1", "inner": False,
        "multi": False, "needToShow": "1",
        "parkingDeviceManufacturerCode": "100061", "parkingDeviceManufacturerName": "大华",
        "parkingDeviceModelCode": "100121", "parkingDeviceModelName": "DH-IPC-HFW5243K-ZFD-LED",
    }
    if new_park_code:
        s7 = call("POST", f"{WEB}/parkingDevice/add", token, _wrap(dict(
            dev_base, **{"code": f"{DEV_CODE}_A", "name": f"{PARK_NAME}_入口A",
                         "location": f"{PARK_NAME}入口车道A",
                         "placeCode": new_park_code, "placeName": PARK_NAME},
        ), env), rec)
        dev_a_ok = _data(s7) is None and (s7.get("response") or {}).get("retCode") == 1
        ev["conclusion"]["device_place_only_ok"] = (s7.get("response") or {}).get("retCode")
        print("[probe] S7 device(placeCode only) ->",
              json.dumps(s7.get("response"), ensure_ascii=False)[:160], flush=True)

        # S7b C3：同厂家/型号对再建一台，看是否非确定性 91010/91110
        s7b = call("POST", f"{WEB}/parkingDevice/add", token, _wrap(dict(
            dev_base, **{"code": f"{DEV_CODE}_B", "name": f"{PARK_NAME}_出口B",
                         "location": f"{PARK_NAME}出口车道B",
                         "parkCode": new_park_code, "parkName": PARK_NAME},
        ), env), rec)
        ev["conclusion"]["device_park_only_ok"] = (s7b.get("response") or {}).get("retCode")
        print("[probe] S7b device(parkCode only, same pair) ->",
              json.dumps(s7b.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S9 负例：非空 parkCode（证明必须传空串） ---
    s9 = call("POST", f"{WEB}/parkingPark/saveOrUpdate", token, _wrap({
        "parkingPark": {"parkCode": f"PROBE_NONEMPTY_{TS}", "parkName": f"{PARK_NAME}_NEG",
                        "mainPark": True, "parentCode": "", "totalSpots": 100,
                        "occupiedSpots": 0, "needCount": 1,
                        "bindPayCode": bind_code, "needPay": 1},
    }, env), rec)
    ev["conclusion"]["park_code_nonempty_retCode"] = (s9.get("response") or {}).get("retCode")
    print("[probe] S9 park create(parkCode non-empty) ->",
          json.dumps(s9.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S10 子场 create（mainPark=false + parentCode） ---
    if new_park_code:
        s10 = call("POST", f"{WEB}/parkingPark/saveOrUpdate", token, _wrap({
            "parkingPark": {"parkCode": "", "parkName": f"{PARK_NAME}_CHILD", "mainPark": False,
                            "parentCode": new_park_code, "totalSpots": 50, "occupiedSpots": 0,
                            "needCount": 1, "bindPayCode": "zhyqC10695652", "needPay": 1},
            "carInConfig": {"parkCode": "", "parkName": f"{PARK_NAME}_CHILD",
                            "tempCarIn": False, "specialCarEnable": False},
        }, env), rec)
        ev["conclusion"]["sub_park_retCode"] = (s10.get("response") or {}).get("retCode")
        print("[probe] S10 sub-park create ->",
              json.dumps(s10.get("response"), ensure_ascii=False)[:160], flush=True)

    # --- S11 清理：试多种删除形状（找可用通道） ---
    if not args.keep and new_park_code:
        shapes = [("codes_array", {"codes": [new_park_code]}),
                  ("codes_str", {"codes": new_park_code}),
                  ("code", {"code": new_park_code}),
                  ("ids", {"ids": [new_park_code]})]
        ev["conclusion"]["delete_shapes"] = {}
        for nm, sd in shapes:
            sr = call("POST", f"{WEB}/parkingPark/deleteByCodes", token, _wrap(sd, env), rec)
            ev["conclusion"]["delete_shapes"][nm] = sr.get("response")
            print(f"[probe] S11 delete[{nm}] ->",
                  json.dumps(sr.get("response"), ensure_ascii=False)[:110], flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print("[probe] evidence written:", args.out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
