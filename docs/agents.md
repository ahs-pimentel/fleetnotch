# Agent adapters

An agent is anything that runs on your Mac and talks to people for you. FleetNotch shows, for each one: online or not, its channels,
what needs attention, the last error and the last activity. **Read-only**: adapters never restart, reconfigure or message an agent.

Every adapter is one function in [`agentes.py`](../agentes.py) returning the same shape:

```json
{"estado": "no ar", "nivel": 0,
 "canais": [{"nome": "WhatsApp", "estado": "conectado", "nivel": 0}],
 "problemas": [{"titulo": "Cron failing: daily report", "nivel": 1}],
 "erro": {"hora": "08:12", "ha": "3h 2min", "texto": "…"},
 "atividade": {"hora": "11:40", "ha": "3min", "tipo": "mensagem recebida", "canal": "whatsapp"}}
```

`nivel`: `-1` no access, `0` fine, `1` attention (amber), `2` critical (red). Text from logs is cleaned before it leaves the adapter
(paths, emails, long ids and phone numbers become `…`); message contents are never read.

## Hermes

[Hermes Agent](https://github.com/NousResearch/hermes-agent). Config: `home` (default `~/.hermes`), `perfil` (empty = the main agent;
otherwise a profile served by the gateway, read from `<home>/profiles/<perfil>`), `rotulo` (LaunchAgent prefix, default `ai.hermes.`).

Reads: the gateway LaunchAgents, `gateway_state.json` and the heartbeat, `served_profiles`/`parked_profiles`, platform states
(`whatsapp`, `telegram`, `<profile>:<platform>`), cron jobs, ticker and `cron-watchdog` issues (each agent gets the issues of its own
jobs), the kanban board (SQLite opened read-only and immutable), and the new lines of `gateway.log` / `errors.log` since the last cycle.

## OpenClaw

[OpenClaw](https://docs.openclaw.ai). Config: `porta` (default 18789), `rotulo` (default `ai.openclaw.gateway`), `home` (default
`~/.openclaw`), `log` (default `~/Library/Logs/openclaw/gateway.log`).

Reads `GET http://127.0.0.1:<porta>/readyz`: `200` = online; `503` with `failing: ["channel:whatsapp:…", …]` = the failing channels
and checks become the agent's channels and problems; connection refused + LaunchAgent not running = stopped.

## Generic

Any agent: `url` (a health endpoint; 2xx = online), `rotulo` (a LaunchAgent label; running = online), `log` (last `ERROR` line and the
file's modification time as last activity). Combine them as you like.

## WhatsApp instances on UAZAPI (any type, optional)

`uazapiURL` and `instancias` (`{"instance-name": "Label"}`); the admin token goes to the Keychain as `uazapi:<host>`. Checked every
5 minutes with `GET /instance/all`; only names and statuses are kept.

## Writing an adapter

1. Add `def meu(a, agora): …` to `agentes.py` returning the shape above (use `servicos()`, `_http()`, `_ultimo_erro()` from the file).
2. Register it in `TIPOS`.
3. Add a test with a fake folder or a fake local HTTP server in `teste_coleta.py` (see the OpenClaw test).
4. Add the type to the picker in `Ajustes.swift` (`AbaAgentes`).
