"""Open the setup wizard in a browser.

    python -m scripts.setup_wizard

Picks a free port, mints a one-off token, opens the page, and serves it on
localhost only until you stop it. On Windows this is what deploy/windows/setup.bat
runs, so the whole setup can be done with buttons instead of commands.
"""

import argparse
import socket
import threading
import time
import webbrowser

import uvicorn

from app.setup_wizard import actions
from app.setup_wizard.server import TOKEN, app


def _free_port(preferred: int) -> int:
    """Prefer a stable port so a bookmarked tab keeps working, but never fail to
    start because something else already has it."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("could not bind a local port for the setup page")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="print the link instead of opening it")
    args = parser.parse_args()

    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/?token={TOKEN}"

    print("=" * 72)
    print("  Trading system setup")
    print("=" * 72)
    print(f"\n  Open this link if a browser tab didn't appear:\n\n    {url}\n")
    print("  This page runs on your computer only and is not reachable from the")
    print("  network. Leave this window open while you use it; press Ctrl+C when")
    print("  you're done.\n")

    if not args.no_browser:
        # Delay so the server is listening before the tab requests the page.
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        # Anything the wizard started is a long-running trading process; leave
        # those alive deliberately, but say so rather than being silent about it.
        running = [n for n, p in actions.process_status().items() if p["running"]]
        if running:
            print(f"\nStill running in the background: {', '.join(running)}")
            print("Those keep trading. Stop them from the setup page, or close their windows.")


if __name__ == "__main__":
    main()
