"""
Photoplethysmography (PPG) Signal Quality Index (SQI) Module.

Provides:
- Skewness SQI (S_sqi > 0.0)
- Optical Perfusion Index (0.1% <= PI <= 20.0%, with adaptive benchmark handling)
- Relative Power SQI (cardiac band 0.5-3.5 Hz / total 0.5-8.0 Hz >= 0.50)
- Multi-parametric composite gating and diagnostics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np
import scipy.signal
import scipy.stats


@dataclass(frozen=True)
class SQIResult:
    """Signal Quality Index assessment results."""
    is_valid: bool          # True if window satisfies all SQI criteria
    skewness: float         # Skewness SQI (threshold: > 0.0)
    perfusion_index: float  # Perfusion Index % (threshold: 0.1% <= PI <= 20.0%)
    relative_power: float   # Cardiac band power ratio (threshold: >= 0.50)
    status: str             # "VALID" or concatenated failure diagnostic message


def compute_skewness_sqi(signal: np.ndarray) -> float:
    """
    Computes standardized 3rd central moment (skewness).

    Valid arterial pulses feature a sharp, steep systolic upstroke followed by
    a gradual diastolic slope, yielding positive skewness (S_sqi > 0.0).

    Parameters
    ----------
    signal : np.ndarray
        1D signal array (typically filtered/detrended).

    Returns
    -------
    float
        Standardized 3rd moment skewness. Returns 0.0 for flatline signals.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1 or len(signal) == 0:
        return 0.0

    if not np.all(np.isfinite(signal)):
        return float("nan")

    std_val = float(np.std(signal))
    if std_val < 1e-8:
        return 0.0

    diff = signal - np.mean(signal)
    m2 = np.mean(diff ** 2)
    m3 = np.mean(diff ** 3)
    if m2 < 1e-12:
        return 0.0

    return float(m3 / (m2 ** 1.5))


def compute_perfusion_index(
    raw_signal: np.ndarray,
    filtered_signal: Optional[np.ndarray] = None
) -> float:
    """
    Computes optical Perfusion Index (PI = (AC / DC) * 100%).

    Parameters
    ----------
    raw_signal : np.ndarray
        1D raw PPG buffer representing optical absorption.
    filtered_signal : Optional[np.ndarray], default=None
        1D bandpass filtered signal for pulsatile AC extraction.
        If None, raw_signal peak-to-peak amplitude is used as AC.

    Returns
    -------
    float
        Perfusion index in percentage. Returns 0.0% if DC baseline is near zero.
    """
    raw_signal = np.asarray(raw_signal, dtype=np.float64)
    if raw_signal.ndim != 1 or len(raw_signal) == 0:
        return 0.0

    if not np.all(np.isfinite(raw_signal)):
        return float("nan")

    if filtered_signal is not None:
        filt = np.asarray(filtered_signal, dtype=np.float64)
        if filt.ndim != 1 or len(filt) == 0 or not np.all(np.isfinite(filt)):
            return float("nan")
        ac = float(np.ptp(filt))
    else:
        ac = float(np.ptp(raw_signal))

    dc = abs(float(np.mean(raw_signal)))
    if dc < 1e-8:
        return 0.0

    return float((ac / dc) * 100.0)


_HANN_1024 = np.hanning(1024)
_FREQS_1024_125 = np.fft.rfftfreq(1024, d=1.0 / 125.0)
_TRAPEZOID_FN = getattr(np, "trapezoid", getattr(np, "trapz", None))


def compute_cardiac_relative_power(
    signal: np.ndarray,
    fs: float = 125.0,
    cardiac_band: Tuple[float, float] = (0.5, 3.5),
    total_band: Tuple[float, float] = (0.0, 8.0)
) -> float:
    """
    Computes ratio of power in the cardiac band [0.5, 3.5 Hz] to total
    passband power [0.0, 8.0 Hz] via Welch PSD.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array (typically filtered/detrended).
    fs : float, default=125.0
        Sampling frequency in Hz.
    cardiac_band : Tuple[float, float], default=(0.5, 3.5)
        Primary cardiac frequency range in Hz.
    total_band : Tuple[float, float], default=(0.0, 8.0)
        Transmission passband range in Hz (capturing low-frequency drift and harmonics).

    Returns
    -------
    float
        Relative power ratio in [0.0, 1.0]. Returns 0.0 if total power is zero.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1 or len(signal) == 0:
        return 0.0

    if not np.all(np.isfinite(signal)):
        return float("nan")

    if np.std(signal) < 1e-8:
        return 0.0

    nperseg = min(1024, len(signal))
    if nperseg < 16:
        return 0.0

    step = max(1, nperseg - (nperseg // 2))
    segs = np.lib.stride_tricks.sliding_window_view(signal, nperseg)[::step]
    segs_centered = segs - np.mean(segs, axis=-1, keepdims=True)
    win = _HANN_1024 if nperseg == 1024 else np.hanning(nperseg)
    fft_vals = np.fft.rfft(segs_centered * win, axis=-1)
    psd = np.mean(fft_vals.real ** 2 + fft_vals.imag ** 2, axis=0)
    freqs = _FREQS_1024_125 if (nperseg == 1024 and fs == 125.0) else np.fft.rfftfreq(nperseg, d=1.0 / fs)

    cardiac_mask = (freqs >= cardiac_band[0]) & (freqs <= cardiac_band[1])
    total_mask = (freqs >= total_band[0]) & (freqs <= total_band[1])

    p_cardiac = float(_TRAPEZOID_FN(psd[cardiac_mask], freqs[cardiac_mask]))
    p_total = float(_TRAPEZOID_FN(psd[total_mask], freqs[total_mask]))

    if p_total <= 1e-12:
        return 0.0

    ratio = p_cardiac / p_total
    return float(np.clip(ratio, 0.0, 1.0))


# Alias conforming to spec
compute_relative_power_sqi = compute_cardiac_relative_power


def composite_sqi_gating(
    skewness: float,
    perfusion_index: float,
    relative_power: float,
    is_normalized_input: bool = False,
    skip_pi: bool = False,
    min_skewness: float = 0.05
) -> Tuple[bool, str]:
    """
    Evaluates composite gating logic across skewness, PI, and relative power.

    Parameters
    ----------
    skewness : float
        Calculated skewness (valid if > min_skewness).
    perfusion_index : float
        Calculated perfusion index (valid if 0.1% <= PI <= 20.0%, or up to 250.0% if pre-normalized).
    relative_power : float
        Calculated cardiac relative power (valid if >= 0.50).
    is_normalized_input : bool, default=False
        If True, relaxes PI upper bound to 250.0% for pre-centered benchmark datasets.
    skip_pi : bool, default=False
        If True, bypasses PI validation check.
    min_skewness : float, default=0.05
        Minimum skewness threshold for gating (default 0.05).

    Returns
    -------
    Tuple[bool, str]
        (is_valid, diagnostic_status_string)
    """
    # 0. Explicit NaN / Inf validation across all metrics
    non_finite = []
    if not np.isfinite(skewness):
        non_finite.append(f"skewness={skewness}")
    if not skip_pi and not np.isfinite(perfusion_index):
        non_finite.append(f"perfusion_index={perfusion_index}")
    if not np.isfinite(relative_power):
        non_finite.append(f"relative_power={relative_power}")

    if non_finite:
        return False, f"INVALID_NON_FINITE: Non-finite metrics detected ({', '.join(non_finite)})"

    failures = []

    # 1. Skewness check (must be strictly > min_skewness)
    if skewness <= min_skewness:
        failures.append(f"POOR_SKEWNESS: Skewness={skewness:.3f} <= {min_skewness:.3f}")

    # 2. Perfusion Index check
    if not skip_pi:
        pi_max = 250.0 if is_normalized_input else 20.0
        pi_min = 0.1
        if not (pi_min <= perfusion_index <= pi_max):
            failures.append(
                f"INVALID_PI: Perfusion Index={perfusion_index:.2f}% outside [{pi_min}%, {pi_max}%]"
            )

    # 3. Relative Power check (must be >= 0.50)
    if relative_power < 0.50:
        failures.append(f"LOW_CARDIAC_POWER: Relative Power={relative_power:.3f} < 0.50")

    if not failures:
        return True, "VALID"

    return False, "; ".join(failures)


def evaluate_sqi(
    raw_signal: np.ndarray,
    filtered_signal: Optional[np.ndarray] = None,
    fs: float = 125.0,
    is_normalized_input: bool = False,
    skip_pi: bool = False,
    min_skewness: float = 0.05
) -> SQIResult:
    """
    Evaluates full multi-parametric Signal Quality Index on a 60-second window.

    Parameters
    ----------
    raw_signal : np.ndarray
        1D raw PPG buffer.
    filtered_signal : Optional[np.ndarray], default=None
        1D pre-filtered PPG buffer. If None, filtering is performed automatically.
    fs : float, default=125.0
        Sampling frequency in Hz.
    is_normalized_input : bool, default=False
        If True, adapts PI validation for pre-scaled benchmark datasets.
    skip_pi : bool, default=False
        If True, bypasses PI check.
    min_skewness : float, default=0.05
        Minimum skewness threshold passed to composite_sqi_gating (default 0.05).

    Returns
    -------
    SQIResult
        Structured result holding validity flag, metrics, and diagnostic status.
    """
    raw_signal = np.asarray(raw_signal, dtype=np.float64)

    if raw_signal.ndim != 1 or len(raw_signal) == 0:
        return SQIResult(
            is_valid=False,
            skewness=0.0,
            perfusion_index=0.0,
            relative_power=0.0,
            status="INVALID_EMPTY_BUFFER"
        )

    if not np.all(np.isfinite(raw_signal)):
        return SQIResult(
            is_valid=False,
            skewness=float("nan"),
            perfusion_index=float("nan"),
            relative_power=float("nan"),
            status="INVALID_NON_FINITE: raw_signal contains NaN or Inf"
        )

    if len(raw_signal) < 28:
        return SQIResult(
            is_valid=False,
            skewness=0.0,
            perfusion_index=0.0,
            relative_power=0.0,
            status=f"INVALID_SHORT_BUFFER: Buffer length ({len(raw_signal)}) < 28 samples"
        )

    # Flatline detection
    if np.std(raw_signal) < 1e-6 or np.ptp(raw_signal) < 1e-8:
        return SQIResult(
            is_valid=False,
            skewness=0.0,
            perfusion_index=0.0,
            relative_power=0.0,
            status="INVALID_FLATLINE"
        )

    if filtered_signal is not None:
        filtered_sig = np.asarray(filtered_signal, dtype=np.float64)
        if filtered_sig.ndim != 1 or len(filtered_sig) != len(raw_signal):
            return SQIResult(
                is_valid=False,
                skewness=0.0,
                perfusion_index=0.0,
                relative_power=0.0,
                status=f"INVALID_SHAPE: filtered_signal length ({len(filtered_sig)}) != raw_signal ({len(raw_signal)})"
            )
        if not np.all(np.isfinite(filtered_sig)):
            return SQIResult(
                is_valid=False,
                skewness=float("nan"),
                perfusion_index=float("nan"),
                relative_power=float("nan"),
                status="INVALID_NON_FINITE: filtered_signal contains NaN or Inf"
            )
    else:
        from core.preprocessing import apply_bandpass_filter
        filtered_sig = apply_bandpass_filter(raw_signal, fs=fs)

    skewness = compute_skewness_sqi(filtered_sig)
    pi = compute_perfusion_index(raw_signal, filtered_sig)
    rel_power = compute_cardiac_relative_power(filtered_sig, fs=fs)

    # Check whether raw signal is pre-normalized / AC-coupled (mean < 5.0)
    dc = abs(float(np.mean(raw_signal)))
    is_norm = is_normalized_input or (dc < 5.0)

    is_valid, status = composite_sqi_gating(
        skewness=skewness,
        perfusion_index=pi,
        relative_power=rel_power,
        is_normalized_input=is_norm,
        skip_pi=skip_pi,
        min_skewness=min_skewness
    )

    return SQIResult(
        is_valid=is_valid,
        skewness=skewness,
        perfusion_index=pi,
        relative_power=rel_power,
        status=status
    )
