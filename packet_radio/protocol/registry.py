"""Resolve a packet profile into its codec, burst composition and link behavior.

Schema 1 retains generic-v5. Schema 2 resolves an explicit reusable definition
into a complete codec, protection and link behavior before hardware access;
unknown formats cannot silently inherit generic packet or correction defaults.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packet_radio.modems.burst import BurstEnvelope, BurstProcessing, PacketProtection
from packet_radio.modems.registry import make_modem, waveform
from packet_radio.protocol.codec import GenericV5Codec
from packet_radio.protocol.generic import GenericV5Behavior
from packet_radio.protocol.interfaces import LinkBehavior, PacketCodec

if TYPE_CHECKING:
    from packet_radio.config import RadioConfig, SDRConfig
    from packet_radio.modems.base import Modem


@dataclass(frozen=True)
class ProcessingProfile:
    codec: PacketCodec
    behavior: LinkBehavior

    name = "generic-v5"

    def tx_idle_samples(self, config: SDRConfig) -> int:
        return 0

    def validate(self, config: RadioConfig) -> None:
        """Validate profile-specific combinations before resources are opened."""

    def describe(self) -> dict:
        return {"format": self.name}

    def make_modem(self, config: SDRConfig, *, clock=time.monotonic) -> Modem:
        return make_modem(
            config,
            processing=self.burst(config),
            candidate_validator=self.codec.candidate_valid,
        )

    def burst(self, config: SDRConfig) -> BurstProcessing:
        raise NotImplementedError


class GenericV5Profile(ProcessingProfile):
    def __init__(self):
        super().__init__(GenericV5Codec(), GenericV5Behavior())

    def burst(self, config: SDRConfig) -> BurstProcessing:
        return BurstProcessing(
            PacketProtection(config.fec_interleaver_depth, config.rs_parity_bytes),
            BurstEnvelope(waveform(config.modulation).training),
        )


def processing_profile(name: str) -> ProcessingProfile:
    if name != "generic-v5":
        raise ValueError(f"unsupported processing profile {name!r}; available: generic-v5")
    return GenericV5Profile()


def resolve_processing(config: RadioConfig) -> ProcessingProfile:
    """Use the normalized schema-2 definition, or the unchanged schema-1 format."""
    if config.emulation is not None:
        from packet_radio.profiles import processing_for_definition

        return processing_for_definition(config.emulation, config.operation, config.input_kind)
    return processing_profile(config.packet_format)
