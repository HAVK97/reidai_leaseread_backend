"""Allow `python -m extract` as an entry point."""

from .cli import main
import sys

sys.exit(main())
