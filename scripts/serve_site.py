"""Local static preview with portable ES-module MIME types (including Windows)."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--directory", default=str(Path(__file__).resolve().parents[1] / "site"))
    args = parser.parse_args()
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("text/javascript", ".js")
    ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=args.directory)).serve_forever()


if __name__ == "__main__":
    main()
