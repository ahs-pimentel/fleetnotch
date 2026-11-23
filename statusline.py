#!/usr/bin/env python3
"""Optional Claude Code statusline that feeds FleetNotch's Claude ring.

Claude Code hands its statusline a JSON with your plan's usage (`rate_limits`: % used and reset time of the 5-hour and
weekly windows). This keeps only that block, plus the model id, in <Claude config dir>/deck-rate-limits.json (at most
every 15 s), which FleetNotch reads. No credential, no prompt, no file path is saved.

install.sh copies it to ~/.claude/fleetnotch-statusline.py. Then, in ~/.claude/settings.json:

    "statusLine": {"type": "command", "command": "~/.claude/fleetnotch-statusline.py"}

Already have a statusline? Pass it as arguments and it gets the same input and prints as before:

    "statusLine": {"type": "command", "command": "~/.claude/fleetnotch-statusline.py ~/.claude/my-statusline.sh"}
"""
import json, os, subprocess, sys, time

ARQ = os.path.join(os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude'), 'deck-rate-limits.json')


def gravar(d, agora):
    rl = d.get('rate_limits')
    if not rl:
        return
    try:
        if agora - os.path.getmtime(ARQ) < 15:
            return
    except OSError:
        pass
    try:
        with open(ARQ + '.tmp', 'w') as f:
            json.dump({'ts': int(agora), 'rl': rl, 'model': (d.get('model') or {}).get('id', '')}, f)
        os.replace(ARQ + '.tmp', ARQ)
    except OSError:
        pass


def linha(d):
    """Default line when there is no other statusline: model · 5h 23% · 7d 60%."""
    rl = d.get('rate_limits') or {}
    partes = [(d.get('model') or {}).get('display_name') or 'Claude']
    for k, r in (('five_hour', '5h'), ('seven_day', '7d')):
        if (rl.get(k) or {}).get('used_percentage') is not None:
            partes.append(f"{r} {rl[k]['used_percentage']:.0f}%")
    return ' · '.join(partes)


if __name__ == '__main__':
    bruto = sys.stdin.read()
    try:
        d = json.loads(bruto)
    except ValueError:
        d = {}
    gravar(d if isinstance(d, dict) else {}, time.time())
    if sys.argv[1:]:
        sys.exit(subprocess.run(sys.argv[1:], input=bruto, text=True).returncode)
    print(linha(d if isinstance(d, dict) else {}))
