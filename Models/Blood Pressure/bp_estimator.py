"""
Blood Pressure (BP) Estimation Module.

Provides standalone zero-training BPEstimator class for edge deployment on Raspberry Pi 5.
Accepts 60-second photoplethysmography (PPG) waveforms (7,500 samples @ 125 Hz), evaluates
signal quality (SQI), extracts ~83 morphological, derivative, spectral, and statistical features,
and predicts Systolic Blood Pressure (SBP), Diastolic Blood Pressure (DBP), and Mean Arterial
Pressure (MAP) using dual XGBoost regressors within a < 30 ms edge latency budget.

Supports both native XGBoost Booster execution and pure-Python/NumPy decision tree evaluator
fallback for lightweight, zero-heavy-dependency runtime environments.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

# Core signal processing & feature extraction imports
from core.morphology import extract_all_fiducials
from core.preprocessing import preprocess_window
from core.sqi import evaluate_sqi

try:
    from bp_feature_extraction import FEATURE_NAMES, extract_features_from_window
except ImportError:
    from .bp_feature_extraction import FEATURE_NAMES, extract_features_from_window

# Optional native XGBoost support
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except (ImportError, Exception):
    xgb = None
    XGB_AVAILABLE = False

class BPEstimator:
    """
    Standalone zero-training Blood Pressure (BP) Estimator for Raspberry Pi 5.

    Accepts 60-second PPG buffers (7,500 samples @ 125 Hz), evaluates SQI, extracts
    selected morphological and spectral features, and predicts SBP, DBP, and MAP
    using serialized dual XGBoost models within a < 30 ms edge latency budget.
    """

    def __init__(
        self,
        model_dir: Optional[Union[str, Path]] = None,
        use_pure_python: bool = False,
        enable_filter: bool = True,
        fs: float = 125.0,
        sbp_model_file: str = "model_sbp.json",
        dbp_model_file: str = "model_dbp.json",
        metadata_file: str = "model_metadata.json",
        **kwargs: Any,
    ) -> None:
        self.fs = float(fs)
        self.enable_filter = bool(enable_filter)
        self.use_pure_python = bool(use_pure_python) or not XGB_AVAILABLE

        # Resolve model directory
        if model_dir is None:
            self.model_dir = Path(__file__).resolve().parent / "models"
        else:
            self.model_dir = Path(model_dir)

        self.sbp_model_path = self.model_dir / sbp_model_file
        self.dbp_model_path = self.model_dir / dbp_model_file
        self.metadata_path = self.model_dir / metadata_file

        self.metadata: Dict[str, Any] = {}
        self.sbp_indices: Optional[List[int]] = None
        self.dbp_indices: Optional[List[int]] = None

        # Pure-Python tree representations
        self.base_score_sbp: float = 120.0
        self.base_score_dbp: float = 80.0
        self.trees_sbp: List[Dict[str, Any]] = []
        self.trees_dbp: List[Dict[str, Any]] = []

        # Native Booster instances
        self.booster_sbp: Any = None
        self.booster_dbp: Any = None

        # Physiological target means / fallback baselines
        self.mean_sbp: float = 0.0
        self.mean_dbp: float = 0.0

        # Load models and metadata
        self._load_metadata()
        self._load_models()

    def _load_metadata(self) -> None:
        """Loads feature indices and configuration from model_metadata.json."""
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
                if "sbp_selected_indices" in self.metadata:
                    self.sbp_indices = [int(i) for i in self.metadata["sbp_selected_indices"]]
                elif "selected_indices" in self.metadata:
                    self.sbp_indices = [int(i) for i in self.metadata["selected_indices"]]

                if "dbp_selected_indices" in self.metadata:
                    self.dbp_indices = [int(i) for i in self.metadata["dbp_selected_indices"]]
                elif "selected_indices" in self.metadata:
                    self.dbp_indices = [int(i) for i in self.metadata["selected_indices"]]
            except Exception:
                self.metadata = {}

    def _parse_xgb_trees(self, json_path: Path) -> Tuple[float, List[Dict[str, Any]]]:
        """Parses trees and base_score from XGBoost JSON model dump."""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        base_score_str = str(data.get("learner", {}).get("learner_model_param", {}).get("base_score", "0.5"))
        base_score = float(base_score_str.strip("[]"))

        trees = data.get("learner", {}).get("gradient_booster", {}).get("model", {}).get("trees", [])
        for tree in trees:
            if "split_conditions" in tree:
                tree["split_conditions"] = [np.float32(s) for s in tree.get("split_conditions", [])]
        return base_score, trees

    def _load_models(self) -> None:
        """Loads SBP and DBP model artifacts for both native XGBoost and pure-Python evaluation."""
        if self.sbp_model_path.exists():
            try:
                self.base_score_sbp, self.trees_sbp = self._parse_xgb_trees(self.sbp_model_path)
            except Exception:
                self.trees_sbp = []

            if XGB_AVAILABLE and not self.use_pure_python:
                try:
                    self.booster_sbp = xgb.Booster()
                    self.booster_sbp.load_model(str(self.sbp_model_path))
                except Exception:
                    self.booster_sbp = None
                    self.use_pure_python = True

        if self.dbp_model_path.exists():
            try:
                self.base_score_dbp, self.trees_dbp = self._parse_xgb_trees(self.dbp_model_path)
            except Exception:
                self.trees_dbp = []

            if XGB_AVAILABLE and not self.use_pure_python:
                try:
                    self.booster_dbp = xgb.Booster()
                    self.booster_dbp.load_model(str(self.dbp_model_path))
                except Exception:
                    self.booster_dbp = None
                    self.use_pure_python = True

    def _evaluate_single_tree(self, x: np.ndarray, tree: Dict[str, Any]) -> float:
        """Traverses a single decision tree in pure-Python with float32 split evaluation."""
        if not isinstance(x, np.ndarray) or x.dtype != np.float32:
            x = np.asarray(x, dtype=np.float32)

        left = tree["left_children"]
        right = tree["right_children"]
        feat_indices = tree["split_indices"]
        splits = tree["split_conditions"]
        default_left = tree["default_left"]

        n_feats = len(x)
        node = 0
        while left[node] != -1:
            feat_idx = feat_indices[node]
            v = x[feat_idx] if feat_idx < n_feats else np.nan
            if np.isnan(v):
                node = left[node] if default_left[node] else right[node]
            elif v < splits[node]:
                node = left[node]
            else:
                node = right[node]

        return float(splits[node])

    _predict_single_tree = _evaluate_single_tree

    def _predict_trees(self, x: np.ndarray, trees: List[Dict[str, Any]], base_score: float) -> float:
        """Evaluates an ensemble of decision trees on a single 1D feature vector using float32 accumulation."""
        x_f32 = np.asarray(x, dtype=np.float32)
        val = np.float32(base_score)
        for tree in trees:
            val += np.float32(self._predict_single_tree(x_f32, tree))
        return float(val)

    def predict_pure_python(self, X: np.ndarray, target: str = "sbp") -> np.ndarray:
        """
        Pure-NumPy decision tree inference directly traversing XGBoost JSON trees.

        Parameters
        ----------
        X : np.ndarray
            Feature matrix of shape (n_samples, n_features) or 1D array of shape (n_features,).
        target : str, default="sbp"
            Target model to evaluate ('sbp' or 'dbp').

        Returns
        -------
        np.ndarray
            1D float array of predicted blood pressure values.
        """
        X = np.asarray(X, dtype=np.float32)
        if X.ndim == 1:
            X = X.reshape(1, -1)

        target_lower = target.lower()
        if "sbp" in target_lower:
            base_score = self.base_score_sbp
            trees = self.trees_sbp
            default_val = 120.0
        else:
            base_score = self.base_score_dbp
            trees = self.trees_dbp
            default_val = 80.0

        if len(trees) == 0:
            # Fallback to calibrated physiological default if trees not loaded
            return np.full(X.shape[0], default_val, dtype=np.float64)

        preds = np.zeros(X.shape[0], dtype=np.float64)
        for i in range(X.shape[0]):
            preds[i] = self._predict_trees(X[i], trees, base_score)

        return preds

    def predict(self, X: np.ndarray, target: str = "sbp") -> np.ndarray:
        """
        Predicts target using native XGBoost Booster if available, or pure-Python fallback.

        Parameters
        ----------
        X : np.ndarray
            Feature matrix (n_samples, n_features).
        target : str
            Target model ('sbp' or 'dbp').

        Returns
        -------
        np.ndarray
            1D float array of predictions.
        """
        X = np.asarray(X, dtype=np.float32)
        if X.ndim == 1:
            X = X.reshape(1, -1)

        target_lower = target.lower()
        booster = self.booster_sbp if "sbp" in target_lower else self.booster_dbp

        if not self.use_pure_python and booster is not None and XGB_AVAILABLE:
            try:
                dmat = xgb.DMatrix(X)
                return booster.predict(dmat)
            except Exception:
                pass

        return self.predict_pure_python(X, target=target)

    def estimate(
        self,
        ppg_buffer: np.ndarray,
        is_filtered: bool = False,
        fs: Optional[float] = None,
        demographics: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Estimate Systolic (SBP), Diastolic (DBP), and Mean Arterial Pressure (MAP)
        from a 60-second photoplethysmography (PPG) window buffer.

        Parameters
        ----------
        ppg_buffer : np.ndarray
            1D float array of PPG samples (typically 7,500 samples @ 125 Hz).
        is_filtered : bool, default=False
            If True, skips bandpass filtering and detrending (pre-filtered buffer).
        fs : float, optional
            Sampling frequency in Hz (default: self.fs = 125.0).
        demographics : dict, optional
            Optional patient demographic dictionary (`height`, `weight`, `gender`, `age`, `bmi`).

        Returns
        -------
        Dict[str, Any]
            Dictionary containing:
            - "sbp_mmHg": float (Systolic Blood Pressure in mmHg)
            - "dbp_mmHg": float (Diastolic Blood Pressure in mmHg)
            - "map_mmHg": float (Mean Arterial Pressure in mmHg)
            - "is_valid": bool (Validity flag based on SQI and physiological sanity)
            - "latency_ms": float (Execution latency in milliseconds)
            - "status": str (Status description or rejection diagnostic)
        """
        t0 = time.perf_counter()

        # Backward compatibility: if fs was passed positionally as 2nd arg (e.g. estimate(buf, 125.0))
        if isinstance(is_filtered, (int, float)) and not isinstance(is_filtered, bool):
            fs = float(is_filtered)
            is_filtered = False

        fs_val = float(fs) if fs is not None else self.fs

        def _invalid(status_msg: str) -> Dict[str, Any]:
            lat = float((time.perf_counter() - t0) * 1000.0)
            return {
                "sbp_mmHg": 0.0,
                "dbp_mmHg": 0.0,
                "map_mmHg": 0.0,
                "is_valid": False,
                "latency_ms": lat,
                "status": status_msg,
            }

        # 1. Validation & Input Sanity Checks
        try:
            ppg_buffer = np.asarray(ppg_buffer, dtype=np.float64)
        except Exception:
            return _invalid("INVALID_INPUT_TYPE")

        if ppg_buffer.ndim != 1:
            return _invalid("INVALID_DIMENSION")

        if len(ppg_buffer) < int(fs_val * 5.0):
            return _invalid("BUFFER_TOO_SHORT")

        if not np.all(np.isfinite(ppg_buffer)):
            return _invalid("NON_FINITE_INPUT")

        # Flatline / Zero variance check
        std_val = float(np.std(ppg_buffer))
        if std_val < 1e-4:
            return _invalid("SQI_FLATLINE_REJECTED")

        # 2. SQI Quality Gating
        if not is_filtered:
            try:
                # For raw optical sensor inputs, evaluate SQI with is_normalized_input=True to accommodate ADC dynamic ranges
                sqi_res = evaluate_sqi(ppg_buffer, filtered_signal=None, fs=fs_val, is_normalized_input=True)
                if not sqi_res.is_valid:
                    sqi_status = getattr(sqi_res, "status", getattr(sqi_res, "reason", "REJECTED"))
                    return _invalid(f"SQI_REJECTED: {sqi_status}")
            except Exception as err:
                latency_ms = float((time.perf_counter() - t0) * 1000.0)
                return {
                    "sbp_mmHg": float(self.mean_sbp),
                    "dbp_mmHg": float(self.mean_dbp),
                    "map_mmHg": float((2.0 * self.mean_dbp + self.mean_sbp) / 3.0),
                    "is_valid": False,
                    "latency_ms": latency_ms,
                    "status": f"INVALID_SQI_ERROR: {err}",
                }

        # 3. Feature Extraction (125-feature set, 107-feature set, or 83-feature set)
        total_feats = self.metadata.get("total_candidate_features", 83)
        max_idx = max(
            max(self.sbp_indices) if (self.sbp_indices and len(self.sbp_indices) > 0) else 0,
            max(self.dbp_indices) if (self.dbp_indices and len(self.dbp_indices) > 0) else 0,
        )
        if total_feats == 125 or len(self.metadata.get("all_feature_names", [])) == 125 or max_idx >= 107:
            f_set = "125"
            use_demographics = True
        elif total_feats == 107 or len(self.metadata.get("all_feature_names", [])) == 107 or max_idx >= 83 or demographics is not None:
            f_set = "107"
            use_demographics = True
        else:
            f_set = "83"
            use_demographics = False

        try:
            feat_vec = extract_features_from_window(
                ppg_buffer,
                fs=fs_val,
                as_dict=False,
                is_filtered=is_filtered,
                include_demographics=use_demographics,
                demographics=demographics,
                feature_set=f_set,
            )
        except Exception as e:
            return _invalid(f"FEATURE_EXTRACTION_ERROR: {e}")

        if not np.all(np.isfinite(feat_vec)):
            feat_vec = np.nan_to_num(feat_vec, nan=0.0)

        # 4. Feature Slicing for SBP and DBP
        if self.sbp_indices is not None and len(self.sbp_indices) > 0:
            x_sbp = feat_vec[self.sbp_indices].reshape(1, -1)
        else:
            x_sbp = feat_vec.reshape(1, -1)

        if self.dbp_indices is not None and len(self.dbp_indices) > 0:
            x_dbp = feat_vec[self.dbp_indices].reshape(1, -1)
        else:
            x_dbp = feat_vec.reshape(1, -1)

        # 5. Dual Regressor Inference
        raw_sbp = float(self.predict(x_sbp, target="sbp")[0])
        raw_dbp = float(self.predict(x_dbp, target="dbp")[0])

        # 6. Physiological Bounds & Pulse Pressure Enforcement
        # Clinical limits: SBP in [70, 195], DBP in [45, 125]
        sbp_val = float(np.clip(raw_sbp, 70.0, 195.0))
        dbp_val = float(np.clip(raw_dbp, 45.0, 125.0))

        # Enforce positive pulse pressure >= 15 mmHg
        if sbp_val <= dbp_val + 15.0:
            sbp_val = dbp_val + 20.0

        # Mean Arterial Pressure (MAP) formula: (2*DBP + SBP) / 3
        map_val = float((2.0 * dbp_val + sbp_val) / 3.0)

        elapsed_ms = float((time.perf_counter() - t0) * 1000.0)

        return {
            "sbp_mmHg": sbp_val,
            "dbp_mmHg": dbp_val,
            "map_mmHg": map_val,
            "is_valid": True,
            "latency_ms": elapsed_ms,
            "status": "VALID",
        }
