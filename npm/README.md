# fixmyslop (npm launcher)

```bash
npx fixmyslop scan            # find AI slop in your UI and code
npx fixmyslop ui              # the app in your browser: issues, fixes, prompts for your agent
npx fixmyslop init --claude   # make Claude Code check its own work
```

This package runs the [FixMySlop](https://github.com/MrSpideyNihal/FixMySlop) CLI through `uvx` or `pipx`
(or a local `pip install fixmyslop`). See the main README for everything it catches.

MCP config for Cursor / Claude Code / Windsurf:

```json
{ "mcpServers": { "fixmyslop": { "command": "npx", "args": ["-y", "fixmyslop", "mcp"] } } }
```
