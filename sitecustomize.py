"""Project-wide Python startup customizations."""

import warnings

# Silence argparse warning emitted by pytest on Python 3.12+ regarding the
# internal "file_or_dir" destination. This does not impact runtime behaviour
# but keeps the test output noise-free for our CI runs.
warnings.filterwarnings(
    "ignore",
    message="Do not expect file_or_dir in Namespace",
    category=UserWarning,
    module="argparse",
)
