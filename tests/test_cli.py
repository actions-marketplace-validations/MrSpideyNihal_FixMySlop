"""Tests for cli/cli_app.py."""
from typer.testing import CliRunner
from fixmyslop.cli.cli_app import app
from fixmyslop.macros import APP_VERSION

runner = CliRunner()


class TestCLI:
    """Tests for the FixMySlop CLI commands."""

    def test_version_command(self):
        """'version' command should print the app version."""
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert APP_VERSION in result.stdout

    def test_scan_nonexistent_path(self):
        """'scan' should fail gracefully for a non-existent path."""
        result = runner.invoke(app, ["scan", "/nonexistent/path/xyz"])
        assert result.exit_code != 0


def test_npm_launcher_version_matches():
    """The npx launcher pins the Python package version, so they must stay in sync."""
    import json
    from pathlib import Path
    package = json.loads((Path(__file__).parent.parent / "npm" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == APP_VERSION


def test_bracket_paths_and_text_survive_rich_markup(tmp_path):
    """Next.js routes like app/[slug]/page.tsx and texts like fixmyslop-ignore[rule] must print verbatim."""
    route = tmp_path / "app" / "[slug]"
    route.mkdir(parents=True)
    (route / "page.tsx").write_text('<a href="#">x</a>\n', encoding="utf-8")
    result = runner.invoke(app, ["scan", str(tmp_path), "--no-static", "--fail-on", "none"])
    assert "app/[slug]/page.tsx" in result.stdout
    explained = runner.invoke(app, ["rules", "ui/arbitrary-color"])
    assert "fixmyslop-ignore[ui/arbitrary-color]" in explained.stdout
    assert "bg-[#1a1a2e]" in explained.stdout



def test_generated_ci_workflow_pins_this_version(tmp_path):
    """`init --github` must reference a real released tag, not a moving branch."""
    runner.invoke(app, ["init", str(tmp_path), "--github"])
    workflow = (tmp_path / ".github" / "workflows" / "fixmyslop.yml").read_text(encoding="utf-8")
    assert f"MrSpideyNihal/FixMySlop@v{APP_VERSION}" in workflow
