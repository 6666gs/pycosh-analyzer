# dbpd_analyzer

[English](README.md) | **简体中文**

**双 BPD 相关自外差（COSH）** 激光频率噪声分析的桌面 UI。PySide6 + matplotlib +
来自 Yuan et al.（*Opt. Express* **30**, 25147, 2022）的
[pycosh](https://github.com/) 参考实现。

Apple 浅色风格，跨平台（macOS / Windows / Linux），支持示波器实时采集
（Siglent SDS7404）、自动线宽提取（Lorentzian + β-separation）、MZI FSR 自校准。

---

## 功能特性

| | |
|---|---|
| **三种数据源** | 单 3 列 CSV · 双 CSV（每路一个文件）· 通过 LAN 从 Siglent SDS7404 实时采集 |
| **多分辨率 Welch 处理** | 可编辑 BW 分段 + offset-start ratio；多频段平均得到低噪声底 |
| **实时绘图切换** | S<sub>ν</sub> ↔ S<sub>φ</sub>、单通道 PSD vs 互相关、误差带 |
| **FSR 自动校准** | 从实际拍频信号检测 MZI 自由谱宽（Welch + Savitzky-Golay + 谷点搜索），反推光纤长度 |
| **Lorentzian 底拟合** | 在指定高偏置带内取 S<sub>ν</sub>(f) 的最小值 → FWHM<sub>L</sub> = π · S<sub>0</sub>（单边谱） |
| **β-separation 线 + 积分** | Di Domenico 2010 方法 —— 叠加 β 线并由其上方面积报告高斯 FWHM |
| **CSV 导出** | 一键将频率与相位噪声谱（全部曲线 + 误差列）同时写入单个 CSV，含完整元数据头（延迟、FSR、AOM、分段、线宽拟合） |

---

## 项目结构

```
dbpd_analyzer/
├── README.md / README.zh-CN.md
├── LICENSE                  MIT（本项目）+ 第三方组件声明
├── requirements.txt
├── main.py                  入口 —— Fusion style + 全局 QSS + MainWindow
├── app/
│   ├── __init__.py
│   ├── styles.py            Apple-light QSS
│   ├── data_io.py           CSV 加载 / 内存数组接入 / 保存
│   ├── mzi_calibrate.py     FSR 自动校准（Hilbert + Welch + 谷点搜索）
│   ├── analysis.py          Lorentz 底拟合 + β-separation 积分
│   ├── scope.py             AcquireWorker（QThread）封装 SDS7404 驱动
│   ├── processor.py         CoshXcorr 包装 + ProcessWorker + CalibrateWorker
│   ├── plot_widget.py       matplotlib FigureCanvas + 分析叠加层
│   ├── settings_panel.py    侧栏：data / optical / segments / display / analysis
│   └── main_window.py       连接侧栏 ↔ 绘图 ↔ Worker 各信号
├── vendor/                  vendored 第三方依赖（详见 vendor/README.md）
│   ├── pycosh/              MIT, Maodong Gao 2022 —— COSH 参考实现
│   └── sds7404/             Siglent SDS7404A LAN 驱动
└── examples/
    ├── sample_data.csv      ~4 MB, 100k 点 × 250 MSa/s × 3 列
    ├── make_sample_data.py  从更长源 CSV 可复现生成
    └── README.md            如何加载 + 该样本专用的 BW_SEGMENT 建议
```

---

## 安装

```bash
git clone <repo-url>
cd dbpd_analyzer
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

就这些 —— `pycosh` 和 `sds7404` 驱动已 vendor 在 `vendor/` 下，无需额外路径
配置、无需 clone 外部仓库。应用在导入时会把 `vendor/` 加入 `sys.path`。

### 高级用户覆盖

如果想指向某个外部开发分支（比如跟踪上游 pycosh 的开发版本）：

```bash
export DBPD_PYCOSH_PARENT=/path/to/folder-containing-pycosh
export DBPD_SDS7404_PARENT=/path/to/folder-containing-sds7404
```

环境变量覆盖只在 `vendor/` 内的拷贝导入失败后才生效，所以全新克隆开箱即用。

## 运行

```bash
.venv/bin/python main.py            # macOS / Linux
.venv\Scripts\python.exe main.py    # Windows
```

## 试跑示例数据

```bash
# 启动 GUI 后:
# 1. 侧栏模式 → "Single CSV (3 columns: t, BPD1, BPD2)"
# 2. Browse → examples/sample_data.csv
# 3. Segments → 把 Bandwidth bins 改为:  10, 30, 100, 300, 1000, 3000, 10000
#    (示例数据仅 400 µs, 默认的 1 kHz 段长不够 —— 详见 examples/README.md)
# 4. ▶ Process
```

---

## 工作流

### 模式 A —— 分析已有 CSV

1. 侧栏 → **Data** → 选 `Single CSV` 或 `Two CSVs` → Browse
2. **Optical path** → 填入延迟长度 / n_core / AOM 载频（或加载数据后点击 *Auto-calibrate FSR*）
3. **Segments** → 留默认值，或参考下方推荐表
4. FSR 校准成功后**自动开始处理**；修改光路或分段设置后，点击 ▶ **Process** 重新运行
5. **Analysis** 卡片实时显示 Lorentz FWHM 和 β-integrated Gaussian FWHM；调整拟合 / 积分区间可细化
6. **Export spectra…** 导出含完整元数据头的单个 CSV，同时包含 S<sub>ν</sub> 与 S<sub>φ</sub>（全部曲线 + 误差列）

### 模式 B —— 从示波器实时采集

1. 侧栏 → **Data** → 模式 `Acquire from oscilloscope (SDS7404)`
2. 设置示波器 IP、BPD1 通道（默认 **C2**）、BPD2 通道（默认 **C4**）
3. ✓ 勾选 *Send SINGle trigger before reading* 触发新一次采集；不勾则直接读屏上当前帧
4. ⏺ **Acquire from scope** → 后台 QThread 拉取数据，UI 不卡死
5. （可选）**Save acquired CSV…** 把原始波形落盘
6. 接续模式 A 的第 3 步

---

## 如何看噪声谱

- **互相关曲线（蓝色）** 即激光噪声估计，两个 BPD 各自独立的电学噪声已被压制
- **Lorentz 底（红虚线）** 是白色频率噪声的渐近线 → FWHM<sub>L</sub> = π · S<sub>0</sub>
- **β 线（橙色点线）**：S<sub>ν</sub> = 8 ln(2) / π² · f ≈ 0.5615 · f。曲线在此线上方的部分贡献高斯（慢）线型展宽，下方的部分贡献洛伦兹（快）线型。线上方的积分面积给出 FWHM<sub>G</sub> = √(8 ln 2 · A)（Di Domenico 2010）
- **高偏置处 n · FSR 的尖锐峰** 是当实际 FSR 与配置不匹配时 G(f) 补偿留下的伪结构 —— 校准正确时它们应该并入 Lorentz 底；如果能看见明显的峰，多半是光纤长度填错了 → 点击 *Auto-calibrate FSR*

---

## 测量推荐

下面是我们在 80 MHz AOM 自外差系统上收敛出来的配置。如果你的 AOM 载频不同，按比例调整数值即可。

### A —— 采样率怎么选

当分析偏置频率接近 AOM 载频 f<sub>c</sub> 时，基于 Hilbert 的相位提取会失效。
安全分析上限大约为 **f<sub>c</sub> / 2**（边带不交叠混叠）；超过 ~f<sub>c</sub>，
Hilbert 滤波器会把下边带折回正频率，结果完全失真。

```
sample_rate ≥ 2 · f_c          (Nyquist 绝对下限)
sample_rate ≥ 2.5 · f_c        (推荐, 给抗混叠滤波留余量)
```

对 **80 MHz AOM**：最低 **200 MSa/s**，舒适 **250 MSa/s**。更高采样率只是浪费内存。

### B —— BW_SEGMENT 怎么选

列表 `BW_SEGMENT = [bw₀, bw₁, …]`（Hz 为单位）定义了多频段 Welch 风格的 PSD 估计。
每个频段使用 `1 / (bw · dt)` 的段长。约束：

| 量 | 约束 | 原因 |
|---|---|---|
| `bw[0]` | ≥ 5 / T | 最低频段至少要 5 段平均，1σ 抖动才能压到 ~45% |
| `bw[0] · ratio` | = f<sub>min</sub>（最低分析偏置） | 默认 `offset_start_ratio = 10` |
| 段间比例 | 3×（平滑）或 10×（紧凑） | 3× 段界统计跳变 ~1.7×；10× 是 ~3.2× |
| `bw[-1]` | > 2/τ（= 2 · FSR） | 最高段进入非相干极限，G(f) 不再放大单频干扰 |
| `bw[-1] · ratio` | < f<sub>c</sub> / 2 | 不要分析到 Hilbert 失效区 |

### C —— 80 MHz AOM、7–10 m 光纤的决策表

| 目标 | sample_rate | T | BW_SEGMENT (Hz) | 偏置范围 | 备注 |
|---|---:|---:|---|---:|---|
| **默认** | 1 GSa/s | 5–10 ms | `1k, 3k, 10k, 30k, 100k, 300k, 1M, 3M, 10M` | 10 kHz – 30 MHz | 当前工作配置 |
| 紧凑/快速 | 2 GSa/s | 1–5 ms | `10k, 30k, 100k, 300k, 1M, 3M, 10M, 30M` | 100 kHz – 30 MHz | 牺牲低频换更快的录波 |
| 中频 | 500 MSa/s | 0.5 s | `10, 30, 100, …, 10M`（12 段） | 100 Hz – 30 MHz | 需要轻度声学隔离 |
| **低频** | **200–250 MSa/s** | **5–10 s** | `1, 3, 10, 30, 100, 300, 1k, …, 1e7`（14 段） | **10 Hz – 30 MHz** | **需要 ≥ 1 GSa 示波器内存 + 100 m 光纤 + 声学隔离** |
| 超低频 | 100–200 MSa/s | 60 s | `0.1, 0.3, 1, …, 1e6` | 1 Hz – 10 MHz | 必须多次采集平均（单次录波放不进示波器内存） |

### D —— 内存换录波时长

`录波时长 = 单通道内存深度 / 采样率`

| 采样率 | 500 MSa 示波器 | 1 GSa 示波器（选件） | 2 GSa 示波器（选件） |
|---:|---:|---:|---:|
| 500 MSa/s | 1.0 s | 2.0 s | 4.0 s |
| 250 MSa/s | 2.0 s | 4.0 s | 8.0 s |
| 200 MSa/s | 2.5 s | 5.0 s | 10.0 s |
| 100 MSa/s | 5.0 s | 10.0 s | 20.0 s |

对于 80 MHz AOM，**200 MSa/s 是实际下限**（再低 AOM 载频就贴上 Nyquist 边沿了），
所以可达 T 从根本上被单通道内存所限。

### E —— 硬件能到的最低偏置

用 `bw[0] · T ≥ 5` 加 `sample_rate ≥ 200 MSa/s`：

| 内存 | 最大 T（@ 200 MSa/s）| 最低 f_min（单次录波）|
|---:|---:|---:|
| 500 MSa | 2.5 s | ≈ 20 Hz |
| 1 GSa | 5 s | ≈ 10 Hz |
| 2 GSa | 10 s | ≈ 5 Hz |

要再往下 —— **必须用多次采集平均**（详见下文）。

---

## 低频测量的硬件清单

偏置低于 ~1 kHz 之后，这些比软件调参更重要：

- **延迟光纤长度** —— 短光纤（5–10 m）测 > 10 kHz 没问题，但低频偏置需要更长的 τ，
  否则 G(f) 会把信号压到 BPD 噪声底以下：
  - 7–10 m：> 10 kHz 偏置可用
  - 100 m：低至 ~100 Hz 偏置可用（低频灵敏度提升约 100×）
  - 1 km：低至 ~1 Hz 偏置可用（Yuan 2022 用的就是 1 km）
- **声学隔离** —— 长光纤会拾取环境振动，伪装成真实的频率噪声：
  - 把光纤盘起来放进泡沫/Sorbothane 衬里的盒子
  - 放在隔振光学桌上
  - 测量时关掉空调 / 风扇 / 抽气泵
- **热隔离** —— 温度漂移直接调制光纤延迟；双层壁封装对超长录波（> 10 s）有帮助
- **AOM 驱动器干净度** —— 80 MHz AOM 驱动器的谐波或边带会直接出现在噪声谱里；用干净的 RF 源

---

## 待开发功能 —— 多次采集平均

当目标偏置低到单次录波装不下（1 GSa 内存下 < ~10 Hz），计划的方案是采集
**N 次独立录波连续叠加**，平均它们的互相关 PSD：

```
单次录波统计:    bw₀ · T → M 段,  1σ = 1/√M
N 次平均后:       N · M 段,        1σ = 1/√(N · M)
```

所以 10 次 × 2 s × bw = 1 Hz 给出 20 段平均 —— 噪声底跟单次 20 秒录波一样，
**但不需要 4 GSa 内存的示波器**。

### 大致形态

侧栏 Acquire 面板会加一个 **Average N captures** 数字框（默认 1）。当 N > 1 时
worker 循环：

```
for i in 1..N:
    scope.single()              # 新触发
    frame_i = scope.read_channels(...)
    result_i = pycosh.process(frame_i)
    psd12_accum += result_i.psd12 / N
    progress(i / N)
```

总耗时 ≈ N · (T + 传输开销)。1 GSa 数据 + Gb-LAN 传输大约 10–20 秒每次，
所以 10 次约 5–10 分钟，100 次约 30–60 分钟。

### 当前未实现

单次采集已经覆盖 1 GSa 内存下 ≥ 10 Hz 偏置的所有需求，对当前工作够用。
等真的需要 ≤ 1 Hz 偏置测量时再实现。

---

## 算法说明（pycosh）

底层数学 —— Hilbert 相位提取、多频段 Welch PSD、G(f) sinc² 补偿（论文 Eq. 22）、
互相关压制 BPD 噪声（论文 Eq. 27–28）—— 请看 `pycosh/CoshXcorr.py` 和 Yuan 2022 论文。
两个不太直观的点：

- AOM 载频**不是**一个参数；pycosh 通过跳过 DC bin 隐式排除掉它（偏置从
  `offset_start_ratio · bw[0]` 开始）
- 应用显示**单边谱（SSB）**功率谱密度：pycosh 输出双边谱，应用将其乘 2 用于显示。
  在该约定下 Lorentz FWHM = π · S<sub>0</sub>，β 线为 8 ln2/π² · f（Di Domenico 2010，单边）

算法本身已通过对白噪声合成激光的数值仿真验证 —— 见姐妹仓库的
[verify_pycosh.py](../sds7404/verify_pycosh.py)。

---

## 协议

MIT —— 见 [LICENSE](LICENSE)。

vendor 内的第三方组件保留各自协议：
- `vendor/pycosh/`  MIT, Copyright (c) 2022 Maodong Gao
- `vendor/sds7404/`  MIT（本项目）
