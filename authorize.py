from __future__ import annotations

import sys

try:
    from .youtube_client import TOKEN_PATH, authorize_youtube
except ImportError:
    from youtube_client import TOKEN_PATH, authorize_youtube  # type: ignore[no-redef]


def main() -> int:
    _, error = authorize_youtube()
    if error:
        print(f"{error['error']}: {error.get('hint', error.get('message', 'authorization failed'))}", file=sys.stderr)
        return 1

    print(f"Authorization complete. Token saved to {TOKEN_PATH}.")
    print("Next step: start the MCP client and call get_channel_info.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
