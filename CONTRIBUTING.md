# Contributing to FleetNotch

Thanks for helping. A few rules keep the app fast, private and glitch-free.

## Run it

```sh
./install.sh                 # builds with swiftc (Command Line Tools) and installs ~/Applications/FleetNotch.app
python3 coleta.py --uma      # one collector cycle, JSON in the terminal
python3 demo.py --uma        # the fake fleet used for screenshots
```

No config yet? The app opens the first-run assistant. Want fake data? Settings › General › Demo data.

## Tests

```sh
python3 teste_coleta.py      # collector: levels, alerts, servers, email, agents, Orca, quick ask safety flags
python3 teste_acoes.py       # actions in a FAKE home folder: only the fixed cache list disappears
open -a Terminal teste/sessao.command   # UI glitch test: ~10 min of automated use, recorded and analysed frame by frame
```

The UI tests need Screen Recording and Accessibility for Terminal.app (System Settings › Privacy & Security).

## Ground rules

- **Read-only.** Probes and adapters only read. Anything that deletes, ejects or restarts goes through `acoes.py`, has a preview, a
  second click, and a log line. No `sudo`, ever.
- **Standard library only** in Python (it must run on `/usr/bin/python3`, 3.9). No pip installs.
- **Secrets never touch disk or argv.** Keychain through `comum.guardar_segredo` / `comum.segredo`, values through stdin.
- **Idle cost matters.** The app idles near 0.02% of a core and the collector near 0.1–0.2%. Expensive reads (Orca, email, connected devices) run on their own clock.
- **Screenshots and videos come from demo mode** (`teste/prints.command`). Never commit anything with your own machines, emails or names.
- **Every string a person reads goes through `t()`** (Swift and Python, same tables) and `python3 teste_i18n.py` must pass.
  The code itself (identifiers and comments) is in Brazilian Portuguese. New languages: [i18n/README.md](i18n/README.md).

## Adding an agent adapter

See [docs/agents.md](docs/agents.md). An adapter is one function in `agentes.py` that returns the common status shape; add a test
with a fake home folder in `teste_coleta.py`.
