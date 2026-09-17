"""探针（aux，不入系统运行）：确定 `mock/monthlyCar/query` 的实际响应形状。

背景：`智慧停车/api_defs.json` 记 `mock/monthlyCar/query` 返回**顶层** `code`（→ `$.code`），
而旧版 VehicleAccess 用 `$.[0].code`（数组）。生成器要据此写 JSONPath，故必须先实测。

用法（框架 venv）：
    C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe \
        有作用/PhaseA/_probe_monthlycar_query_shape.py

只读为主：仅 add 一条探针包月车并立即 delete（若 query 能回读到 code）。
"""
import json
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = "https://dev.damaiiot.com:40443"
CONF = "/park-access-parking-web"
RULE = "/park-access-parking-rule-new"

PLATE = "京A88888"  # 探针专用，避免随机车牌上限


def login() -> dict:
    r = requests.post(
        f"{HOST}/park-base-auth/login",
        json={"identityCode": "", "name": "admin",
              "password": "aa60fab6276567c09dcdffe80df5f0f1",
              "randomStr": "", "yqAppCode": "test"},
        headers={"Content-Type": "application/json;charset=UTF-8"},
        verify=False, timeout=20)
    token = r.json()["data"]["token"]
    return {"Content-Type": "application/json;charset=UTF-8",
            "token": token, "yq-app-code": "test"}


def post(path: str, headers: dict, body) -> tuple[int, str]:
    r = requests.post(f"{HOST}{path}", json=body, headers=headers,
                      verify=False, timeout=20)
    try:
        return r.status_code, json.dumps(r.json(), ensure_ascii=False)
    except ValueError:
        return r.status_code, r.text[:500]


def main() -> int:
    h = login()
    print("=== 0. 登录 ===")
    print("token 前缀:", h["token"][:16], "...")

    # 找一个真实存在的 parkCode
    code, txt = post(f"{CONF}/parkingPark/getAllWithDevices", h,
                     {"mainPark": True})
    print("\n=== 1. parkingPark/getAllWithDevices ===")
    print(code, txt[:800])
    park_code = "test"
    try:
        data = json.loads(txt).get("data") or []
        if data:
            park_code = data[0]["parkingPark"]["parkCode"]
    except Exception as e:  # noqa: BLE001
        print("解析失败:", e)
    print("→ 探测用 parkCode =", park_code)

    print("\n=== 2. mock/monthlyCar/add ===")
    code, txt = post(f"{RULE}/mock/monthlyCar/add", h,
                     {"parkCode": park_code, "carNumber": PLATE,
                      "startTime": "2026-01-01 00:00:00",
                      "endTime": "2027-12-31 23:59:59",
                      "mainCount": 1})
    print(code, txt[:800])

    print("\n=== 3. mock/monthlyCar/query  ★ 关键 ===")
    code, txt = post(f"{RULE}/mock/monthlyCar/query", h,
                     {"carNumber": PLATE, "parkCode": park_code})
    print(code, txt)
    print("\n--- 形状判定 ---")
    try:
        j = json.loads(txt)
        d = j.get("data")
        print("data 类型:", type(d).__name__)
        if isinstance(d, list):
            print("→ 数组；code 路径 = $.data[0].code ；len =", len(d))
        elif isinstance(d, dict):
            print("→ 对象；code 路径 = $.data.code ；keys =", list(d.keys()))
        else:
            print("→ 其它/空:", repr(d)[:200])
    except Exception as e:  # noqa: BLE001
        print("解析失败:", e)

    print("\n=== 4. mock/monthlyCar/delete（传车牌而非 code）===")
    code, txt = post(f"{RULE}/mock/monthlyCar/delete", h, {"codes": [PLATE]})
    print(code, txt[:400])

    print("\n=== 5. 删除后再 query（确认是否真删）===")
    code, txt = post(f"{RULE}/mock/monthlyCar/query", h,
                     {"carNumber": PLATE, "parkCode": park_code})
    print(code, txt)
    if txt.strip() in ("[]", ""):
        print("→ 传车牌即可删除（无需回读 code）")
    else:
        print("→ 传车牌**未**删掉，仍需回读 code 再删")

    print("\n=== 6. 对照组：delete 传真实 code ===")
    code, txt = post(f"{RULE}/mock/monthlyCar/add", h,
                     {"parkCode": park_code, "carNumber": PLATE,
                      "startTime": "2026-01-01 00:00:00",
                      "endTime": "2027-12-31 23:59:59", "mainCount": 1})
    code, txt = post(f"{RULE}/mock/monthlyCar/query", h,
                     {"carNumber": PLATE, "parkCode": park_code})
    real_code = None
    try:
        real_code = json.loads(txt)[0]["code"]
    except Exception:  # noqa: BLE001
        pass
    print("回读 code =", real_code)
    if real_code:
        code, txt = post(f"{RULE}/mock/monthlyCar/delete", h, {"codes": [real_code]})
        print("delete(真实code):", code, txt[:200])
        code, txt = post(f"{RULE}/mock/monthlyCar/query", h,
                         {"carNumber": PLATE, "parkCode": park_code})
        print("再 query:", code, txt)

    print("\n=== 7. 清理探针残留（按真实 code 逐条删）===")
    code, txt = post(f"{RULE}/mock/monthlyCar/query", h,
                     {"carNumber": PLATE, "parkCode": park_code})
    try:
        codes = [r["code"] for r in json.loads(txt)]
    except Exception:  # noqa: BLE001
        codes = []
    print("待删 codes =", codes)
    for c in codes:
        print("  delete", c, "→", post(f"{RULE}/mock/monthlyCar/delete", h,
                                       {"codes": [c]})[1][:120])
    code, txt = post(f"{RULE}/mock/monthlyCar/query", h,
                     {"carNumber": PLATE, "parkCode": park_code})
    print("清理后 query:", txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
