# -*- coding: utf-8 -*-
"""修复 _26 test_SmartPower.py 的 fixture：specification_yaml(get_testcase_yaml(...)) -> run_blocks(...)
根因：get_testcase_yaml 返回 list（多 block），specification_yaml 只收单 dict；
      run_blocks(path) 内部遍历所有 block，才是 fixture 的正确入口。"""
import io

path = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower\test_SmartPower.py'
src = io.open(path, encoding='utf-8').read()

old_call = 'base.specification_yaml(get_testcase_yaml('
n_call = src.count(old_call)
src = src.replace(old_call, 'base.run_blocks(')

# 配对括号：调用由 `xxx(get_testcase_yaml(\n 'path.yaml'))` 变为 `run_blocks(\n 'path.yaml'))`，
# 需把闭合的 .yaml')) 收成 .yaml')（run_blocks 只开了一个括号）
n_close = src.count(".yaml'))")
src = src.replace(".yaml'))", ".yaml')")

io.open(path, 'w', encoding='utf-8').write(src)

print(f'替换 fixture 调用: {n_call} 处')
print(f'闭合括号修正: {n_close} 处')
# 校验
src2 = io.open(path, encoding='utf-8').read()
print(f'残留 specification_yaml(get_testcase_yaml: {src2.count("specification_yaml(get_testcase_yaml")}')
print(f'残留 .yaml\')): {src2.count(".yaml\'))")}')
print(f'run_blocks 总数(含测试方法): {src2.count("run_blocks(")}')
