# -*- coding: utf-8 -*-
"""多场白名单记录的编码回读形状核实（2026-09-11，一次性 aux，只读）。

背景：`changelog/2026-09-11_parkcode_readback_by_name.md` §5.4。
`carWhiteBlack/getPage` 的 `parkCode` 是**等值匹配**，而多场记录的 `parkCode` 存的是
逗号复合串（如 `PS1M码,PS1S码`）→ 任何等值查询都命中不了（含它自己那个串）。

候选修法：回读体**不传 `parkCode`**，只留 `searchKey`；`extract` 改用
`$.data.list[?(@.carNumber=='<车牌>')].code` 精确到车。

本脚本要证的是三件事（**只读，不发任何写请求**）：
  1. 列表项里到底有没有 `carNumber` 字段（filter 表达式的字段名不能靠猜）；
  2. 不传 `parkCode` + `searchKey=<整牌>` 能不能命中多场记录；
  3. filter 表达式取到的 code 是否**等于**该记录自己的 `code`。
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


def get_page(body):
    r = requests.post(f"{WEB}/carWhiteBlack/getPage", json=body,
                      headers=h(), verify=False, timeout=30)
    return r.json()


def show(tag, body):
    resp = get_page(body)
    data = resp.get("data") or {}
    lst = data.get("list") or []
    print(f"\n--- {tag}")
    print(f"    请求体: {json.dumps(body, ensure_ascii=False)}")
    print(f"    retCode={resp.get('retCode')} total={data.get('total')} 返回条数={len(lst)}")
    for it in lst[:6]:
        print(f"      code={it.get('code')!r} carNumber={it.get('carNumber')!r} "
              f"parkCode={it.get('parkCode')!r} parkName={it.get('parkName')!r}")
    return lst


def main():
    login()
    # 1) 先看列表项有哪些字段（不筛车，取前几条）
    lst = show("基线：不传 parkCode / 不传 searchKey", {
        "isWhite": 1, "pageNum": 1, "pageSize": 5})
    if not lst:
        print("\n⚠️  没有白名单记录可查（上一轮已被清？），无法核实——请先跑一轮 setup")
        return
    print(f"\n    列表项字段名: {sorted(lst[0].keys())}")

    # 2) 逐条用 searchKey=<整牌> 复核，并对比 filter 取码
    import jsonpath_ng.ext as jpx
    ok = bad = miss = 0
    for it in lst:
        car, code = it.get("carNumber"), it.get("code")
        if not car:
            continue
        resp = get_page({"isWhite": 1, "pageNum": 1, "pageSize": 10,
                         "searchKey": car})
        items = (resp.get("data") or {}).get("list") or []
        hit = [x for x in items if x.get("code") == code]
        expr = "$.data.list[?(@.carNumber=='%s')].code" % car
        got = [m.value for m in jpx.parse(expr).find(resp)]
        verdict = "OK" if got[:1] == [code] else "差异"
        if not items:
            miss += 1
        elif hit:
            ok += 1
            if got[:1] == [code]:
                pass
            else:
                bad += 1
        print(f"  {car!r} 期望 code={code!r} | searchKey命中{len(items)}条 "
              f"| filter取到={got!r} → {verdict} | parkCode字段={it.get('parkCode')!r}")
    print(f"\n汇总：命中且 filter 正确 {ok} 条，filter 取错 {bad} 条，searchKey 空命中 {miss} 条")


if __name__ == "__main__":
    main()
