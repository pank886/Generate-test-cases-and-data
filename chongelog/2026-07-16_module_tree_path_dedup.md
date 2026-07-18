# 变更计划：输出目录按模块树结构 + 智能合并

## 基本信息

| 项目 | 内容 |
|:---|:---|
| 变更日期 | 2026-07-16 |
| 变更类型 | 功能增强 |
| 影响范围 | Excel 输出路径生成 |

---

## 1. 为什么改

当前输出目录用时间戳区分每次生成：

```
TESTCASE_BASE/园区基线_20260716_140000/
TESTCASE_BASE/园区基线_20260716_150000/
```

用户无法快速区分哪个是健身房管理的、哪个是设施管理的。尤其在「模块有改动补测」「模块重构重测」场景下，需要人工翻阅 Excel 才能确认。

---

## 2. 方案：模块树路径 + 智能合并

### 输出路径格式

```
TESTCASE_BASE/<父节点>/<当前模块>/    ← 首次输出
TESTCASE_BASE/<父节点>/<当前模块>_2/  ← 第二次输出同一路径
TESTCASE_BASE/<父节点>/<当前模块>_3/  ← 第三次
```

### 合并规则（仅两条）

**规则 1：路径不存在或为空 → 直接写入**

```
TESTCASE_BASE/园区基线/健身房管理/        ← 不存在 → 创建
TESTCASE_BASE/园区基线/健身房管理/        ← 存在但空目录 → 复用
```

**规则 2：路径有内容 → 加编号**

```
TESTCASE_BASE/园区基线/健身房管理/        ← 已有 test_plan.xlsx
TESTCASE_BASE/园区基线/健身房管理_2/      ← 新输出到这里
TESTCASE_BASE/园区基线/健身房管理_3/      ← _2 也有了就试 _3
```

> "有内容" = 目录下存在任何非空文件。仅检查目标目录本身，不递归。

### 编号策略

- 从 `_2` 开始，递增查重，找到第一个不存在的（或可用的空目录）
- 空目录优先复用：如果 `_3` 目录存在但为空，直接复用 `_3`，不跳到 `_4`
- 上限为 `_999`，超过则退回到时间戳方案兜底

---

## 3. 目录结构示例

### 首次：两个模块各自初版

```
TESTCASE_BASE/
├── 园区基线/
│   ├── 健身房管理/
│   │   └── test_plan.xlsx       ← 初次生成
│   └── 设施管理/
│       └── test_plan.xlsx
└── 集团平台/
    └── 门禁签到/
        └── test_plan.xlsx
```

### 第二次：健身房管理有改动，重新生成

```
TESTCASE_BASE/
├── 园区基线/
│   ├── 健身房管理/              ← 上次的，不动
│   │   └── test_plan.xlsx
│   ├── 健身房管理_2/            ← 这次新生成的
│   │   └── test_plan.xlsx
│   └── 设施管理/
│       └── test_plan.xlsx
```

### 第三次：健身房管理重构，再生成

```
├── 健身房管理/
├── 健身房管理_2/
├── 健身房管理_3/                ← 新
```

用户删除 `健身房管理_2/` 后，下次生成会优先复用 `_2`。

---

## 4. 实现

### 调用点

`_generate_excel_plan_node` 中，替换当前 `output_dir` 生成逻辑。

### 算法

```
def resolve_output_path(base: str, parent: str, module: str) -> str:
    dir_name = f"{parent}/{module}"
    path = os.path.join(base, dir_name)

    # 规则 1：不存在或为空 → 直接用
    if not os.path.exists(path) or is_dir_empty(path):
        os.makedirs(path, exist_ok=True)
        return path

    # 规则 2：有内容 → 加编号
    for n in range(2, 1000):
        alt = os.path.join(base, f"{dir_name}_{n}")
        if not os.path.exists(alt) or is_dir_empty(alt):
            os.makedirs(alt, exist_ok=True)
            return alt

    # 兜底：时间戳
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = os.path.join(base, f"{dir_name}_{ts}")
    os.makedirs(fallback, exist_ok=True)
    return fallback
```

---

## 5. 涉及文件

| 文件 | 改动 |
|:---|:---|
| `agent_components/nodes.py` | **改** — `_generate_excel_plan_node` 调用新逻辑 |
| `changelog/2026-07-16_module_tree_path_dedup.md` | 本文件 |

---

## 6. 不涉及

- 模块树数据（不写）
- Excel 格式（不变）
- 前端
- 旧的 testcase 目录清理（用户手动管理）
