"""Run local development servers and stop their process trees on exit."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time

from common import ENV, ROOT, command


def stop(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--no-reload", action="store_true", help="Restart explicitly after updates")
    args = parser.parse_args()
    if args.api_port == args.web_port:
        parser.error("API and frontend ports must differ")
    for port in (args.api_port, args.web_port):
        if not 1 <= port <= 65535:
            parser.error("Ports must be between 1 and 65535")
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        except OSError:
            parser.error(f"127.0.0.1:{port} is occupied; no existing process was stopped")
    ENV["CAMPUSINBOX_API_TARGET"] = f"http://127.0.0.1:{args.api_port}"
    signal.signal(signal.SIGTERM, stop)
    processes: list[subprocess.Popen] = []
    services = [
        (
            "backend",
            command(
                "uv",
                "run",
                "--locked",
                "uvicorn",
                "campus_inbox.main:app",
                *([] if args.no_reload else ["--reload"]),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
            ),
        ),
        ("frontend", command("npm", "run", "dev", "--", "--port", str(args.web_port))),
    ]
    try:
        for directory, service_args in services:
            options = (
                {"start_new_session": True}
                if os.name != "nt"
                else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
                }
            )
            processes.append(
                subprocess.Popen(service_args, cwd=ROOT / directory, env=ENV, **options)
            )
        print(
            f"Frontend: http://127.0.0.1:{args.web_port} | "
            f"API docs: http://127.0.0.1:{args.api_port}/docs",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        return next(
            (process.returncode or 1 for process in processes if process.poll() is not None),
            1,
        )
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if os.name == "nt":
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()


if __name__ == "__main__":
    sys.exit(main())
