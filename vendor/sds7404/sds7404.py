"""鼎阳 SDS7404A H12(12-bit, 4 GHz, 20 GSa/s, 4 通道)远程控制驱动。

仅依赖 pyvisa,通过 LAN(VXI-11 / TCPIP)与示波器通信。

参考资料
---------
- Siglent SDS Series Programming Guide EN11G(SDS800XHD / SDS6000A / SDS7000A 通用)
- Siglent 官方 :WAVeform: 命令族;PREamble 为 346 字节固定结构二进制块
- 字节偏移与 JoshGenao/SiglentSDS2000xPlusPy 项目实测一致(同一代 HD 协议)

注意:本驱动只暴露『读取多通道波形』需要的最小命令集,刻意不做更大的封装。
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import numpy as np
import pyvisa


CHANNELS = ("C1", "C2", "C3", "C4")
PREAMBLE_HEADER_LEN = 11               # "#9" + 9 位十进制长度
PREAMBLE_PAYLOAD_LEN = 346             # SDS 新一代 HD 协议固定长度
DEFAULT_TIMEOUT_MS = 20_000            # 长存储深度读取可能耗时数秒
DEFAULT_CHUNK_POINTS = 10_000_000      # 单次 :WAV:DATA? 最多取回的点数
SETTLE_AFTER_STOP_S = 0.05             # STOP 后的安顿时间


@dataclass(frozen=True)
class WaveformPreamble:
    """从 :WAVeform:PREamble? 解析出的波形元数据(已做 probe 修正的物理量)。"""

    total_points: int       # 当前 SOURce 通道实际可读样本数
    vdiv: float             # V/div(已乘以探头衰减)
    voffset: float          # 垂直偏置 V(已乘以探头衰减)
    code_per_div: float     # ADC 每格码值(12-bit HD 通常 ≈ 30 * 256)
    interval: float         # 采样间隔 s(= 1 / 实际采样率)
    delay: float            # 触发时刻相对屏幕中心的时间偏移 s
    probe: float            # 探头衰减倍率

    @property
    def sample_rate(self) -> float:
        return 1.0 / self.interval


def _parse_preamble(raw: bytes) -> WaveformPreamble:
    """解析 :WAVeform:PREamble? 返回的二进制块。"""
    if len(raw) < PREAMBLE_HEADER_LEN + PREAMBLE_PAYLOAD_LEN:
        raise ValueError(f"preamble 太短:{len(raw)} 字节")
    if raw[:2] != b"#9":
        raise ValueError(f"preamble 头部不是 '#9':{raw[:2]!r}")

    p = raw[PREAMBLE_HEADER_LEN:PREAMBLE_HEADER_LEN + PREAMBLE_PAYLOAD_LEN]
    probe = struct.unpack("<f", p[328:332])[0]
    return WaveformPreamble(
        total_points=struct.unpack("<i", p[116:120])[0],
        vdiv=struct.unpack("<f", p[156:160])[0] * probe,
        voffset=struct.unpack("<f", p[160:164])[0] * probe,
        code_per_div=struct.unpack("<f", p[164:168])[0],
        interval=struct.unpack("<f", p[176:180])[0],
        delay=struct.unpack("<d", p[180:188])[0],
        probe=probe,
    )


def _codes_to_volts(codes: np.ndarray, pre: WaveformPreamble) -> np.ndarray:
    """ADC 码 → 电压(V)。"""
    return codes.astype(np.float64) * (pre.vdiv / pre.code_per_div) - pre.voffset


class SDS7404:
    """SDS7404A H12 远程控制句柄。

    典型用法::

        with SDS7404("192.168.1.50") as scope:
            frame = scope.read_channels(["C1", "C2", "C3", "C4"])
            t = frame.time_axis
            for ch, v in frame.voltages.items():
                ...
    """

    def __init__(
        self,
        host: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        resource_manager: pyvisa.ResourceManager | None = None,
        connect_retries: int = 3,
        connect_retry_delay_s: float = 2.0,
    ) -> None:
        self._rm = resource_manager or pyvisa.ResourceManager()
        resource = f"TCPIP0::{host}::inst0::INSTR"
        # 上一会话异常退出后,Siglent VXI-11 服务器有时会拒新连(VI_ERROR_RSRC_NFOUND),
        # 短暂等待后通常自行释放。
        last_err: Exception | None = None
        for _ in range(max(1, connect_retries)):
            try:
                self._scope = self._rm.open_resource(resource)
                break
            except pyvisa.errors.VisaIOError as e:
                last_err = e
                time.sleep(connect_retry_delay_s)
        else:
            raise last_err  # type: ignore[misc]
        self._scope.timeout = timeout_ms
        self._scope.chunk_size = 20 * 1024 * 1024     # 20 MB,长存储拉取必需
        self._scope.read_termination = None
        self._scope.write_termination = "\n"
        # 统一使用 little-endian、16-bit 模式 —— SDS7404A H12 是 12-bit ADC
        self._scope.write(":WAVeform:BYTeorder LSB")
        self._scope.write(":WAVeform:WIDTH WORD")

    # ---- 基本生命周期 -----------------------------------------------------

    def close(self) -> None:
        self._scope.close()

    def __enter__(self) -> "SDS7404":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def idn(self) -> str:
        return self._scope.query("*IDN?").strip()

    # ---- 采集控制 ---------------------------------------------------------

    def stop(self) -> None:
        """停止采集 —— 必须在读取多通道前调用,保证各通道来自同一帧。"""
        self._scope.write(":TRIGger:STOP")
        time.sleep(SETTLE_AFTER_STOP_S)

    def run(self, continuous: bool = True) -> None:
        """恢复采集。continuous=True 时先切回 AUTO 触发模式，保证屏幕持续刷新
        (single() 会把模式设成 SINGle，读完后若不切回就停在单次态)。"""
        if continuous:
            self._scope.write(":TRIGger:MODE AUTO")
        self._scope.write(":TRIGger:RUN")

    def single(self) -> None:
        """触发一次 single shot 并等待 STOP 状态。"""
        self._scope.write(":TRIGger:MODE SINGle")
        self._scope.write(":TRIGger:RUN")
        # 轮询直到进入 Stop 态(或 timeout)
        deadline = time.time() + self._scope.timeout / 1000.0
        while time.time() < deadline:
            if self._scope.query(":TRIGger:STATus?").strip() == "Stop":
                return
            time.sleep(0.02)
        raise TimeoutError("等待 SINGle 触发超时")

    # ---- 单通道读取 -------------------------------------------------------

    def _read_preamble(self, channel: str) -> WaveformPreamble:
        self._scope.write(f":WAVeform:SOURce {channel}")
        self._scope.write(":WAVeform:STARt 0")
        self._scope.write(":WAVeform:PREamble?")
        raw = self._scope.read_raw()
        return _parse_preamble(raw)

    def _read_codes(self, channel: str, total_points: int) -> np.ndarray:
        """分块读取一个通道的全部 ADC 码。"""
        self._scope.write(f":WAVeform:SOURce {channel}")
        self._scope.write(f":WAVeform:MAXPoint {DEFAULT_CHUNK_POINTS}")

        chunks: list[np.ndarray] = []
        start = 0
        while start < total_points:
            self._scope.write(f":WAVeform:STARt {start}")
            self._scope.write(":WAVeform:DATA?")
            raw = self._scope.read_raw()
            payload = _strip_block_header(raw)
            # 12-bit WORD 模式:每点 2 字节,signed little-endian
            chunk = np.frombuffer(payload, dtype="<i2")
            if chunk.size == 0:
                raise RuntimeError(f"通道 {channel} 在 start={start} 处返回 0 点")
            chunks.append(chunk)
            start += chunk.size

        return np.concatenate(chunks)[:total_points]

    # ---- 多通道读取(同一帧)---------------------------------------------

    def read_channels(self, channels: list[str] | tuple[str, ...] = CHANNELS) -> "MultiChannelFrame":
        """停止采集 → 逐路读取 → 全部转电压。所有通道来自同一帧采样。

        参数:
            channels:要读取的通道列表,例如 ("C1", "C3")。会自动跳过 OFF 通道。
        """
        for ch in channels:
            if ch not in CHANNELS:
                raise ValueError(f"未知通道 {ch};可选 {CHANNELS}")

        self.stop()

        active = [ch for ch in channels if self._is_channel_on(ch)]
        if not active:
            raise RuntimeError("没有任何指定通道处于 ON 状态")

        preambles: dict[str, WaveformPreamble] = {}
        voltages: dict[str, np.ndarray] = {}
        for ch in active:
            pre = self._read_preamble(ch)
            codes = self._read_codes(ch, pre.total_points)
            preambles[ch] = pre
            voltages[ch] = _codes_to_volts(codes, pre)

        ref = preambles[active[0]]
        # 时间轴相对『触发时刻』:t=0 在触发事件处,负值在触发之前。
        # Siglent HD 系列约定:t[i] = (i - N/2) * Δt − trigger_delay
        n = ref.total_points
        time_axis = (np.arange(n) - n / 2.0) * ref.interval - ref.delay
        return MultiChannelFrame(
            time_axis=time_axis,
            voltages=voltages,
            preambles=preambles,
            sample_rate=ref.sample_rate,
        )

    def _is_channel_on(self, channel: str) -> bool:
        # SDS7000A 用 :CHANnel<n>:SWITch? 查询通道开关(ON/OFF)
        idx = channel[-1]
        return self._scope.query(f":CHANnel{idx}:SWITch?").strip().upper() == "ON"


@dataclass(frozen=True)
class MultiChannelFrame:
    """一帧多通道波形。所有通道共享 time_axis,值已转换为伏特。"""

    time_axis: np.ndarray              # 形状 (N,),单位 s
    voltages: dict[str, np.ndarray]    # 通道名 → 电压数组 (N,),单位 V
    preambles: dict[str, WaveformPreamble]
    sample_rate: float                 # Hz

    @property
    def num_points(self) -> int:
        return int(self.time_axis.size)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _strip_block_header(raw: bytes) -> bytes:
    """剥掉 IEEE 488.2 二进制块头 `#N<N 位长度>` 与可选的尾部 \\n。"""
    if not raw or raw[0:1] != b"#":
        raise ValueError("响应缺少 '#' 块头")
    n = int(raw[1:2])
    data_len = int(raw[2:2 + n])
    start = 2 + n
    end = start + data_len
    return raw[start:end]
