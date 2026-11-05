import logging
import sys


def main() -> None:
    # stdout é o canal do protocolo MCP (e o JSON da CLI); logs vão para stderr.
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if sys.argv[1:] and sys.argv[1] != "serve":
        from .cli import run

        sys.exit(run(sys.argv[1:]))

    logging.getLogger("cds_tools").setLevel(logging.INFO)
    from .server import mcp

    mcp.run()


if __name__ == "__main__":
    main()
