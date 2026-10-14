"""Implement minimum-shift keying as continuous-phase FSK with index one half.

MSKModem derives deviation as one quarter of the bit rate, so every bit advances
phase by plus or minus pi/2. It reuses the rectangular-pulse FSK generator and
streaming frequency-discriminator receiver. Each burst starts with zero phase
before its first sample; there is no shaping filter or appended filter tail.
"""

from .fsk import FSK2Modem


class MSKModem(FSK2Modem):
    """Constrain the shared continuous-phase modem to the MSK waveform."""

    def __init__(self, samples_per_symbol=8, *, sample_rate=1_000_000, **kwargs):
        if "frequency_deviation_hz" in kwargs:
            raise ValueError("MSK derives frequency deviation from bit rate; omit the override")
        if samples_per_symbol < 4:
            raise ValueError("MSK requires at least 4 samples/symbol")
        if kwargs.get("bit_repetitions", 1) != 1 or kwargs.get("rrc_rolloff", 0):
            raise ValueError("MSK requires no bit repetition or RRC shaping")
        super().__init__(
            samples_per_symbol,
            sample_rate=sample_rate,
            frequency_deviation_hz=sample_rate / (4 * samples_per_symbol),
            **kwargs,
        )
