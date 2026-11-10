"""Basic, rule-based PPG signal-quality checks for the laptop application.

This module evaluates technical indicators over a rolling window of PPGSample
objects. It does NOT estimate SpO2, PI, or other physiological parameters, and
its status is not a clinical assessment or validation of measurement accuracy.

Expected integration:
    from signal_quality import SignalQualityChecker

    checker = SignalQualityChecker(expected_sample_rate_hz=100)
    result = checker.update(sample)  # sample is acquisition.PPGSample
    print(result.status, result.message)

The caller (later main.py) should pass each sample to the checker once, as it
already does for the recorder and waveform widget.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil, isfinite
from typing import Deque, Optional, Sequence, Tuple

from acquisition import PPGSample


UINT32_ROLLOVER_MS = 1 << 32
ROLLOVER_HIGH_WATERMARK = 0xF0000000
ROLLOVER_LOW_WATERMARK = 0x0FFFFFFF


@dataclass(frozen=True)
class SignalQualityResult:
    """Snapshot of rule-based checks for the current rolling window.

    status values:
        WAITING: no samples have been received yet.
        COLLECTING: accumulating enough samples for window-based checks.
        NO_FINGER: the latest sample reports no finger detected.
        WARNING: one or more technical warning conditions were detected.
        POOR_SIGNAL: both raw channels are effectively flat within the window.
        CHECKS_PASSED: no configured basic technical warning was detected.

    CHECKS_PASSED is not a claim of clinical signal quality or accuracy.
    """

    status: str
    message: str
    sample_count: int
    finger_present_ratio: Optional[float]
    ir_range_counts: Optional[int]
    red_range_counts: Optional[int]
    estimated_sample_rate_hz: Optional[float]
    timestamp_gap_count: int
    timestamp_discontinuity_count: int
    warnings: Tuple[str, ...]

    @property
    def checks_passed(self) -> bool:
        """Whether basic configured checks found no warning condition."""
        return self.status == "CHECKS_PASSED"


class SignalQualityChecker:
    """Evaluate simple technical PPG indicators over a rolling sample window.

    Args:
        expected_sample_rate_hz: configured/expected sensor sample rate. The
            current firmware default is 100 Hz; pass another value if changed.
        window_seconds: duration of the rolling window used for checks.
        min_samples: minimum number of samples required before evaluating
            channel flatness and timing consistency.
        flatline_range_counts: a channel with peak-to-peak range at or below
            this threshold is considered nearly flat. This is a configurable
            technical heuristic, not a medically validated threshold.
        max_adc_count: expected upper bound for raw 18-bit sample counts. Set
            to None to disable this range check.
        gap_factor: timestamp intervals larger than this multiple of the
            expected interval are counted as gaps.

    Typical use at the application layer:
        checker = SignalQualityChecker(expected_sample_rate_hz=100)
        result = checker.update(sample)

    Call reset() at the start of a new recording/session if desired. The
    checker can also be used continuously, since it only retains a rolling
    window of samples.
    """

    def __init__(
        self,
        expected_sample_rate_hz: float = 100.0,
        window_seconds: float = 2.0,
        min_samples: int = 50,
        flatline_range_counts: int = 2,
        max_adc_count: Optional[int] = (1 << 18) - 1,
        gap_factor: float = 2.5,
    ) -> None:
        if isinstance(expected_sample_rate_hz, bool) or not isfinite(float(expected_sample_rate_hz)) or expected_sample_rate_hz <= 0:
            raise ValueError("expected_sample_rate_hz must be a positive finite number.")
        if isinstance(window_seconds, bool) or not isfinite(float(window_seconds)) or window_seconds <= 0:
            raise ValueError("window_seconds must be a positive finite number.")
        if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 3:
            raise ValueError("min_samples must be an integer of at least 3.")
        if isinstance(flatline_range_counts, bool) or not isinstance(flatline_range_counts, int) or flatline_range_counts < 0:
            raise ValueError("flatline_range_counts must be a non-negative integer.")
        if max_adc_count is not None and (
            isinstance(max_adc_count, bool) or not isinstance(max_adc_count, int) or max_adc_count <= 0
        ):
            raise ValueError("max_adc_count must be a positive integer or None.")
        if isinstance(gap_factor, bool) or not isfinite(float(gap_factor)) or gap_factor <= 1:
            raise ValueError("gap_factor must be a finite number greater than 1.")

        self.expected_sample_rate_hz = float(expected_sample_rate_hz)
        self.window_seconds = float(window_seconds)
        self.min_samples = min_samples
        self.flatline_range_counts = flatline_range_counts
        self.max_adc_count = max_adc_count
        self.gap_factor = float(gap_factor)

        expected_window_samples = ceil(self.expected_sample_rate_hz * self.window_seconds)
        capacity = max(self.min_samples, expected_window_samples, 3)
        self._samples: Deque[PPGSample] = deque(maxlen=capacity)
        self._latest_result = SignalQualityResult(
            status="WAITING",
            message="Waiting for PPG samples.",
            sample_count=0,
            finger_present_ratio=None,
            ir_range_counts=None,
            red_range_counts=None,
            estimated_sample_rate_hz=None,
            timestamp_gap_count=0,
            timestamp_discontinuity_count=0,
            warnings=(),
        )

    @property
    def latest_result(self) -> SignalQualityResult:
        """Return the latest evaluation snapshot."""
        return self._latest_result

    @property
    def sample_count(self) -> int:
        """Number of samples currently retained in the rolling window."""
        return len(self._samples)

    def reset(self) -> None:
        """Clear the rolling window and return to the waiting state."""
        self._samples.clear()
        self._latest_result = SignalQualityResult(
            status="WAITING",
            message="Waiting for PPG samples.",
            sample_count=0,
            finger_present_ratio=None,
            ir_range_counts=None,
            red_range_counts=None,
            estimated_sample_rate_hz=None,
            timestamp_gap_count=0,
            timestamp_discontinuity_count=0,
            warnings=(),
        )

    def update(self, sample: PPGSample) -> SignalQualityResult:
        """Add one sample and evaluate the rolling signal-quality window."""
        if not isinstance(sample, PPGSample):
            raise TypeError("sample must be a PPGSample from acquisition.py.")
        self._samples.append(sample)
        self._latest_result = self._evaluate(tuple(self._samples))
        return self._latest_result

    def _evaluate(self, samples: Sequence[PPGSample]) -> SignalQualityResult:
        count = len(samples)
        if count == 0:
            return self._latest_result

        finger_ratio = sum(s.finger_status == 1 for s in samples) / count
        latest_finger_present = samples[-1].finger_status == 1
        ir_values = [s.ir_value for s in samples]
        red_values = [s.red_value for s in samples]
        ir_range = max(ir_values) - min(ir_values)
        red_range = max(red_values) - min(red_values)

        gaps, discontinuities, sample_rate = self._timing_metrics(samples)

        if not latest_finger_present:
            message = "No finger detected in the latest sample. Check finger placement."
            return SignalQualityResult(
                status="NO_FINGER",
                message=message,
                sample_count=count,
                finger_present_ratio=finger_ratio,
                ir_range_counts=ir_range,
                red_range_counts=red_range,
                estimated_sample_rate_hz=sample_rate,
                timestamp_gap_count=gaps,
                timestamp_discontinuity_count=discontinuities,
                warnings=(message,),
            )

        if count < self.min_samples:
            return SignalQualityResult(
                status="COLLECTING",
                message=f"Collecting samples for quality checks ({count}/{self.min_samples}).",
                sample_count=count,
                finger_present_ratio=finger_ratio,
                ir_range_counts=ir_range,
                red_range_counts=red_range,
                estimated_sample_rate_hz=sample_rate,
                timestamp_gap_count=gaps,
                timestamp_discontinuity_count=discontinuities,
                warnings=(),
            )

        warnings = []
        ir_flat = ir_range <= self.flatline_range_counts
        red_flat = red_range <= self.flatline_range_counts

        if finger_ratio < 0.8:
            warnings.append(
                f"Finger detection was intermittent ({finger_ratio * 100:.1f}% of samples)."
            )
        if ir_flat:
            warnings.append(
                f"IR channel is nearly flat (peak-to-peak range: {ir_range} counts)."
            )
        if red_flat:
            warnings.append(
                f"RED channel is nearly flat (peak-to-peak range: {red_range} counts)."
            )
        if gaps:
            warnings.append(f"Detected {gaps} unusually long device-timestamp interval(s).")
        if discontinuities:
            warnings.append(
                f"Detected {discontinuities} device timestamp reset/discontinuity event(s)."
            )
        if self.max_adc_count is not None:
            out_of_range = sum(
                value > self.max_adc_count for value in (*ir_values, *red_values)
            )
            if out_of_range:
                warnings.append(
                    f"{out_of_range} raw value(s) exceed the configured maximum count "
                    f"({self.max_adc_count}). Verify the sensor/driver data format."
                )

        if ir_flat and red_flat:
            status = "POOR_SIGNAL"
            message = "Both raw channels are nearly flat in the current window."
        elif warnings:
            status = "WARNING"
            message = "Potential technical signal issue detected. See warnings."
        else:
            status = "CHECKS_PASSED"
            message = "No configured basic technical warning detected; this is not clinical validation."

        return SignalQualityResult(
            status=status,
            message=message,
            sample_count=count,
            finger_present_ratio=finger_ratio,
            ir_range_counts=ir_range,
            red_range_counts=red_range,
            estimated_sample_rate_hz=sample_rate,
            timestamp_gap_count=gaps,
            timestamp_discontinuity_count=discontinuities,
            warnings=tuple(warnings),
        )

    def _timing_metrics(
        self, samples: Sequence[PPGSample]
    ) -> Tuple[int, int, Optional[float]]:
        """Calculate device-timestamp gaps, discontinuities, and rate estimate."""
        if len(samples) < 2:
            return 0, 0, None

        adjusted_times = []
        gap_count = 0
        discontinuity_count = 0
        offset = 0
        previous_raw: Optional[int] = None
        previous_adjusted: Optional[int] = None
        expected_interval_ms = 1000.0 / self.expected_sample_rate_hz
        gap_threshold_ms = expected_interval_ms * self.gap_factor

        for sample in samples:
            raw_ms = sample.timestamp_ms
            if previous_raw is not None and raw_ms < previous_raw:
                is_rollover = (
                    previous_raw >= ROLLOVER_HIGH_WATERMARK
                    and raw_ms <= ROLLOVER_LOW_WATERMARK
                )
                if is_rollover:
                    offset += UINT32_ROLLOVER_MS
                else:
                    # A device reboot/timebase reset breaks this window's rate estimate.
                    discontinuity_count += 1
                    offset = 0
                    adjusted_times.clear()

            adjusted = raw_ms + offset
            if previous_adjusted is not None and adjusted > previous_adjusted:
                interval_ms = adjusted - previous_adjusted
                if interval_ms > gap_threshold_ms:
                    gap_count += 1
            elif previous_adjusted is not None and adjusted == previous_adjusted:
                # Device timestamps have millisecond resolution; duplicate values
                # can occur at high sample rates and are not automatically a gap.
                pass

            adjusted_times.append(adjusted)
            previous_raw = raw_ms
            previous_adjusted = adjusted

        sample_rate: Optional[float] = None
        if len(adjusted_times) >= 2:
            elapsed_ms = adjusted_times[-1] - adjusted_times[0]
            if elapsed_ms > 0:
                sample_rate = (len(adjusted_times) - 1) * 1000.0 / elapsed_ms

        return gap_count, discontinuity_count, sample_rate
