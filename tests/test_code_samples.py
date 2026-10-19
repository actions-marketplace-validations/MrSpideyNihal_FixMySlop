"""Code shown to users inside JS/TS template strings is text, not code (FlowSync's API-docs modal)."""
from pathlib import Path

from fixmyslop.core.slop.engine import SlopEngine
from fixmyslop.core.slop.rule import mask_multiline_templates


def scan(root: Path, name: str, text: str) -> list:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    (root / "package.json").write_text('{"dependencies": {"react": "18"}}', encoding="utf-8")
    engine = SlopEngine(root)
    return [i.rule_id for i in engine.check_file(path, text) + engine.finalize()]


SAMPLE_MODAL = """import { useState } from 'react';

export function ApiDocs() {
  const examples = {
    typescript: `import { FlowSyncClient } from '@flowsync/sdk';

const client = new FlowSyncClient();
// Trigger the pipeline
console.log(\\`Pipeline \\${client.id} done\\`);`,
  };
  return <pre>{examples.typescript}</pre>;
}
"""


def test_code_sample_in_template_string_is_not_code(tmp_path):
    found = scan(tmp_path, "src/ApiDocs.tsx", SAMPLE_MODAL)
    assert "deps/phantom-import" not in found
    assert "slop/console-log" not in found


def test_real_code_after_regex_and_jsx_is_still_scanned(tmp_path):
    code = ("import { lost } from 'made-up-pkg';\n"
            "const csv = rows.map((c) => `\"${String(c).replace(/\"/g, '\"\"')}\"`).join('\\n');\n"
            "export const Row = () => <p>a</p>;\n"
            "export function save() {\n  try { persist(); } catch {}\n}\n")
    found = scan(tmp_path, "src/Export.tsx", code)
    assert "deps/phantom-import" in found
    assert "slop/empty-catch" in found


def test_masking_keeps_offsets_and_single_line_templates():
    text = "const a = `px-${pad}`;\nconst b = `line one\nline two`;\n"
    masked = mask_multiline_templates(text)
    assert len(masked) == len(text) and masked.count("\n") == text.count("\n")
    assert "`px-${pad}`" in masked            # single-line template untouched
    assert "line one" not in masked and "line two" not in masked
