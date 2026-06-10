# 设计：数据面板按算法分 Tab + 平均算法文件 I/O

日期：2026-06-10
状态：已实现（113 项测试通过；代码审查 2 项 HIGH 已修复）

## 背景与目标

当前左侧 `DataSection` 用一个 4 选项下拉把两个**正交维度**混在一起：

- 算法：CoshXcorr（BW 分段、双 BPD 互相关）vs 多次平均（单 BPD、Hann 窗）
- 数据源：文件 vs 示波器

现有 4 模式：`MODE_SINGLE_CSV`（CoshXcorr+单文件）、`MODE_TWO_CSV`（CoshXcorr+双文件）、`MODE_ACQUIRE`（CoshXcorr+示波器）、`MODE_AVERAGE`（平均+示波器）。

目标：把界面重组成"先选算法、再选数据源"的两层结构，并简化数据源（取消单/双文件显式区分），同时为平均算法补上文件读取路径。

## 设计决策（已与用户确认）

1. **数据源只分两种**：`文件读取` / `从示波器读取`，不再区分单文件 / 双文件。
2. **CoshXcorr + 文件** = 仅单个 3 列文件（t, BPD1, BPD2）。彻底移除双文件支持。
3. **平均 + 文件** = 读取一个"多记录"文件（一个文件内含 N 条原始测量记录），逐条做平均。复用现有 `PsdAverager`，处理逻辑不变。
4. **平均 tab 隐藏无关区**：隐藏整个 `Segments` 区；隐藏 `Display` 的 BPD1/BPD2/互相关 三个勾选；隐藏底部整排 **Monitor 控件**（Monitor 按钮 + Save 勾选 + Clear + 最近帧回退组）——实时监测只属于双 BPD 算法。保留频率/相位单选 + 误差带。`Optical`(FSR) 与 `Analysis` 两算法都保留。
5. **原始记录按需保留**：默认不保留 N 条原始记录（维持现有省内存设计）；勾选"保留原始记录"后才在内存累积并启用保存。

## 模型：算法 / 数据源 双轴

```
ALGO_XCORR = "xcorr"      # BW 分段 · 双 BPD
ALGO_AVG   = "average"    # 多次平均 · 单 BPD (Hann)
SRC_FILE   = "file"
SRC_SCOPE  = "scope"
```

`DataSection` 暴露 `algorithm` 与 `source` 两个属性。为最小化对 `main_window` / `scope.py` 的改动，保留一个**兼容属性** `mode`，把 (algorithm, source) 映射回旧语义：

| algorithm | source | 兼容 `mode` | 行为 |
|---|---|---|---|
| XCORR | FILE  | `MODE_SINGLE_CSV` | 加载单 3 列文件 → 自动标定 → Process |
| XCORR | SCOPE | `MODE_ACQUIRE`    | 采双通道 → Process；可 Monitor |
| AVG   | FILE  | （新）`MODE_AVERAGE_FILE` | 加载多记录文件 → 平均 |
| AVG   | SCOPE | `MODE_AVERAGE`    | 采 N 次 → 平均 |

`MODE_TWO_CSV` 删除。Monitor 门控仍是 `mode == MODE_ACQUIRE`，即 (XCORR, SCOPE)，不变。

## UI 布局（DataSection 卡片内）

自上而下三层：

1. **算法 Tab 条**：两个分段按钮 `BW 分段 · 双 BPD` / `多次平均 · 单 BPD (Hann)`，默认前者。样式与现有 `sectionCard` 协调（QPushButton checkable 或自绘分段控件，单选互斥）。
2. **数据源切换**：`文件读取` / `从示波器读取` 两个分段按钮。
3. **内容区**：`QStackedWidget`，4 个面板：
   - **XCORR + FILE**：单文件选择行（Browse + 只读路径，占位符 "BPD1+BPD2 单文件 (t,BPD1,BPD2)"）。
   - **XCORR + SCOPE**：Scope IP、BPD1 通道、BPD2 通道、Send SINGle、Test connection、Acquire、Save acquired。（= 现有 scope 面板双通道形态）
   - **AVG + FILE**：多记录文件选择行 + Edge skip + Show convergence + Save averaged spectrum。
   - **AVG + SCOPE**：Scope IP、BPD1 通道、Average N、Edge skip、Show convergence、`保留原始记录` 勾选、Acquire ×N & average、Save averaged spectrum、`保存原始记录 (N 合 1)`（仅勾选保留后启用）。

切换算法 tab 时发 `algorithmChanged` 信号；`SettingsPanel` 据此切换 `Segments` / `Display` 子项 / 底部 Monitor 控件的可见性（平均算法下全部隐藏）。切换数据源时只换 `QStackedWidget` 页。

## 平均算法的多记录文件 I/O（唯一新增能力）

### 文件格式
新增 `app/data_io.py`（或 `averaging.py`）函数：
- `save_records(path, records: np.ndarray, sample_rate: float)`：`records` 形状 `(N, n_samples)` 单 BPD。写 `.npz`（`records`, `sample_rate`, `n_skip` 可选）。
- `load_records(path) -> tuple[np.ndarray, float]`：返回 `(records, sample_rate)`；逐行可惰性取用。

### 保存端（AVG + SCOPE）
- `AverageAcquireWorker` 增加 `keep_raw: bool` 参数。仅当为真时把每条原始记录追加进列表，结束时随结果一并返回。
- "保留原始记录"勾选 → 传 `keep_raw=True`；完成后启用"保存原始记录 (N 合 1)"按钮 → `save_records`。

### 读取端（AVG + FILE）
- 新增 `AverageFileWorker(path, fsr|None, n_skip, fmax, with_convergence, n_core)`：在线程里 `load_records` → 逐条 `PsdAverager.add()` →（首条 `_auto_fsr` 标定，除非手动）→ 复用现有 `_on_average_ok` 渲染 `render_averaged`。
- 由底部 **Process** 按钮触发（AVG+FILE 时 `_start_process` 路由到该 worker）。

## 受影响文件

- `app/settings_panel.py`：`DataSection` 重写为双轴 + tab；新增 `algorithm`/`source`/`algorithmChanged`；`SettingsPanel` 按算法切换区域可见性；`snapshot()` 增加 algorithm/source。
- `app/main_window.py`：`_on_file_changed`、`_start_acquire`、`_start_process` 按新 `mode` 路由；新增 AVG+FILE 路径；删 `load_two_records` 数据路径与双文件拖放分支。
- `app/scope.py`：`AverageAcquireWorker` 加 `keep_raw`；新增 `AverageFileWorker`。
- `app/data_io.py` 或 `app/averaging.py`：`save_records` / `load_records`。
- 测试：`tests/test_gui.py`、`tests/test_ui_inputs.py`、`tests/test_scope.py` 更新模式引用；新增多记录 I/O 与 AVG+FILE 用例。

## 测试计划

- 单元：`save_records`/`load_records` 往返；`AverageFileWorker` 用合成多记录文件得到与 `average_records` 一致的结果。
- GUI：算法 tab 切换正确隐藏/显示 Segments 与 Display 子项；数据源切换换页；Process/Monitor 门控在四种组合下正确。
- 回归：删除双文件后，原单文件 / 示波器 / 平均-示波器 路径行为不变。

## 不在范围内

- CoshXcorr 窗函数（保持矩形，见 [[coshxcorr-uses-rectangular-window]]）。
- MZI 谐振峰掩膜。
- Monitor 实时监测逻辑。
