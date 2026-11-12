"""Apply a test-only host-clock offset inside an owned radio child.

Only the test process's injected wall clock changes. The operating system clock,
NTP and other applications are untouched; hardware timestamps still drive RF sync.
"""

import json
import math
import signal
import sys
import time
from functools import partial

from packet_radio.runtime.child import protect_parent


def main():
    parent = int(sys.argv.pop(1))
    offset = float(sys.argv.pop(1))
    if not math.isfinite(offset) or abs(offset) > 86400:
        raise ValueError("test clock offset must be finite and within one day")
    experiment = json.loads(sys.argv.pop(1))
    protect_parent(parent)
    from packet_radio import __main__ as cli
    from packet_radio.app import run

    if experiment:
        from packet_radio.app import RadioApp
        from packet_radio.runtime.bench_experiment import apply_experiment, experiment_config

        def run_experiment(config, *, on_ready=None, **options):
            experiment_config(config, experiment)
            app = RadioApp(config, wall_clock=lambda: time.time() + offset, **options)
            try:
                apply_experiment(app, experiment)
                signal.signal(signal.SIGINT, app.stop)
                signal.signal(signal.SIGTERM, app.stop)
                app.run(**({"on_ready": on_ready} if on_ready is not None else {}))
            finally:
                app.close()

        cli.run = run_experiment
    else:
        cli.run = partial(run, wall_clock=lambda: time.time() + offset)
    cli.main()


if __name__ == "__main__":
    main()
