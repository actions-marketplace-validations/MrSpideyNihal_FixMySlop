"""Docs slop rules: READMEs that are wrong about the repo, or written in the generated-README voice."""
from pathlib import Path

from fixmyslop.core.slop.engine import SlopEngine

PACKAGE = '{"name": "app", "scripts": {"dev": "vite", "build": "vite build"}}'


def scan(root: Path, name: str, text: str, extra: dict | None = None) -> list:
    for rel, content in (extra or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    engine = SlopEngine(root)
    return [i.rule_id for i in engine.check_file(path, text) + engine.finalize()]


def test_missing_script(tmp_path):
    readme = "## Setup\n\n```bash\nnpm install\nnpm run dev\nnpm run test\n```\n"
    assert "docs/missing-script" in scan(tmp_path, "README.md", readme, {"package.json": PACKAGE})
    ok = "```bash\nnpm run dev\npnpm run build\n```\n"
    assert "docs/missing-script" not in scan(tmp_path / "b", "README.md", ok, {"package.json": PACKAGE})
    # no package.json: nothing to compare against
    assert "docs/missing-script" not in scan(tmp_path / "c", "README.md", readme)


def test_dead_link_and_missing_license(tmp_path):
    readme = ("![Demo](screenshots/demo.png)\n\nSee [the setup guide](docs/setup.md#install) and "
              "[the API](https://example.com/api).\n\n## License\n\nMIT, see the LICENSE file.\n")
    found = scan(tmp_path, "README.md", readme)
    assert found.count("docs/dead-link") == 3
    files = {"screenshots/demo.png": "x", "docs/setup.md": "# Setup\n", "LICENSE": "MIT"}
    assert "docs/dead-link" not in scan(tmp_path / "ok", "README.md", readme, files)


def test_placeholder_and_chatbot_residue(tmp_path):
    readme = ("Certainly! Here's a comprehensive README for your project.\n\n"
              "```bash\ngit clone https://github.com/yourusername/my-app.git\n```\n\n[Insert screenshot here]\n")
    found = scan(tmp_path, "README.md", readme)
    assert "docs/placeholder" in found and found.count("docs/chatbot-residue") == 2
    fork = "1. Fork the repo\n2. `git clone https://github.com/your-username/my-app.git`\n"
    assert "docs/placeholder" not in scan(tmp_path / "f", "README.md", fork)
    link = "* [Add React to an Existing Project](https://react.dev/learn/add-react)\n"
    assert "docs/chatbot-residue" not in scan(tmp_path / "r", "README.md", link)


def test_generated_readme_look(tmp_path):
    readme = ("# 🚀 TaskFlow\n\nA seamless, cutting-edge way to unlock the power of your tasks.\n\n"
              "## ✨ Features\n\n- Fast\n\n## 🛠️ Tech Stack\n\n- React\n\n## 📦 Install\n\nnpm i\n\n"
              "Made with ❤️ by the team. Happy coding!\n")
    found = scan(tmp_path, "README.md", readme)
    assert "docs/emoji-headings" in found and "docs/hype-copy" in found
    # emoji feature bullets alone (Vite's README) and quoted taglines in link lists are fine
    vite = "# Vite\n\n## Features\n\n" + "".join(f"- 💡 Feature {i}\n" for i in range(6))
    assert "docs/emoji-headings" not in scan(tmp_path / "v", "README.md", vite)
    awesome = "".join(f"* [tool{i}](https://x.dev/{i}) - A blazing fast, seamless, next-gen tool\n" for i in range(4))
    assert "docs/hype-copy" not in scan(tmp_path / "a", "README.md", awesome)


def test_docs_rules_only_run_on_project_docs(tmp_path):
    hype = "Seamless. Cutting-edge. Unlock the power of next-gen apps. Happy coding!\n" * 2
    assert "docs/hype-copy" not in scan(tmp_path, "src/data/readmes/other-project.md", hype)
    assert "docs/hype-copy" in scan(tmp_path, "docs/intro.md", hype)
    # code samples in docs elide on purpose
    sample = "```js\nexport default {\n  // ...\n  plugins: [react()],\n}\n```\n"
    assert "slop/elided-code" not in scan(tmp_path / "e", "README.md", sample)
