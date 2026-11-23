# Security

FleetNotch has no server and sends nothing anywhere you didn't configure. What it touches:

| What | How | Where it goes |
|---|---|---|
| Other Macs | `ssh` with your key, a read-only probe sent through stdin | only your machines |
| Linux servers | `ssh` + POSIX `sh` probe through stdin (`/proc`, `df`, `systemctl is-active`, `docker ps`) | only your servers |
| AI agents | local files, LaunchAgents, `http://127.0.0.1:<port>/readyz` or the health URL you set | localhost |
| Email | Composio (your key) or IMAP (your app password): unread count + latest header, never the body | Composio / your mail server |
| Quick ask | Claude Code / Codex CLI you are signed in to, or your API key; empty temp folder, all tools disabled | Anthropic / OpenAI |
| Secrets | Keychain, service `FleetNotch`, written and read through `/usr/bin/security` (stdin) | your login Keychain |

Note on the Keychain: items are created with `/usr/bin/security`, so they are readable without a prompt by that tool, which means
by any process running as your user (the same trust level as the CLIs that keep tokens in files). This keeps rebuilt, ad-hoc
signed copies of the app from asking for your Keychain password after every update.

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security › Report a vulnerability) instead of a public issue.
