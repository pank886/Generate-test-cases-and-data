# -*- coding: utf-8 -*-
"""白名单/包月车记录的有效期窗口 vs 访问时刻（2026-09-11，一次性 aux，自建自清）。

背景：ParkingRuleTable 全量跑里 **表1-08…1-22 整片红**：
  * 表1-01…07（P1 单场白名单，add 体**无日期**）→ 绿；
  * 表1-12…22（包月车，add 体 `startTime=今天 / endTime=今天+365d`）→ 红；
  * 表1-08/09/10（多车场白名单，add 体带 `enterStartDate=今天 / enterEndDate=今天+365d`）→ 红。
而所有用例的访问时刻是字面量 **2029-12-31**（生成物的时间线基准）。

假设：记录有效期只到 **2027-09-11**，而访问发生在 **2029-12-31** ⇒ 记录已过期 ⇒
准入不认账（白名单被拒 / 包月车按临停）。

本脚本**同一车场（P1）单变量对照**：
  A. 包月车 有效期 2026→2027（现状写法）   → 期望 openGate=false
  B. 包月车 有效期 2029→2030（覆盖访问时刻） → 期望 openGate=true
  C. 白名单 无日期（现状写法，对应表1-01）  → 期望 openGate=true
  D. 白名单 日期 2026→2027（现状写法）      → 期望 openGate=false
  E. 白名单 日期 2029→2030                 → 期望 openGate=true

跑法（**框架 venv**）：
    C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe \
        有作用/PhaseA/_probe_vehicle_record_validity.py
"""
import json
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MISC = Path(r"C:\Users\damai\PyCharmMiscProject")
sys.path.insert(0, str(MISC))

from common.readyaml import ReadYamlData  # noqa: E402
from data_factory import PlateGenerator  # noqa: E402

BASE = "https://dev.damaiiot.com:40443"
MOCK = f"{BASE}/park-access-parking-rule-new/mock"
WEB = f"{BASE}/park-access-parking-web"
READ = ReadYamlData()

ACCESS_TIME = "2029-12-31 10:00:00"      # 生成物时间线基准


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


def post(url, body, tag):
    r = requests.post(url, json=body, headers=h(), verify=False, timeout=30)
    try:
        js = r.json()
    except Exception:
        js = r.text
    print(f"  [{r.status_code}] {tag:44} {json.dumps(js, ensure_ascii=False)[:160]}")
    return js


def last_extract(key):
    import re
    t = (MISC / "extract.last_run.yaml").read_text(encoding="utf-8")
    m = re.search(key + r":\s*(\S+)", t)
    return m.group(1) if m else None


def main():
    login()
    park = last_extract("preP1ParkCode")
    dev_in = last_extract("preP1InDeviceCode")
    print(f"P1 parkCode={park}  inDevice={dev_in}  访问时刻={ACCESS_TIME}")

    # 合法车牌（服务端校验车牌格式，手写字面量会被 400 挡下）
    pg = PlateGenerator()
    A, B, C, D, E = (pg.generate(1) for _ in range(5))
    D1, E1 = pg.generate(1), pg.generate(1)

    def enter(car, tag):
        post(f"{MOCK}/access/enter",
             {"carNumber": car, "deviceCode": dev_in, "time": ACCESS_TIME},
             f"enter {car}  ({tag})")

    # --- A/B 包月车：有效期是唯一变量 ---
    print("\n=== A. 包月车 有效期 2026-09-11 → 2027-09-11（现状写法）===")
    post(f"{MOCK}/monthlyCar/add",
         {"carNumber": A, "parkCode": park,
          "startTime": "2026-09-11", "endTime": "2027-09-11", "mainCount": 1},
         "monthlyCar/add A")
    enter(A, "A 有效期到 2027")

    print("\n=== B. 包月车 有效期 2029-01-01 → 2030-12-31（覆盖访问时刻）===")
    post(f"{MOCK}/monthlyCar/add",
         {"carNumber": B, "parkCode": park,
          "startTime": "2029-01-01", "endTime": "2030-12-31", "mainCount": 1},
         "monthlyCar/add B")
    enter(B, "B 有效期含 2029")

    # --- C/D/E 白名单：日期是唯一变量 ---
    print("\n=== C. 白名单 无日期（对应表1-01 绿的那批）===")
    post(f"{WEB}/carWhiteBlack/add",
         {"carNumber": C, "isWhite": 1, "multi": False,
          "parkName": "PROBE", "parkCode": park, "status": "3"}, "carWhiteBlack/add C")
    enter(C, "C 无日期")

    print("\n=== D. 白名单 日期 2026→2027（对应表1-08 红的那批）===")
    post(f"{WEB}/carWhiteBlack/add",
         {"carNumber": D, "isWhite": 1, "multi": True,
          "parkName": "PROBE", "parkCode": park, "status": "3",
          "subCarNumbers": [D1], "carOwnerName": "张三",
          "carOwnerPhone": "13800138000",
          "enterStartDate": "2026-09-11", "enterEndDate": "2027-09-11"},
         "carWhiteBlack/add D")
    enter(D, "D 日期到 2027")

    print("\n=== E. 白名单 日期 2029→2030 ===")
    post(f"{WEB}/carWhiteBlack/add",
         {"carNumber": E, "isWhite": 1, "multi": True,
          "parkName": "PROBE", "parkCode": park, "status": "3",
          "subCarNumbers": [E1], "carOwnerName": "张三",
          "carOwnerPhone": "13800138000",
          "enterStartDate": "2029-01-01", "enterEndDate": "2030-12-31"},
         "carWhiteBlack/add E")
    enter(E, "E 日期含 2029")

    # --- F/G/H P3(`carWhiteOtherIsCasual`)/P4(`otherParkWhiteAccess`)：分组配置是变量 ---
    p3, p3dev = last_extract("preP3ParkCode"), last_extract("preP3InDeviceCode")
    p4, p4dev = last_extract("preP4ParkCode"), last_extract("preP4InDeviceCode")
    po = last_extract("prePOParkCode")
    F, G, G1, H = (pg.generate(1) for _ in range(4))
    print(f"\nP3={p3} ({p3dev})  P4={p4} ({p4dev})  PO={po}")

    print("\n=== F. P3(carWhiteOtherIsCasual=true) 白名单 multi=false ===")
    post(f"{WEB}/carWhiteBlack/add",
         {"carNumber": F, "isWhite": 1, "multi": False,
          "parkName": "PROBE", "parkCode": p3, "status": "3"}, "carWhiteBlack/add F @P3")
    post(f"{MOCK}/access/enter", {"carNumber": F, "deviceCode": p3dev, "time": ACCESS_TIME},
         f"enter {F} @P3  (F multi=false)")

    print("\n=== G. P3 白名单 multi=true（对齐表1-08 的写法）===")
    post(f"{WEB}/carWhiteBlack/add",
         {"carNumber": G, "isWhite": 1, "multi": True,
          "parkName": "PROBE", "parkCode": p3, "status": "3",
          "subCarNumbers": [G1], "carOwnerName": "张三",
          "carOwnerPhone": "13800138000",
          "enterStartDate": "2026-09-11", "enterEndDate": "2027-09-11"},
         "carWhiteBlack/add G @P3")
    post(f"{MOCK}/access/enter", {"carNumber": G, "deviceCode": p3dev, "time": ACCESS_TIME},
         f"enter {G} @P3  (G 主车)")
    post(f"{MOCK}/access/enter", {"carNumber": G1, "deviceCode": p3dev, "time": ACCESS_TIME},
         f"enter {G1} @P3  (G 子车)")

    print("\n=== H. 记录在 PO、进场在 P4（otherParkWhiteAccess，对齐表1-11）===")
    post(f"{WEB}/carWhiteBlack/add",
         {"carNumber": H, "isWhite": 1, "multi": False,
          "parkName": "PROBE", "parkCode": po, "status": "3"}, "carWhiteBlack/add H @PO")
    post(f"{MOCK}/access/enter", {"carNumber": H, "deviceCode": p4dev, "time": ACCESS_TIME},
         f"enter {H} @P4  (H 记录在 PO)")

    print("\n=== 对照：carTypeQuery 看类型判定 ===")
    for car, tag in ((A, "A 包月(过期)"), (B, "B 包月(有效)"),
                     (C, "C 白名单(无日期)"), (D, "D 白名单(过期)"),
                     (E, "E 白名单(有效)")):
        post(f"{MOCK}/carTypeQuery",
             {"carNumber": car, "parkCode": park, "startTime": ACCESS_TIME},
             f"carTypeQuery {tag}")


if __name__ == "__main__":
    main()
