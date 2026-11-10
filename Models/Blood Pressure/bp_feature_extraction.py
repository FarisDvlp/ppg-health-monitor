"""
Blood Pressure Feature Extraction Module.

Provides:
- Incremental batched UCI dataset reader (`load_uci_record`, `iter_cuffless_bp_windows`)
- MIMIC-BP dataset reader (`load_mimic_bp_subject`, `iter_mimic_bp_windows`)
- Synchronized reference ABP target extraction (`extract_abp_targets`, `extract_abp_ground_truth`)
- Comprehensive 83-feature and 107-feature extraction from 60s PPG windows (`extract_features_from_window`, `extract_107_features_from_window`)
  Categories A–F: Morphological time/amplitude, Widths, Derivatives, Demographic Stiffness/Velocity Ratios (58–75), Spectral (76–91), Statistical (92–101), Demographic & Heart Rate (102–107)
- ReliefF feature selection for continuous targets (`relieff_feature_selection`)
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

try:
    import h5py
except ImportError:
    h5py = None
import numpy as np
import scipy.signal
import scipy.stats
import scipy.integrate

from core.morphology import extract_all_fiducials, BeatSegment, FiducialResult
from core.preprocessing import preprocess_window
from core.sqi import evaluate_sqi


# Canonical 83 feature names corresponding to Categories A through E
FEATURE_NAMES: List[str] = [
    # Category A: Morphological Time & Amplitude Features (1-24)
    "systolic_peak_amp",          # 1
    "diastolic_peak_amp",         # 2
    "dicrotic_notch_amp",         # 3
    "systolic_peak_time",         # 4
    "dicrotic_notch_time",        # 5
    "diastolic_peak_time",        # 6
    "delta_t",                    # 7
    "pulse_interval",             # 8
    "peak_to_peak_interval",      # 9
    "pulse_width_50",             # 10
    "inflection_point_area_ratio",# 11
    "augmentation_index",         # 12
    "alt_augmentation_index",     # 13
    "systolic_output_curve",      # 14
    "diastolic_downward_curve",   # 15
    "t1_tpp_ratio",               # 16
    "t2_tpp_ratio",               # 17
    "t3_tpp_ratio",               # 18
    "delta_t_tpp_ratio",          # 19
    "z_x_ratio",                  # 20
    "t2_z_ratio",                 # 21
    "t3_y_ratio",                 # 22
    "x_decay_rate",               # 23
    "z_decay_rate",               # 24
    # Category B: Width-Related Pulse Features (25-41)
    "pulse_width_25",             # 25
    "pulse_width_75",             # 26
    "w25_t1_ratio",               # 27
    "w25_t2_ratio",               # 28
    "w25_t3_ratio",               # 29
    "w25_delta_t_ratio",          # 30
    "w25_tpi_ratio",              # 31
    "w50_t1_ratio",               # 32
    "w50_t2_ratio",               # 33
    "w50_t3_ratio",               # 34
    "w50_delta_t_ratio",          # 35
    "w50_tpi_ratio",              # 36
    "w75_t1_ratio",               # 37
    "w75_t2_ratio",               # 38
    "w75_t3_ratio",               # 39
    "w75_delta_t_ratio",          # 40
    "w75_tpi_ratio",              # 41
    # Category C: APG & VPG Derivative Features (42-57)
    "apg_a1",                     # 42
    "time_a1",                    # 43
    "vpg_a2",                     # 44
    "time_a2",                    # 45
    "apg_b1",                     # 46
    "time_b1",                    # 47
    "vpg_b2",                     # 48
    "time_b2",                    # 49
    "vpg_ratio_b2_a2",            # 50
    "apg_ratio_b1_a1",            # 51
    "ta1_tpp_ratio",              # 52
    "tb1_tpp_ratio",              # 53
    "tb2_tpp_ratio",              # 54
    "ta2_tpp_ratio",              # 55
    "ta1_ta2_diff_ratio",         # 56
    "tb1_tb2_diff_ratio",         # 57
    # Category D: Frequency-Domain Spectral Features (76-91 in plan, 58-73 here)
    "spectral_peak_1",            # 58
    "spectral_peak_2",            # 59
    "spectral_peak_3",            # 60
    "spectral_freq_1",            # 61
    "spectral_freq_2",            # 62
    "spectral_freq_3",            # 63
    "spectral_area_0_2",          # 64
    "spectral_area_2_5",          # 65
    "spectral_area_ratio",        # 66
    "harmonic_peak_ratio_1",      # 67
    "harmonic_peak_ratio_2",      # 68
    "harmonic_freq_ratio_1",      # 69
    "harmonic_freq_ratio_2",      # 70
    "f_max",                      # 71
    "magnitude_at_f_max",         # 72
    "relative_spectral_energy",   # 73
    # Category E: Statistical & Information-Theoretic Features (92-101 in plan, 74-83 here)
    "mean",                       # 74
    "median",                     # 75
    "std",                        # 76
    "percentile_25",              # 77
    "percentile_75",              # 78
    "mad",                        # 79
    "iqr",                        # 80
    "skewness",                   # 81
    "kurtosis",                   # 82
    "shannon_entropy",            # 83
]

# Newly added 18 Demographic-to-Interval Ratio Features (Features 58-75 in 107-feature specification)
DEMOGRAPHIC_RATIO_FEATURE_NAMES: List[str] = [
    "height_delta_t_ratio",       # 58: Height/∆T (stiffness index)
    "weight_delta_t_ratio",       # 59: Weight/∆T
    "bmi_delta_t_ratio",          # 60: BMI/∆T
    "height_t1_ratio",            # 61: Height/t1
    "weight_t1_ratio",            # 62: Weight/t1
    "bmi_t1_ratio",               # 63: BMI/t1
    "height_t2_ratio",            # 64: Height/t2
    "weight_t2_ratio",            # 65: Weight/t2
    "bmi_t2_ratio",               # 66: BMI/t2
    "height_t3_ratio",            # 67: Height/t3
    "weight_t3_ratio",            # 68: Weight/t3
    "bmi_t3_ratio",               # 69: BMI/t3
    "height_tpi_ratio",           # 70: Height/tpi
    "weight_tpi_ratio",           # 71: Weight/tpi
    "bmi_tpi_ratio",              # 72: BMI/tpi
    "height_tpp_ratio",           # 73: Height/tpp
    "weight_tpp_ratio",           # 74: Weight/tpp
    "bmi_tpp_ratio",              # 75: BMI/tpp
]

# Newly added 6 Demographic & Heart Rate Features (Features 102-107 in 107-feature specification)
DEMOGRAPHIC_HR_FEATURE_NAMES: List[str] = [
    "height",                     # 102: Height (cm)
    "weight",                     # 103: Weight (kg)
    "gender",                     # 104: Gender indicator [0, 1]
    "age",                        # 105: Age (years)
    "bmi",                        # 106: Body Mass Index (kg/m^2)
    "heart_rate",                 # 107: Heart rate (BPM)
]

# Complete canonical 107 feature names (Features 1 through 107 in exact specification order)
FEATURE_NAMES_107: List[str] = (
    FEATURE_NAMES[:57]            # 1-57: Categories A, B, C
    + DEMOGRAPHIC_RATIO_FEATURE_NAMES  # 58-75: Demographic-to-Interval Stiffness & Velocity Ratios
    + FEATURE_NAMES[57:73]        # 76-91: Category D (Spectral)
    + FEATURE_NAMES[73:83]        # 92-101: Category E (Statistical & Entropy)
    + DEMOGRAPHIC_HR_FEATURE_NAMES     # 102-107: Demographic & Heart Rate
)

# Category G: Multi-Beat Respiratory & Dynamic Compliance Dispersion (Features 108-117)
DISPERSION_FEATURE_NAMES: List[str] = [
    "ppg_amp_std",                # 108: Std dev of systolic peak amplitudes across beats
    "ppg_amp_iqr",                # 109: IQR of systolic peak amplitudes across beats
    "t1_std",                     # 110: Std dev of systolic crest time t1
    "t1_iqr",                     # 111: IQR of systolic crest time t1
    "delta_t_std",                # 112: Std dev of reflection delay delta_t
    "delta_t_iqr",                # 113: IQR of reflection delay delta_t
    "w50_std",                    # 114: Std dev of 50% pulse width
    "w50_iqr",                    # 115: IQR of 50% pulse width
    "tpi_std",                    # 116: Std dev of pulse interval (PPI standard deviation)
    "tpi_iqr",                    # 117: IQR of pulse interval
]

# Category H: Discrete Wavelet Transform (DWT) Sub-Band Energy & Entropy (Features 118-125)
WAVELET_FEATURE_NAMES: List[str] = [
    "wavelet_energy_d1",          # 118: Detail coefficient D1 relative energy (high-frequency resonance)
    "wavelet_energy_d2",          # 119: Detail coefficient D2 relative energy
    "wavelet_energy_d3",          # 120: Detail coefficient D3 relative energy (dicrotic reflection)
    "wavelet_energy_d4",          # 121: Detail coefficient D4 relative energy (systolic peak wave)
    "wavelet_energy_d5",          # 122: Detail coefficient D5 relative energy (cardiac fundamental)
    "wavelet_energy_a5",          # 123: Approximation coefficient A5 relative energy
    "wavelet_entropy",            # 124: Wavelet sub-band distribution Shannon entropy
    "wavelet_detail_ratio",       # 125: Ratio of detail energies (D1-D4) to fundamental (D5+A5)
]

# Complete canonical 125 feature names (Features 1 through 125)
FEATURE_NAMES_125: List[str] = (
    FEATURE_NAMES_107
    + DISPERSION_FEATURE_NAMES
    + WAVELET_FEATURE_NAMES
)


def load_uci_record(
    part_idx: int = 1,
    record_idx: int = 0,
    duration_sec: float = 60.0,
    start_sec: float = 0.0,
    data_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, np.ndarray]:
    """
    Incremental memory-safe loader for a single record from the UCI Cuff-less BP dataset.

    Parameters
    ----------
    part_idx : int, default=1
        Part index (1 to 4).
    record_idx : int, default=0
        Subject cell index in the part file.
    duration_sec : float, default=60.0
        Duration to slice in seconds.
    start_sec : float, default=0.0
        Offset in seconds from start of record.
    data_dir : Path or str, optional
        Custom directory containing Part_X.mat files.

    Returns
    -------
    Dict[str, np.ndarray]
        Dictionary with keys 'ppg', 'abp', 'ecg' (1D float64 arrays).
    """
    if data_dir is None:
        base_dir = Path(__file__).resolve().parent
    else:
        base_dir = Path(data_dir)

    mat_candidates = [
        base_dir / f"Part_{part_idx}.mat",
        base_dir / f"part_{part_idx}.mat",
        base_dir / "dataset" / f"Part_{part_idx}.mat",
        base_dir / "dataset" / f"part_{part_idx}.mat",
    ]
    mat_file = None
    for cand in mat_candidates:
        if cand.exists():
            mat_file = cand
            break

    if h5py is None:
        raise ImportError(
            "h5py is required to load UCI dataset records, but it is not installed. "
            "Please install h5py to use offline dataset loading functions."
        )

    if mat_file is None:
        raise FileNotFoundError(f"UCI dataset file not found for Part_{part_idx} in {base_dir}")

    fs = 125.0
    start_sample = int(round(start_sec * fs))
    n_samples = int(round(duration_sec * fs))

    with h5py.File(mat_file, "r") as f:
        key = f"Part_{part_idx}" if f"Part_{part_idx}" in f else f"part_{part_idx}"
        target_ds = f[key]
        ref = target_ds[record_idx, 0] if target_ds.ndim > 1 else target_ds[record_idx]
        raw_record = f[ref]

        if raw_record.shape[0] == 3 and raw_record.shape[1] > 3:
            slice_data = raw_record[:, start_sample : start_sample + n_samples].T
        else:
            slice_data = raw_record[start_sample : start_sample + n_samples, :]

        slice_data = np.asarray(slice_data, dtype=np.float64)
        return {
            "ppg": slice_data[:, 0],
            "abp": slice_data[:, 1],
            "ecg": slice_data[:, 2],
        }


def iter_cuffless_bp_windows(
    dataset_dir: Optional[Union[str, Path]] = None,
    part_files: Optional[List[str]] = None,
    max_records: Optional[int] = 50,
    max_records_per_part: Optional[int] = None,
    min_record_samples: int = 7500,
    window_len_sec: float = 60.0,
    fs: float = 125.0,
) -> Iterator[Tuple[str, int, np.ndarray, np.ndarray, np.ndarray]]:
    """
    Memory-safe incremental generator yielding 60s windows directly via HDF5 slicing.
    Never loads entire .mat files into RAM.

    Parameters
    ----------
    dataset_dir : Path or str, optional
        Path to folder containing Part_1.mat ... Part_4.mat.
    part_files : list of str, optional
        List of .mat files to iterate across.
    max_records : int, optional
        Total maximum records to extract across all parts.
    max_records_per_part : int, optional
        Maximum records to extract per individual part file (useful for balanced multi-part cohorts).
    min_record_samples : int, default=7500
        Minimum length required to process a record.
    window_len_sec : float, default=60.0
        Duration in seconds per extracted window.
    fs : float, default=125.0
        Sampling frequency in Hz.
    """
    if h5py is None:
        raise ImportError(
            "h5py is required to iterate UCI dataset records, but it is not installed. "
            "Please install h5py to use offline dataset loading functions."
        )

    if dataset_dir is None:
        dataset_dir = Path(__file__).resolve().parent
    else:
        dataset_dir = Path(dataset_dir)

    if part_files is None:
        part_files = ["Part_1.mat", "Part_2.mat", "Part_3.mat", "Part_4.mat"]

    window_size = int(round(window_len_sec * fs))
    records_processed = 0

    for part_name in part_files:
        file_path = dataset_dir / part_name
        if not file_path.exists():
            file_path = dataset_dir / "dataset" / part_name
        if not file_path.exists():
            continue

        var_name = part_name.replace(".mat", "")
        with h5py.File(file_path, "r") as f:
            v_key = var_name if var_name in f else var_name.lower()
            if v_key not in f:
                continue
            ds = f[v_key]
            total_cells = len(ds)
            records_in_this_part = 0

            for cell_idx in range(total_cells):
                if max_records is not None and records_processed >= max_records:
                    return
                if max_records_per_part is not None and records_in_this_part >= max_records_per_part:
                    break

                ref = ds[cell_idx, 0] if ds.ndim > 1 else ds[cell_idx]
                target_ds = f[ref]
                s = target_ds.shape
                n_samples = max(s[0], s[1])
                if n_samples < min_record_samples:
                    continue

                records_processed += 1
                records_in_this_part += 1
                group_id = f"{var_name}_rec_{cell_idx:04d}"
                n_windows = n_samples // window_size

                for w in range(n_windows):
                    start = w * window_size
                    end = start + window_size

                    if s[1] == 3:
                        w_slice = target_ds[start:end, :]
                    else:
                        w_slice = target_ds[:, start:end].T

                    yield (
                        group_id,
                        w,
                        np.asarray(w_slice[:, 0], dtype=np.float64),
                        np.asarray(w_slice[:, 1], dtype=np.float64),
                        np.asarray(w_slice[:, 2], dtype=np.float64),
                    )


def load_mimic_bp_subject(
    subject_id: str,
    dataset_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, np.ndarray]:
    """
    Loads PPG, ABP, RESP waveforms and [SBP, DBP] ground-truth labels for a MIMIC-BP subject.

    Parameters
    ----------
    subject_id : str
        Subject identifier (e.g., 'p000188').
    dataset_dir : Path or str, optional
        Path to MIMIC-BP dataset root directory (`Blood Pressure/dataset/mimic-bp`).

    Returns
    -------
    Dict[str, np.ndarray]
        Dictionary containing:
          - 'ppg': shape (30, 3750), float64
          - 'abp': shape (30, 3750), float64
          - 'resp': shape (30, 3750), float64
          - 'labels': shape (30, 2), float64 ([SBP, DBP] per 30s segment)
    """
    if dataset_dir is None:
        base_dir = Path(__file__).resolve().parent / "dataset" / "mimic-bp"
    else:
        base_dir = Path(dataset_dir)
        if not (base_dir / "ppg").exists() and (base_dir / "dataset" / "mimic-bp" / "ppg").exists():
            base_dir = base_dir / "dataset" / "mimic-bp"

    ppg_path = base_dir / "ppg" / f"{subject_id}_ppg.npy"
    abp_path = base_dir / "abp" / f"{subject_id}_abp.npy"
    resp_path = base_dir / "resp" / f"{subject_id}_resp.npy"
    lbl_path = base_dir / "labels" / f"{subject_id}_labels.npy"

    if not ppg_path.exists():
        raise FileNotFoundError(f"MIMIC-BP PPG file not found: {ppg_path}")

    return {
        "ppg": np.asarray(np.load(ppg_path), dtype=np.float64),
        "abp": np.asarray(np.load(abp_path), dtype=np.float64) if abp_path.exists() else np.zeros((30, 3750), dtype=np.float64),
        "resp": np.asarray(np.load(resp_path), dtype=np.float64) if resp_path.exists() else np.zeros((30, 3750), dtype=np.float64),
        "labels": np.asarray(np.load(lbl_path), dtype=np.float64) if lbl_path.exists() else np.zeros((30, 2), dtype=np.float64),
    }


def iter_mimic_bp_windows(
    dataset_dir: Optional[Union[str, Path]] = None,
    split: str = "all",
    max_subjects: Optional[int] = None,
    max_windows_per_subject: int = 3,
    sbp_range: Tuple[float, float] = (85.0, 160.0),
    dbp_range: Tuple[float, float] = (45.0, 100.0),
    max_pair_delta_bp: float = 5.0,
) -> Iterator[Tuple[str, int, np.ndarray, np.ndarray, np.ndarray, float, float]]:
    """
    Incremental generator yielding 60-second (7,500-sample @ 125 Hz) steady-state windows
    from the MIMIC-BP dataset by pairing hemodynamically stable 30-second segments.

    Parameters
    ----------
    dataset_dir : Path or str, optional
        Path to `Blood Pressure/dataset/mimic-bp`.
    split : str, default="all"
        One of 'train', 'val', 'test', or 'all'.
    max_subjects : int, optional
        Maximum number of subjects to yield windows from.
    max_windows_per_subject : int, default=3
        Maximum number of 60s windows to extract per subject.
    sbp_range : tuple of float, default=(85.0, 160.0)
        Physiological steady-state SBP inclusion range in mmHg.
    dbp_range : tuple of float, default=(45.0, 100.0)
        Physiological steady-state DBP inclusion range in mmHg.
    max_pair_delta_bp : float, default=5.0
        Maximum allowed BP difference (mmHg) between paired 30s segments.

    Yields
    ------
    Tuple[str, int, np.ndarray, np.ndarray, np.ndarray, float, float]
        (subject_id, window_idx, ppg_60s, abp_60s, resp_60s, sbp_mmHg, dbp_mmHg)
    """
    if dataset_dir is None:
        base_dir = Path(__file__).resolve().parent / "dataset" / "mimic-bp"
    else:
        base_dir = Path(dataset_dir)
        if not (base_dir / "ppg").exists() and (base_dir / "dataset" / "mimic-bp" / "ppg").exists():
            base_dir = base_dir / "dataset" / "mimic-bp"

    split_files = {
        "train": ["train_subjects.txt"],
        "val": ["val_subjects.txt"],
        "test": ["test_subjects.txt"],
        "all": ["train_subjects.txt", "val_subjects.txt", "test_subjects.txt"],
    }.get(split.lower(), ["train_subjects.txt", "val_subjects.txt", "test_subjects.txt"])

    subject_ids: List[str] = []
    for sf in split_files:
        sf_path = base_dir / sf
        if sf_path.exists():
            subject_ids.extend(ast.literal_eval(sf_path.read_text(encoding="utf-8")))

    subjects_yielded = 0
    for subj_id in subject_ids:
        if max_subjects is not None and subjects_yielded >= max_subjects:
            return

        lbl_path = base_dir / "labels" / f"{subj_id}_labels.npy"
        ppg_path = base_dir / "ppg" / f"{subj_id}_ppg.npy"
        if not lbl_path.exists() or not ppg_path.exists():
            continue

        labels = np.asarray(np.load(lbl_path), dtype=np.float64)
        if labels.ndim != 2 or labels.shape[1] < 2 or len(labels) < 2:
            continue

        med_sbp = float(np.median(labels[:, 0]))
        med_dbp = float(np.median(labels[:, 1]))
        if not (sbp_range[0] <= med_sbp <= sbp_range[1]):
            continue
        if not (dbp_range[0] <= med_dbp <= dbp_range[1]):
            continue
        if med_sbp < med_dbp + 20.0:
            continue

        # Rank segments by closeness to subject's steady-state median BP
        dist_to_med = np.abs(labels[:, 0] - med_sbp) + np.abs(labels[:, 1] - med_dbp)
        steady_order = np.argsort(dist_to_med)

        # Pair consecutive steady-state segments into 60s (7,500-sample) windows
        candidate_pairs: List[Tuple[int, int]] = []
        for k in range(0, len(steady_order) - 1, 2):
            idx_a = int(steady_order[k])
            idx_b = int(steady_order[k + 1])
            if (
                abs(labels[idx_a, 0] - labels[idx_b, 0]) <= max_pair_delta_bp
                and abs(labels[idx_a, 1] - labels[idx_b, 1]) <= max_pair_delta_bp
                and abs(0.5 * (labels[idx_a, 0] + labels[idx_b, 0]) - med_sbp) <= max_pair_delta_bp
            ):
                candidate_pairs.append((idx_a, idx_b))
            if len(candidate_pairs) >= max_windows_per_subject:
                break

        if not candidate_pairs:
            continue

        data = load_mimic_bp_subject(subj_id, base_dir)
        ppg_arr = data["ppg"]
        abp_arr = data["abp"]
        resp_arr = data["resp"]

        windows_for_subj = 0
        for w_idx, (ia, ib) in enumerate(candidate_pairs):
            ppg_60s = np.concatenate([ppg_arr[ia], ppg_arr[ib]])
            abp_60s = np.concatenate([abp_arr[ia], abp_arr[ib]])
            resp_60s = np.concatenate([resp_arr[ia], resp_arr[ib]])
            sbp_gt = float(0.5 * (labels[ia, 0] + labels[ib, 0]))
            dbp_gt = float(0.5 * (labels[ia, 1] + labels[ib, 1]))
            if not (sbp_range[0] <= sbp_gt <= sbp_range[1] and dbp_range[0] <= dbp_gt <= dbp_range[1]):
                continue
            if sbp_gt < dbp_gt + 20.0:
                continue

            windows_for_subj += 1
            yield (subj_id, w_idx, ppg_60s, abp_60s, resp_60s, sbp_gt, dbp_gt)

        if windows_for_subj > 0:
            subjects_yielded += 1


def extract_abp_targets(
    abp_signal: np.ndarray,
    fs: float = 125.0,
) -> Dict[str, float]:
    """
    Extracts reference SBP, DBP, and MAP from a continuous arterial blood pressure waveform.
    """
    abp_signal = np.asarray(abp_signal, dtype=np.float64)
    if len(abp_signal) < int(fs * 2):
        return {"sbp": 120.0, "dbp": 80.0, "map": 93.333}

    min_dist = max(int(0.35 * fs), 10)
    peaks, _ = scipy.signal.find_peaks(abp_signal, distance=min_dist, prominence=10.0)
    troughs, _ = scipy.signal.find_peaks(-abp_signal, distance=min_dist, prominence=10.0)

    if len(peaks) >= 5 and len(troughs) >= 5:
        sbp = float(np.median(abp_signal[peaks]))
        dbp = float(np.median(abp_signal[troughs]))
    else:
        sbp = float(np.percentile(abp_signal, 95))
        dbp = float(np.percentile(abp_signal, 5))

    if sbp < dbp + 15.0:
        sbp = dbp + 25.0

    map_val = float((2.0 * dbp + sbp) / 3.0)
    return {"sbp": sbp, "dbp": dbp, "map": map_val}


def extract_abp_ground_truth(
    abp_window: np.ndarray,
    fs: float = 125.0,
) -> Optional[Tuple[float, float]]:
    """
    Extracts median SBP and DBP from a 60-second calibrated ABP window.
    Enforces strict physiological validity bounds:
      - 70 <= SBP <= 200 mmHg
      - 40 <= DBP <= 130 mmHg
      - SBP - DBP >= 15 mmHg
      - MAP in [50, 150] mmHg
    Returns (sbp, dbp) or None if non-physiological.
    """
    if len(abp_window) < int(fs * 10):
        return None

    min_dist = int(0.4 * fs)
    peaks, _ = scipy.signal.find_peaks(abp_window, distance=min_dist, prominence=12.0)
    troughs, _ = scipy.signal.find_peaks(-abp_window, distance=min_dist, prominence=12.0)

    if len(peaks) < 20 or len(troughs) < 20:
        # Fallback to percentile estimation
        sbp_target = float(np.percentile(abp_window, 95))
        dbp_target = float(np.percentile(abp_window, 5))
    else:
        sbp_vals = abp_window[peaks]
        dbp_vals = abp_window[troughs]
        if float(np.std(sbp_vals)) > 20.0:
            return None
        sbp_target = float(np.median(sbp_vals))
        dbp_target = float(np.median(dbp_vals))

    pulse_pressure = sbp_target - dbp_target
    map_target = (2.0 * dbp_target + sbp_target) / 3.0

    if not (70.0 <= sbp_target <= 200.0):
        return None
    if not (40.0 <= dbp_target <= 130.0):
        return None
    if pulse_pressure < 15.0:
        return None
    if not (50.0 <= map_target <= 150.0):
        return None

    return sbp_target, dbp_target


def _compute_pulse_width(
    pulse_waveform: np.ndarray,
    peak_idx: int,
    height_fraction: float,
    fs: float,
) -> float:
    """Computes pulse width in seconds at a given height fraction."""
    v = pulse_waveform[0]
    x = pulse_waveform[peak_idx]
    if x <= v + 1e-6:
        return 0.2

    h = v + height_fraction * (x - v)
    
    # Rising side: search backwards from peak
    t_rise = 0.0
    for i in range(peak_idx - 1, -1, -1):
        if pulse_waveform[i] <= h <= pulse_waveform[i + 1]:
            diff = pulse_waveform[i + 1] - pulse_waveform[i]
            frac = (h - pulse_waveform[i]) / max(diff, 1e-6)
            t_rise = float(i + frac)
            break

    # Falling side: search forwards from peak
    t_fall = float(len(pulse_waveform) - 1)
    for i in range(peak_idx, len(pulse_waveform) - 1):
        if pulse_waveform[i] >= h >= pulse_waveform[i + 1]:
            diff = pulse_waveform[i] - pulse_waveform[i + 1]
            frac = (pulse_waveform[i] - h) / max(diff, 1e-6)
            t_fall = float(i + frac)
            break

    width_sec = max((t_fall - t_rise) / fs, 0.01)
    return width_sec


def _extract_single_beat_features(
    beat: BeatSegment,
    next_beat_peak: Optional[int],
    norm_signal: np.ndarray,
    fs: float,
) -> Dict[str, float]:
    """Extracts Categories A, B, and C features (57 features) for a single beat cycle."""
    t_onset = beat.onset_idx
    t_sys = beat.systolic_peak_idx
    t_notch = beat.dicrotic_notch_idx
    t_dia = beat.diastolic_peak_idx
    t_offset = beat.offset_idx

    v = beat.onset_amp
    x_val = beat.systolic_amp
    z_val = beat.notch_amp if t_notch >= 0 else (v + 0.3 * (x_val - v))
    y_val = beat.diastolic_amp if t_dia >= 0 else (v + 0.5 * (x_val - v))

    t_pi = max((t_offset - t_onset) / fs, 0.1)
    t1 = max((t_sys - t_onset) / fs, 0.02)
    t2 = (t_notch - t_onset) / fs if t_notch > t_onset else (0.4 * t_pi)
    t3 = (t_dia - t_onset) / fs if t_dia > t_onset else (0.6 * t_pi)
    if t3 <= t1:
        t3 = t1 + 0.2 * t_pi
    delta_t = max(t3 - t1, 0.01)

    if next_beat_peak is not None and next_beat_peak > t_sys:
        t_pp = max((next_beat_peak - t_sys) / fs, 0.1)
    else:
        t_pp = t_pi

    # Waveform slice for width and area
    start_s = max(0, t_onset)
    end_s = min(len(norm_signal), max(t_offset + 1, t_sys + 2))
    pulse_slice = norm_signal[start_s:end_s]
    local_peak = min(max(t_sys - start_s, 1), len(pulse_slice) - 1)

    w25 = _compute_pulse_width(pulse_slice, local_peak, 0.25, fs)
    w50 = _compute_pulse_width(pulse_slice, local_peak, 0.50, fs)
    w75 = _compute_pulse_width(pulse_slice, local_peak, 0.75, fs)

    # Inflection point area ratio A1 / A2
    split_k = min(max(int(t2 * fs), 1), len(pulse_slice) - 1)
    a1_area = float(np.sum(np.maximum(pulse_slice[:split_k] - v, 0.0)))
    a2_area = float(np.sum(np.maximum(pulse_slice[split_k:] - v, 0.0)))
    area_ratio = a1_area / max(a2_area, 1e-4)

    # Amplitudes above baseline
    amp_x = max(x_val - v, 1e-4)
    amp_y = max(y_val - v, 1e-4)
    amp_z = max(z_val - v, 1e-4)

    # Category A: Features 1-24
    feat: Dict[str, float] = {}
    feat["systolic_peak_amp"] = float(x_val)
    feat["diastolic_peak_amp"] = float(y_val)
    feat["dicrotic_notch_amp"] = float(z_val)
    feat["systolic_peak_time"] = float(t1)
    feat["dicrotic_notch_time"] = float(t2)
    feat["diastolic_peak_time"] = float(t3)
    feat["delta_t"] = float(delta_t)
    feat["pulse_interval"] = float(t_pi)
    feat["peak_to_peak_interval"] = float(t_pp)
    feat["pulse_width_50"] = float(w50)
    feat["inflection_point_area_ratio"] = float(area_ratio)
    feat["augmentation_index"] = float(amp_y / amp_x)
    feat["alt_augmentation_index"] = float((amp_x - amp_y) / amp_x)
    feat["systolic_output_curve"] = float(t1 / amp_x)
    feat["diastolic_downward_curve"] = float(amp_y / max(t_pi - t3, 0.01))
    feat["t1_tpp_ratio"] = float(t1 / t_pp)
    feat["t2_tpp_ratio"] = float(t2 / t_pp)
    feat["t3_tpp_ratio"] = float(t3 / t_pp)
    feat["delta_t_tpp_ratio"] = float(delta_t / t_pp)
    feat["z_x_ratio"] = float(amp_z / amp_x)
    feat["t2_z_ratio"] = float(t2 / amp_z)
    feat["t3_y_ratio"] = float(t3 / amp_y)
    feat["x_decay_rate"] = float(amp_x / max(t_pi - t1, 0.01))
    feat["z_decay_rate"] = float(amp_z / max(t_pi - t2, 0.01))

    # Category B: Features 25-41
    feat["pulse_width_25"] = float(w25)
    feat["pulse_width_75"] = float(w75)
    feat["w25_t1_ratio"] = float(w25 / t1)
    feat["w25_t2_ratio"] = float(w25 / max(t2, 0.01))
    feat["w25_t3_ratio"] = float(w25 / max(t3, 0.01))
    feat["w25_delta_t_ratio"] = float(w25 / delta_t)
    feat["w25_tpi_ratio"] = float(w25 / t_pi)
    feat["w50_t1_ratio"] = float(w50 / t1)
    feat["w50_t2_ratio"] = float(w50 / max(t2, 0.01))
    feat["w50_t3_ratio"] = float(w50 / max(t3, 0.01))
    feat["w50_delta_t_ratio"] = float(w50 / delta_t)
    feat["w50_tpi_ratio"] = float(w50 / t_pi)
    feat["w75_t1_ratio"] = float(w75 / t1)
    feat["w75_t2_ratio"] = float(w75 / max(t2, 0.01))
    feat["w75_t3_ratio"] = float(w75 / max(t3, 0.01))
    feat["w75_delta_t_ratio"] = float(w75 / delta_t)
    feat["w75_tpi_ratio"] = float(w75 / t_pi)

    # Category C: Features 42-57
    ta1 = max((beat.a1_idx - t_onset) / fs, 0.005)
    ta2 = max((beat.a2_idx - t_onset) / fs, 0.005)
    tb1 = max((beat.b1_idx - t_onset) / fs, 0.005)
    tb2 = max((beat.b2_idx - t_onset) / fs, 0.005)

    feat["apg_a1"] = float(beat.a1_amp)
    feat["time_a1"] = float(ta1)
    feat["vpg_a2"] = float(beat.a2_amp)
    feat["time_a2"] = float(ta2)
    feat["apg_b1"] = float(beat.b1_amp)
    feat["time_b1"] = float(tb1)
    feat["vpg_b2"] = float(beat.b2_amp)
    feat["time_b2"] = float(tb2)
    feat["vpg_ratio_b2_a2"] = float(beat.b2_amp / (abs(beat.a2_amp) + 1e-4))
    feat["apg_ratio_b1_a1"] = float(beat.b1_amp / (abs(beat.a1_amp) + 1e-4))
    feat["ta1_tpp_ratio"] = float(ta1 / t_pp)
    feat["tb1_tpp_ratio"] = float(tb1 / t_pp)
    feat["tb2_tpp_ratio"] = float(tb2 / t_pp)
    feat["ta2_tpp_ratio"] = float(ta2 / t_pp)
    feat["ta1_ta2_diff_ratio"] = float((ta1 - ta2) / t_pp)
    feat["tb1_tb2_diff_ratio"] = float((tb1 - tb2) / t_pp)

    return feat


def estimate_demographic_priors(
    agg_beat_features: Dict[str, float],
    cat_d_features: Dict[str, float],
    cat_e_features: Dict[str, float],
    has_beats: bool = True,
    demographics: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Computes demographic & heart rate features (Features 102-107).
    When explicit patient demographics are provided via `demographics`, uses them directly;
    otherwise derives physiologically calibrated vascular-aging and anthropometric priors
    (Takazawa APG aging index b1/a1, augmentation index, reflection time delta_t, and
    pulse width habitus ratios) deterministically from the PPG waveform.
    """
    if not has_beats:
        base = {
            "height": 170.0,
            "weight": 70.0,
            "gender": 0.5,
            "age": 55.0,
            "bmi": 24.22,
            "heart_rate": 0.0,
        }
        if demographics:
            for k in ("height", "weight", "gender", "age", "bmi", "heart_rate"):
                if k in demographics and np.isfinite(demographics[k]):
                    base[k] = float(demographics[k])
        return base

    t1 = max(agg_beat_features.get("systolic_peak_time", 0.16), 0.02)
    t2 = max(agg_beat_features.get("dicrotic_notch_time", 0.32), 0.04)
    delta_t = max(agg_beat_features.get("delta_t", 0.22), 0.02)
    t_pi = max(agg_beat_features.get("pulse_interval", 0.80), 0.20)
    t_pp = max(agg_beat_features.get("peak_to_peak_interval", t_pi), 0.25)
    ai = agg_beat_features.get("augmentation_index", 0.45)
    b1_a1 = agg_beat_features.get("apg_ratio_b1_a1", -0.65)
    b2_a2 = agg_beat_features.get("vpg_ratio_b2_a2", -0.50)
    w50_tpi = agg_beat_features.get("w50_tpi_ratio", 0.38)
    w75_tpi = agg_beat_features.get("w75_tpi_ratio", 0.22)
    ipa = agg_beat_features.get("inflection_point_area_ratio", 1.8)
    soc = agg_beat_features.get("systolic_output_curve", 0.12)
    z_x = agg_beat_features.get("z_x_ratio", 0.40)
    ta1_ta2 = agg_beat_features.get("ta1_ta2_diff_ratio", -0.04)
    z_dec = agg_beat_features.get("z_decay_rate", 1.2)
    x_dec = agg_beat_features.get("x_decay_rate", 2.5)
    vpg_a2 = agg_beat_features.get("vpg_a2", 0.15)
    spec_ratio = cat_d_features.get("spectral_area_ratio", 2.5)
    h_freq_2 = cat_d_features.get("harmonic_freq_ratio_2", 0.35)
    skew_val = cat_e_features.get("skewness", 0.3)

    # Heart rate (BPM) from median peak-to-peak interval
    hr_bpm = float(np.clip(60.0 / t_pp, 30.0, 220.0))

    # Vascular age proxy (years) from Takazawa APG index (b1/a1), AI, ta1-ta2 diff, and reflection time delta_t
    est_age = float(
        np.clip(
            58.0
            + 20.0 * (ai - 0.45)
            - 55.0 * (delta_t - 0.22)
            + 9.5 * (b1_a1 + 0.65)
            + 4.0 * (b2_a2 + 0.50)
            - 85.0 * (ta1_ta2 + 0.04)
            + 6.0 * (h_freq_2 - 0.35)
            - 1.2 * (spec_ratio - 2.5),
            20.0,
            88.0,
        )
    )

    # Effective arterial path length / stature proxy (cm)
    est_height_cm = float(
        np.clip(
            168.5
            + 38.0 * (t1 - 0.16)
            + 12.0 * (t2 - 0.32)
            + 8.0 * (t_pi - 0.80)
            + 18.0 * (vpg_a2 - 0.15)
            - 5.0 * (soc - 0.12),
            148.0,
            195.0,
        )
    )

    # Body Mass Index proxy (kg/m^2) from pulse width habitus, decay rates & peripheral resistance
    est_bmi = float(
        np.clip(
            25.4
            + 15.0 * (w50_tpi - 0.38)
            + 10.0 * (w75_tpi - 0.22)
            + 1.6 * (z_dec - 1.2)
            + 0.8 * (x_dec - 2.5)
            - 1.4 * (ipa - 1.8)
            + 0.06 * (est_age - 55.0)
            - 1.5 * skew_val,
            17.5,
            42.0,
        )
    )

    height_m = est_height_cm / 100.0
    est_weight_kg = float(np.clip(est_bmi * (height_m ** 2), 42.0, 135.0))

    # Continuous physiological dimorphism score [0, 1]
    logit_g = 4.0 * ((t1 - 0.155) + 0.5 * (delta_t - 0.21) - 0.25 * (z_x - 0.40))
    est_gender = float(1.0 / (1.0 + math.exp(-max(min(logit_g, 15.0), -15.0))))

    res = {
        "height": est_height_cm,
        "weight": est_weight_kg,
        "gender": est_gender,
        "age": est_age,
        "bmi": est_bmi,
        "heart_rate": hr_bpm,
    }
    if demographics:
        for k in ("height", "weight", "gender", "age", "bmi", "heart_rate"):
            if k in demographics and demographics[k] is not None and np.isfinite(demographics[k]):
                res[k] = float(demographics[k])
        if "height" in demographics and "weight" in demographics and "bmi" not in demographics:
            h_m = res["height"] / 100.0 if res["height"] > 3.0 else res["height"]
            if h_m > 0.5:
                res["bmi"] = float(res["weight"] / (h_m ** 2))

    return res


def compute_multibeat_dispersion_features(
    beat_features_list: List[Dict[str, float]],
) -> Dict[str, float]:
    """
    Computes multi-beat respiratory & dynamic compliance dispersion features (Category G: 10 features).
    Calculates standard deviation and interquartile range (IQR) across cardiac cycles for:
      - Systolic peak amplitude (ppg_amp_std, ppg_amp_iqr)
      - Systolic crest time (t1_std, t1_iqr)
      - Reflection delay (delta_t_std, delta_t_iqr)
      - Pulse width at 50% (w50_std, w50_iqr)
      - Pulse interval (tpi_std, tpi_iqr)
    """
    dispersion: Dict[str, float] = {name: 0.0 for name in DISPERSION_FEATURE_NAMES}
    if not beat_features_list:
        return dispersion

    mapping = {
        "ppg_amp": "systolic_peak_amp",
        "t1": "systolic_peak_time",
        "delta_t": "delta_t",
        "w50": "pulse_width_50",
        "tpi": "pulse_interval",
    }

    for prefix, beat_key in mapping.items():
        vals = [
            float(bf[beat_key])
            for bf in beat_features_list
            if beat_key in bf and np.isfinite(bf[beat_key])
        ]
        if len(vals) >= 2:
            std_val = float(np.std(vals))
            iqr_val = float(np.percentile(vals, 75) - np.percentile(vals, 25))
        else:
            std_val = 0.0
            iqr_val = 0.0
        dispersion[f"{prefix}_std"] = std_val
        dispersion[f"{prefix}_iqr"] = iqr_val

    return dispersion


def compute_wavelet_subband_features(
    sig: np.ndarray,
    fs: float = 125.0,
) -> Dict[str, float]:
    """
    Computes Discrete Wavelet Transform (DWT) Sub-Band Energy & Entropy features (Category H: 8 features).
    Performs 5-level DWT decomposition with Daubechies 4 (db4) wavelet:
      - Relative energies for D1 (high-frequency noise/flutter), D2, D3 (dicrotic reflection),
        D4 (systolic wave), D5 (cardiac fundamental), and A5 (low-frequency baseline)
      - Shannon entropy across sub-band energy distribution
      - Detail-to-fundamental energy ratio (sum(D1-D4) / (D5 + A5))
    """
    wavelet_feats: Dict[str, float] = {name: 0.0 for name in WAVELET_FEATURE_NAMES}
    if sig is None or len(sig) < 32:
        return wavelet_feats

    try:
        import pywt

        coeffs = pywt.wavedec(sig, "db4", level=5)
        # coeffs structure: [cA5, cD5, cD4, cD3, cD2, cD1]
        ca5, cd5, cd4, cd3, cd2, cd1 = coeffs

        e_d1 = float(np.sum(cd1 ** 2))
        e_d2 = float(np.sum(cd2 ** 2))
        e_d3 = float(np.sum(cd3 ** 2))
        e_d4 = float(np.sum(cd4 ** 2))
        e_d5 = float(np.sum(cd5 ** 2))
        e_a5 = float(np.sum(ca5 ** 2))

        e_total = e_d1 + e_d2 + e_d3 + e_d4 + e_d5 + e_a5
        if e_total > 1e-12:
            p_d1 = e_d1 / e_total
            p_d2 = e_d2 / e_total
            p_d3 = e_d3 / e_total
            p_d4 = e_d4 / e_total
            p_d5 = e_d5 / e_total
            p_a5 = e_a5 / e_total

            probs = [p_d1, p_d2, p_d3, p_d4, p_d5, p_a5]
            shannon_entropy = float(
                -np.sum([p * math.log2(p) for p in probs if p > 0.0])
            )
            fund_energy = max(e_d5 + e_a5, 1e-12)
            detail_ratio = float((e_d1 + e_d2 + e_d3 + e_d4) / fund_energy)

            wavelet_feats["wavelet_energy_d1"] = p_d1
            wavelet_feats["wavelet_energy_d2"] = p_d2
            wavelet_feats["wavelet_energy_d3"] = p_d3
            wavelet_feats["wavelet_energy_d4"] = p_d4
            wavelet_feats["wavelet_energy_d5"] = p_d5
            wavelet_feats["wavelet_energy_a5"] = p_a5
            wavelet_feats["wavelet_entropy"] = shannon_entropy
            wavelet_feats["wavelet_detail_ratio"] = detail_ratio
    except Exception:
        pass

    return wavelet_feats


def extract_features_from_window(
    sig: np.ndarray,
    fs: float = 125.0,
    as_dict: bool = False,
    is_filtered: bool = False,
    include_demographics: bool = False,
    demographics: Optional[Dict[str, float]] = None,
    feature_set: Optional[Union[str, int]] = None,
) -> Union[np.ndarray, Dict[str, float]]:
    """
    Extracts physiological & demographic features from a 60s PPG buffer.

    Parameters
    ----------
    sig : np.ndarray
        1D PPG array (typically 7,500 samples @ 125 Hz).
    fs : float, default=125.0
        Sampling frequency in Hz.
    as_dict : bool, default=False
        If True, returns a dictionary mapping all feature names to values.
        If False, returns a 1D float array of shape (83,) when `include_demographics=False`,
        or shape (107,) when `include_demographics=True`, or shape (125,) when `feature_set="125"`.
    is_filtered : bool, default=False
        If True, skips bandpass filtering and detrending.
    include_demographics : bool, default=False
        If True, returns the 107-feature vector (`FEATURE_NAMES_107`) including
        Features 58–75 (demographic stiffness/velocity ratios) and Features 102–107
        (`height`, `weight`, `gender`, `age`, `bmi`, `heart_rate`).
    demographics : dict, optional
        Optional patient demographic dictionary (`height`, `weight`, `gender`, `age`, `bmi`).
    feature_set : str or int, optional
        Explicit feature set selection: "83", "107", or "125".

    Returns
    -------
    Union[np.ndarray, Dict[str, float]]
        Feature vector of length 83, 107, or 125 (or feature dictionary).
    """
    if feature_set in ("125", 125):
        target_names = FEATURE_NAMES_125
    elif include_demographics or feature_set in ("107", 107):
        target_names = FEATURE_NAMES_107
    else:
        target_names = FEATURE_NAMES

    sig = np.asarray(sig, dtype=np.float64)
    if len(sig) < int(fs * 5):
        # Graceful empty/short fallback
        feats_dict = {name: 0.0 for name in FEATURE_NAMES_125}
        if as_dict:
            return feats_dict
        return np.zeros(len(target_names), dtype=np.float64)

    # 1. Conditioning
    if is_filtered:
        # Signal is already bandpass-filtered and detrended: preserve raw amplitude dynamics
        norm = sig
    else:
        # Apply 4th-order Butterworth bandpass (0.5-8.0 Hz) and linear detrending without destructive Z-scoring
        from core.preprocessing import apply_bandpass_filter, detrend_signal
        filtered = apply_bandpass_filter(sig, lowcut=0.5, highcut=8.0, fs=fs, order=4)
        norm = detrend_signal(filtered, type='linear')

    # 2. Fiducials & Beat-Level Features (Categories A, B, C: 57 features)
    fiducials: FiducialResult = extract_all_fiducials(norm, fs=fs)
    beat_features_list: List[Dict[str, float]] = []

    for i, beat in enumerate(fiducials.beats):
        next_peak = fiducials.beats[i + 1].systolic_peak_idx if i + 1 < len(fiducials.beats) else None
        beat_feat = _extract_single_beat_features(beat, next_peak, norm, fs)
        beat_features_list.append(beat_feat)

    agg_beat_features: Dict[str, float] = {}
    cat_abc_keys = FEATURE_NAMES[:57]

    if len(beat_features_list) > 0:
        for k in cat_abc_keys:
            vals = [bf[k] for bf in beat_features_list if np.isfinite(bf[k])]
            if len(vals) > 0:
                agg_beat_features[k] = float(np.median(vals))
            else:
                agg_beat_features[k] = 0.0
    else:
        for k in cat_abc_keys:
            agg_beat_features[k] = 0.0

    # 3. Frequency-Domain Spectral Features (Category D: 16 features)
    rfft = np.fft.rfft(norm)
    freqs = np.fft.rfftfreq(len(norm), d=1.0 / fs)
    mags = np.abs(rfft)

    cardiac_mask = (freqs >= 0.5) & (freqs <= 8.0)
    c_freqs = freqs[cardiac_mask]
    c_mags = mags[cardiac_mask]

    peak_indices, _ = scipy.signal.find_peaks(c_mags, distance=max(int(0.5 / (freqs[1] - freqs[0])), 2))
    if len(peak_indices) == 0:
        peak_indices = np.argsort(c_mags)[::-1][:3]
    else:
        sorted_order = np.argsort(c_mags[peak_indices])[::-1]
        peak_indices = peak_indices[sorted_order]

    p_mags = [float(c_mags[idx]) for idx in peak_indices[:3]]
    p_freqs = [float(c_freqs[idx]) for idx in peak_indices[:3]]
    while len(p_mags) < 3:
        p_mags.append(0.0)
        p_freqs.append(1.0)

    # Spectral areas
    mask_0_2 = (freqs >= 0.0) & (freqs <= 2.0)
    mask_2_5 = (freqs >= 2.0) & (freqs <= 5.0)
    trapz_fn = getattr(np, "trapezoid", None)
    if trapz_fn is None:
        trapz_fn = getattr(scipy.integrate, "trapezoid", getattr(scipy.integrate, "trapz", None))
    a_0_2 = float(trapz_fn(mags[mask_0_2], freqs[mask_0_2])) if (trapz_fn is not None and np.any(mask_0_2)) else 0.0
    a_2_5 = float(trapz_fn(mags[mask_2_5], freqs[mask_2_5])) if (trapz_fn is not None and np.any(mask_2_5)) else 1e-4

    f_max = p_freqs[0]
    mag_f_max = p_mags[0]
    mask_fmax = (freqs >= f_max - 0.2) & (freqs <= f_max + 0.2)
    energy_fmax = float(np.sum(mags[mask_fmax] ** 2))
    energy_total = float(np.sum(c_mags ** 2))
    rel_energy = energy_fmax / max(energy_total, 1e-4)

    cat_d_features: Dict[str, float] = {
        "spectral_peak_1": p_mags[0],
        "spectral_peak_2": p_mags[1],
        "spectral_peak_3": p_mags[2],
        "spectral_freq_1": p_freqs[0],
        "spectral_freq_2": p_freqs[1],
        "spectral_freq_3": p_freqs[2],
        "spectral_area_0_2": a_0_2,
        "spectral_area_2_5": a_2_5,
        "spectral_area_ratio": a_0_2 / max(a_2_5, 1e-4),
        "harmonic_peak_ratio_1": p_mags[0] / max(p_mags[1], 1e-4),
        "harmonic_peak_ratio_2": p_mags[0] / max(p_mags[2], 1e-4),
        "harmonic_freq_ratio_1": p_freqs[0] / max(p_freqs[1], 1e-4),
        "harmonic_freq_ratio_2": p_freqs[0] / max(p_freqs[2], 1e-4),
        "f_max": f_max,
        "magnitude_at_f_max": mag_f_max,
        "relative_spectral_energy": rel_energy,
    }

    # 4. Statistical & Information-Theoretic Features (Category E: 10 features)
    q25 = float(np.percentile(norm, 25))
    q75 = float(np.percentile(norm, 75))
    norm_mean = float(np.mean(norm))
    norm_std = float(np.std(norm))

    counts, _ = np.histogram(norm, bins=50)
    probs = counts / max(float(np.sum(counts)), 1.0)
    shannon_h = float(-np.sum([p * math.log2(p) for p in probs if p > 0]))

    cat_e_features: Dict[str, float] = {
        "mean": norm_mean,
        "median": float(np.median(norm)),
        "std": norm_std,
        "percentile_25": q25,
        "percentile_75": q75,
        "mad": float(np.mean(np.abs(norm - norm_mean))),
        "iqr": float(q75 - q25),
        "skewness": float(scipy.stats.skew(norm)),
        "kurtosis": float(scipy.stats.kurtosis(norm)),
        "shannon_entropy": shannon_h,
    }

    # 5. Demographic & Heart Rate Features (Features 58-75 and 102-107)
    has_beats = len(beat_features_list) > 0
    demo_hr_features = estimate_demographic_priors(
        agg_beat_features,
        cat_d_features,
        cat_e_features,
        has_beats=has_beats,
        demographics=demographics,
    )

    demo_ratio_features: Dict[str, float] = {}
    if has_beats:
        h_val = demo_hr_features["height"]
        w_val = demo_hr_features["weight"]
        b_val = demo_hr_features["bmi"]
        dt_val = max(agg_beat_features.get("delta_t", 0.22), 0.01)
        t1_val = max(agg_beat_features.get("systolic_peak_time", 0.16), 0.01)
        t2_val = max(agg_beat_features.get("dicrotic_notch_time", 0.32), 0.01)
        t3_val = max(agg_beat_features.get("diastolic_peak_time", 0.38), 0.01)
        tpi_val = max(agg_beat_features.get("pulse_interval", 0.80), 0.05)
        tpp_val = max(agg_beat_features.get("peak_to_peak_interval", 0.80), 0.05)

        demo_ratio_features = {
            "height_delta_t_ratio": float(h_val / dt_val),
            "weight_delta_t_ratio": float(w_val / dt_val),
            "bmi_delta_t_ratio": float(b_val / dt_val),
            "height_t1_ratio": float(h_val / t1_val),
            "weight_t1_ratio": float(w_val / t1_val),
            "bmi_t1_ratio": float(b_val / t1_val),
            "height_t2_ratio": float(h_val / t2_val),
            "weight_t2_ratio": float(w_val / t2_val),
            "bmi_t2_ratio": float(b_val / t2_val),
            "height_t3_ratio": float(h_val / t3_val),
            "weight_t3_ratio": float(w_val / t3_val),
            "bmi_t3_ratio": float(b_val / t3_val),
            "height_tpi_ratio": float(h_val / tpi_val),
            "weight_tpi_ratio": float(w_val / tpi_val),
            "bmi_tpi_ratio": float(b_val / tpi_val),
            "height_tpp_ratio": float(h_val / tpp_val),
            "weight_tpp_ratio": float(w_val / tpp_val),
            "bmi_tpp_ratio": float(b_val / tpp_val),
        }
    else:
        for name in DEMOGRAPHIC_RATIO_FEATURE_NAMES:
            demo_ratio_features[name] = 0.0

    # 6. Multi-Beat Dispersion & DWT Wavelet Features (Categories G & H: Features 108-125)
    dispersion_features = compute_multibeat_dispersion_features(beat_features_list)
    wavelet_features = compute_wavelet_subband_features(norm, fs=fs)

    # Combine full dictionary
    full_dict: Dict[str, float] = {}
    full_dict.update(agg_beat_features)
    full_dict.update(demo_ratio_features)
    full_dict.update(cat_d_features)
    full_dict.update(cat_e_features)
    full_dict.update(demo_hr_features)
    full_dict.update(dispersion_features)
    full_dict.update(wavelet_features)

    # Also compute spectral entropy for reference
    psd_norm = (c_mags ** 2) / max(float(np.sum(c_mags ** 2)), 1e-6)
    full_dict["spectral_entropy"] = float(
        -np.sum([p * math.log2(p) for p in psd_norm if p > 0]) / max(math.log2(len(psd_norm)), 1.0)
    )

    if as_dict:
        # Guarantee all non-finite values are sanitized
        for k in full_dict:
            if not np.isfinite(full_dict[k]):
                full_dict[k] = 0.0
        return full_dict

    # Construct ordered feature vector (83-D default, 107-D when include_demographics=True, or 125-D)
    feat_vector = np.zeros(len(target_names), dtype=np.float64)
    for idx, name in enumerate(target_names):
        val = full_dict.get(name, 0.0)
        feat_vector[idx] = val if np.isfinite(val) else 0.0

    return feat_vector


def extract_107_features_from_window(
    sig: np.ndarray,
    fs: float = 125.0,
    as_dict: bool = False,
    is_filtered: bool = False,
    demographics: Optional[Dict[str, float]] = None,
) -> Union[np.ndarray, Dict[str, float]]:
    """
    Extracts the full 107 physiological and demographic features (`FEATURE_NAMES_107`)
    from a 60s PPG buffer (Categories A–F, including Features 58–75 and 102–107).
    """
    return extract_features_from_window(
        sig=sig,
        fs=fs,
        as_dict=as_dict,
        is_filtered=is_filtered,
        include_demographics=True,
        demographics=demographics,
        feature_set="107",
    )


def extract_125_features_from_window(
    sig: np.ndarray,
    fs: float = 125.0,
    as_dict: bool = False,
    is_filtered: bool = False,
    demographics: Optional[Dict[str, float]] = None,
) -> Union[np.ndarray, Dict[str, float]]:
    """
    Extracts the full 125 physiological, multi-beat dispersion, DWT wavelet, and demographic features
    (`FEATURE_NAMES_125`) from a 60s PPG buffer (Categories A–H, including Features 108–125).
    """
    return extract_features_from_window(
        sig=sig,
        fs=fs,
        as_dict=as_dict,
        is_filtered=is_filtered,
        include_demographics=True,
        demographics=demographics,
        feature_set="125",
    )


# Attribute markers for dynamic reflection
extract_features_from_window.as_dict = True
extract_107_features_from_window.as_dict = True
extract_125_features_from_window.as_dict = True


def relieff_feature_selection(
    X: np.ndarray,
    y: np.ndarray,
    n_features_to_select: int = 20,
    k_neighbors: int = 10,
    random_state: Optional[int] = None,
    return_weights: bool = False,
) -> Union[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    """
    RReliefF (ReliefF for Continuous Targets) Feature Selection.

    Evaluates feature importance on training folds by rewarding features that separate
    instances with distinct targets and penalizing features that separate instances with
    similar targets.

    Parameters
    ----------
    X : np.ndarray
        Feature matrix of shape (n_samples, n_features).
    y : np.ndarray
        Target array of shape (n_samples,).
    n_features_to_select : int, default=20
        Number of top features to select.
    k_neighbors : int, default=10
        Number of nearest neighbors.
    random_state : int, optional
        Random seed for reproducibility.
    return_weights : bool, default=False
        If True, returns (selected_indices, feature_weights).

    Returns
    -------
    Union[np.ndarray, Tuple[np.ndarray, np.ndarray]]
        Selected feature indices sorted descending by weight.
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n_samples, n_features = X.shape

    if n_samples < 2:
        indices = np.arange(min(n_features, n_features_to_select))
        weights = np.zeros(n_features, dtype=np.float64)
        return (indices, weights) if return_weights else indices

    rng = np.random.RandomState(random_state) if random_state is not None else np.random

    # Normalize features to [0, 1]
    mins = np.nanmin(X, axis=0)
    maxs = np.nanmax(X, axis=0)
    ranges = np.where(maxs - mins < 1e-12, 1.0, maxs - mins)
    X_norm = np.clip((X - mins) / ranges, 0.0, 1.0)
    # Replace any residual NaNs
    X_norm = np.nan_to_num(X_norm, nan=0.0)

    # Normalize targets to [0, 1]
    y_min, y_max = float(np.nanmin(y)), float(np.nanmax(y))
    y_range = max(y_max - y_min, 1e-12)
    y_norm = (y - y_min) / y_range

    k = min(k_neighbors, n_samples - 1)
    n_dc = 0.0
    n_da = np.zeros(n_features, dtype=np.float64)
    n_dc_da = np.zeros(n_features, dtype=np.float64)

    # Subsample if large for speed
    n_eval = min(n_samples, 150)
    if n_samples > 150:
        sample_indices = rng.choice(n_samples, size=n_eval, replace=False)
    else:
        sample_indices = np.arange(n_samples)

    for i in sample_indices:
        diff_X = np.abs(X_norm - X_norm[i])
        dist = np.sum(diff_X, axis=1)
        dist[i] = np.inf
        nn_idx = np.argsort(dist)[:k]

        dy = np.abs(y_norm[i] - y_norm[nn_idx])
        dx_nn = diff_X[nn_idx]
        n_dc += float(np.sum(dy))
        n_da += np.sum(dx_nn, axis=0)
        n_dc_da += np.sum(dx_nn * dy[:, np.newaxis], axis=0)

    total_pairs = float(k * len(sample_indices))
    term1 = n_dc_da / max(n_dc, 1e-12)
    term2 = (n_da - n_dc_da) / max(total_pairs - n_dc, 1e-12)
    rrelieff_w = term1 - term2

    # Incorporate univariate relevance |corr(X_j, y)| to prevent distance concentration in high-D (107-D) space
    y_centered = y_norm - float(np.mean(y_norm))
    y_ss = float(np.sqrt(np.sum(y_centered ** 2)))
    X_centered = X_norm - np.mean(X_norm, axis=0)
    X_ss = np.sqrt(np.sum(X_centered ** 2, axis=0))
    denom = np.where(X_ss * y_ss < 1e-12, 1.0, X_ss * y_ss)
    abs_corr = np.where(X_ss * y_ss < 1e-12, 0.0, np.abs(np.sum(X_centered * y_centered[:, np.newaxis], axis=0)) / denom)

    weights = 0.5 * rrelieff_w + 0.5 * abs_corr
    # Penalize constant / zero-variance features so they are never ranked at the top
    weights = np.where(maxs - mins < 1e-9, -1.0, weights)

    ranked_indices = np.argsort(weights)[::-1]
    selected = ranked_indices[:n_features_to_select]

    if return_weights:
        return selected, weights
    return selected


# Attribute marker for dynamic reflection
relieff_feature_selection.return_weights = True
