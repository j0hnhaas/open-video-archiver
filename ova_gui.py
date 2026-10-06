"""Launcher for the optional OVA PySide6 desktop application."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from ova_gui_app import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print(
                "OVA Desktop requires PySide6. Install the GUI extra with:\n"
                '  python -m pip install -e ".[gui]"',
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        raise
    gui_main()


if __name__ == "__main__":
    main()
