# -*- coding: utf-8 -*-
"""Round 0 探针：智慧停车真实事实采集 + saveOrUpdate 真车场回放（2026-09-10，一次性 aux）。

用途：四轮日志显示 `parkingPark/saveOrUpdate` 从未成功过一次（~170 次调用 retCode 全 0），
本探针取权威事实并定性：
  1) 只读采集：真实车场全量 payload / 真实设备厂家·型号字典值 / 真实收费规则 code。
  2) 回放定性：取一个真实主场，按 `getDetail` 拿到的全量 payload **同值回放**一次
     saveOrUpdate —— 成功 ⇒ 属「未文档化必填结构」；仍「请求失败」⇒ 判为接口缺陷。

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


def _headers(token):
    return {"Content-Type": "application/json", "token": token, "yq-app-code": YQ}


def _post(url, token, body, rec, timeout=20):
    t0 = time.time()
    try:
        r = requests.post(url, json=body, headers=_headers(token), verify=False, timeout=timeout)
        out = {"url": url, "request": body, "http": r.status_code,
               "elapsed_ms": int((time.time() - t0) * 1000)}
        try:
            out["response"] = r.json()
        except Exception:
            out["response"] = r.text[:1000]
        rec.append(out)
        return out
    except Exception as e:  # noqa: BLE001
        out = {"url": url, "request": body, "error": repr(e),
               "elapsed_ms": int((time.time() - t0) * 1000)}
        rec.append(out)
        return out


def main():
    ap = argparse.ArgumentParser(description="智慧停车真实事实探针（只读 + 一次回放）")
    ap.add_argument("--out", required=True, help="证据 JSON 输出路径")
    ap.add_argument("--no-replay", action="store_true", help="跳过 saveOrUpdate 回放（纯只读）")
    ap.add_argument("--variants", action="store_true",
                    help="额外回放变体：原样（含 id）/ 仅补 id / api_defs 最小样板")
    args = ap.parse_args()

    ev = {"run_at": datetime.now().isoformat(timespec="seconds"), "host": HOST,
          "calls": [], "summary": {}}
    rec = ev["calls"]

    # --- 登录 ---
    r = requests.post(LOGIN, json=_login_payload,
                      headers={"Content-Type": "application/json;charset=UTF-8"},
                      verify=False, timeout=20)
    token = r.json()["data"]["token"]
    print("[probe] login ok", flush=True)

    # --- 1) 真实车场 ---
    parks = _post(f"{WEB}/parkingPark/getAllWithDevices", token, {"json": {}}, rec)
    main, child = [], []
    if isinstance(parks.get("response"), dict):
        # 注意：每条记录形如 {"parkingPark": {...}, "carInDeviceList": [...], ...}
        for p in (parks["response"].get("data") or []):
            pp = p.get("parkingPark") if isinstance(p, dict) else None
            if isinstance(pp, dict):
                (main if pp.get("mainPark") else child).append(pp)
    ev["summary"]["parks_detail"] = [{"parkCode": p.get("parkCode"), "parkName": p.get("parkName"),
                                      "mainPark": p.get("mainPark"), "bindPayCode": p.get("bindPayCode"),
                                      "parentCode": p.get("parentCode"), "createTime": p.get("createTime")}
                                     for p in (main + child)]
    ev["summary"]["parks_total"] = len(main) + len(child)
    ev["summary"]["parks_main"] = len(main)
    ev["summary"]["parks_child"] = len(child)
    print(f"[probe] parks total={len(main)+len(child)} main={len(main)} child={len(child)}", flush=True)

    # --- 2) 真实设备字典（厂家/型号 成对）---
    dev = _post(f"{WEB}/parkingDevice/getList", token, {"json": {}}, rec)
    combos = {}
    if isinstance(dev.get("response"), dict):
        for d in (dev["response"].get("data") or []):
            k = (d.get("parkingDeviceManufacturerCode"), d.get("parkingDeviceManufacturerName"),
                 d.get("parkingDeviceModelCode"), d.get("parkingDeviceModelName"))
            if k[0] and k[2]:
                combos[k] = combos.get(k, 0) + 1
    ev["summary"]["device_combos"] = [{"manufacturerCode": k[0], "manufacturerName": k[1],
                                       "modelCode": k[2], "modelName": k[3], "seen": v}
                                      for k, v in sorted(combos.items(), key=lambda x: -x[1])]
    print(f"[probe] distinct device manufacturer/model combos={len(combos)}", flush=True)

    # --- 3) 真实收费规则 code（mainPark true/false）---
    rules = {}
    for mp in (True, False):
        rr = _post(f"{WEB}/parkingPaymentConfig/getParkingPaymentConfigPage", token,
                   {"json": {"pageNum": 1, "pageSize": 50, "configType": "3", "mainPark": mp}}, rec)
        lst = []
        if isinstance(rr.get("response"), dict):
            data = rr["response"].get("data") or {}
            lst = [{"code": x.get("code"), "name": x.get("name"), "mainPark": x.get("mainPark"),
                    "parkCode": x.get("parkCode"), "parkingLotType": x.get("parkingLotType")}
                   for x in (data.get("list") or [])]
        rules[mp] = lst
        print(f"[probe] rules mainPark={mp}: {len(lst)} (total={((rr.get('response') or {}).get('data') or {}).get('total')})", flush=True)
    ev["summary"]["rules_main"] = rules[True]
    ev["summary"]["rules_child"] = rules[False]

    # --- 4) 真车场全量 payload ---
    replay = None
    if main:
        # 取最新创建的真实主场（payload 最接近当前生产形态）
        main.sort(key=lambda x: str(x.get("createTime")), reverse=True)
        pc = main[0].get("parkCode")
        det = _post(f"{WEB}/parkingPark/getDetail/{pc}", token, {"json": {}}, rec)
        d = (det.get("response") or {}).get("data") or {}
        replay = {"parkCode": pc, "detail": d}
        ev["summary"]["replay_target_park"] = pc
        print(f"[probe] detail of {pc} fetched", flush=True)

    # --- 5) 同值回放 ---
    if replay and not args.no_replay:
        d = replay["detail"]
        body = {"parkingPark": d.get("parkingPark"), "carInConfig": d.get("carInConfig")}
        for k in ("parkingPark", "carInConfig"):
            if isinstance(body[k], dict):
                body[k] = {kk: vv for kk, vv in body[k].items()
                           if kk not in ("id", "createTime", "updateTime", "yqAppCode")}
        rp = _post(f"{WEB}/parkingPark/saveOrUpdate", token, {"json": body}, rec)
        ev["summary"]["replay_result"] = rp.get("response")
        print("[probe] replay saveOrUpdate ->", json.dumps(rp.get("response"), ensure_ascii=False)[:200], flush=True)

        if args.variants:
            vs = ev["summary"]["replay_variants"] = []

            def _v(name, req):
                rr = _post(f"{WEB}/parkingPark/saveOrUpdate", token, {"json": req}, rec)
                vs.append({"name": name, "request": req, "response": rr.get("response")})
                print(f"[probe] variant {name} ->",
                      json.dumps(rr.get("response"), ensure_ascii=False)[:160], flush=True)

            # V1: 服务端原样 detail（含 id/createTime/updateTime/yqAppCode）
            _v("as_returned", {"parkingPark": d.get("parkingPark"), "carInConfig": d.get("carInConfig")})
            # V2: 剥掉 id/createTime/updateTime/yqAppCode（只补必要字段，同首次回放但保留 place 相关）
            pp2 = {k: v for k, v in (d.get("parkingPark") or {}).items()
                   if k not in ("id", "createTime", "updateTime", "yqAppCode")}
            ci2 = {k: v for k, v in (d.get("carInConfig") or {}).items()
                   if k not in ("id", "createTime", "updateTime", "yqAppCode")}
            _v("stripped_meta", {"parkingPark": pp2, "carInConfig": ci2})
            # V3: api_defs 最小样板（新建一条全新 parkCode）
            _v("api_defs_sample", {"parkingPark": {
                "parkCode": "PROBE-PARK-001", "parkName": "探针车场", "mainPark": True,
                "parentCode": "", "totalSpots": 100, "occupiedSpots": 0, "needCount": 1,
                "bindPayCode": (d.get("parkingPark") or {}).get("bindPayCode"), "needPay": 1},
                "carInConfig": {"parkCode": "PROBE-PARK-001", "parkName": "探针车场",
                                "tempCarIn": True, "carWhiteOtherIsCasual": False,
                                "nobodyPark": False, "specialCarEnable": False,
                                "enableMisMatch": True, "innerOpenWhenNotMatch": True,
                                "otherParkWhiteAccess": False, "otherParkMonthAccess": False}})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print("[probe] evidence written:", args.out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
