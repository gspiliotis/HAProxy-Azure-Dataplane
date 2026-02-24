"""Entry point for `python -m haproxy_azure_discovery`."""

import sys

from .cli import main

sys.exit(main())
