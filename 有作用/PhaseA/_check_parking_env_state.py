# -*- coding: utf-8 -*-
"""只读盘点 dev 环境里 ParkingRuleTable 留下的数据（2026-09-11，一次性 aux）。

跑完一轮后「看不到车辆记录/进出记录」时用来定位：到底是
  (a) 服务端没有（被清了 / 压根没建），还是
  (b) 有但不在你查看的位置。

**只读，不写任何数据**。用框架同款鉴权（token + yq-app-code）。
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


def main():
    login()
    print("=" * 60)

    d = post(f"{WEB}/parkingPark/getAllWithDevices", {"mainPark": True})
    parks = (d.get("data") or []) if isinstance(d, dict) else []
    print(f"[车场] total={len(parks)}")
    for p in parks[:20]:
        pp = p.get("parkingPark", {}) if isinstance(p, dict) else {}
        print(f"   {pp.get('parkName')}  code={pp.get('parkCode')}  "
              f"设备={len(p.get('carInDeviceList') or [])}进/"
              f"{len(p.get('carOutDeviceList') or [])}出")

    print("=" * 60)
    for label, body in (
        ("白名单", {"isWhite": 1, "pageNum": 1, "pageSize": 50}),
        ("黑名单", {"isWhite": 0, "pageNum": 1, "pageSize": 50}),
    ):
        d = post(f"{WEB}/carWhiteBlack/getPage", body)
        if isinstance(d, dict):
            print(f"[{label}] total={d.get('data', {}).get('total')}  "
                  f"条数={len(d.get('data', {}).get('list') or [])}")
            for x in (d.get("data", {}).get("list") or [])[:10]:
                print(f"   {x.get('carNumber')} parkCode={x.get('parkCode')} "
                      f"code={x.get('code')}")
        else:
            print(f"[{label}] {d}")

    print("=" * 60)
    d = post(f"{WEB}/parkingPaymentConfig/getParkingPaymentConfigPage",
             {"pageNum": 1, "pageSize": 50})
    if isinstance(d, dict):
        lst = d.get("data", {}).get("list") or []
        print(f"[收费规则] total={d.get('data', {}).get('total')} 条数={len(lst)}")
        for x in lst[:10]:
            print(f"   {x.get('name')} code={x.get('code')}")

    print("=" * 60)
    print("[mock 侧] 探测车是否在场（只有在线车才有的查）")
    for plate in ("我A12345",):
        d = post(f"{MOCK}/carTypeQuery",
                 {"carNumber": plate, "parkCode": "PARK-FIXTURE",
                  "startTime": "2029-12-31 10:00:00"})
        print(f"   {plate} -> {json.dumps(d, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
