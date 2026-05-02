"""
One-time TikTok OAuth2 helper — prints your access token and open_id.

Usage:
  python tools/tiktok_auth.py \
    --client-key YOUR_CLIENT_KEY \
    --client-secret YOUR_CLIENT_SECRET \
    --redirect-uri https://your-redirect-uri.example.com/callback
"""
import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from publisher.tiktok_client import TikTokOAuth2Client

REQUIRED_SCOPES = ["video.upload", "video.publish"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Get a TikTok access token via OAuth2")
    parser.add_argument("--client-key", required=True, help="TikTok app Client Key")
    parser.add_argument("--client-secret", required=True, help="TikTok app Client Secret")
    parser.add_argument(
        "--redirect-uri",
        required=True,
        help="Redirect URI registered in your TikTok app",
    )
    parser.add_argument(
        "--scopes",
        default=",".join(REQUIRED_SCOPES),
        help=f"Comma-separated scopes (default: {','.join(REQUIRED_SCOPES)})",
    )
    args = parser.parse_args()

    client = TikTokOAuth2Client(
        client_key=args.client_key,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
    )
    scopes = [s.strip() for s in args.scopes.split(",")]
    auth_url = client.get_authorization_url(scopes=scopes, state="tiktok-factory-setup")

    print("\n=== Step 1: Authorize ===")
    print("Open this URL in your browser and authorize the app:\n")
    print(f"  {auth_url}\n")
    print("After authorizing, TikTok will redirect to your redirect URI with a ?code= parameter.")

    code = input("\n=== Step 2: Paste the 'code' from the redirect URL: ").strip()
    if not code:
        print("No code provided — aborting.", file=sys.stderr)
        sys.exit(1)

    print("\nExchanging code for token...")
    token = client.exchange_code_for_token(code)

    print("\n=== Success! Add these to your .env ===\n")
    print(f"TIKTOK_ACCESS_TOKEN={token.access_token}")
    print(f"TIKTOK_REFRESH_TOKEN={token.refresh_token}")
    print(f"TIKTOK_OPEN_ID={token.open_id}")
    print(f"\nAccess token expires in: {token.expires_in}s")
    print(f"Refresh token expires in: {token.refresh_expires_in}s")
    print(f"Granted scopes: {token.scope}")


if __name__ == "__main__":
    main()
