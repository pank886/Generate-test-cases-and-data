# -*- coding: utf-8 -*-
"""测试候选后端 host：先登录，再调业务接口"""
import json, requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

login_payload = {
    "identityCode": "", "name": "admin",
    "password": "889d0039326ce09aa2ae27401967411f",
    "randomStr": "", "yqAppCode": "test",
}
login_headers = {"Content-Type": "application/json;charset=UTF-8"}

candidates = [
    "http://192.168.1.201:8041",   # config 注释掉的后端
    "http://192.168.1.55",          # config 最后一行
    "https://dev.damaiiot.com:40443", # 当前前端(对照)
]

for base in candidates:
    print('=' * 60)
    print(f'候选 host: {base}')
    try:
        r = requests.post(f"{base}/park-base-auth/login", json=login_payload,
                          headers=login_headers, verify=False, timeout=8)
        ok = r.status_code == 200 and 'token' in r.text
        token = ''
        try:
            token = r.json().get('data', {}).get('token', '')
        except Exception:
            pass
        print(f'  登录: status={r.status_code}, 有token={bool(token)}, body={r.text[:120]!r}')
        if not token:
            continue
        hdrs = {"Content-Type": "application/json;charset=UTF-8", "token": token, "yq-app-code": "test"}
        body = {"code": "DIAG_001", "name": "诊断表", "meterTypeCode": "1", "electricity": "0",
                "leasingEntity": "1", "whetherToCount": True, "useType": "2", "yqAppCode": "YQ001"}
        b = requests.post(f"{base}/electricMeter/add", json=body, headers=hdrs, verify=False, timeout=8)
        print(f'  业务 POST /electricMeter/add: status={b.status_code}, body={b.text[:150]!r}')
    except Exception as e:
        print(f'  异常: {type(e).__name__}: {e}')
