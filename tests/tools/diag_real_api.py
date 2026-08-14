# -*- coding: utf-8 -*-
"""诊断：按当前配置解析 host，登录拿 token，实测真实业务接口"""
import sys, json, requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.path.insert(0, r'C:\Users\damai\PyCharmMiscProject')
from conf.operationConfig import OperationConfig

conf = OperationConfig()
host = conf.get_envi('host')
print(f'[1] get_envi("host") = {host!r}')

# 登录拿 token（conftest 同样逻辑）
login_url = "https://dev.damaiiot.com:40443/park-base-auth/login"
payload = {
    "identityCode": "", "name": "admin",
    "password": "889d0039326ce09aa2ae27401967411f",
    "randomStr": "", "yqAppCode": "test",
}
r = requests.post(login_url, json=payload, headers={"Content-Type": "application/json;charset=UTF-8"}, verify=False, timeout=30)
print(f'[2] 登录 {login_url}: status={r.status_code}, body={r.text[:200]}')
token = r.json().get('data', {}).get('token', '') if r.status_code == 200 else ''
print(f'[3] token = {token[:40]}...' if token else '[3] 登录未拿到 token')

# 用解析出的 host 调用业务接口
base = host
print(f'\n[4] 用解析 host={base!r} 调 /electricMeter/add (POST)')
url = f"{base}/electricMeter/add"
headers = {"Content-Type": "application/json;charset=UTF-8", "token": token, "yq-app-code": "test"}
body = {"code": "DIAG_001", "name": "诊断表", "meterTypeCode": "1", "electricity": "0",
        "leasingEntity": "1", "whetherToCount": True, "useType": "2", "yqAppCode": "YQ001"}
try:
    resp = requests.post(url, json=body, headers=headers, verify=False, timeout=30)
    print(f'    POST status={resp.status_code}, body={resp.text[:300]}')
except Exception as e:
    print(f'    POST 异常: {type(e).__name__}: {e}')

# 变体 1：GET 同一个路径
print(f'\n[5] 变体 GET {url}')
try:
    resp = requests.get(url, headers=headers, verify=False, timeout=30)
    print(f'    GET status={resp.status_code}, body={resp.text[:200]}')
except Exception as e:
    print(f'    GET 异常: {type(e).__name__}: {e}')

# 变体 2：直接打 dev host（与日志一致）
print(f'\n[6] 用日志中的 dev host 调 POST')
url2 = "https://dev.damaiiot.com:40443/electricMeter/add"
try:
    resp = requests.post(url2, json=body, headers=headers, verify=False, timeout=30)
    print(f'    POST status={resp.status_code}, body={resp.text[:200]}')
except Exception as e:
    print(f'    POST 异常: {type(e).__name__}: {e}')
