"""
Pulse Rate Variability (PRV) Estimation Module.

Provides standalone zero-training PRVEstimator class for edge deployment on Raspberry Pi 5.
Extracts systolic peak-to-peak interval (PPI) series from photoplethysmography (PPG) waveforms,
cleans physiological outliers and ectopic beats using a two-tier filter (envelope gating + 5-beat
running median +/- 20% deviation with linear interpolation), extracts time-domain metrics
(Mean PPI, SDPPI, RMSSD, pPPI50), resamples intervals onto a uniform 4 Hz time grid via cubic spline
interpolation, computes Welch PSD power spectra, and integrates LF [0.04, 0.15 Hz] and HF [0.15, 0.40 Hz]
spectral bands.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import scipy.integrate
import scipy.signal
from scipy.interpolate import CubicSpline

from core.preprocessing import preprocess_window
from core.morphology import detect_systolic_peaks
from core.sqi import evaluate_sqi

# NumPy 2.x safe trapezoidal integration resolver
if hasattr(np, "trapezoid"):
    _trapezoid_integrate = np.trapezoid
elif hasattr(scipy.integrate, "trapezoid"):
    _trapezoid_integrate = scipy.integrate.trapezoid
elif hasattr(scipy.integrate, "trapz"):
    _trapezoid_integrate = scipy.integrate.trapz
else:
    _trapezoid_integrate = getattr(np, "trapz")



class PRVEstimator:
    """
    Standalone zero-training Pulse Rate Variability (PRV) Estimator for Raspberry Pi 5.

    Accepts 60-second PPG buffers (7,500 samples @ 125 Hz), extracts systolic crests,
    cleans intervals, and extracts time-domain and frequency-domain autonomic metrics
    within a < 30 ms edge latency budget.
    """

    def __init__(
        self,
        fs: float = 125.0,
        window_sec: float = 60.0,
        resample_fs: float = 4.0,
        envelope_bounds_ms: Tuple[float, float] = (300.0, 1500.0),
        median_dev_tol: float = 0.20,
        median_window_len: int = 5,
        welch_nperseg: int = 128,
        lf_band: Tuple[float, float] = (0.04, 0.15),
        hf_band: Tuple[float, float] = (0.15, 0.40),
        **kwargs: Any,
    ) -> None:
        self.fs = float(fs)
        self.window_sec = float(window_sec)
        self.resample_fs = float(resample_fs)
        # Allow alias envelope_ms
        if "envelope_ms" in kwargs:
            self.envelope_bounds = (float(kwargs["envelope_ms"][0]), float(kwargs["envelope_ms"][1]))
        else:
            self.envelope_bounds = (float(envelope_bounds_ms[0]), float(envelope_bounds_ms[1]))
        self.median_dev_tol = float(median_dev_tol)
        self.median_window_len = int(median_window_len)
        self.welch_nperseg = int(welch_nperseg)
        self.lf_band = (float(lf_band[0]), float(lf_band[1]))
        self.hf_band = (float(hf_band[0]), float(hf_band[1]))

    def extract_ppi(self, signal: np.ndarray, fs: Optional[float] = None) -> np.ndarray:
        """
        Extract raw peak-to-peak interval (PPI) series in milliseconds from PPG signal.

        Parameters
        ----------
        signal : np.ndarray
            1D array of PPG signal (raw or filtered).
        fs : float, optional
            Sampling frequency in Hz (default: self.fs).

        Returns
        -------
        np.ndarray
            1D array of successive interval durations in milliseconds (length N-1).
            Returns empty array if fewer than 2 peaks detected.
        """
        fs = float(fs) if fs is not None else self.fs
        signal = np.asarray(signal, dtype=np.float64)
        if signal.ndim != 1 or len(signal) == 0 or not np.all(np.isfinite(signal)) or float(np.std(signal)) < 1e-4:
            return np.array([], dtype=np.float64)

        # Check if signal is already normalized
        if abs(float(np.mean(signal))) < 0.1 and 0.7 < float(np.std(signal)) < 1.3:
            proc_sig = signal
        else:
            prep_res = preprocess_window(signal, fs=fs)
            proc_sig = prep_res.normalized

        peaks = detect_systolic_peaks(proc_sig, fs=fs, min_distance_sec=0.25)
        if len(peaks) < 2:
            return np.array([], dtype=np.float64)

        ppi = np.diff(peaks) * (1000.0 / fs)
        # Guarantee strictly positive
        valid_mask = ppi > 0
        return np.asarray(ppi[valid_mask], dtype=np.float64)

    def filter_intervals(self, raw_ppi: np.ndarray) -> np.ndarray:
        """
        Filter ectopic and outlier intervals using a two-tier cleaning architecture:
        1. Hard physiological envelope bounds [300, 1500] ms (40 - 200 bpm).
        2. 5-beat running median +/- 20% relative deviation rejection.
        Replaces rejected beats via linear interpolation, preserving original sequence length.

        Parameters
        ----------
        raw_ppi : np.ndarray
            1D array of raw interval durations in milliseconds.

        Returns
        -------
        np.ndarray
            1D array of cleaned intervals matching length of input.
        """
        raw_ppi = np.asarray(raw_ppi, dtype=np.float64)
        n = len(raw_ppi)
        if n == 0:
            return np.array([], dtype=np.float64)

        low_env, high_env = self.envelope_bounds
        if n < 3:
            return np.clip(raw_ppi, low_env, high_env)

        # Stage 1: Hard physiological envelope gating
        mask_env = (raw_ppi >= low_env) & (raw_ppi <= high_env)

        # Stage 2: 5-beat running median on envelope-clamped series
        ppi_clamped = np.clip(raw_ppi, low_env, high_env)
        half_win = self.median_window_len // 2
        is_median_outlier = np.zeros(n, dtype=bool)

        for i in range(n):
            w_start = max(0, i - half_win)
            w_end = min(n, i + half_win + 1)
            local_median = float(np.median(ppi_clamped[w_start:w_end]))
            if local_median > 0:
                rel_dev = abs(raw_ppi[i] - local_median) / local_median
                if rel_dev > self.median_dev_tol:
                    is_median_outlier[i] = True

        valid_mask = mask_env & (~is_median_outlier)
        valid_indices = np.where(valid_mask)[0]

        if len(valid_indices) == 0:
            return np.clip(raw_ppi, low_env, high_env)
        elif len(valid_indices) == 1:
            val = float(raw_ppi[valid_indices[0]])
            return np.full_like(raw_ppi, np.clip(val, low_env, high_env))
        elif len(valid_indices) == n:
            clean_ppi = raw_ppi.copy()
        else:
            all_indices = np.arange(n)
            clean_ppi = np.interp(all_indices, valid_indices, raw_ppi[valid_indices])

        return np.clip(clean_ppi, low_env, high_env)

    def compute_time_domain(self, ppi_ms: np.ndarray) -> Dict[str, float]:
        """
        Compute standard time-domain PRV metrics: Mean PPI, SDPPI, RMSSD, pPPI50.

        Parameters
        ----------
        ppi_ms : np.ndarray
            1D array of cleaned intervals in milliseconds.

        Returns
        -------
        Dict[str, float]
            {"mean_ppi": float, "sdppi": float, "rmssd": float, "pppi50": float}
        """
        ppi_ms = np.asarray(ppi_ms, dtype=np.float64)
        n = len(ppi_ms)
        if n == 0:
            return {"mean_ppi": 0.0, "sdppi": 0.0, "rmssd": 0.0, "pppi50": 0.0}
        if n == 1:
            return {"mean_ppi": float(ppi_ms[0]), "sdppi": 0.0, "rmssd": 0.0, "pppi50": 0.0}

        mean_ppi = float(np.mean(ppi_ms))
        sdppi = float(np.std(ppi_ms, ddof=1))
        diff = np.diff(ppi_ms)
        rmssd = float(np.sqrt(np.mean(diff ** 2)))
        pppi50 = float(np.mean(np.abs(diff) > 50.0) * 100.0)

        return {
            "mean_ppi": float(mean_ppi),
            "sdppi": float(sdppi),
            "rmssd": float(rmssd),
            "pppi50": float(pppi50),
        }

    def compute_freq_domain(
        self, ppi_ms: np.ndarray, duration_sec: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Compute frequency-domain PRV metrics via 4 Hz cubic spline resampling and Welch PSD.

        Parameters
        ----------
        ppi_ms : np.ndarray
            1D array of cleaned intervals in milliseconds.
        duration_sec : float, optional
            Total duration of signal window in seconds (default: derived from intervals).

        Returns
        -------
        Dict[str, float]
            {"lf": float, "hf": float, "lf_hf_ratio": float}
        """
        ppi_ms = np.asarray(ppi_ms, dtype=np.float64)
        if len(ppi_ms) < 4:
            return {"lf": 0.0, "hf": 0.0, "lf_hf_ratio": 0.0}

        # Zero variability check (constant pulse train)
        if float(np.std(ppi_ms)) < 1e-6:
            return {"lf": 0.0, "hf": 0.0, "lf_hf_ratio": 0.0}

        # Build cumulative timestamps for beats
        t_cum = np.cumsum(ppi_ms) / 1000.0
        t_points = np.insert(t_cum, 0, 0.0)
        ppi_points = np.insert(ppi_ms, 0, ppi_ms[0])

        dur = float(duration_sec) if duration_sec is not None else float(t_cum[-1])
        dur = max(dur, float(t_cum[-1]))
        t_grid = np.arange(0.0, dur, 1.0 / self.resample_fs)

        if len(t_grid) < 8:
            return {"lf": 0.0, "hf": 0.0, "lf_hf_ratio": 0.0}

        try:
            cs = CubicSpline(t_points, ppi_points, bc_type="natural", extrapolate=True)
            resampled = cs(t_grid)
        except Exception:
            resampled = np.interp(t_grid, t_points, ppi_points)

        # Linear detrending to eliminate sub-0.04 Hz DC drift
        detrended = scipy.signal.detrend(resampled, type="linear")

        # Welch Power Spectral Density
        n_perseg = min(len(detrended), self.welch_nperseg)
        n_overlap = min(len(detrended) // 2, n_perseg // 2)
        f, psd = scipy.signal.welch(
            detrended,
            fs=self.resample_fs,
            window="hann",
            nperseg=n_perseg,
            noverlap=n_overlap,
            nfft=max(512, n_perseg),
        )

        lf_mask = (f >= self.lf_band[0]) & (f <= self.lf_band[1])
        hf_mask = (f >= self.hf_band[0]) & (f <= self.hf_band[1])

        lf = float(_trapezoid_integrate(psd[lf_mask], f[lf_mask])) if np.any(lf_mask) else 0.0
        hf = float(_trapezoid_integrate(psd[hf_mask], f[hf_mask])) if np.any(hf_mask) else 0.0

        lf = max(0.0, lf)
        hf = max(0.0, hf)
        if hf > 1e-6:
            lf_hf_ratio = float(lf / hf)
        elif lf > 0.0:
            lf_hf_ratio = float(lf / 1e-12)
        else:
            lf_hf_ratio = 0.0

        return {
            "lf": float(lf),
            "hf": float(hf),
            "lf_hf_ratio": float(lf_hf_ratio),
        }

    def estimate(self, ppg_buffer: np.ndarray, is_filtered: bool = False) -> Dict[str, Any]:
        """
        Estimate complete PRV time and frequency metrics from a 60-second PPG buffer.

        Parameters
        ----------
        ppg_buffer : np.ndarray
            1D array representing 60 seconds of PPG (e.g. 7,500 samples at 125 Hz).
        is_filtered : bool, default=False
            If True, skips bandpass filter and detrending (buffer is already preprocessed).

        Returns
        -------
        Dict[str, Any]
            10-key contract dictionary:
            mean_ppi, sdppi, rmssd, pppi50, lf, hf, lf_hf_ratio, is_valid, latency_ms, status.
        """
        t0 = time.perf_counter()
        ppg_buffer = np.asarray(ppg_buffer, dtype=np.float64)

        def _invalid(status: str) -> Dict[str, Any]:
            elapsed = (time.perf_counter() - t0) * 1000.0
            return {
                "mean_ppi": 0.0,
                "sdppi": 0.0,
                "rmssd": 0.0,
                "pppi50": 0.0,
                "lf": 0.0,
                "hf": 0.0,
                "lf_hf_ratio": 0.0,
                "is_valid": False,
                "latency_ms": round(elapsed, 2),
                "status": status,
            }

        # 1. Non-finite check
        if ppg_buffer.ndim != 1 or not np.all(np.isfinite(ppg_buffer)):
            return _invalid("INVALID_NON_FINITE")

        # 2. Buffer length validation (must have at least 10s of data)
        if len(ppg_buffer) < int(self.fs * 10.0):
            return _invalid("INVALID_SHORT_BUFFER")

        # 3. Flatline check
        std_val = float(np.std(ppg_buffer))
        ptp_val = float(np.ptp(ppg_buffer))
        if std_val < 1e-4 or ptp_val < 1e-5:
            return _invalid("INVALID_FLATLINE")

        # 4. SQI and Preprocessing
        if is_filtered:
            proc_sig = ppg_buffer
        else:
            prep_res = preprocess_window(ppg_buffer, fs=self.fs)
            proc_sig = prep_res.normalized
            dc_val = abs(float(np.mean(ppg_buffer)))
            skip_pi = dc_val < 1.0
            sqi_res = evaluate_sqi(
                ppg_buffer,
                prep_res.filtered,
                fs=self.fs,
                skip_pi=skip_pi,
                is_normalized_input=(dc_val < 5.0),
            )
            if not sqi_res.is_valid:
                return _invalid(sqi_res.status)

        # 5. Systolic crest detection
        try:
            peaks = detect_systolic_peaks(proc_sig, fs=self.fs, min_distance_sec=0.25)
        except Exception as err:
            return _invalid(f"ERROR_PEAKS: {err}")

        if len(peaks) < 4:
            return _invalid("POOR_FIDUCIALS")

        # 6. Raw PPI interval series
        raw_ppi = np.diff(peaks) * (1000.0 / self.fs)
        if len(raw_ppi) < 3:
            return _invalid("INSUFFICIENT_INTERVALS")

        # 7. Ectopic & physiological outlier filtering
        clean_ppi = self.filter_intervals(raw_ppi)

        # 8. Time-domain and Frequency-domain metrics
        time_metrics = self.compute_time_domain(clean_ppi)
        duration_sec = len(ppg_buffer) / self.fs
        freq_metrics = self.compute_freq_domain(clean_ppi, duration_sec=duration_sec)

        elapsed = (time.perf_counter() - t0) * 1000.0
        return {
            "mean_ppi": round(time_metrics["mean_ppi"], 2),
            "sdppi": round(time_metrics["sdppi"], 2),
            "rmssd": round(time_metrics["rmssd"], 2),
            "pppi50": round(time_metrics["pppi50"], 2),
            "lf": round(freq_metrics["lf"], 4),
            "hf": round(freq_metrics["hf"], 4),
            "lf_hf_ratio": round(freq_metrics["lf_hf_ratio"], 4),
            "is_valid": True,
            "latency_ms": round(elapsed, 2),
            "status": "VALID",
        }
