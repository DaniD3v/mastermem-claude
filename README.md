# mastermem for Claude Code

Team memory for Claude Code: the right memory before the agent makes the mistake, lessons saved when you
correct it. This repository is a Claude Code plugin marketplace with one plugin, `mastermem`.

## Install

In a terminal:

```
claude plugin marketplace add mastermem/mastermem-claude; claude plugin install mastermem@mastermem
```

Then start Claude Code: it opens mastermem in the browser (or shows the link). Approve it and pick the team; the
MCP tools work in that same session (the helper makes the device token before the login and the MCP connection
sends it from the start, the server creates it on approve). The token goes into
`~/.config/mastermem/claude.json` (0600).

Another team or a revoked token: delete that file and start a new session.

## What it does

- Hooks on session start, prompt, tool batch, shell commands (Bash, PowerShell) and Stop send the hook
  event to `<server>/v1/hook/<event>`; the answer adds the matching memories to the agent's context and
  asks for a lesson after a correction.
- The MCP server (`<server>/mcp`, named `mastermem` in Claude Code) gives the agent the memory
  tools.

When the server is down or slow, Claude Code keeps working without memories: every hook gives up after
1 to 3 seconds and never blocks.

## Requirements

Nothing to install: the plugin ships a small helper (`hooks/bin/`, macOS and Linux on Intel and Arm,
Windows 10 and 11) that sends the hook events. The token is kept in the platform's secure credential store
(on Linux `~/.claude/.credentials.json`, mode 0600), never in settings files or `hooks.json`, and the helper
gets it through its environment, so other users on a shared machine cannot see it in the process list.

Build the helper (maintainers): `./build.sh` (needs Go), then commit `plugins/mastermem/hooks/bin/`. Tests:
`cd ../server && go test ./cmd/mastermem-hook` (add `MASTERMEM_PLUGIN_E2E=1` for the end to end run with `claude`).

## Upgrade from srmem

The plugin was called `srmem@srmem` before 0.4.0. Remove it, then install as above:

```
claude plugin uninstall srmem@srmem
claude plugin marketplace remove srmem
```

## Uninstall

```
claude plugin uninstall mastermem@mastermem
claude plugin marketplace remove mastermem
```

Had the older srmem command line client installed (`srmem install --remote`)? Run `srmem uninstall` first, or every hook
fires twice.
