# -*- coding: utf-8 -*-
"""`mock/bill/unpaid` 在「出场时间 ≤ 真实当前时间」下到底返什么（2026-09-11，一次性 aux，自建自清）。

背景：文档写响应就是 `{"fee": 10.00}`，body 只有 `{carNumber, currentTime}`。
上一版探测把 `currentTime` 喂成 **2029-12-31（未来）** → 返 400 `请求失败`；
未入场就查 → 返 400（附带一句人工提示）。用户口径（2026-09-11）：
**「出场时间不要超过当前时间，入场时间可以提前」**。

本脚本据此重测，定死三件事：
  1. 车**在场**、`currentTime` = **真实当前时间** → 是否返 `{fee}`？
  2. `currentTime` 早于入场时间 / 等于入场时间 → 返什么？
  3. 停留 4h 时 `fee` 是否 = 20（单价 5 元/h）？

跑法（**框架 venv**）：
    C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe \
        有作用/PhaseA/_probe_bill_unpaid_shape.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MISC = Path(r"C:\Users\damai\PyCharmMiscProject")
sys.path.insert(0, str(MISC))

from common.readyaml import ReadYamlData  # noqa: E402

BASE = "https://dev.damaiiot.com:40443"
MOCK = f"{BASE}/park-access-parking-rule-new/mock"
READ = ReadYamlData()

P6 = ("zhyq668050497924372", "DEV-P6-1_sb6aaj", "DEV-P6-2_vvj1ty")   # needPay=1

FMT = "%Y-%m-%d %H:%M:%S"


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
    print(f"  [{r.status_code}] {tag:36} {json.dumps(js, ensure_ascii=False)[:300]}")
    return js


def unpaid(car, t, tag=""):
    return post(f"{MOCK}/bill/unpaid", {"carNumber": car, "currentTime": t},
                f"unpaid currentTime={t} {tag}")


def main():
    login()
    now = datetime.now()
    t_in = (now - timedelta(hours=4)).strftime(FMT)     # 入场 4h 前（可提前）
    t_now = now.strftime(FMT)                           # 出场 = 当前时间（不过期）
    t_mid = (now - timedelta(hours=2)).strftime(FMT)

    print(f"\n真实当前时间 = {t_now}")
    car = "粤B7X2Y9"

    print("\n=== 1. 未入场就查（上一版的对照项）===")
    unpaid(car, t_now, "未入场")

    print("\n=== 2. 入场（提前 4h）后，逐个 currentTime ===")
    post(f"{MOCK}/access/enter",
         {"carNumber": car, "deviceCode": P6[1], "time": t_in}, f"enter {car} @P6 t={t_in}")
    # ★ 前置校验：车到底在不在场（VehicleAccess 002 就是漏了这一步才误判）
    day0 = now.strftime("%Y-%m-%d 00:00:00")
    post(f"{MOCK}/carTypeQuery",
         {"carNumber": car, "parkCode": P6[0], "startTime": day0}, "carTypeQuery ← 在不在场")
    unpaid(car, t_in, "= 入场时刻")
    unpaid(car, t_mid, "中途（2h 前）")
    unpaid(car, t_now, "= 当前时间 ← 我们想要的")
    unpaid(car, (now + timedelta(hours=1)).strftime(FMT), "未来 1h")

    print("\n=== 2b. header 变体（在场中，currentTime = 当前时间）===")
    body = {"carNumber": car, "currentTime": t_now}
    base_h = h()
    for tag, hd in (
        ("我们现在的 header", dict(base_h)),
        ("+charset（VehicleAccess 的写法）",
         {**base_h, "Content-Type": "application/json;charset=UTF-8"}),
        ("+charset 且不带 token",
         {"Content-Type": "application/json;charset=UTF-8", "yq-app-code": "test"}),
        ("只 yq-app-code，不带 token/yq",
         {"Content-Type": "application/json;charset=UTF-8"}),
    ):
        r = requests.post(f"{MOCK}/bill/unpaid", json=body, headers=hd,
                          verify=False, timeout=30)
        try:
            js = r.json()
        except Exception:
            js = r.text
        print(f"  [{r.status_code}] {tag:34} {json.dumps(js, ensure_ascii=False)[:220]}")

    print("\n=== 3. 对照 bill/latest（在场中）===")
    post(f"{MOCK}/bill/latest", {"carNumber": car}, "latest 在场中")

    print("\n=== 4. 出场（= 当前时间）后 ===")
    post(f"{MOCK}/access/exit",
         {"carNumber": car, "deviceCode": P6[2], "time": t_now}, f"exit  {car} @P6")
    unpaid(car, t_now, "出场后")
    post(f"{MOCK}/bill/latest", {"carNumber": car}, "latest 出场后")

    print("\n=== 5. ★ 生成物实际用的组合：2029-12-31 字面量（未来时间）===")
    car2 = "粤C9Z4W1"
    post(f"{MOCK}/access/enter",
         {"carNumber": car2, "deviceCode": P6[1], "time": "2029-12-31 10:00:00"},
         f"enter {car2} @P6 t=2029-12-31 10:00:00")
    unpaid(car2, "2029-12-31 10:00:00", "= 入场时刻（我们表2 的写法）")
    unpaid(car2, "2029-12-31 14:00:00", "= 出场时刻（我们表2 的写法）")
    post(f"{MOCK}/access/exit",
         {"carNumber": car2, "deviceCode": P6[2], "time": "2029-12-31 14:00:00"},
         f"exit  {car2} @P6 t=2029-12-31 14:00:00")
    post(f"{MOCK}/bill/latest", {"carNumber": car2}, "latest 出场后")


if __name__ == "__main__":
    main()
