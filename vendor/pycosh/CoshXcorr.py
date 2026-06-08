from .CoshConfig import CoshConfig
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import scipy.signal
import matplotlib.pyplot as plt
import threading
import time


# Cap on the number of frequency bands processed concurrently on the CPU.
# numpy's FFT releases the GIL, so a handful of threads scale across cores.
_MAX_BAND_THREADS = 8
# Segments per GPU sub-batch (both channels stacked → 2× rows per FFT call).
# Bounds GPU memory for bands with many short segments.
_GPU_SUB_BATCH = 256


class CoshXcorr(object):
    def __init__(self, trace1=None, trace2=None, config: CoshConfig = CoshConfig()):
        self.config = config
        if trace1 is None:
            trace1 = [0, 0, 0]
        if trace2 is None:
            trace2 = trace1
        self.trace1 = trace1
        self.trace2 = trace2

        # Following data will be updated after calling method process()
        self.phasechange1 = None
        self.phasechange2 = None
        self.psd11 = None
        self.psd11_err = None
        self.psd22 = None
        self.psd22_err = None
        self.psd12 = None
        self.psd12_err = None
        # freq_list / freq_filter are normally derived from the config, but the
        # processing routines below rebuild them to cover only the bands that
        # actually had enough data — these caches hold that rebuilt version.
        self._freq_list: np.ndarray | None = None
        self._freq_filter: np.ndarray | None = None

    @property
    def freq_list(self):
        if self._freq_list is not None:
            return self._freq_list
        freq_list = np.array([])
        for x in self.config.offset_freq_list:
            freq_list = np.append(freq_list, x)
        return freq_list

    @property
    def freq_filter(self):
        if self._freq_filter is not None:
            return self._freq_filter
        freq_filter = np.array([])
        for x in self.config.offset_freq_filter_list:
            freq_filter = np.append(freq_filter, x)
        return freq_filter

    # ------------------------------------------------------------------
    # Shared band geometry
    # ------------------------------------------------------------------
    def _band_plan(self, n_samples, print_progress):
        """Return [(ii, bw, seg_len, seg_cnt, offset_pos), ...] for every band
        that fits in ``n_samples`` phase-change points. Bands too short for one
        full segment are skipped (instead of producing NaNs)."""
        band_args = []
        for ii, bw in enumerate(self.config.bw_segment[:-1]):
            seg_len = int(np.round(1 / (bw * self.config.time_unit)))
            seg_cnt = int(np.floor(n_samples / seg_len)) if seg_len else 0
            if seg_cnt < 1:
                if print_progress:
                    print(f"  Band {ii} (bw={bw:g} Hz) skipped: data too short "
                          f"(need {seg_len} samples, have {n_samples}).")
                continue
            bw_next = self.config.bw_segment[ii + 1]
            offset_pos = list(range(
                self.config.offset_start_ratio,
                int(np.round(self.config.offset_start_ratio * bw_next / bw))))
            if not offset_pos:
                continue
            band_args.append((ii, bw, seg_len, seg_cnt, offset_pos))
        return band_args

    def _store_bands(self, results_by_idx):
        """Concatenate per-band results (in band order) into the psd_* arrays
        and rebuild freq_list / freq_filter to match the processed bands."""
        processed = sorted(results_by_idx.keys())
        for key in ('psd11', 'psd11_err', 'psd22', 'psd22_err', 'psd12', 'psd12_err'):
            setattr(self, key, np.concatenate([results_by_idx[ii][key] for ii in processed])
                    if processed else np.array([]))
        self._freq_list = np.concatenate(
            [np.array(self.config.offset_freq_list[ii]) for ii in processed]
        ) if processed else np.array([])
        self._freq_filter = np.concatenate(
            [np.array(self.config.offset_freq_filter_list[ii]) for ii in processed]
        ) if processed else np.array([])

    # ------------------------------------------------------------------
    # CPU path (parallel)
    # ------------------------------------------------------------------
    def process(self, hilbert=scipy.signal.hilbert, fft=np.fft.fft, print_progress=True):
        """Parallel CPU processing. Numerically identical to the original
        serial implementation; the Hilbert transforms run on two threads and
        the frequency bands on a small thread pool (numpy FFT releases the
        GIL, so this scales across cores)."""
        if print_progress:
            self.config.print_config()
        t_process_start = time.time()
        trace1 = np.asarray(self.trace1[self.config.range_start:self.config.range_stop],
                            dtype=np.float64)
        trace2 = np.asarray(self.trace2[self.config.range_start:self.config.range_stop],
                            dtype=np.float64)

        t_start = time.time()
        if print_progress:
            print("Calculating phase change using Hilbert Transformation...")

        def _hilbert_phase(tr):
            return np.mod(np.diff(np.angle(hilbert(tr - np.mean(tr)))), 2 * np.pi)

        # Autocorrelation (identical traces) → compute once. Cross-correlation
        # → two threads in parallel.
        if np.array_equal(trace1, trace2):
            phasechange1 = _hilbert_phase(trace1)
            phasechange2 = phasechange1.copy()
        else:
            out: dict = {}
            t1 = threading.Thread(target=lambda: out.__setitem__('1', _hilbert_phase(trace1)))
            t2 = threading.Thread(target=lambda: out.__setitem__('2', _hilbert_phase(trace2)))
            t1.start(); t2.start(); t1.join(); t2.join()
            phasechange1, phasechange2 = out['1'], out['2']

        self.phasechange1 = phasechange1
        self.phasechange2 = phasechange2
        if print_progress:
            print(f"Hilbert Transformation finished in {time.time() - t_start:.3f} second(s).")

        band_args = self._band_plan(len(phasechange1), print_progress)
        if not band_args:
            raise RuntimeError(
                "No frequency bands can be processed: the data is too short for "
                "the smallest configured bandwidth. Increase the recording "
                "duration or raise bw_segment[0].")

        scale_base = 1.0 / np.power(2 * np.pi * self.config.time_unit, 2)

        def _compute_band(ii, bw, seg_len, seg_cnt, offset_pos):
            t0 = time.time()
            n = seg_cnt * seg_len
            pc1seg = phasechange1[:n].reshape((seg_cnt, seg_len))
            pc2seg = phasechange2[:n].reshape((seg_cnt, seg_len))
            pc1f = fft(pc1seg) / seg_len
            pc2f = fft(pc2seg) / seg_len
            scale = scale_base / bw
            cor11 = pc1f[:, offset_pos] * np.conj(pc1f[:, offset_pos]) * scale
            cor22 = pc2f[:, offset_pos] * np.conj(pc2f[:, offset_pos]) * scale
            cor12 = pc1f[:, offset_pos] * np.conj(pc2f[:, offset_pos]) * scale
            return {
                'ii': ii,
                'psd11': np.mean(cor11, axis=0),
                'psd11_err': np.std(cor11, axis=0) / np.sqrt(seg_cnt),
                'psd22': np.mean(cor22, axis=0),
                'psd22_err': np.std(cor22, axis=0) / np.sqrt(seg_cnt),
                'psd12': np.mean(cor12, axis=0),
                'psd12_err': np.std(cor12, axis=0) / np.sqrt(seg_cnt),
                'elapsed': time.time() - t0,
            }

        results_by_idx = {}
        n_workers = min(len(band_args), _MAX_BAND_THREADS)
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = [executor.submit(_compute_band, *args) for args in band_args]
            for future in as_completed(futures):
                r = future.result()
                results_by_idx[r['ii']] = r
                if print_progress:
                    print(f"  Band {r['ii']} finished in {r['elapsed']:.3f} s.")

        self._store_bands(results_by_idx)
        if print_progress:
            print(f"All data processing finished in {time.time() - t_process_start:.3f} second(s).")

    # ------------------------------------------------------------------
    # GPU path (auto device selection, CPU fallback)
    # ------------------------------------------------------------------
    def process_gpu(self, print_progress=True):
        """Run on the GPU when a CUDA (NVIDIA) or ROCm (AMD-on-Linux) device is
        available — both expose the ``torch.cuda`` API — otherwise fall back to
        the parallel CPU path.

        Apple Metal (MPS) is intentionally NOT used: ``torch.fft`` is not
        implemented on MPS and silently runs on CPU, so it only adds
        host<->device copies with no speedup. On Apple Silicon and any machine
        without CUDA/ROCm this method therefore uses :meth:`process`."""
        try:
            import torch
        except ImportError:
            if print_progress:
                print("PyTorch not installed — using parallel CPU path.")
            return self.process(print_progress=print_progress)

        try:
            if torch.cuda.is_available():
                if print_progress:
                    print(f"Using GPU: {torch.cuda.get_device_name(0)}")
                return self._process_gpu_impl(torch, print_progress)
        except RuntimeError as exc:
            if print_progress:
                print(f"GPU error ({exc}) — falling back to CPU.")

        if print_progress:
            print("No CUDA/ROCm GPU available — using parallel CPU path.")
        return self.process(print_progress=print_progress)

    def _process_gpu_impl(self, torch, print_progress):
        """Full on-device pipeline: Hilbert + batched FFT + correlation +
        statistics on the GPU; only the final PSD vectors return to the CPU.
        Uses float32 (ample for 12-bit scope data)."""
        t_total = time.time()
        device = torch.device('cuda')

        trace1 = np.asarray(self.trace1[self.config.range_start:self.config.range_stop],
                            dtype=np.float32)
        trace2 = np.asarray(self.trace2[self.config.range_start:self.config.range_stop],
                            dtype=np.float32)

        def _gpu_hilbert(tr):
            tr = tr - tr.mean()
            X = torch.fft.fft(tr)
            n = X.shape[-1]
            X[1:n // 2] *= 2.0      # double positive frequencies
            X[n // 2:] = 0.0        # zero negative frequencies
            return torch.fft.ifft(X)

        t1 = torch.as_tensor(trace1, dtype=torch.float32, device=device)
        t2 = torch.as_tensor(trace2, dtype=torch.float32, device=device)
        if torch.equal(t1, t2):
            pc1 = torch.remainder(torch.diff(torch.angle(_gpu_hilbert(t1))), 2 * np.pi)
            pc2 = pc1.clone()
        else:
            pc1 = torch.remainder(torch.diff(torch.angle(_gpu_hilbert(t1))), 2 * np.pi)
            pc2 = torch.remainder(torch.diff(torch.angle(_gpu_hilbert(t2))), 2 * np.pi)

        self.phasechange1 = pc1.cpu().numpy()
        self.phasechange2 = pc2.cpu().numpy()
        del t1, t2
        torch.cuda.empty_cache()

        band_args = self._band_plan(int(pc1.shape[0]), print_progress)
        if not band_args:
            raise RuntimeError(
                "No frequency bands can be processed: the data is too short for "
                "the smallest configured bandwidth.")

        scale_base = 1.0 / (2 * np.pi * self.config.time_unit) ** 2
        results_by_idx = {}
        for ii, bw, seg_len, seg_cnt, offset_pos in band_args:
            t0 = time.time()
            n_used = seg_cnt * seg_len
            s1 = pc1[:n_used].view(seg_cnt, seg_len)
            s2 = pc2[:n_used].view(seg_cnt, seg_len)
            ot = torch.tensor(offset_pos, device=device, dtype=torch.long)
            n_off = len(offset_pos)
            scale = scale_base / bw
            inv_sqrt_n = 1.0 / np.sqrt(seg_cnt)

            sum11 = torch.zeros(n_off, device=device, dtype=torch.float32)
            sum22 = torch.zeros(n_off, device=device, dtype=torch.float32)
            sum12 = torch.zeros(n_off, device=device, dtype=torch.complex64)
            sq11 = torch.zeros(n_off, device=device, dtype=torch.float32)
            sq22 = torch.zeros(n_off, device=device, dtype=torch.float32)
            sq12 = torch.zeros(n_off, device=device, dtype=torch.float32)

            for start in range(0, seg_cnt, _GPU_SUB_BATCH):
                end = min(start + _GPU_SUB_BATCH, seg_cnt)
                batch_n = end - start
                stacked = torch.cat([s1[start:end], s2[start:end]], dim=0)
                fst = torch.fft.fft(stacked, dim=-1) / seg_len
                f1s = fst[:batch_n, ot]
                f2s = fst[batch_n:, ot]
                cor11 = (f1s * f1s.conj()).real * scale
                cor22 = (f2s * f2s.conj()).real * scale
                cor12 = f1s * f2s.conj() * scale
                sum11 += cor11.sum(dim=0)
                sum22 += cor22.sum(dim=0)
                sum12 += cor12.sum(dim=0)
                sq11 += (cor11 * cor11).sum(dim=0)
                sq22 += (cor22 * cor22).sum(dim=0)
                sq12 += (cor12.real ** 2 + cor12.imag ** 2).sum(dim=0)

            mean11 = sum11 / seg_cnt
            mean22 = sum22 / seg_cnt
            mean12 = torch.abs(sum12 / seg_cnt)
            var11 = (sq11 / seg_cnt - mean11 * mean11).clamp(min=0)
            var22 = (sq22 / seg_cnt - mean22 * mean22).clamp(min=0)
            var12 = (sq12 / seg_cnt - mean12 * mean12).clamp(min=0)
            results_by_idx[ii] = {
                'ii': ii,
                'psd11': mean11.cpu().numpy(),
                'psd11_err': torch.sqrt(var11).cpu().numpy() * inv_sqrt_n,
                'psd22': mean22.cpu().numpy(),
                'psd22_err': torch.sqrt(var22).cpu().numpy() * inv_sqrt_n,
                'psd12': mean12.cpu().numpy(),
                'psd12_err': torch.sqrt(var12).cpu().numpy() * inv_sqrt_n,
            }
            if print_progress:
                print(f"  Band {ii} ({seg_cnt}x{seg_len}) {n_off} pts → {time.time()-t0:.2f}s")

        self._store_bands(results_by_idx)
        del pc1, pc2
        torch.cuda.empty_cache()
        if print_progress:
            print(f"GPU processing finished in {time.time() - t_total:.3f} second(s).")

    # ------------------------------------------------------------------
    # Plotting (single-sideband convention)
    # ------------------------------------------------------------------
    def plot_SSB_freq_noise(self, freq_lim=None):
        if freq_lim is None:
            freq_lim = [np.min(self.freq_list), np.max(self.freq_list)]

        plt_index = np.logical_and(self.freq_list > np.min(freq_lim), self.freq_list < np.max(freq_lim))
        plt_freq = self.freq_list[plt_index]
        fn = np.abs(self.psd12 / self.freq_filter)[plt_index]
        fn_err = np.abs(self.psd12_err / self.freq_filter)[plt_index]
        plt.figure(figsize=(12, 6))
        plt.errorbar(plt_freq, fn, yerr=fn_err)
        plt.loglog()
        plt.fill_between(plt_freq, fn - fn_err, fn + fn_err, alpha=0.3)
        plt.xlabel('Frequency offset (Hz)')
        plt.ylabel('SSB frequency noise (Hz^2/Hz)')

    def plot_SSB_phase_noise(self, freq_lim=None):
        if freq_lim is None:
            freq_lim = [np.min(self.freq_list), np.max(self.freq_list)]

        plt_index = np.logical_and(self.freq_list > np.min(freq_lim), self.freq_list < np.max(freq_lim))
        plt_freq = self.freq_list[plt_index]
        fn = self.psd12 / self.freq_filter
        fn_err = self.psd12_err / self.freq_filter
        fpn = np.abs(fn / np.power(self.freq_list, 2))[plt_index]
        fpn_err = np.abs(fn_err / np.power(self.freq_list, 2))[plt_index]
        plt.figure(figsize=(12, 6))
        plt.errorbar(plt_freq, fpn, yerr=fpn_err)
        plt.loglog()
        plt.fill_between(plt_freq, fpn - fpn_err, fpn + fpn_err, alpha=0.3)
        plt.xlabel('Frequency offset (Hz)')
        plt.ylabel('SSB phase noise (rad^2/Hz)')
