"""Download LocateAnything-3B-4bit (MLX) model from ModelScope.

Usage:
    python scripts/download_locateanything.py
    python scripts/download_locateanything.py --local-dir ./models/LocateAnything-3B-4bit
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Download LocateAnything-3B-4bit MLX model")
    parser.add_argument(
        "--local-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "models", "LocateAnything-3B-4bit"),
        help="Target directory (default: ./models/LocateAnything-3B-4bit)",
    )
    args = parser.parse_args()

    local_dir = os.path.abspath(args.local_dir)
    print(f"Downloading LocateAnything-3B-4bit to: {local_dir}")

    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        print("ERROR: modelscope not installed. Run: pip install modelscope")
        sys.exit(1)

    path = snapshot_download(
        "mlx-community/LocateAnything-3B-4bit",
        local_dir=local_dir,
    )
    print(f"Done: {path}")


if __name__ == "__main__":
    main()
