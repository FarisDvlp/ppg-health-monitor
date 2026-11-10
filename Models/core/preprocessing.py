"""
Photoplethysmography (PPG) Preprocessing Module.

Provides 4th-order zero-phase Butterworth bandpass filtering, linear least-squares
detrending, 60-second window segmentation, and post-SQI Z-score normalization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple
import numpy as np
import scipy.signal


@dataclass(frozen=True)
class PreprocessResult:
    """Preprocessed 60-second PPG window container."""
    filtered: np.ndarray    # shape: (N,), dtype: np.float64
    detrended: np.ndarray   # shape: (N,), dtype: np.float64
    normalized: np.ndarray  # shape: (N,), dtype: np.float64, mu=0, sigma=1
    mean: float             # float scalar, mean of detrended window
    std: float              # float scalar, sample standard deviation


# Module-level cache for default filter coefficients (4th-order, 0.5-8.0 Hz, fs=125.0 Hz)
_DEFAULT_FS = 125.0
_DEFAULT_LOWCUT = 0.5
_DEFAULT_HIGHCUT = 8.0
_DEFAULT_ORDER = 4
_CACHED_B, _CACHED_A = scipy.signal.butter(
    _DEFAULT_ORDER,
    [_DEFAULT_LOWCUT, _DEFAULT_HIGHCUT],
    btype='bandpass',
    fs=_DEFAULT_FS
)


def apply_bandpass_filter(
    signal: np.ndarray,
    lowcut: float = 0.5,
    highcut: float = 8.0,
    fs: float = 125.0,
    order: int = 4
) -> np.ndarray:
    """
    Applies a 4th-order zero-phase Butterworth bandpass filter.

    Parameters
    ----------
    signal : np.ndarray
        1D input signal array.
    lowcut : float, default=0.5
        Lower cutoff frequency in Hz.
    highcut : float, default=8.0
        Upper cutoff frequency in Hz.
    fs : float, default=125.0
        Sampling frequency in Hz.
    order : int, default=4
        Filter order (effective order is 2*order due to forward-backward filtering).

    Returns
    -------
    np.ndarray
        Zero-phase filtered 1D array of dtype float64.

    Raises
    ------
    ValueError
        If input signal is not 1D, contains NaN/Inf, or length <= padlen (27).
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1:
        raise ValueError(f"Input signal must be 1D array, got shape {signal.shape}")
    if np.any(np.isnan(signal)) or np.any(np.isinf(signal)):
        raise ValueError("Input signal contains NaN or Inf values")

    # Order 4 bandpass has len(a)=9, len(b)=9; padlen = 3 * max(len(a), len(b)) = 27
    expected_padlen = 3 * (2 * order + 1)
    if len(signal) <= expected_padlen:
        raise ValueError(
            f"The length of the input vector ({len(signal)}) must be greater "
            f"than padlen ({expected_padlen})."
        )

    # Use cached coefficients if matching default parameters
    if (
        lowcut == _DEFAULT_LOWCUT
        and highcut == _DEFAULT_HIGHCUT
        and fs == _DEFAULT_FS
        and order == _DEFAULT_ORDER
    ):
        b, a = _CACHED_B, _CACHED_A
    else:
        b, a = scipy.signal.butter(order, [lowcut, highcut], btype='bandpass', fs=fs)

    return scipy.signal.filtfilt(b, a, signal)


# Alias conforming to spec
butterworth_bandpass_filter = apply_bandpass_filter


def detrend_signal(signal: np.ndarray, type: str = 'linear') -> np.ndarray:
    """
    Performs linear least-squares detrending to eliminate DC wander and baseline drift.

    Parameters
    ----------
    signal : np.ndarray
        1D input signal array.
    type : str, default='linear'
        Detrending type ('linear' or 'constant').

    Returns
    -------
    np.ndarray
        Detrended 1D signal array.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1:
        raise ValueError(f"Input signal must be 1D array, got shape {signal.shape}")
    if len(signal) <= 1:
        return signal.copy()
    if np.std(signal) < 1e-12:
        return np.zeros_like(signal, dtype=np.float64)

    return scipy.signal.detrend(signal, type=type, axis=-1)


def segment_windows(
    signal: np.ndarray,
    window_len_sec: float = 60.0,
    fs: float = 125.0,
    overlap_sec: float = 0.0
) -> List[np.ndarray]:
    """
    Segments a continuous signal into fixed-length windows.

    Parameters
    ----------
    signal : np.ndarray
        1D continuous signal array.
    window_len_sec : float, default=60.0
        Window length in seconds.
    fs : float, default=125.0
        Sampling frequency in Hz.
    overlap_sec : float, default=0.0
        Overlap between adjacent windows in seconds.

    Returns
    -------
    List[np.ndarray]
        List of 1D arrays each of length window_len_sec * fs.

    Raises
    ------
    ValueError
        If signal is shorter than window length or overlap >= window_len_sec.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1:
        raise ValueError(f"Input signal must be 1D array, got shape {signal.shape}")

    window_size = int(round(window_len_sec * fs))
    if window_size <= 0:
        raise ValueError(f"Window size must be positive, got {window_size}")
    if len(signal) < window_size:
        raise ValueError(
            f"Signal length ({len(signal)}) is less than window length ({window_size} samples)."
        )

    if overlap_sec >= window_len_sec:
        raise ValueError("overlap_sec must be strictly less than window_len_sec")

    step_size = int(round((window_len_sec - overlap_sec) * fs))
    if step_size <= 0:
        step_size = 1

    windows: List[np.ndarray] = []
    start = 0
    while start + window_size <= len(signal):
        windows.append(signal[start : start + window_size].copy())
        start += step_size

    return windows


def zscore_normalize(
    signal: np.ndarray,
    eps: float = 1e-8
) -> Tuple[np.ndarray, float, float]:
    """
    Applies Z-score standardization: (x - mu) / (sigma + eps).

    Guards against flatline inputs (sigma < eps) by returning a zero array.

    Parameters
    ----------
    signal : np.ndarray
        1D input signal array.
    eps : float, default=1e-8
        Numerical stability epsilon.

    Returns
    -------
    Tuple[np.ndarray, float, float]
        (normalized_signal, mean, std)
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1:
        raise ValueError(f"Input signal must be 1D array, got shape {signal.shape}")
    if len(signal) == 0:
        return np.array([], dtype=np.float64), 0.0, 0.0

    mean_val = float(np.mean(signal))
    std_val = float(np.std(signal))

    if std_val < eps:
        return np.zeros_like(signal, dtype=np.float64), mean_val, 0.0

    normalized = (signal - mean_val) / std_val
    return normalized, mean_val, std_val


# Alias conforming to spec
normalize_zscore = zscore_normalize


def preprocess_window(
    raw_signal: np.ndarray,
    fs: float = 125.0
) -> PreprocessResult:
    """
    Full preprocessing pipeline for a single 60-second PPG window buffer.

    Performs:
    1. Input validation (1D, finite, length >= 28).
    2. 4th-order zero-phase Butterworth bandpass filter (0.5–8.0 Hz).
    3. Linear baseline detrending.
    4. Post-filtering Z-score standardization.

    Parameters
    ----------
    raw_signal : np.ndarray
        1D raw PPG buffer (typically 7,500 samples @ 125 Hz).
    fs : float, default=125.0
        Sampling frequency in Hz.

    Returns
    -------
    PreprocessResult
        Structured dataclass holding filtered, detrended, normalized arrays and statistics.
    """
    raw_signal = np.asarray(raw_signal, dtype=np.float64)
    if raw_signal.ndim != 1:
        raise ValueError(f"Expected 1D buffer, got shape {raw_signal.shape}")
    if np.any(np.isnan(raw_signal)) or np.any(np.isinf(raw_signal)):
        raise ValueError("raw_signal contains NaN or Inf values")
    if len(raw_signal) < 28:
        raise ValueError(f"Buffer length ({len(raw_signal)}) must be >= 28 samples")

    if np.std(raw_signal) < 1e-6:
        # Flatline protection
        zeros = np.zeros_like(raw_signal, dtype=np.float64)
        return PreprocessResult(
            filtered=zeros,
            detrended=zeros,
            normalized=zeros,
            mean=float(np.mean(raw_signal)),
            std=0.0
        )

    filtered = apply_bandpass_filter(raw_signal, lowcut=0.5, highcut=8.0, fs=fs, order=4)
    detrended = detrend_signal(filtered, type='linear')
    normalized, mean_val, std_val = zscore_normalize(detrended)

    return PreprocessResult(
        filtered=filtered,
        detrended=detrended,
        normalized=normalized,
        mean=mean_val,
        std=std_val
    )
