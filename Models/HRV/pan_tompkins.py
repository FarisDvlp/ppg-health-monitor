"""
Pan-Tompkins ECG QRS Detector Module adapted for 125 Hz Lead II waveforms.

Implements the classical Pan-Tompkins (1985) digital signal processing pipeline:
1. Zero-phase Butterworth bandpass filter (5.0 - 15.0 Hz, 3rd-order SOS)
2. 5-point central difference derivative filter (fs / 8 * [-1, -2, 0, 2, 1])
3. Point-by-point nonlinear energy squaring
4. Moving Window Integration (MWI: 19 samples / 150 ms @ 125 Hz)
5. Dual adaptive thresholding with running signal/noise estimators and searchback
6. Wide-QRS refractory blanking (200 ms / 25 samples @ 125 Hz)
7. T-wave discrimination based on derivative slope comparison for intervals 200 - 360 ms
8. Polar extremum refinement (+/- 6 samples) supporting upright and inverted Lead II
"""

from __future__ import annotations

from typing import Union
import numpy as np
import scipy.signal


def pan_tompkins_qrs(ecg: np.ndarray, fs: float = 125.0) -> np.ndarray:
    """
    Detect ECG R-peak locations using the Pan-Tompkins algorithm adapted for 125 Hz.

    Parameters
    ----------
    ecg : np.ndarray
        Raw or pre-conditioned ECG Lead II signal array (1D float).
    fs : float, optional
        Sampling frequency in Hz (default: 125.0).

    Returns
    -------
    np.ndarray
        Sorted 1D integer array of sample indices corresponding to detected R-peaks.
        Returns empty np.array([], dtype=int) if signal is invalid, flatline, or too short.
    """
    ecg = np.asarray(ecg, dtype=np.float64)
    if ecg.ndim != 1 or len(ecg) < int(fs * 1.0) or not np.all(np.isfinite(ecg)) or float(np.std(ecg)) < 1e-4:
        return np.array([], dtype=int)

    # 1. 3rd-order zero-phase Butterworth bandpass filter (5.0 - 15.0 Hz)
    sos = scipy.signal.butter(3, [5.0, 15.0], btype="bandpass", fs=fs, output="sos")
    bpf = scipy.signal.sosfiltfilt(sos, ecg)

    # 2. 5-point central difference derivative filter
    h_der = np.array([-1.0, -2.0, 0.0, 2.0, 1.0]) * (fs / 8.0)
    der = np.convolve(bpf, h_der, mode="same")

    # 3. Squaring
    sq = der ** 2

    # 4. Moving Window Integration (150 ms = 19 samples @ 125 Hz)
    n_mwi = max(1, int(round(0.150 * fs)))
    mwi = np.convolve(sq, np.ones(n_mwi) / float(n_mwi), mode="same")

    # 5. Dual Adaptive Thresholding with 200 ms Refractory Gating
    min_dist = max(1, int(round(0.200 * fs)))  # 25 samples @ 125 Hz
    t_wave_dist = int(round(0.360 * fs))       # 45 samples @ 125 Hz

    # Initialize from full window using 95th percentile to avoid artifact spikes
    mwi_p95 = float(np.percentile(mwi, 95)) if len(mwi) > 0 else 1.0
    bpf_p95 = float(np.percentile(np.abs(bpf), 95)) if len(bpf) > 0 else 1.0

    spki = mwi_p95 * 0.75 if mwi_p95 > 1e-6 else 1.0
    npki = float(np.median(mwi)) if len(mwi) > 0 else 0.1
    th1 = npki + 0.25 * (spki - npki)
    th2 = 0.5 * th1

    spkf = bpf_p95 * 0.75 if bpf_p95 > 1e-6 else 1.0
    npkf = float(np.median(np.abs(bpf))) if len(bpf) > 0 else 0.1
    th_f1 = npkf + 0.25 * (spkf - npkf)
    th_f2 = 0.5 * th_f1

    # Candidate peaks in MWI
    cand_indices, _ = scipy.signal.find_peaks(mwi, distance=min_dist)
    if len(cand_indices) == 0:
        return np.array([], dtype=int)

    qrs_peaks = []
    rr_intervals = []
    last_qrs_idx = -min_dist
    last_qrs_slope = 1.0
    rr_avg1 = int(round(fs * 0.8))  # nominal 75 bpm (100 samples)

    for c in cand_indices:
        val_mwi = mwi[c]
        val_bpf = float(np.max(np.abs(bpf[max(0, c - 6):min(len(bpf), c + 7)])))
        dist = c - last_qrs_idx

        if dist < min_dist:
            continue

        is_qrs = False
        is_searchback = False
        if val_mwi > th1 and val_bpf > th_f1:
            # T-wave discrimination for intervals 200 - 360 ms
            if dist < t_wave_dist:
                slope = float(np.max(np.abs(der[max(0, c - 3):min(len(der), c + 4)])))
                if slope < 0.5 * last_qrs_slope:
                    # T-wave rejected as noise
                    npki = 0.125 * val_mwi + 0.875 * npki
                    npkf = 0.125 * val_bpf + 0.875 * npkf
                    th1 = npki + 0.25 * (spki - npki)
                    th2 = 0.5 * th1
                    th_f1 = npkf + 0.25 * (spkf - npkf)
                    th_f2 = 0.5 * th_f1
                    continue
            is_qrs = True
        elif dist > int(round(1.66 * rr_avg1)) and val_mwi > th2 and val_bpf > th_f2:
            # Searchback hit
            is_qrs = True
            is_searchback = True
            spki = 0.25 * val_mwi + 0.75 * spki
            spkf = 0.25 * val_bpf + 0.75 * spkf
        else:
            # Noise peak
            npki = 0.125 * val_mwi + 0.875 * npki
            npkf = 0.125 * val_bpf + 0.875 * npkf

        # Update thresholds
        th1 = npki + 0.25 * (spki - npki)
        th2 = 0.5 * th1
        th_f1 = npkf + 0.25 * (spkf - npkf)
        th_f2 = 0.5 * th_f1

        if is_qrs:
            # 6. Refine to true R-peak extremum in BPF (+/- 6 samples = 48 ms)
            w_start = max(0, c - 6)
            w_end = min(len(bpf), c + 7)
            seg_bpf = bpf[w_start:w_end]
            if len(seg_bpf) > 0:
                pos_max = int(np.argmax(seg_bpf))
                neg_min = int(np.argmin(seg_bpf))
                if abs(seg_bpf[neg_min]) > abs(seg_bpf[pos_max]):
                    true_r = w_start + neg_min
                else:
                    true_r = w_start + pos_max
            else:
                true_r = c

            if last_qrs_idx >= 0 and (true_r - last_qrs_idx) < (min_dist - 3):
                continue

            qrs_peaks.append(int(true_r))
            if last_qrs_idx > 0:
                cur_rr = true_r - last_qrs_idx
                rr_intervals.append(cur_rr)
                if len(rr_intervals) > 8:
                    rr_intervals.pop(0)
                rr_avg1 = int(round(float(np.mean(rr_intervals))))

            last_qrs_idx = true_r
            last_qrs_slope = float(np.max(np.abs(der[max(0, c - 3):min(len(der), c + 4)])))

            # Update signal levels
            if not is_searchback:
                spki = 0.125 * val_mwi + 0.875 * spki
                spkf = 0.125 * val_bpf + 0.875 * spkf
            th1 = npki + 0.25 * (spki - npki)
            th2 = 0.5 * th1
            th_f1 = npkf + 0.25 * (spkf - npkf)
            th_f2 = 0.5 * th_f1

    # Ensure unique and sorted
    sorted_unique = np.unique(np.array(qrs_peaks, dtype=int))
    return sorted_unique
