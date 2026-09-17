# -*- coding: utf-8 -*-
"""`bill/latest` 在哪些时点可读 + 免费车场的账单形状（2026-09-11，一次性 aux，自建自清）。

接 `_probe_pay_semantics.py` 的结论：`bill/unpaid` 在该环境**恒返 `请求失败`**
（与 `currentTime` 无关）⇒ 金额断言只能走 `bill/latest.payableAmount`。
本脚本要定死两件事：

  1. **免费车场**（P2，`needPay`=0，即表2-01）走完 入→出 后 `latest` 返回什么
     —— 决定表2-01 断言写 `payableAmount: 0` 还是改成别的；
  2. `latest` 在**出场前**可不可靠（同一辆车连查两次，间隔 1s）
     —— 决定 表2-06（提前缴费后金额归零）的金额断言点能不能放在出场前。

跑法（**框架 venv**）：
    C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe \
        有作用/PhaseA/_probe_bill_latest_points.py
"""
import json
import sys
import time
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

# 取自上一轮 extract.last_run.yaml
P2 = ("zhyq674524329197917", "DEV-P2-1_hzlpcm", "DEV-P2-2_sgkr7b")   # needPay=0
P6 = ("zhyq668050497924372", "DEV-P6-1_sb6aaj", "DEV-P6-2_vvj1ty")   # needPay=1

T_IN = "2029-12-31 10:00:00"
T_OUT = "2029-12-31 14:00:00"


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
    print(f"  {tag:34} → {json.dumps(js, ensure_ascii=False)[:260]}")
    return js


def latest(car, tag=""):
    return post(f"{MOCK}/bill/latest", {"carNumber": car}, f"latest {car} {tag}")


def main():
    login()

    print("\n=== 1. 免费车场 P2：入→出→latest ===")
    car = "津E5W8R2"
    post(f"{MOCK}/access/enter",
         {"carNumber": car, "deviceCode": P2[1], "time": T_IN}, f"enter {car} @P2")
    g = post(f"{MOCK}/access/exit",
             {"carNumber": car, "deviceCode": P2[2], "time": T_OUT}, f"exit  {car} @P2")
    print(f"    → openGate = {g.get('openGate') if isinstance(g, dict) else g}")
    latest(car, "出场后")

    print("\n=== 2. 收费车场 P6：出场前连查两次（间隔 1s）===")
    car = "湘B8Q4M6"
    post(f"{MOCK}/access/enter",
         {"carNumber": car, "deviceCode": P6[1], "time": T_IN}, f"enter {car} @P6")
    latest(car, "出场前 #1")
    time.sleep(1)
    latest(car, "出场前 #2")
    post(f"{MOCK}/access/exit",
         {"carNumber": car, "deviceCode": P6[2], "time": T_OUT}, f"exit  {car} @P6")
    latest(car, "出场后")

    print("\n=== 3. 收费车场 P6：不停留时长（默认 currentTime?）===")
    latest("赣T6N3H8", "上一脚本的车，已出场")


if __name__ == "__main__":
    main()
