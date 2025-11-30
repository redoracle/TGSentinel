#!/usr/bin/env python3
"""Generate a Telegram session file for upload authentication.

This tool creates a valid Telethon session file that can be uploaded
to the TG Sentinel UI for authentication without SMS codes.

Usage:
    python tools/generate_session.py [--phone PHONE] [--output PATH]

Options:
    --phone     Phone number in international format (e.g., +1234567890)
    --output    Output path for the session file (default: ./tgsentinel_upload.session)
    --api-id    Telegram API ID (default: from TG_API_ID env)
    --api-hash  Telegram API hash (default: from TG_API_HASH env)

The script will:
1. Check and install required dependencies (telethon, cryptg)
2. Connect to Telegram using your credentials
3. Complete the authentication flow (SMS code + optional 2FA)
4. Generate a portable session file
5. Verify the session is valid
6. Save it for upload to TG Sentinel UI

Example:
    python tools/generate_session.py --phone +1234567890
    python tools/generate_session.py --phone +1234567890 --output my_session.session
"""

import argparse
import asyncio
import getpass
import os
import subprocess
import sys
from pathlib import Path


def install_dependencies():
    """Install required dependencies if not already installed.

    This function is idempotent and safe to call multiple times.
    """
    required_packages = ["telethon", "cryptg"]
    missing_packages = []

    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print(f"Installing required packages: {', '.join(missing_packages)}")
        print("This may take a moment...\n")

        try:
            # Use pip from current Python interpreter
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + missing_packages,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print("✓ Dependencies installed successfully\n")
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies: {e}")
            print("Please install manually: pip install telethon cryptg")
            sys.exit(1)


def get_credentials():
    """Get API credentials from environment or prompt user."""
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    if not api_id:
        print("\nTelegram API credentials required.")
        print("Get them from: https://my.telegram.org/apps\n")
        api_id = input("Enter API ID: ").strip()

    if not api_hash:
        api_hash = input("Enter API Hash: ").strip()

    if not api_id or not api_hash:
        print("Error: API ID and API Hash are required")
        sys.exit(1)

    try:
        api_id = int(api_id)
    except ValueError:
        print("Error: API ID must be a number")
        sys.exit(1)

    return api_id, api_hash


async def create_session(phone: str, output_path: Path, api_id: int, api_hash: str):
    """Create and authenticate a Telegram session."""
    # Lazy import of telethon
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.tl.types import User

    print(f"\n{'='*60}")
    print("TG Sentinel Session Generator")
    print(f"{'='*60}\n")

    print(f"Phone: {phone}")
    print(f"Output: {output_path}")
    print(f"API ID: {api_id}")
    print(f"API Hash: {api_hash[:8]}...")
    print()

    # Create temporary session
    temp_session = output_path.parent / f".temp_{output_path.name}"

    try:
        # Create client with file session
        client = TelegramClient(str(temp_session.with_suffix("")), api_id, api_hash)

        print("Connecting to Telegram...")
        await client.connect()

        if not await client.is_user_authorized():
            print(f"\nSending authentication code to {phone}...")
            await client.send_code_request(phone)

            # Get code from user
            code = input("\nEnter the code you received: ").strip()

            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                # 2FA is enabled
                print("\nTwo-factor authentication is enabled.")
                password = getpass.getpass("Enter your 2FA password: ").strip()
                await client.sign_in(password=password)

        # Verify authentication
        me = await client.get_me()
        if not me or not isinstance(me, User):
            print("\n✗ Error: Failed to get user info after authentication")
            if client.is_connected():
                disconnect_result = client.disconnect()
                if disconnect_result is not None:
                    await disconnect_result
            return False

        print("\n✓ Successfully authenticated as:")
        print(f"  ID: {me.id}")
        print(f"  Username: @{me.username}" if me.username else "  Username: None")
        print(f"  Name: {me.first_name}" + (f" {me.last_name}" if me.last_name else ""))
        print(f"  Phone: {me.phone}")

        # Ensure session is saved
        if client.is_connected():
            disconnect_result = client.disconnect()
            if disconnect_result is not None:
                await disconnect_result

        # Move session file to final location
        session_file = temp_session.with_suffix(".session")
        if session_file.exists():
            # Copy to output path
            import shutil

            shutil.copy2(session_file, output_path)

            # Clean up temp files
            for ext in [".session", ".session-journal"]:
                temp_file = temp_session.parent / f"{temp_session.stem}{ext}"
                if temp_file.exists():
                    temp_file.unlink()

            print(f"\n✓ Session file saved: {output_path}")
            print(f"  Size: {output_path.stat().st_size} bytes")

            # Verify session file
            print("\nVerifying session file...")
            if await verify_session(output_path, api_id, api_hash):
                print("✓ Session file is valid and can be uploaded to TG Sentinel\n")
                return True
            else:
                print("✗ Session file verification failed\n")
                return False
        else:
            print(f"\n✗ Error: Session file not created at {session_file}")
            return False

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        # Clean up any remaining temp files
        for ext in [".session", ".session-journal"]:
            temp_file = temp_session.parent / f"{temp_session.stem}{ext}"
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass


async def verify_session(session_path: Path, api_id: int, api_hash: str) -> bool:
    """Verify that the session file is valid."""
    # Lazy import of telethon
    from telethon import TelegramClient
    from telethon.tl.types import User

    try:
        client = TelegramClient(str(session_path.with_suffix("")), api_id, api_hash)
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            if me and isinstance(me, User):
                first_name = me.first_name if me.first_name else "Unknown"
                username = me.username
                print(
                    f"  Session contains authorization for: {first_name} (@{username if username else 'no username'})"
                )
            if client.is_connected():
                disconnect_result = client.disconnect()
                if disconnect_result is not None:
                    await disconnect_result
            return True
        else:
            print("  Session exists but is not authorized")
            if client.is_connected():
                disconnect_result = client.disconnect()
                if disconnect_result is not None:
                    await disconnect_result
            return False

    except Exception as e:
        print(f"  Verification error: {e}")
        return False


def main():
    # Install dependencies at the start of main() when script is executed directly
    # This ensures packages are available before any async functions that import them
    install_dependencies()

    # Verify telethon is now importable
    try:
        import telethon  # noqa: F401
    except ImportError:
        print("\n✗ Error: Failed to import telethon after installation")
        print("Please try installing manually: pip install telethon cryptg")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Generate a Telegram session file for TG Sentinel UI upload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Generate session for a phone number:
    python tools/generate_session.py --phone +1234567890

  Specify custom output path:
    python tools/generate_session.py --phone +1234567890 --output ./my_session.session

  Provide API credentials:
    python tools/generate_session.py --phone +1234567890 --api-id 12345 --api-hash abcdef
        """,
    )

    parser.add_argument(
        "--phone",
        required=True,
        help="Phone number in international format (e.g., +1234567890)",
    )

    parser.add_argument(
        "--output",
        default="./tgsentinel_upload.session",
        help="Output path for the session file (default: ./tgsentinel_upload.session)",
    )

    parser.add_argument(
        "--api-id", help="Telegram API ID (default: from TG_API_ID env)"
    )

    parser.add_argument(
        "--api-hash", help="Telegram API hash (default: from TG_API_HASH env)"
    )

    args = parser.parse_args()

    # Get API credentials
    if args.api_id and args.api_hash:
        try:
            api_id = int(args.api_id)
        except ValueError:
            print("Error: API ID must be a number")
            sys.exit(1)
        api_hash = args.api_hash
    else:
        api_id, api_hash = get_credentials()

    # Validate phone number
    phone = args.phone.strip()
    if not phone.startswith("+"):
        print("Warning: Phone number should start with '+' (international format)")
        phone = "+" + phone.lstrip("+")

    # Prepare output path
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Run session creation
    success = asyncio.run(create_session(phone, output_path, api_id, api_hash))

    if success:
        print("=" * 60)
        print("Next Steps:")
        print("=" * 60)
        print("1. Open TG Sentinel UI in your browser")
        print("2. Click 'Authenticate Telegram Session'")
        print("3. Select 'Upload Session' tab")
        print(f"4. Upload the file: {output_path}")
        print("5. Click 'Upload & Restore'")
        print("\n✓ You'll be logged in without SMS codes!")
        sys.exit(0)
    else:
        print("\n✗ Session generation failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
