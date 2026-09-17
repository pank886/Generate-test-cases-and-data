# -*- coding: utf-8 -*-
"""缴费接口语义实证（2026-09-11，一次性 aux，自建自清）。

要定死的三件事（表2 判定表动作列的**实际语义**，不靠推断）：

  1. `bill/unpaid` 到底在什么条件下才返回真正的 `fee`
     （判定表生成器现在用「入场时刻」调它 → 服务端返 `data:"请求失败"`）；
  2. `bill/latest` 在**出场前/后**分别返回什么（决定断言点放在哪）；
  3. `pay/simulate`（提前缴费）/`payWhenLeave/simulate`（出场缴费）执行后
     `bill/latest.paid` 与 `openGate` 各变成什么 —— 直接决定
     表2-05/06/10/11 的断言写法（尤其表2-06 判定表写「动作（是否缴费）=否」
     但条件列「是否提前缴费=是」，需实证哪个是对的口径）。

跑法（**框架 venv**，需要 requests）：
    C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe \
        有作用/PhaseA/_probe_pay_semantics.py

现场：P6（取自上一轮 `extract.last_run.yaml`）。跑完车全部出场，不留场状态。
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

BASE = "https://dev.damaiiot.com:40443"
WEB = f"{BASE}/park-access-parking-web"
MOCK = f"{BASE}/park-access-parking-rule-new/mock"
READ = ReadYamlData()

PARK = "zhyq668050497924372"          # PK-P6_px67ia
DEV_IN = "DEV-P6-1_sb6aaj"
DEV_OUT = "DEV-P6-2_vvj1ty"
T_IN = "2029-12-31 10:00:00"
T_OUT = "2029-12-31 14:00:00"         # +4h，与 map 的 timeline 一致（应缴 20）


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
    print(f"  {tag:38} → {json.dumps(js, ensure_ascii=False)[:260]}")
    return js


def d(js, key, default=None):
    return js.get(key, default) if isinstance(js, dict) else default


def enter(car, t=T_IN):
    return post(f"{MOCK}/access/enter",
                {"carNumber": car, "deviceCode": DEV_IN, "time": t}, f"enter {car} @{t}")


def exit_(car, t=T_OUT):
    return post(f"{MOCK}/access/exit",
                {"carNumber": car, "deviceCode": DEV_OUT, "time": t}, f"exit  {car} @{t}")


def unpaid(car, t, extra=None):
    body = {"carNumber": car, "currentTime": t}
    body.update(extra or {})
    return post(f"{MOCK}/bill/unpaid", body, f"unpaid {car} @{t} {extra or ''}")


def latest(car, tag=""):
    return post(f"{MOCK}/bill/latest", {"carNumber": car}, f"latest {car} {tag}")


def main():
    login()

    print("\n=== 场景 A：`bill/unpaid` 在各时点的返回值（同一辆车，不缴费）===")
    CAR = "赣T6N3H8"
    enter(CAR)
    unpaid(CAR, T_IN)                                   # 入场时刻
    unpaid(CAR, T_OUT)                                  # 出场时刻（未出场）
    unpaid(CAR, T_OUT, {"parkCode": PARK})              # 带 parkCode
    latest(CAR, "出场前")
    exit_(CAR)                                          # 被拦 → 触发账单
    unpaid(CAR, T_OUT)                                  # 出场尝试之后
    latest(CAR, "出场后")
    exit_(CAR)                                          # 收尾：放行? （应仍被拦）

    print("\n=== 场景 B：pay/simulate 提前缴费 → 出场 ===")
    CAR = "冀R7Y2Q9"
    enter(CAR)
    post(f"{MOCK}/pay/simulate",
         {"carNumber": CAR, "fee": 20, "payTime": T_OUT, "deviceCode": DEV_OUT},
         "pay/simulate fee=20")
    latest(CAR, "提前缴费后/出场前")
    g = exit_(CAR)
    print(f"    → openGate = {d(g, 'openGate')}")
    latest(CAR, "出场后")

    print("\n=== 场景 C：出场被拦 → payWhenLeave/simulate → 再出场 ===")
    CAR = "皖P4K8L3"
    enter(CAR)
    g = exit_(CAR)
    print(f"    → openGate = {d(g, 'openGate')}")
    post(f"{MOCK}/payWhenLeave/simulate",
         {"carNumber": CAR, "fee": 20, "payTime": T_OUT, "deviceCode": DEV_OUT},
         "payWhenLeave/simulate fee=20")
    latest(CAR, "出场缴费后")
    g = exit_(CAR)
    print(f"    → openGate = {d(g, 'openGate')}")
    latest(CAR, "出场后")


if __name__ == "__main__":
    main()
