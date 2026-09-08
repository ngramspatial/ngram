"""Release version for ngram (harness).

Bump manually in this file, or run::

    python scripts/bump_version.py patch   # 1.2.3 -> 1.2.4
    python scripts/bump_version.py minor   # 1.2.3 -> 1.3.0
    python scripts/bump_version.py major   # 1.2.3 -> 2.0.0

The CLI ``ngram version`` and Telegram ``/version`` report this value.
"""

__version__ = "1.0.1"
