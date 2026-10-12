import argparse
import locale
import os


def main():
    # Keep application and GTK messages in English without changing the desktop locale.
    os.environ["LANGUAGE"] = "en"
    os.environ["LC_MESSAGES"] = "C"
    locale.setlocale(locale.LC_MESSAGES, "C")
    from .protocol import PROFILE_ALIASES
    parser = argparse.ArgumentParser(description="SSH Screen Manager — access your computers")
    parser.add_argument("--serve", action="store_true", help="GNOME agent over stdin/stdout (used by the client)")
    parser.add_argument("--check", action="store_true", help="Check availability without sharing the screen")
    parser.add_argument("--profile", type=lambda value: PROFILE_ALIASES.get(value, value), choices=["low-bandwidth", "balanced", "sharp"], default="balanced")
    parser.add_argument("--host", help="Computer address or SSH alias")
    parser.add_argument("--connect", action="store_true", help="Connect on launch")
    args = parser.parse_args()
    if args.check:
        import json
        from .discovery import check_local
        print(json.dumps(check_local()))
        return 0
    if args.serve:
        from .server import Server
        return Server(args.profile).run()
    from .client import Application
    return Application(args).run(None)


if __name__ == "__main__":
    raise SystemExit(main())
