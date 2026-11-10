"""
Core Photoplethysmography (PPG) Signal Conditioning and Morphology Package.

Exports standard signal processing primitives for preprocessing, signal quality
indexing (SQI), and fiducial extraction.
"""

from core.preprocessing import (
    PreprocessResult,
    apply_bandpass_filter,
    butterworth_bandpass_filter,
    detrend_signal,
    segment_windows,
    zscore_normalize,
    normalize_zscore,
    preprocess_window,
)

from core.sqi import (
    SQIResult,
    compute_skewness_sqi,
    compute_perfusion_index,
    compute_cardiac_relative_power,
    compute_relative_power_sqi,
    composite_sqi_gating,
    evaluate_sqi,
)

from core.morphology import (
    BeatSegment,
    FiducialResult,
    compute_derivatives,
    detect_systolic_peaks,
    detect_diastolic_troughs,
    detect_dicrotic_features,
    detect_dicrotic_notches,
    extract_derivative_fiducials,
    extract_beat_segments,
    segment_beats,
    extract_all_fiducials,
)

__all__ = [
    # Data containers
    "PreprocessResult",
    "SQIResult",
    "BeatSegment",
    "FiducialResult",
    # Preprocessing
    "apply_bandpass_filter",
    "butterworth_bandpass_filter",
    "detrend_signal",
    "segment_windows",
    "zscore_normalize",
    "normalize_zscore",
    "preprocess_window",
    # Signal Quality Index
    "compute_skewness_sqi",
    "compute_perfusion_index",
    "compute_cardiac_relative_power",
    "compute_relative_power_sqi",
    "composite_sqi_gating",
    "evaluate_sqi",
    # Morphology & Derivatives
    "compute_derivatives",
    "detect_systolic_peaks",
    "detect_diastolic_troughs",
    "detect_dicrotic_features",
    "detect_dicrotic_notches",
    "extract_derivative_fiducials",
    "extract_beat_segments",
    "segment_beats",
    "extract_all_fiducials",
]
