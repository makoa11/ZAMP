from __future__ import annotations

from app.config import ConfigError, load_config
from app.server import create_server


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc

    server = create_server(config)
    print(f"ZAMP auth server running on http://{config.host}:{config.port}")
    print(f"Session max age: {config.session_max_age_seconds} seconds")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
