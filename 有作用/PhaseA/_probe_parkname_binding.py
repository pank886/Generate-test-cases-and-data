# -*- coding: utf-8 -*-
"""只给 parkName、不给 parkCode，服务端能不能把关联数据挂对车场？（2026-09-11，一次性 aux）

背景：`changelog/_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md` §五。
生成器所有 `preXXXParkCode` 靠 `getAllWithDevices{parkName}` 回读 `$.data[0]` 拿，
而该 `parkName` 过滤**服务端不生效**（实测传任意名都返回全量 107 条）→ 编码取错 →
设备/白名单/进出全挂到别人家。

若 `parkingDevice/add`、`carWhiteBlack/add` 能用 `parkName` 自行定位车场，
则 `parkCode` 回读整条链可以删掉 —— 车场名是我们自己生成的、完全可控。

**本脚本自建自清**，跑完不留残留。默认 dry-run，`--apply` 才真发请求。
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MISC = Path(r"C:\Users\damai\PyCharmMiscProject")
sys.path.insert(0, str(MISC))

from common.readyaml import ReadYamlData  # noqa: E402

BASE = "https://dev.damaiiot.com:40443"
WEB = f"{BASE}/park-access-parking-web"
LOG = MISC / "log" / "test.20260911145342.log"
READ = ReadYamlData()


def login():
    r = requests.post(f"{BASE}/park-base-auth/login", verify=False, timeout=20,
                      headers={"Content-Type": "application/json"},
                      json={"identityCode": "", "name": "admin",
                            "password": "aa60fab6276567c09dcdffe80df5f0f1",
                            "randomStr": "", "yqAppCode": "test"})
    READ.write_yaml_data({"accessToken": r.json()["data"]["token"]})
    READ.write_yaml_data({"yqAppCode": "test"})


def h():
    return {"Content-Type": "application/json",
            "token": READ.get_extract_yaml("accessToken"),
            "yq-app-code": READ.get_extract_yaml("yqAppCode")}


def post(url, body):
    r = requests.post(url, json=body, headers=h(), verify=False, timeout=30)
    try:
        return r.json()
    except Exception:
        return r.text


def park_body(name: str) -> dict:
    """从上次成功跑的历史日志里取一份**已解析**的车场新建报文（含真实 bindPayCode）。"""
    import ast
    for ln in open(LOG, encoding="utf-8", errors="replace"):
        if "请求参数:" in ln and "parentCode" in ln:
            j = ast.literal_eval(ln.split("请求参数:", 1)[1]).get("json", {})
            if (j.get("parkingPark") or {}).get("mainPark") is True:
                j["parkingPark"]["parkName"] = name
                return j
    raise SystemExit("日志里没找到可复用的车场新建报文")


def find_park(name: str):
    d = post(f"{WEB}/parkingPark/getAllWithDevices", {})
    for x in (d.get("data") or []):
        if x["parkingPark"].get("parkName") == name:
            return x
    return None


def park_devices(code: str):
    d = post(f"{WEB}/parkingPark/getAllWithDevices", {})
    for x in (d.get("data") or []):
        if x["parkingPark"].get("parkCode") == code:
            ins = [v["code"] for v in (x.get("carInDeviceList") or [])]
            outs = [v["code"] for v in (x.get("carOutDeviceList") or [])]
            return ins, outs
    return [], []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.apply:
        print("dry-run：加 --apply 才发请求")
        return

    login()
    tag = uuid.uuid4().hex[:6]
    pname = f"PK-PROBE4_{tag}"
    dcode = f"DEV-PROBE4-{tag}"
    plate = "京Z" + tag[:5].upper()          # 7 位，符合常规车牌格式
    print(f"[目标] parkName={pname} device={dcode} plate={plate}")

    created = {"park": None, "device": None, "white": None}
    try:
        # 1) 建车场
        r = post(f"{WEB}/parkingPark/saveOrUpdate", park_body(pname))
        print("[1] 建车场 ->", r)
        park = find_park(pname)
        if not park:
            raise SystemExit("建场后按名查不到（预期内：过滤不生效，改客户端过滤也没找到）")
        code = park["parkingPark"]["parkCode"]
        created["park"] = code
        print(f"    车场编码 = {code}")

        # 2) 加设备：**只给 parkName，不给 parkCode**
        dev = {
            "code": dcode, "name": f"CAM-{tag}", "deviceStatus": "1",
            "deviceType": "1", "inner": False, "multi": False, "needToShow": "1",
            "location": f"LOC-{tag}", "passType": "1",
            "parkingDeviceManufacturerCode": "100061",
            "parkingDeviceManufacturerName": "大华",
            "parkingDeviceModelCode": "100121",
            "parkingDeviceModelName": "DH-IPC-HFW5234K-ZFD-LED",
            "parkName": pname,                      # ← 只给名字
        }
        print("[2] 加设备(仅 parkName) ->", str(post(f"{WEB}/parkingDevice/add", dev))[:120])
        created["device"] = dcode
        ins, outs = park_devices(code)
        print(f"    → 该车场 进设备={ins} 出设备={outs}")
        print(f"    ✅ 挂对了" if dcode in ins else "    ❌ 没挂上（parkName 不能定位）")

        # 3) 加白名单：**只给 parkName，不给 parkCode**
        white = {"carNumber": plate, "isWhite": 1, "multi": False,
                 "parkName": pname, "status": "3"}
        print("[3] 加白名单(仅 parkName) ->", str(post(f"{WEB}/carWhiteBlack/add", white))[:120])
        d = post(f"{WEB}/carWhiteBlack/getPage",
                 {"isWhite": 1, "pageNum": 1, "pageSize": 10, "searchKey": plate})
        for x in ((d.get("data") or {}).get("list") or []):
            print(f"    → 记录 parkCode={x.get('parkCode')!r} parkName={x.get('parkName')!r}")
            print(f"    ✅ 挂对了" if x.get("parkCode") == code
                  else "    ❌ 没挂上 / 挂错（parkName 不能定位）")
            created["white"] = x.get("code")
    finally:
        print("\n=== 清场 ===")
        if created["white"]:
            print("  删白名单 ->", str(post(f"{WEB}/carWhiteBlack/delete", [created["white"]]))[:100])
        if created["device"]:
            print("  删设备 ->", str(post(f"{WEB}/parkingDevice/deleteByCodes",
                                        {"codes": created["device"]}))[:100])
        if created["park"]:
            print("  删车场 ->", str(post(f"{WEB}/parkingPark/deleteByCodes",
                                        [created["park"]]))[:100])
        print("  残留检查:", find_park(pname) or "无")


if __name__ == "__main__":
    main()
