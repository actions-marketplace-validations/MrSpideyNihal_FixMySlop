"""
FixMySlop entry point. CLI-first: `fixmyslop` alone prints help; `fixmyslop ui` opens the app.
"""
import sys


def main():
    """Run the CLI (no arguments shows help)."""
    from fixmyslop.cli.cli_app import app

    if len(sys.argv) == 1:
        sys.argv.append("--help")
    app()


if __name__ == "__main__":
    main()
