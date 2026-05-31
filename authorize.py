from __future__ import annotations

import sys

try:
    from .youtube_client import CREDENTIALS_PATH, SCOPES, TOKEN_PATH
except ImportError:
    from youtube_client import CREDENTIALS_PATH, SCOPES, TOKEN_PATH  # type: ignore[no-redef]


def main() -> int:
    if not CREDENTIALS_PATH.exists():
        print(
            f"Missing {CREDENTIALS_PATH}. Download a Desktop OAuth client JSON "
            "from Google Cloud Console and save it there.",
            file=sys.stderr,
        )
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    credentials = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")

    print(f"Authorization complete. Token saved to {TOKEN_PATH}.")
    print("Next step: start the MCP client and call get_channel_info.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
