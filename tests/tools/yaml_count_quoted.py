# -*- coding: utf-8 -*-
import re, os

files = [
 'setup_data/setup_billing_rule_management.yaml',
 'setup_data/setup_meter_management.yaml',
 'setup_data/teardown_billing_rule_management.yaml',
 'test_billing_rule_add_tou_duplicate_type_negative/test_data.yaml',
 'test_billing_rule_add_tou_positive/test_data.yaml',
 'test_billing_rule_batch_bind_positive/test_data.yaml',
 'test_billing_rule_batch_unbind_positive/test_data.yaml',
 'test_common_area_confirm_positive/test_data.yaml',
 'test_common_area_detail_page_positive/test_data.yaml',
 'test_common_area_get_company_residents_positive/test_data.yaml',
 'test_common_area_retry_generate_positive/test_data.yaml',
 'test_meter_add_tou_positive/test_data.yaml',
 'test_meter_history_page_positive/test_data.yaml',
 'test_prepaid_update_invoice_positive/test_data.yaml',
]
root = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_25\SmartPower'
tot = 0
for f in files:
    txt = open(os.path.join(root, f), encoding='utf-8').read()
    exprs = re.findall(r'\$\{[^}]*\}', txt)
    quoted = [e for e in exprs if re.search(r"['\"][^'\"]*['\"]", e)]
    tot += len(quoted)
    print(f'{len(quoted):2d}  {f}')
    for e in sorted(set(quoted)):
        print(f'      {e}')
print(f'TOTAL: {tot}')
