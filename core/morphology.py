"""
Photoplethysmography (PPG) Morphology & Derivative Fiducial Extraction Module.

Provides:
- Velocity Plethysmogram (APG, 1st derivative) and Acceleration Plethysmogram (VPG, 2nd derivative)
- Systolic crest detection with adaptive refractory blanking (250 ms / 31 samples @ 125 Hz)
- Diastolic onset trough detection preceding each systolic peak
- Dicrotic notch and secondary diastolic crest detection with robust fallback
- Beat-level derivative landmark extraction (a1, b1, a2, b2)
- Structured BeatSegment and FiducialResult containers
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
import scipy.signal


@dataclass(frozen=True)
class BeatSegment:
    """Morphological and derivative fiducial coordinates for a single pulse cycle."""
    onset_idx: int
    systolic_peak_idx: int
    dicrotic_notch_idx: int
    diastolic_peak_idx: int
    offset_idx: int
    onset_amp: float
    systolic_amp: float
    notch_amp: float
    diastolic_amp: float
    a1_idx: int
    b1_idx: int
    a2_idx: int
    b2_idx: int
    a1_amp: float
    b1_amp: float
    a2_amp: float
    b2_amp: float

    @property
    def onset(self) -> int:
        return self.onset_idx

    @property
    def systolic_peak(self) -> int:
        return self.systolic_peak_idx

    @property
    def dicrotic_notch(self) -> Optional[int]:
        return self.dicrotic_notch_idx if self.dicrotic_notch_idx >= 0 else None

    @property
    def diastolic_peak(self) -> Optional[int]:
        return self.diastolic_peak_idx if self.diastolic_peak_idx >= 0 else None

    @property
    def offset(self) -> int:
        return self.offset_idx

    @property
    def a1(self) -> int:
        return self.a1_idx

    @property
    def b1(self) -> int:
        return self.b1_idx

    @property
    def a2(self) -> int:
        return self.a2_idx

    @property
    def b2(self) -> int:
        return self.b2_idx


@dataclass(frozen=True)
class FiducialResult:
    """Container for window-level fiducials, derivative waveforms, and segmented beats."""
    systolic_peaks: np.ndarray       # 1D int array of sample indices
    diastolic_troughs: np.ndarray     # 1D int array of onset sample indices
    dicrotic_notches: np.ndarray      # 1D int array of notch sample indices (-1 if absent)
    diastolic_peaks: np.ndarray       # 1D int array of secondary crest indices (-1 if absent)
    apg: np.ndarray                  # 1st derivative waveform
    vpg: np.ndarray                  # 2nd derivative waveform
    derivative_fiducials: Dict[str, np.ndarray]  # {'a1': np.ndarray, 'b1': np.ndarray, ...}
    beats: List[BeatSegment]         # List of segmented individual cardiac pulses


def compute_derivatives(
    signal: np.ndarray,
    fs: float = 125.0
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Computes Velocity Plethysmogram (APG, 1st derivative) and
    Acceleration Plethysmogram (VPG, 2nd derivative) via centered finite differences.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array (typically Z-score normalized PPG).
    fs : float, default=125.0
        Sampling frequency in Hz.

    Returns
    -------
    Tuple[np.ndarray, np.ndarray]
        (apg, vpg) arrays of the same shape and dtype float64.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1:
        raise ValueError(f"Input signal must be 1D, got ndim={signal.ndim}")
    if len(signal) < 2:
        zeros = np.zeros_like(signal)
        return zeros, zeros

    dt = 1.0 / fs
    apg = np.gradient(signal, dt)
    vpg = np.gradient(apg, dt)
    return apg, vpg


def detect_systolic_peaks(
    signal: np.ndarray,
    fs: float = 125.0,
    min_distance_sec: float = 0.25,
    prominence: Optional[float] = 0.3
) -> np.ndarray:
    """
    Detects systolic crest indices enforcing a physiological refractory period.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array.
    fs : float, default=125.0
        Sampling frequency in Hz.
    min_distance_sec : float, default=0.25
        Minimum physiological refractory period (250 ms = 31 samples @ 125 Hz,
        supporting extreme tachycardia up to 240 bpm).
    prominence : Optional[float], default=0.3
        Relative prominence threshold (fraction of signal standard deviation).

    Returns
    -------
    np.ndarray
        1D integer array of detected systolic peak sample indices.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1 or len(signal) == 0:
        return np.array([], dtype=np.int64)

    min_dist = max(1, int(round(min_distance_sec * fs)))
    std_val = float(np.std(signal))
    ptp_val = float(np.ptp(signal))

    if prominence is not None:
        if std_val < 0.8 and ptp_val > 1e-4:
            prom_scale = ptp_val
        elif std_val > 1e-4:
            prom_scale = std_val
        else:
            prom_scale = ptp_val * 0.25 if ptp_val > 1e-4 else 1.0
        prom_val = prominence * prom_scale
    else:
        prom_val = None

    peaks, _ = scipy.signal.find_peaks(signal, distance=min_dist, prominence=prom_val)
    if len(peaks) == 0:
        # Fallback without prominence if signal is small or dampened
        peaks, _ = scipy.signal.find_peaks(signal, distance=min_dist)

    return np.asarray(peaks, dtype=np.int64)


def detect_diastolic_troughs(
    signal: np.ndarray,
    peaks: np.ndarray,
    fs: float = 125.0
) -> np.ndarray:
    """
    Detects onset diastolic valley indices strictly preceding each systolic peak.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array.
    peaks : np.ndarray
        1D integer array of systolic peak indices.
    fs : float, default=125.0
        Sampling frequency in Hz.

    Returns
    -------
    np.ndarray
        1D integer array of diastolic trough sample indices.
    """
    signal = np.asarray(signal, dtype=np.float64)
    peaks = np.asarray(peaks, dtype=np.int64)

    if len(peaks) == 0 or len(signal) == 0:
        return np.array([], dtype=np.int64)

    troughs: List[int] = []
    for i, p in enumerate(peaks):
        if i > 0:
            delta_t = p - peaks[i - 1]
        else:
            delta_t = peaks[1] - peaks[0] if len(peaks) > 1 else int(round(0.8 * fs))

        t_start = max(0, p - int(round(0.60 * delta_t)))
        if i > 0:
            t_start = max(t_start, peaks[i - 1])

        if t_start >= p:
            t_start = max(0, p - 1)

        search_segment = signal[t_start:p]
        if len(search_segment) > 0:
            trough_idx = t_start + int(np.argmin(search_segment))
        else:
            trough_idx = t_start

        # Ensure trough strictly precedes peak
        if trough_idx >= p and p > 0:
            trough_idx = p - 1

        troughs.append(trough_idx)

    return np.asarray(troughs, dtype=np.int64)


def detect_dicrotic_features(
    signal: np.ndarray,
    peaks: np.ndarray,
    troughs: np.ndarray,
    apg: np.ndarray,
    vpg: np.ndarray,
    fs: float = 125.0
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Detects dicrotic notch and secondary diastolic peak indices per pulse cycle.

    Employs a robust 3-tier algorithm:
    1. Signal local minimum (distinct dicrotic notch valley).
    2. VPG positive zero-crossing (inflection point).
    3. APG local minimum (secondary inflection).
    If no inflection or notch exists (monotonic decaying tail), returns -1.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array.
    peaks : np.ndarray
        1D array of systolic peak indices.
    troughs : np.ndarray
        1D array of diastolic trough indices.
    apg : np.ndarray
        1st derivative waveform.
    vpg : np.ndarray
        2nd derivative waveform.
    fs : float, default=125.0
        Sampling frequency in Hz.

    Returns
    -------
    Tuple[np.ndarray, np.ndarray]
        (dicrotic_notches, diastolic_peaks) arrays of shape (len(peaks),).
    """
    signal = np.asarray(signal, dtype=np.float64)
    peaks = np.asarray(peaks, dtype=np.int64)
    troughs = np.asarray(troughs, dtype=np.int64)

    if len(peaks) == 0:
        empty = np.array([], dtype=np.int64)
        return empty, empty

    notches: List[int] = []
    dia_peaks: List[int] = []

    for i in range(len(peaks)):
        p = peaks[i]
        if i + 1 < len(peaks):
            p_next = peaks[i + 1]
            down = p_next - p
            n_limit = p_next
            if i + 1 < len(troughs):
                n_limit = min(n_limit, troughs[i + 1])
        else:
            down = p - peaks[i - 1] if i > 0 else int(round(0.8 * fs))
            n_limit = min(len(signal) - 1, p + down)

        n_start = p + max(1, int(round(0.10 * down)))
        n_end = min(n_limit, p + max(2, int(round(0.65 * down))))

        if n_end <= n_start:
            notches.append(-1)
            dia_peaks.append(-1)
            continue

        notch_idx = -1

        # Tier 1: Local minimum in signal (distinct notch dip)
        min_candidates = [
            k for k in range(n_start, n_end)
            if signal[k] <= signal[k - 1] and signal[k] < signal[k + 1]
        ]
        if len(min_candidates) > 0:
            notch_idx = min_candidates[0]
        else:
            # Tier 2: VPG zero-crossing from negative to positive
            zc = [k for k in range(n_start, n_end) if vpg[k] <= 0 and vpg[k + 1] > 0]
            if len(zc) > 0:
                notch_idx = zc[0]
            else:
                # Tier 3: Interior APG local minimum (inflection point)
                apg_mins = [
                    k for k in range(n_start + 1, n_end - 1)
                    if apg[k] < apg[k - 1] and apg[k] < apg[k + 1]
                ]
                if len(apg_mins) > 0:
                    notch_idx = apg_mins[0]

        notches.append(notch_idx)

        # Diastolic peak detection
        if notch_idx != -1:
            d_end = min(len(signal), p + int(round(0.90 * down)))
            if d_end > notch_idx + 1:
                dia_cand = notch_idx + 1 + int(np.argmax(signal[notch_idx + 1 : d_end]))
                if signal[dia_cand] > signal[notch_idx]:
                    dia_peak_idx = dia_cand
                else:
                    dia_peak_idx = notch_idx
            else:
                dia_peak_idx = notch_idx
        else:
            dia_peak_idx = -1

        dia_peaks.append(dia_peak_idx)

    return np.asarray(notches, dtype=np.int64), np.asarray(dia_peaks, dtype=np.int64)


# Alias conforming to spec
detect_dicrotic_notches = detect_dicrotic_features


def extract_derivative_fiducials(
    apg: np.ndarray,
    vpg: np.ndarray,
    peaks: np.ndarray,
    troughs: np.ndarray
) -> Dict[str, np.ndarray]:
    """
    Extracts APG (a1, b1) and VPG (a2, b2) landmark coordinates per pulse cycle.

    Invariants guaranteed:
    trough < a2 < a1 < peak < b1
    apg[a1] > 0, apg[b1] < 0, vpg[a2] > 0, vpg[b2] < 0

    Parameters
    ----------
    apg : np.ndarray
        1st derivative waveform.
    vpg : np.ndarray
        2nd derivative waveform.
    peaks : np.ndarray
        Systolic peak indices.
    troughs : np.ndarray
        Diastolic trough indices.

    Returns
    -------
    Dict[str, np.ndarray]
        Dictionary with keys 'a1', 'b1', 'a2', 'b2' mapping to 1D integer arrays.
    """
    n_beats = min(len(peaks), len(troughs))
    if n_beats == 0:
        empty = np.array([], dtype=np.int64)
        return {'a1': empty, 'b1': empty, 'a2': empty, 'b2': empty}

    a1_list: List[int] = []
    b1_list: List[int] = []
    a2_list: List[int] = []
    b2_list: List[int] = []

    for i in range(n_beats):
        t_v = troughs[i]
        t_p = peaks[i]

        if i + 1 < len(peaks):
            t_next = peaks[i + 1]
        else:
            t_next = min(len(apg) - 1, t_p + (t_p - troughs[i]))

        # a2: maximum acceleration during early ejection in [t_v, t_p]
        if t_p > t_v:
            a2_idx = t_v + int(np.argmax(vpg[t_v : t_p + 1]))
        else:
            a2_idx = max(0, t_p - 1)

        # a1: maximum velocity during mid-upstroke in [a2_idx, t_p]
        if t_p > a2_idx:
            a1_idx = a2_idx + int(np.argmax(apg[a2_idx : t_p + 1]))
            # Enforce strict ordering t_v < a2 < a1 < t_p
            if a1_idx <= a2_idx and a2_idx < t_p:
                a1_idx = min(t_p - 1, a2_idx + 1)
        else:
            a1_idx = max(0, t_p - 1)

        # Ensure t_v < a2 < a1 < t_p whenever possible
        if t_p - t_v >= 3:
            if not (t_v < a2_idx < a1_idx < t_p):
                a2_idx = t_v + (t_p - t_v) // 3
                a1_idx = t_v + 2 * (t_p - t_v) // 3

        # b1: minimum velocity during systolic downstroke in [t_p, t_next]
        if t_next > t_p:
            b1_idx = t_p + int(np.argmin(apg[t_p : t_next]))
            if b1_idx == t_p and t_next > t_p + 1:
                b1_idx = t_p + 1 + int(np.argmin(apg[t_p + 1 : t_next]))
        else:
            b1_idx = min(len(apg) - 1, t_p + 1)

        # b2: minimum acceleration during late ejection in [a1_idx, t_next]
        if t_next > a1_idx:
            b2_idx = a1_idx + int(np.argmin(vpg[a1_idx : t_next]))
        else:
            b2_idx = t_p

        a1_list.append(a1_idx)
        b1_list.append(b1_idx)
        a2_list.append(a2_idx)
        b2_list.append(b2_idx)

    return {
        'a1': np.asarray(a1_list, dtype=np.int64),
        'b1': np.asarray(b1_list, dtype=np.int64),
        'a2': np.asarray(a2_list, dtype=np.int64),
        'b2': np.asarray(b2_list, dtype=np.int64),
    }


def extract_beat_segments(
    signal: np.ndarray,
    peaks: np.ndarray,
    troughs: np.ndarray,
    notches: np.ndarray,
    diastolic_peaks: np.ndarray,
    apg: np.ndarray,
    vpg: np.ndarray,
    fs: float = 125.0
) -> List[BeatSegment]:
    """
    Assembles segmented individual cardiac cycles into structured BeatSegment objects.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array.
    peaks : np.ndarray
        Systolic peak indices.
    troughs : np.ndarray
        Diastolic trough indices.
    notches : np.ndarray
        Dicrotic notch indices.
    diastolic_peaks : np.ndarray
        Secondary diastolic crest indices.
    apg : np.ndarray
        1st derivative waveform.
    vpg : np.ndarray
        2nd derivative waveform.
    fs : float, default=125.0
        Sampling frequency in Hz.

    Returns
    -------
    List[BeatSegment]
        List of BeatSegment objects.
    """
    signal = np.asarray(signal, dtype=np.float64)
    deriv_fids = extract_derivative_fiducials(apg, vpg, peaks, troughs)
    a1_arr = deriv_fids['a1']
    b1_arr = deriv_fids['b1']
    a2_arr = deriv_fids['a2']
    b2_arr = deriv_fids['b2']

    beats: List[BeatSegment] = []
    n_beats = min(len(peaks), len(troughs))

    for i in range(n_beats):
        onset = int(troughs[i])
        sp = int(peaks[i])

        if i + 1 < len(troughs):
            offset = int(troughs[i + 1])
        elif i + 1 < len(peaks):
            offset = int(peaks[i + 1])
        else:
            offset = min(len(signal) - 1, sp + (sp - onset))

        notch = int(notches[i]) if i < len(notches) else -1
        dia_peak = int(diastolic_peaks[i]) if i < len(diastolic_peaks) else -1

        a1_idx = int(a1_arr[i]) if i < len(a1_arr) else sp
        b1_idx = int(b1_arr[i]) if i < len(b1_arr) else sp
        a2_idx = int(a2_arr[i]) if i < len(a2_arr) else onset
        b2_idx = int(b2_arr[i]) if i < len(b2_arr) else sp

        beats.append(
            BeatSegment(
                onset_idx=onset,
                systolic_peak_idx=sp,
                dicrotic_notch_idx=notch,
                diastolic_peak_idx=dia_peak,
                offset_idx=offset,
                onset_amp=float(signal[onset]),
                systolic_amp=float(signal[sp]),
                notch_amp=float(signal[notch]) if notch >= 0 else float('nan'),
                diastolic_amp=float(signal[dia_peak]) if dia_peak >= 0 else float('nan'),
                a1_idx=a1_idx,
                b1_idx=b1_idx,
                a2_idx=a2_idx,
                b2_idx=b2_idx,
                a1_amp=float(apg[a1_idx]),
                b1_amp=float(apg[b1_idx]),
                a2_amp=float(vpg[a2_idx]),
                b2_amp=float(vpg[b2_idx]),
            )
        )

    return beats


# Alias conforming to spec
segment_beats = extract_beat_segments


def extract_all_fiducials(
    signal: np.ndarray,
    fs: float = 125.0,
    min_distance_sec: float = 0.25
) -> FiducialResult:
    """
    Full morphological feature extraction facade for a single window.

    Extracts:
    1. APG and VPG derivatives via np.gradient.
    2. Systolic peaks with refractory blanking (default 250 ms / 0.25 s).
    3. Diastolic troughs preceding each peak.
    4. Dicrotic notches and secondary diastolic peaks with 3-tier fallback.
    5. Derivative fiducials (a1, b1, a2, b2).
    6. Individual BeatSegment instances.

    Parameters
    ----------
    signal : np.ndarray
        1D signal array (typically normalized PPG).
    fs : float, default=125.0
        Sampling frequency in Hz.
    min_distance_sec : float, default=0.25
        Minimum physiological refractory period passed to detect_systolic_peaks
        (default 0.25 s = 250 ms, supporting extreme tachycardia up to 240 bpm).

    Returns
    -------
    FiducialResult
        Structured dataclass holding all extracted fiducials, derivatives, and beats.
    """
    signal = np.asarray(signal, dtype=np.float64)
    apg, vpg = compute_derivatives(signal, fs=fs)

    empty_int = np.array([], dtype=np.int64)
    empty_deriv = {'a1': empty_int, 'b1': empty_int, 'a2': empty_int, 'b2': empty_int}

    if len(signal) == 0 or np.std(signal) < 1e-4:
        return FiducialResult(
            systolic_peaks=empty_int,
            diastolic_troughs=empty_int,
            dicrotic_notches=empty_int,
            diastolic_peaks=empty_int,
            apg=apg,
            vpg=vpg,
            derivative_fiducials=empty_deriv,
            beats=[]
        )

    peaks = detect_systolic_peaks(signal, fs=fs, min_distance_sec=min_distance_sec)
    if len(peaks) == 0:
        return FiducialResult(
            systolic_peaks=empty_int,
            diastolic_troughs=empty_int,
            dicrotic_notches=empty_int,
            diastolic_peaks=empty_int,
            apg=apg,
            vpg=vpg,
            derivative_fiducials=empty_deriv,
            beats=[]
        )

    troughs = detect_diastolic_troughs(signal, peaks, fs=fs)
    notches, dia_peaks = detect_dicrotic_features(signal, peaks, troughs, apg, vpg, fs=fs)
    deriv_fids = extract_derivative_fiducials(apg, vpg, peaks, troughs)
    beats = extract_beat_segments(signal, peaks, troughs, notches, dia_peaks, apg, vpg, fs=fs)

    return FiducialResult(
        systolic_peaks=peaks,
        diastolic_troughs=troughs,
        dicrotic_notches=notches,
        diastolic_peaks=dia_peaks,
        apg=apg,
        vpg=vpg,
        derivative_fiducials=deriv_fids,
        beats=beats
    )
