# -*- coding: utf-8 -*-
"""dev 后端真实行为调查：负向 add 真实 msg / 未选方案行为 / getParentList 结构 / 删除行为。

用框架同款 header（token + yq-app-code），POST 真实请求，记录 retCode/msg。
创建的测试电表 code 打印到 stdout 供清理。
"""
import json
import random
import string
import warnings
import requests

warnings.filterwarnings("ignore")

BASE = "https://dev.damaiiot.com:40443"


def rand(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def probe():
    # 1) 登录
    r = requests.post(
        f"{BASE}/park-base-auth/login",
        json={"name": "admin", "password": "889d0039326ce09aa2ae27401967411f", "yqAppCode": "test"},
        verify=False, timeout=30)
    data = r.json()
    tok = data["data"]["token"]
    H = {"token": tok, "yq-app-code": "test", "Content-Type": "application/json"}
    print("login retCode:", data.get("retCode"), "token头:", bool(tok))

    def add(payload):
        return requests.post(f"{BASE}/park-energy-electric-web/electricMeter/add",
                             json=payload, headers=H, verify=False, timeout=30).json()

    def getlist(params):
        return requests.post(f"{BASE}/park-energy-electric-web/electricMeter/getList",
                             json=params, headers=H, verify=False, timeout=30).json()

    def delete(code):
        return requests.post(f"{BASE}/park-energy-electric-web/electricMeter/delete",
                             json={"code": code}, headers=H, verify=False, timeout=30).json()

    def base_payload(code, name, **over):
        p = dict(
            accessMethod='1', accessType='4', autoOff='0', billingFactor=1,
            code=code, electricity='0', level=1,
            meterDeviceManufacturerCode='华立', meterDeviceManufacturerName='华立',
            meterDeviceModelCode='DDS28', meterDeviceModelName='DDS28',
            meterDeviceType='单相', meterTypeCode='1',
            mutualInductorOnOrOff='0', name=name, payConfigCode='zhyqP25529429',
            powerRate=1, purpose='测试', sceneCode='zhyqE29999415',
            sceneName='公司场所', useType='1', whetherToCount=True,
            electricControlDeviceCode='pank001', electricControlDeviceName='pank集中器',
            regionCode='zhyqE96265606', regionName='合约专区',
            buildingCode='zhyqG45805263', buildingName='合约楼',
            floorCode='zhyqL29193557', floorName='1',
            roomCode='zhyqE58365936', roomNumber='101', roomName='101',
            personCode='zhyqB31966477', personName='程文虎',
            parentDeviceCode='', parentDeviceName='', memo='', maxValue='100',
            initDetailList=[],
        )
        p.update(over)
        return p

    created = []

    def show(tag, result, extra=""):
        rc = result.get("retCode")
        msg = result.get("msg")
        print(f"[{tag}] retCode={rc} msg={msg!r} {extra}")

    # S0 合法基线（供 S2 重复编号 / S10 删除用）
    c0 = "PROBE_" + rand()
    show("S0_合法基线", add(base_payload(c0, "PROBE基线")), f"code={c0}")
    created.append(c0)

    # S1 缺必填（去 sceneCode）
    c1 = "PROBE_" + rand()
    show("S1_缺sceneCode", add(base_payload(c1, "PROBE缺必填", sceneCode="")), f"code={c1}")

    # S2 重复编号（code=c0 再 add）
    show("S2_重复编号", add(base_payload(c0, "PROBE重复" + rand())))

    # S3 非法分类（accessMethod='9'）
    c3 = "PROBE_" + rand()
    show("S3_非法accessMethod9", add(base_payload(c3, "PROBE非法分类", accessMethod='9')), f"code={c3}")
    created.append(c3)

    # S3b 非法电表类型
    c3b = "PROBE_" + rand()
    show("S3b_非法meterDeviceType", add(base_payload(c3b, "PROBE非法类型", meterDeviceType='四相')), f"code={c3b}")
    created.append(c3b)

    # S4 负初始电量
    c4 = "PROBE_" + rand()
    show("S4_负初始电量", add(base_payload(c4, "PROBE负电量",
        initDetailList=[{"elepayTypeCode": "flat", "initElectricity": "-1"}])), f"code={c4}")
    created.append(c4)

    # S5 精度超2位
    c5 = "PROBE_" + rand()
    show("S5_精度超2位", add(base_payload(c5, "PROBE精度",
        initDetailList=[{"elepayTypeCode": "flat", "initElectricity": "1.234"}])), f"code={c5}")
    created.append(c5)

    # S6 名称超长
    c6 = "PROBE_" + rand()
    show("S6_名称超长100", add(base_payload(c6, "超" * 100)), f"code={c6}")
    created.append(c6)

    # S7 SQL注入名
    c7 = "PROBE_" + rand()
    show("S7_SQL注入名", add(base_payload(c7, "' OR 1=1 --")), f"code={c7}")
    created.append(c7)

    # S8 未选计费方案
    c8 = "PROBE_" + rand()
    show("S8_未选计费方案", add(base_payload(c8, "PROBE无方案", payConfigCode="", payConfigName="")), f"code={c8}")
    created.append(c8)

    # S9 未选接入协议（去 accessMethod）
    c9 = "PROBE_" + rand()
    p9 = base_payload(c9, "PROBE无协议")
    del p9["accessMethod"]
    show("S9_未选接入协议", add(p9), f"code={c9}")
    created.append(c9)

    # S10 delete 绑定方案电表（c0 带 payConfigCode）
    show("S10_删除绑定方案电表", delete(c0), f"code={c0}")

    # S11 delete 未绑定电表（c8 若无方案则未绑定）
    show("S11_删除无方案电表", delete(c8), f"code={c8}")

    # S12 delete 不存在电表
    show("S12_删除不存在", delete("PROBE_NOT_EXIST_" + rand()))

    # S13 getParentList 结构
    r = requests.post(f"{BASE}/park-energy-electric-web/electricMeter/getParentList",
                      json={"pageNum": 1, "pageSize": 10}, headers=H, verify=False, timeout=30).json()
    rc = r.get("retCode")
    d = r.get("data")
    if isinstance(d, list) and d:
        print("[S13_getParentList] retCode=", rc, "data类型=list 元素数=", len(d))
        print("[S13] 首元素键:", list(d[0].keys())[:20])
        print("[S13] 首元素code:", d[0].get("code"), "name:", d[0].get("name"))
    else:
        print("[S13_getParentList] retCode=", rc, "data=", str(d)[:300])

    # S14 非法页码
    show("S14_页码0", getlist({"pageNum": 0, "pageSize": 10}))
    show("S14_页码-1", getlist({"pageNum": -1, "pageSize": 10}))

    # S15 getList 过滤 code（确认 data 是列表 + 过滤行为）
    r = getlist({"code": c0, "pageNum": 1, "pageSize": 10})
    d = r.get("data")
    if isinstance(d, list):
        print("[S15_getList过滤] retCode=", r.get("retCode"), "data列表元素数=", len(d),
              "code命中:", [x.get("code") for x in d])
    else:
        print("[S15_getList过滤] data=", str(d)[:200])

    print("\n=== 创建的电表 code（供清理）===")
    for c in created:
        print("  ", c)


if __name__ == "__main__":
    probe()
