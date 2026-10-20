"""Identify a launched session and its effective configuration without exposing paths."""

import hashlib
import json
import os
from dataclasses import asdict
from uuid import uuid4


def config_identity(config):
    return hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def runtime_identity(config, run_id=None):
    return {
        "run_id": run_id or uuid4().hex,
        "config_sha256": config_identity(config),
        "pid": os.getpid(),
        "node_id": config.node_id,
        "channel": config.sdr.channel,
    }
