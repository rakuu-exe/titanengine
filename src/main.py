import argparse

from titanengine.web_app import run_web_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Launch Titan Study Engine.")
    parser.add_argument("--host", default="127.0.0.1", help="Local interface to bind.")
    parser.add_argument("--port", default=5000, type=int, help="Local web server port.")
    parser.add_argument("--no-browser", action="store_true", help="Start the server without opening a browser.")
    parser.add_argument("--tui", action="store_true", help="Open the legacy terminal interface.")
    args = parser.parse_args()

    if args.tui:
        from titanengine.app import TitanApp

        TitanApp().run()
    else:
        run_web_app(host=args.host, port=args.port, open_browser=not args.no_browser)
