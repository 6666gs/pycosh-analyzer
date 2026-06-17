# dbpd_analyzer

[English](README.en.md) | **简体中文**

**双 BPD 相关自外差（COSH）** 激光频率噪声分析的桌面 UI。PySide6 + matplotlib +
来自 Yuan et al.（*Opt. Express* **30**, 25147, 2022）的
[pycosh](https://github.com/) 参考实现。

Apple 浅色风格，跨平台（macOS / Windows / Linux），支持示波器实时采集
（Siglent SDS7404）、自动线宽提取（Lorentzian + β-separation）、MZI FSR 自校准。

---

## 功能特性

| | |
|---|---|
| **两种处理算法** | **BW 分段 · 双 BPD**（CoshXcorr 互相关，压制各 BPD 独立电学噪声）／ **多次平均 · 单 BPD（Hann 窗）**（多记录累积，逼近白噪声底） |
| **两种数据源** | **文件读取**／**从示波器读取**（LAN 连接 Siglent SDS7404）。算法与数据源在数据面板上按"算法 Tab × 数据源"两轴选择 |
| **多记录文件 I/O** | 多次平均可把采集的 N 条原始记录存进**一个 `.npz`**，之后离线重载、重新平均 |
| **多分辨率 Welch 处理** | 可编辑 BW 分段 + offset-start ratio；多频段平均得到低噪声底（仅双 BPD 算法） |
| **FSR 自校准 / 手动覆盖** | 从实际拍频信号检测 MZI 自由谱宽并反推光纤长度；也可手动输入光纤长度 ΔL / 延时 τ（**任意位数**，τ 与 ΔL 双向联动） |
| **Lorentzian 底拟合** | 在指定高偏置带内取 S<sub>ν</sub>(f) 的最小值 → FWHM<sub>L</sub> = π · S<sub>0</sub>（单边谱） |
| **β-separation 线 + 积分** | Di Domenico 2010 方法 —— 叠加 β 线并由其上方面积报告高斯 FWHM |
| **导出 / 保存** | 双 BPD：**Export spectra** 把 S<sub>ν</sub>/S<sub>φ</sub>（全部曲线 + 误差列 + 元数据头）写入单个 CSV；平均：**Save averaged spectrum** 保存平均谱 + 线宽/FSR（CSV 或 npz） |
| **实时监测**（仅双 BPD） | Acquire 后示波器自动恢复 live；"▶ Monitor (live)" 反复采集+处理单次帧，实时刷新噪声谱与 Lorentz 线宽-时间趋势，观察激光器锁定稳定性 |

---

## 两种处理算法

数据面板左上是**算法选择**，下面的数据源与参数跟随当前算法：

- **BW 分段 · 双 BPD（CoshXcorr）**：标准的相关自外差互相关法。需要两路 BPD
  （C2/C4），用多分辨率 Welch 分段，互相关压掉两路各自的电学噪声。数据源：
  - **文件读取** —— 一个 3 列文件 `t, BPD1, BPD2`（csv/npy/npz）
  - **从示波器读取** —— 同时采两个通道
- **多次平均 · 单 BPD（Hann 窗）**：单路 BPD，多次测量累积 `2·|rfft(Hann·相位)|²`，
  除以 MZI 传递函数 `G(f) = 4·sin²(πfτ)`，多次平均逼近白频率噪声底。数据源：
  - **从示波器读取** —— 连续采 N 次自动平均（可勾选"保留原始记录"以便保存）
  - **文件读取** —— 读取一个含 N 条原始记录的多记录 `.npz`，离线复算平均

> 平均算法是单条曲线、不用 BW 分段、也没有实时监测，所以切到该 Tab 时
> Segments 区、互相关/双 BPD 显示项、Monitor 与 Export 按钮都会自动隐藏。

---

## 项目结构

```
dbpd_analyzer/
├── README.md / README.en.md   主文档（中文）/ 英文链接
├── LICENSE                  MIT（本项目）+ 第三方组件声明
├── requirements.txt
├── main.py                  入口 —— Fusion style + 全局 QSS + MainWindow
├── app/
│   ├── styles.py            Apple-light QSS
│   ├── data_io.py           CSV/npy/npz 加载与保存 + 多记录文件 I/O
│   ├── averaging.py         多次平均（单 BPD Hann）：PsdAverager + 保存
│   ├── mzi_calibrate.py     FSR 自动校准（Hilbert + Welch + 谷点搜索）
│   ├── analysis.py          Lorentz 底拟合 + β-separation 积分
│   ├── scope.py             采集 Worker（采集 / 多次平均 / 文件平均 / 连接测试）
│   ├── processor.py         CoshXcorr 包装 + ProcessWorker + CalibrateWorker
│   ├── monitor.py           实时监测 Worker
│   ├── monitor_io.py        监测谱 / 趋势 落盘
│   ├── plot_widget.py       matplotlib FigureCanvas + 分析叠加层
│   ├── settings_panel.py    侧栏：data（算法×数据源）/ optical / segments / display / analysis
│   └── main_window.py       连接侧栏 ↔ 绘图 ↔ Worker 各信号
├── scripts/                 独立分析/调试脚本（非 GUI）
│   ├── measure_delay.py     延迟线 FSR / τ 测量
│   ├── process_sin_noise.py 双 BPD 实测数据 → pycosh 噪声谱
│   └── verify_pycosh.py     pycosh 数值自检（白频噪声）
├── vendor/                  外部依赖
│   └── pycosh/              MIT, Maodong Gao 2022 —— COSH 参考实现（vendored）
└── examples/                示例数据 + 说明
```

> SDS7404A 驱动**不在本仓库内**：它独立成库,经 `requirements.txt` 用 pip 从 git
> 拉取（见下方安装）。

---

## 安装

```bash
git clone <repo-url>
cd dbpd_analyzer
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**开箱即用,无需额外步骤。** `pycosh` 直接 vendor 在 `vendor/pycosh/`；`sds7404` 驱动
则由 `requirements.txt` 里的一行

```text
sds7404 @ git+https://github.com/6666gs/sds7404.git
```

在 `pip install` 时自动从 git 拉取并装进虚拟环境（连「Download ZIP」下载本项目也照样
能装）。需要锁版本时在该 URL 末尾加 `@<tag 或 commit>`。示波器连接走 **pyvisa-py**（纯
Python，随 requirements 安装），无需系统 NI-VISA。

### 高级用户覆盖

本地改驱动/上游库,不想每次重装时,指向你的开发目录：

```bash
export DBPD_PYCOSH_PARENT=/path/to/folder-containing-pycosh   # 含 pycosh/ 包的目录
export DBPD_SDS7404_PARENT=/path/to/sds7404                   # 含 sds7404.py 的仓库目录
```

覆盖只在正常 `import` 失败后才生效,所以默认安装下开箱即用。

## 运行

```bash
.venv/bin/python main.py            # macOS / Linux
.venv\Scripts\python.exe main.py    # Windows
```

---

## 工作流

### 模式 A —— 双 BPD（CoshXcorr）分析

1. 数据面板顶部选 **BW 分段 · 双 BPD**
2. 数据源选 **文件读取**（一个 3 列文件 `t, BPD1, BPD2`）或 **从示波器读取**（设 IP、
   BPD1=C2、BPD2=C4，⏺ Acquire；后台 QThread 拉取，UI 不卡死，采完自动恢复 live）
3. **Optical path**：填 n_core / AOM 载频；FSR 由数据自动校准，或勾 **Manual FSR**
   手动输入光纤长度 ΔL / τ（任意位数）
4. FSR 校准成功后**自动开始处理**；改了光路/分段后点 ▶ **Process** 重算
5. **Analysis** 卡片实时显示 Lorentz FWHM 与 β-integrated Gaussian FWHM
6. **Export spectra…** 导出含完整元数据头的单个 CSV（S<sub>ν</sub> 与 S<sub>φ</sub>、全部曲线 + 误差列）
7. **▶ Monitor (live)**（先 Acquire 一次完成 FSR 校准后）反复抓取单次帧重处理，刷新噪声谱与
   **Lorentz 线宽 vs 时间** 趋势条，确认 self-lock 激光器是否保持锁定

### 模式 B —— 多次平均（单 BPD Hann）

1. 数据面板顶部选 **多次平均 · 单 BPD (Hann)**
2. 数据源 **从示波器读取**：设 IP、BPD1 通道、平均次数 N、边缘裁剪（Edge skip）；
   勾 **保留原始记录** 可在平均后用 **Save raw records** 把 N 条原始记录存成一个 `.npz`
3. 点 ▶ **Process**（或面板内的 Acquire ×N & average）：连续采 N 次，首帧自动标定 FSR
   （或用手动 FSR），逐条累积平均
4. 数据源 **文件读取**：选一个多记录 `.npz`（之前保存的 N 条原始记录），▶ Process 离线复算平均
5. **Save averaged spectrum…** 保存平均谱 + n_avg/FSR/底噪/线宽（CSV 或 npz）

---

## 如何看噪声谱

- **互相关曲线（蓝色）** 即激光噪声估计，两个 BPD 各自独立的电学噪声已被压制
- **Lorentz 底（红虚线）** 是白色频率噪声的渐近线 → FWHM<sub>L</sub> = π · S<sub>0</sub>
- **β 线（橙色点线）**：S<sub>ν</sub> = 8 ln(2) / π² · f ≈ 0.5615 · f。曲线在此线上方贡献高斯（慢）展宽，下方贡献洛伦兹（快）。上方积分面积给出 FWHM<sub>G</sub> = √(8 ln 2 · A)（Di Domenico 2010）
- **高偏置处 n · FSR 的尖锐峰** 是 MZI 传递函数零点处的反卷积奇点（除以 `2sin²(πf/FSR)→0`），与窗函数无关；若与配置 FSR 不符会更明显 → 多半是光纤长度填错 → 点 *Auto-calibrate FSR* 或手动改 τ

---

## 测量推荐

下面是在 80 MHz AOM 自外差系统上收敛出来的配置。AOM 载频不同时按比例调整。

### A —— 采样率怎么选

当分析偏置频率接近 AOM 载频 f<sub>c</sub> 时，基于 Hilbert 的相位提取会失效。
安全分析上限大约 **f<sub>c</sub> / 2**；超过 ~f<sub>c</sub>，Hilbert 滤波器会把下边带折回正频率，结果失真。

```
sample_rate ≥ 2 · f_c          (Nyquist 绝对下限)
sample_rate ≥ 2.5 · f_c        (推荐, 给抗混叠滤波留余量)
```

对 **80 MHz AOM**：最低 **200 MSa/s**，舒适 **250 MSa/s**。更高采样率只是浪费内存。

### B —— BW_SEGMENT 怎么选（双 BPD 算法）

列表 `BW_SEGMENT = [bw₀, bw₁, …]`（Hz）定义多频段 Welch 风格 PSD 估计，每段段长 `1 / (bw · dt)`。约束：

| 量 | 约束 | 原因 |
|---|---|---|
| `bw[0]` | ≥ 5 / T | 最低频段至少 5 段平均，1σ 抖动才压到 ~45% |
| `bw[0] · ratio` | = f<sub>min</sub>（最低分析偏置） | 默认 `offset_start_ratio = 10` |
| 段间比例 | 3×（平滑）或 10×（紧凑） | 3× 段界统计跳变 ~1.7×；10× 是 ~3.2× |
| `bw[-1]` | > 2/τ（= 2 · FSR） | 最高段进入非相干极限，G(f) 不再放大单频干扰 |
| `bw[-1] · ratio` | < f<sub>c</sub> / 2 | 不要分析到 Hilbert 失效区 |

### C —— 80 MHz AOM、7–10 m 光纤的决策表

| 目标 | sample_rate | T | BW_SEGMENT (Hz) | 偏置范围 | 备注 |
|---|---:|---:|---|---:|---|
| **默认** | 1 GSa/s | 5–10 ms | `1k, 3k, 10k, 30k, 100k, 300k, 1M, 3M, 10M` | 10 kHz – 30 MHz | 当前工作配置 |
| 紧凑/快速 | 2 GSa/s | 1–5 ms | `10k, 30k, 100k, 300k, 1M, 3M, 10M, 30M` | 100 kHz – 30 MHz | 牺牲低频换更快录波 |
| 中频 | 500 MSa/s | 0.5 s | `10, 30, 100, …, 10M`（12 段） | 100 Hz – 30 MHz | 需要轻度声学隔离 |
| **低频** | **200–250 MSa/s** | **5–10 s** | `1, 3, 10, 30, 100, 300, 1k, …, 1e7`（14 段） | **10 Hz – 30 MHz** | **需 ≥ 1 GSa 内存 + 100 m 光纤 + 声学隔离** |
| 超低频 | 100–200 MSa/s | 60 s | `0.1, 0.3, 1, …, 1e6` | 1 Hz – 10 MHz | 必须多次采集平均（单次录波放不下） |

### D —— 内存换录波时长

`录波时长 = 单通道内存深度 / 采样率`

| 采样率 | 500 MSa | 1 GSa | 2 GSa |
|---:|---:|---:|---:|
| 500 MSa/s | 1.0 s | 2.0 s | 4.0 s |
| 250 MSa/s | 2.0 s | 4.0 s | 8.0 s |
| 200 MSa/s | 2.5 s | 5.0 s | 10.0 s |
| 100 MSa/s | 5.0 s | 10.0 s | 20.0 s |

对 80 MHz AOM，**200 MSa/s 是实际下限**，所以可达 T 从根本上被单通道内存所限。

### E —— 硬件能到的最低偏置

用 `bw[0] · T ≥ 5` 加 `sample_rate ≥ 200 MSa/s`：

| 内存 | 最大 T（@ 200 MSa/s）| 最低 f_min（单次录波）|
|---:|---:|---:|
| 500 MSa | 2.5 s | ≈ 20 Hz |
| 1 GSa | 5 s | ≈ 10 Hz |
| 2 GSa | 10 s | ≈ 5 Hz |

要再往下 —— 用**多次平均算法**（见上）。

---

## 低频测量的硬件清单

偏置低于 ~1 kHz 之后，这些比软件调参更重要：

- **延迟光纤长度** —— 短光纤（5–10 m）测 > 10 kHz 没问题；低频偏置需要更长 τ，否则 G(f) 把信号压到 BPD 噪声底以下：
  - 7–10 m：> 10 kHz 偏置可用
  - 100 m：低至 ~100 Hz 偏置可用（低频灵敏度 ~100×）
  - 1 km：低至 ~1 Hz 偏置可用（Yuan 2022 用 1 km）
- **声学隔离** —— 长光纤拾取环境振动，伪装成真实频率噪声：盘进泡沫/Sorbothane 盒、放隔振台、关空调/风扇/泵
- **热隔离** —— 温漂直接调制光纤延迟；双层壁封装对超长录波（> 10 s）有帮助
- **AOM 驱动干净度** —— 驱动器谐波/边带会直接进谱，用干净 RF 源

---

## 示波器连接说明

驱动用 `pyvisa` 经 LAN 连接 SDS7404，**默认走 pyvisa-py（`@py`）后端**，资源串
`TCPIP0::<ip>::inst0::INSTR`（VXI-11）。

- macOS 上系统 **NI-VISA 偶发卡死在 open_resource**（VXI-11 协商与 Siglent 实现不完全兼容），所以默认不用它；要强制用可传 `visa_backend="@ivi"` 或自带 `resource_manager`。
- 若 VXI-11 报 `VI_ERROR_RSRC_NFOUND`，多半是上次会话异常退出占住了 VXI-11 link 槽（重启示波器即可清除），或示波器 LXI/VXI-11 未启用。
- 数据面板有 **Test connection** 按钮可先验证连通（`*IDN?`）。

---

## 算法说明（pycosh）

底层数学 —— Hilbert 相位提取、多频段 Welch PSD、G(f) sinc² 补偿（论文 Eq. 22）、互相关压制 BPD 噪声（Eq. 27–28）—— 见 `vendor/pycosh/CoshXcorr.py` 与 Yuan 2022 论文。两点不直观：

- AOM 载频**不是**参数；pycosh 跳过 DC bin 隐式排除（偏置从 `offset_start_ratio · bw[0]` 开始）
- 应用显示**单边谱（SSB）**：pycosh 输出双边谱，应用乘 2 显示。该约定下 Lorentz FWHM = π · S<sub>0</sub>，β 线为 8 ln2/π² · f（Di Domenico 2010，单边）
- CoshXcorr 用**矩形窗**（原论文方法）；多次平均算法用 **Hann 窗**——两者刻意不同

---

## 协议

MIT —— 见 [LICENSE](LICENSE)。

第三方组件保留各自协议：
- `vendor/pycosh/`  MIT, Copyright (c) 2022 Maodong Gao
- `sds7404` 驱动（pip 从 git 安装）  MIT,本项目 —— <https://github.com/6666gs/sds7404>
