"""PyInstaller's GUI entry point; worker modules are shipped as separate sources."""
from leap_desktop.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
