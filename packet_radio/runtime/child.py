"""Arm Linux parent-death protection before invoking the existing radio CLI.

The kernel kills this process, including all radio threads, if its owning editor
exits. Check the parent again after arming to close the creation/arming race. No
radio imports or hardware operations happen until this protection is established.
"""

import ctypes
import os
import signal
import sys


def protect_parent(parent_pid: int) -> None:
    if sys.platform != "linux":
        raise RuntimeError("Owned radio launch requires Linux parent-death protection.")
    if parent_pid <= 1 or os.getppid() != parent_pid:
        raise RuntimeError("Radio owner exited before startup.")
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_PDEATHSIG; SIGKILL cannot be blocked by a hung UHD call or Python thread.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot arm radio parent-death protection")
    if os.getppid() != parent_pid:
        raise RuntimeError("Radio owner exited during startup.")


def main():
    protect_parent(int(sys.argv.pop(1)))
    from packet_radio.__main__ import main as radio_main

    radio_main()


if __name__ == "__main__":
    main()
