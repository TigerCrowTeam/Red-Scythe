"""Keep a dedicated X310/UBX-160 hop table inside one analog LO window.

Each RX/TX LO is programmed once per opened channel. Later requests leave RF
untouched and let UHD calculate the DSP offset, including direction-specific
signs. Readbacks must preserve the LO and requested center; no RF fallback is
allowed. Fixed-frequency and other hardware retain ordinary automatic tuning.
"""

from __future__ import annotations

import math


class FrequencyTuner:
    def __init__(self, api, device, config, frequencies):
        self.api, self.device, self.config = api, device, config
        self.lo_hz = (
            (min(frequencies) + max(frequencies)) / 2
            if config.device.get("type") == "x300" and len(set(frequencies)) > 1
            else None
        )
        self.rf_actual: dict[str, float] = {}
        self.rf_requests = dict.fromkeys(("rx", "tx"), 0)
        self.dsp_requests = dict.fromkeys(("rx", "tx"), 0)
        self.settings: dict[str, float | str] = {
            "tuning_mode": "fixed-lo-dsp" if self.lo_hz is not None else "automatic"
        }
        if self.lo_hz is not None:
            self.settings["requested_lo_hz"] = self.lo_hz
            for direction in ("rx", "tx"):
                info = getattr(device, f"get_usrp_{direction}_info")(config.channel)
                if info.get("mboard_id") != "X310" or not info.get(
                    direction + "_id", ""
                ).startswith("UBX-160"):
                    raise ValueError("Fixed-LO hopping requires the X310 with UBX-160 boards")
            for frequency in frequencies:
                self.check_frequency(frequency)

    def check_frequency(self, frequency):
        if not math.isfinite(frequency):
            raise ValueError("Tune frequency must be finite")
        if self.lo_hz is not None and (
            not 10e6 <= frequency <= 6e9
            or 2 * abs(frequency - self.lo_hz) + self.config.bandwidth > 160e6
        ):
            raise ValueError("Frequency is outside the fixed LO's UBX-160 hopping window")

    def tune(self, direction, frequency):
        self.check_frequency(frequency)
        request = self.api.types.TuneRequest(frequency)
        if self.lo_hz is not None:
            policy = self.api.types.TuneRequestPolicy
            request.rf_freq_policy = policy.none if direction in self.rf_actual else policy.manual
            request.rf_freq = self.lo_hz
            request.dsp_freq_policy = policy.auto
            counts = self.dsp_requests if direction in self.rf_actual else self.rf_requests
            counts[direction] += 1
        result = getattr(self.device, f"set_{direction}_freq")(request, self.config.channel)
        actual = float(getattr(self.device, f"get_{direction}_freq")(self.config.channel))
        if not math.isfinite(actual) or not math.isclose(actual, frequency, rel_tol=0, abs_tol=1):
            raise ValueError(f"UHD {direction} frequency coerced: {frequency} -> {actual}")
        if self.lo_hz is not None:
            rf, dsp = float(result.actual_rf_freq), float(result.actual_dsp_freq)
            expected = self.rf_actual.get(direction, self.lo_hz)
            if (
                not math.isfinite(rf)
                or not math.isfinite(dsp)
                or not math.isclose(rf, expected, rel_tol=0, abs_tol=1)
            ):
                raise ValueError(
                    f"UHD {direction} did not preserve the fixed LO: {expected} -> {rf}"
                )
            # Use the actual LO to check the edge reserve, including LO rounding.
            if 2 * abs(actual - rf) + self.config.bandwidth > 160e6:
                raise ValueError("Actual tuning exceeds the fixed LO's UBX-160 hopping window")
            self.rf_actual[direction] = rf
            self.settings[f"{direction}_lo_hz"] = rf
            self.settings[f"{direction}_dsp_hz"] = dsp
            self.settings[f"{direction}_rf_tune_requests"] = self.rf_requests[direction]
            self.settings[f"{direction}_dsp_tune_requests"] = self.dsp_requests[direction]
        self.settings[f"{direction}_frequency"] = actual
        return actual
