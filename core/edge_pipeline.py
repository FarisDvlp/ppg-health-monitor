"""
Edge Deployment Packaging Module for Raspberry Pi 5.

Provides unified edge orchestration facades:
- `PPGEdgePipeline`: Coordinates shared zero-redundancy signal conditioning,
  multi-parametric SQI validation, and parallel-ready estimation across
  `RREstimator`, `PRVEstimator`, and `BPEstimator`.
- `EdgePPGMonitor`: Standard alias for `PPGEdgePipeline`.

Architecture:
- Single-pass 4th-order zero-phase Butterworth bandpass (0.5–8.0 Hz) and Z-score normalization.
- Sub-5ms SQI early-rejection gating for corrupted/non-physiological buffers.
- Unified telemetry packet containing validity flags, SQI metrics, RR, PRV, and BP predictions.
- Strict sub-500ms timing budget on Raspberry Pi 5 (Quad-core ARM Cortex-A76 @ 2.4 GHz).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np

# Resolve module paths so RREstimator, PRVEstimator, and BPEstimator can be cleanly imported
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _subdir in ["Respiratory Rate", "HRV", "Blood Pressure", "core"]:
    _path_str = str(_REPO_ROOT / _subdir)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from core.preprocessing import preprocess_window
from core.sqi import SQIResult, evaluate_sqi
from rr_estimator import RREstimator
from prv_estimator import PRVEstimator
from bp_estimator import BPEstimator


class PPGEdgePipeline:
    """
    Unified multi-parameter physiological monitoring pipeline for edge deployment on Raspberry Pi 5.

    Coordinates signal conditioning, quality gating, and inference across:
    1. Respiratory Rate (`RREstimator`)
    2. Pulse Rate Variability (`PRVEstimator`)
    3. Blood Pressure (`BPEstimator`)
    """

    def __init__(
        self,
        fs: float = 125.0,
        use_pure_python: bool = True,
        model_dir: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the edge deployment pipeline and domain estimators.

        Parameters
        ----------
        fs : float, default=125.0
            Sampling frequency in Hz.
        use_pure_python : bool, default=True
            If True, uses pure-Python/NumPy tree evaluator for BPEstimator (zero xgboost dependency).
        model_dir : Optional[Union[str, Path]], default=None
            Path to serialized model directory containing JSON models.
        kwargs : Any
            Additional options passed to BPEstimator.
        """
        self.fs = float(fs)
        self.use_pure_python = bool(use_pure_python)
        self.rr_estimator = RREstimator(fs=self.fs)
        self.prv_estimator = PRVEstimator(fs=self.fs)
        self.bp_estimator = BPEstimator(
            fs=self.fs,
            use_pure_python=self.use_pure_python,
            model_dir=model_dir,
            **kwargs,
        )

    def warmup(self, duration_sec: float = 60.0) -> Dict[str, Any]:
        """
        Execute a single synthetic warm-up cycle to pre-allocate memory and initialize caches.

        Returns
        -------
        Dict[str, Any]
            Telemetry dictionary from the warm-up window.
        """
        try:
            from e2e_tests.test_helpers import SignalGenerator
            synth, _ = SignalGenerator.generate_synthetic_ppg(duration_sec=duration_sec, fs=self.fs)
        except Exception:
            n_samples = int(round(duration_sec * self.fs))
            period = int(round(self.fs * 0.8))  # 75 bpm
            t_beat = np.linspace(0.0, 1.0, period, endpoint=False)
            beat = np.exp(-0.5 * ((t_beat - 0.2) / 0.05) ** 2) + 0.3 * np.exp(-0.5 * ((t_beat - 0.5) / 0.1) ** 2)
            synth = np.tile(beat, n_samples // period + 1)[:n_samples] + 1.0
        return self.process_window(synth, fs=self.fs)

    def process_window(
        self,
        ppg_buffer: np.ndarray,
        fs: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Process a single photoplethysmography (PPG) window buffer (typically 60s @ 125 Hz).

        Pipeline Steps:
        1. Input validation & SQI evaluation: Returns clean rejection dictionary in < 5 ms if invalid.
        2. Preprocessing: Executes 4th-order zero-phase Butterworth bandpass filter & Z-score normalization once.
        3. Dispatch: Passes normalized buffer with `is_filtered=True` to RR, PRV, and BP estimators.
        4. Bundling: Packages unified telemetry dictionary with latency tracking.

        Parameters
        ----------
        ppg_buffer : np.ndarray
            1D array of PPG samples (typically 7,500 samples for a 60s window at 125 Hz).
        fs : Optional[float], default=None
            Sampling frequency in Hz. If None, uses self.fs.

        Returns
        -------
        Dict[str, Any]
            Telemetry dictionary with keys:
            - "is_valid": bool
            - "sqi": Dict[str, Any]
            - "rr": Dict[str, Any]
            - "prv": Dict[str, Any]
            - "bp": Dict[str, Any]
            - "total_latency_ms": float
            - "status": str
        """
        t0 = time.perf_counter()
        fs_val = float(fs) if fs is not None else self.fs

        def _reject(
            status_msg: str,
            sqi_dict: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            lat = float((time.perf_counter() - t0) * 1000.0)
            if sqi_dict is None:
                sqi_dict = {
                    "is_valid": False,
                    "skewness": 0.0,
                    "perfusion_index": 0.0,
                    "relative_power": 0.0,
                    "status": status_msg,
                }
            return {
                "is_valid": False,
                "sqi": sqi_dict,
                "rr": {
                    "rr_bpm": 0.0,
                    "confidence": 0.0,
                    "is_valid": False,
                    "latency_ms": 0.0,
                    "status": status_msg,
                },
                "prv": {
                    "mean_ppi": 0.0,
                    "sdppi": 0.0,
                    "rmssd": 0.0,
                    "pppi50": 0.0,
                    "lf": 0.0,
                    "hf": 0.0,
                    "lf_hf_ratio": 0.0,
                    "is_valid": False,
                    "latency_ms": 0.0,
                    "status": status_msg,
                },
                "bp": {
                    "sbp_mmHg": 0.0,
                    "dbp_mmHg": 0.0,
                    "map_mmHg": 0.0,
                    "is_valid": False,
                    "latency_ms": 0.0,
                    "status": status_msg,
                },
                "total_latency_ms": lat,
                "status": status_msg,
            }

        # 1. Input Sanity Checks
        if ppg_buffer is None:
            return _reject("INVALID_INPUT_NONE")

        try:
            ppg_arr = np.asarray(ppg_buffer, dtype=np.float64)
        except Exception as err:
            return _reject(f"INVALID_INPUT_TYPE: {err}")

        if ppg_arr.ndim != 1 or len(ppg_arr) == 0:
            return _reject("INVALID_EMPTY_BUFFER")

        if not np.all(np.isfinite(ppg_arr)):
            return _reject("INVALID_NON_FINITE: Buffer contains NaN or Inf")

        # Flatline / zero variance check
        std_val = float(np.std(ppg_arr))
        ptp_val = float(np.ptp(ppg_arr))
        if std_val < 1e-6 or ptp_val < 1e-8:
            return _reject("INVALID_FLATLINE")

        # Buffer length check (minimum 5s physiological threshold)
        min_samples = int(fs_val * 5.0)
        if len(ppg_arr) < min_samples:
            return _reject(
                f"INVALID_SHORT_BUFFER: Buffer length ({len(ppg_arr)}) < {min_samples} samples ({5.0}s @ {fs_val} Hz)"
            )

        # 2. SQI Quality Gating (< 5 ms execution)
        try:
            sqi_res = evaluate_sqi(ppg_arr, fs=fs_val)
        except Exception as err:
            return _reject(f"SQI_EVALUATION_ERROR: {err}")

        sqi_dict = {
            "is_valid": bool(sqi_res.is_valid),
            "skewness": float(sqi_res.skewness) if np.isfinite(sqi_res.skewness) else 0.0,
            "perfusion_index": float(sqi_res.perfusion_index) if np.isfinite(sqi_res.perfusion_index) else 0.0,
            "relative_power": float(sqi_res.relative_power) if np.isfinite(sqi_res.relative_power) else 0.0,
            "status": str(sqi_res.status),
        }

        if not sqi_res.is_valid:
            return _reject(f"SQI_REJECTED: {sqi_res.status}", sqi_dict=sqi_dict)

        # 3. Single-Pass Signal Preprocessing
        try:
            prep = preprocess_window(ppg_arr, fs=fs_val)
        except Exception as err:
            return _reject(f"PREPROCESSING_ERROR: {err}", sqi_dict=sqi_dict)

        # 4. Dispatch to Domain Estimators with is_filtered=True
        # RR and PRV receive Z-score normalized buffer for peak detection;
        # BP receives detrended buffer to preserve true pulsatile AC amplitude dynamics
        res_rr = self.rr_estimator.estimate(prep.normalized, is_filtered=True)
        res_prv = self.prv_estimator.estimate(prep.normalized, is_filtered=True)
        res_bp = self.bp_estimator.estimate(prep.detrended, is_filtered=True, fs=fs_val)

        total_latency_ms = float((time.perf_counter() - t0) * 1000.0)

        # Determine overall validity
        overall_valid = bool(
            sqi_res.is_valid
            and res_rr.get("is_valid", False)
            and res_prv.get("is_valid", False)
            and res_bp.get("is_valid", False)
        )

        if overall_valid:
            overall_status = "VALID"
        else:
            reasons = []
            if not res_rr.get("is_valid", False):
                reasons.append(f"RR:{res_rr.get('status', 'INVALID')}")
            if not res_prv.get("is_valid", False):
                reasons.append(f"PRV:{res_prv.get('status', 'INVALID')}")
            if not res_bp.get("is_valid", False):
                reasons.append(f"BP:{res_bp.get('status', 'INVALID')}")
            overall_status = "; ".join(reasons) if reasons else "ESTIMATOR_INVALID"

        return {
            "is_valid": overall_valid,
            "sqi": sqi_dict,
            "rr": res_rr,
            "prv": res_prv,
            "bp": res_bp,
            "total_latency_ms": total_latency_ms,
            "status": overall_status,
        }


# Canonical alias for edge deployment monitor
EdgePPGMonitor = PPGEdgePipeline

__all__ = ["PPGEdgePipeline", "EdgePPGMonitor"]
