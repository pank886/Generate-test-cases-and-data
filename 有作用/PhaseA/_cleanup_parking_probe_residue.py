# -*- coding: utf-8 -*-
"""清理 Round 0 探针在 dev 留下的残留（2026-09-10，一次性 aux）。

背景：早前探针误以为删除接口不可用（实际是入参形状错：应为**裸数组** `["code",…]`），
故未清理。已确认可用后，本工具回收探针自建的车场/设备/收费规则。

只删**本探针命名前缀**的对象，绝不触碰用户数据：
  车场/设备：`PROBE_*`、`探针*`（含 A–F 变体）
  收费规则：name 以 `PROBE_` 开头

安全：默认 --dry-run 只打印；加 --apply 才真删。
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

PARK_PREFIXES = ("PROBE_", "探针")
RULE_PREFIX = "PROBE_"


def _post(token, path, body, rec, timeout=25):
    url = WEB + path
    t0 = time.time()
    r = requests.post(url, json=body,
                      headers={"Content-Type": "application/json", "token": token, "yq-app-code": YQ},
                      verify=False, timeout=timeout)
    try:
        resp = r.json()
    except Exception:
        resp = r.text[:500]
    rec.append({"url": path, "request": body, "http": r.status_code, "response": resp,
                "elapsed_ms": int((time.time() - t0) * 1000)})
    return resp


def main():
    ap = argparse.ArgumentParser(description="清理智慧停车探针残留")
    ap.add_argument("--out", required=True, help="证据 JSON 输出路径")
    ap.add_argument("--apply", action="store_true", help="真删；缺省只 dry-run 打印")
    args = ap.parse_args()

    ev = {"run_at": datetime.now().isoformat(timespec="seconds"), "dry_run": not args.apply,
          "calls": [], "deleted": {"devices": [], "parks": [], "rules": []}}
    rec = ev["calls"]

    token = requests.post(LOGIN, json={
        "identityCode": "", "name": "admin",
        "password": "aa60fab6276567c09dcdffe80df5f0f1", "randomStr": "", "yqAppCode": YQ,
    }, headers={"Content-Type": "application/json;charset=UTF-8"},
        verify=False, timeout=20).json()["data"]["token"]
    print("[cleanup] login ok", flush=True)

    def hit(name):
        return name.startswith(PARK_PREFIXES) if name else False

    # --- 目标车场（含其设备） ---
    parks = _post(token, "/parkingPark/getAllWithDevices", {}, rec).get("data") or []
    targets = []
    for e in parks:
        pp = e.get("parkingPark") or {}
        if hit(pp.get("parkName")):
            targets.append({"parkCode": pp.get("parkCode"), "parkName": pp.get("parkName"),
                            "devices": [x.get("code") for x in (e.get("carInDeviceList") or []) if x.get("code")]})
    print(f"[cleanup] target parks={len(targets)}", flush=True)
    for t in targets:
        print(f"    {t['parkName']} | {t['parkCode']} | dev={t['devices']}", flush=True)

    # --- 设备：先解除再删车场（否则服务端拒绝） ---
    # 形状实测：parkingDevice/deleteByCodes 收 {"codes": "a,b"} **字符串**（非数组）
    dev_codes = sorted({c for t in targets for c in t["devices"]})
    if dev_codes:
        ev["deleted"]["devices"] = dev_codes
        if args.apply:
            r = _post(token, "/parkingDevice/deleteByCodes", {"codes": ",".join(dev_codes)}, rec)
            print("[cleanup] DELETE devices ->", json.dumps(r, ensure_ascii=False)[:140], flush=True)
        else:
            print(f"[cleanup] DRY devices n={len(dev_codes)}: {dev_codes}", flush=True)

    # --- 车场：形状实测为**裸数组** ---
    park_codes = [t["parkCode"] for t in targets]
    if park_codes:
        ev["deleted"]["parks"] = park_codes
        if args.apply:
            r = _post(token, "/parkingPark/deleteByCodes", park_codes, rec)
            print("[cleanup] DELETE parks ->", json.dumps(r, ensure_ascii=False)[:200], flush=True)
        else:
            print(f"[cleanup] DRY parks n={len(park_codes)}: {park_codes}", flush=True)

    # --- 收费规则（按 name 前缀）：形状实测为**裸数组** ---
    rules = _post(token, "/parkingPaymentConfig/getParkingPaymentConfigList",
                  {"configType": "3"}, rec).get("data") or []
    rule_codes = [x.get("code") for x in rules
                  if str(x.get("name") or "").startswith(RULE_PREFIX) and x.get("code")]
    if rule_codes:
        ev["deleted"]["rules"] = rule_codes
        if args.apply:
            r = _post(token, "/parkingPaymentConfig/delete", rule_codes, rec)
            print(f"[cleanup] DELETE rules n={len(rule_codes)} ->",
                  json.dumps(r, ensure_ascii=False)[:140], flush=True)
        else:
            print(f"[cleanup] DRY rules n={len(rule_codes)}: {rule_codes}", flush=True)

    # --- 复核 ---
    after = _post(token, "/parkingPark/getAllWithDevices", {}, rec).get("data") or []
    remain = [(e.get("parkingPark") or {}).get("parkName") for e in after
              if hit((e.get("parkingPark") or {}).get("parkName"))]
    ev["remaining_probe_parks"] = remain
    print(f"[cleanup] remaining probe parks={remain}", flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print("[cleanup] evidence written:", args.out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
