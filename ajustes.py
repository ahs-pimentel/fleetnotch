#!/usr/bin/env python3
"""O que a janela de Ajustes e o assistente de primeira execução pedem ao sistema. Cada comando imprime 1 JSON.

    descobrir                      Macs (e Linux) da sua Tailscale
    ssh-hosts                      apelidos do ~/.ssh/config (pra adicionar servidor com um clique)
    chave | gerar-chave            chave SSH (~/.ssh/id_ed25519): existe? gera se não existir (nunca sobrescreve)
    instalar-chave --host H [--usuario U] [--porta P]   abre o Terminal com ssh-copy-id (a senha é digitada lá)
    testar --host H [--usuario U] [--porta P] [--linux] [--servicos a,b] [--docker]   uma sonda, agora
    ia-fontes                      contas de IA achadas neste Mac (Claude Code, perfis do Orca, Codex)
    agentes                        agentes achados neste Mac (Hermes e perfis, OpenClaw)
    agente-testar < json           roda o adaptador de UM agente (config pelo stdin)
    email-contas                   contas Gmail/Outlook conectadas no Composio (precisa da chave no Keychain)
    segredo-guardar --conta C < valor | segredo-apagar --conta C | segredo-existe --conta C
    composio-importar              copia a chave do `composio login` (~/.composio/user_data.json) pro Keychain
Nada aqui lê conteúdo de e-mail, mensagem de agente ou arquivo do usuário."""
import glob, json, os, re, shlex, sys

from comum import APP, CACHE, apagar_segredo, guardar_segredo, run, segredo, t

ORCA = os.path.expanduser('~/Library/Application Support/orca')
CHAVE = os.path.expanduser('~/.ssh/id_ed25519')


def descobrir(_):
    import coleta
    rede = coleta.tailnet()
    if not rede:
        return {'erro': t('Tailscale não encontrado ou desligado'), 'nos': []}
    return {'nos': [{'nome': n, 'host': n, 'ip': x['ip'], 'os': x.get('os'), 'online': x['online'], 'este': x['self']}
                    for n, x in sorted(rede.items(), key=lambda kv: (not kv[1]['self'], kv[0])) if x.get('os') in ('macOS', 'linux')]}


def ssh_hosts(_):
    r, atual = [], None
    try:
        linhas = open(os.path.expanduser('~/.ssh/config')).read().splitlines()
    except OSError:
        return {'hosts': []}
    for l in linhas:
        m = re.match(r'\s*(\w+)\s+(.+)', l)
        if not m:
            continue
        k, v = m.group(1).lower(), m.group(2).strip()
        if k == 'host':
            atual = None
            for apelido in v.split():
                if not any(c in apelido for c in '*?!'):
                    atual = {'apelido': apelido, 'host': apelido, 'usuario': None, 'porta': None}
                    r.append(atual)
        elif atual and k in ('user', 'port'):
            atual['usuario' if k == 'user' else 'porta'] = v
    return {'hosts': r}


def chave(_):
    if not os.path.exists(CHAVE + '.pub'):
        return {'existe': False, 'caminho': CHAVE}
    _, out, _ = run(['ssh-keygen', '-lf', CHAVE + '.pub'], timeout=5)
    return {'existe': True, 'caminho': CHAVE, 'digital': (out.split() + ['', ''])[1]}


def gerar_chave(_):
    if os.path.exists(CHAVE):
        return dict(chave(_), ja=True)
    os.makedirs(os.path.dirname(CHAVE), mode=0o700, exist_ok=True)
    rc, _, err = run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', CHAVE, '-C', f'{APP.lower()}@{os.uname().nodename.split(".")[0]}'], timeout=20)
    return chave(_) if rc == 0 else {'erro': err.strip()[:160] or t('ssh-keygen falhou')}


def _dest(a):
    import coleta
    no = coleta.resolver(a.get('host'), coleta.tailnet())
    alvo = dict(no, usuario=a.get('usuario'), porta=a.get('porta'))
    return no, alvo, coleta.destino(alvo)


def instalar_chave(a):
    """ssh-copy-id precisa da senha do outro lado: vai num Terminal, onde a pessoa digita (o app nunca vê a senha)."""
    if not os.path.exists(CHAVE + '.pub'):
        return {'erro': t('gere a chave primeiro')}
    no, _, dest = _dest(a)
    if no['self']:
        return {'erro': t('este é o próprio Mac')}
    if not re.fullmatch(r'[\w.@:-]+', dest) or not re.fullmatch(r'\d*', str(a.get('porta') or '')):
        return {'erro': t('host inválido')}
    nome = re.sub(r'[^\w.-]', '_', a['host'])
    p = os.path.join(CACHE, f'instalar-chave-{nome}.command')
    os.makedirs(CACHE, exist_ok=True)
    porta = f" -p {a['porta']}" if a.get('porta') and str(a['porta']) != '22' else ''
    # o texto vai entre aspas simples do shell (shlex.quote): tradução nenhuma vira comando
    antes = shlex.quote(t('Instalando a chave SSH em {0}. Digite a senha da conta lá quando pedir (só desta vez).', dest))
    depois = shlex.quote(t('Pronto. Pode fechar esta janela e voltar ao app.'))
    open(p, 'w').write('#!/bin/zsh\nclear\n'
                       f'echo {antes}\n'
                       f'ssh-copy-id -i {CHAVE}.pub{porta} {dest} && echo && echo {depois}\n')
    os.chmod(p, 0o700)
    rc, _, _ = run(['open', '-a', 'Terminal', p])
    return {'ok': rc == 0}


def testar(a):
    import coleta
    no, alvo, _ = _dest(a)
    if no.get('online') is False:
        return {'ok': False, 'erro': t('offline na Tailscale')}
    if a.get('linux'):
        d, erro = coleta.sondar_linux(dict(alvo, servicos=[x for x in (a.get('servicos') or '').split(',') if x],
                                           docker='docker' in a))
        if d is None:
            return {'ok': False, 'erro': erro, 'dica': dica(erro, alvo)}
        s = coleta.servidor(dict(alvo, host=a['host'], nome=a['host']), d, None)
        return {'ok': True, 'resumo': f"{d['so'] or 'Linux'} · CPU {d['cpu']}% · RAM {s['medidas']['ram']}% · " + t('{0} disco(s)', len(d['discos'])),
                'servicos': d['servicos'], 'docker': d.get('docker')}
    d, erro = coleta.sondar(alvo, False)
    if d is None:
        return {'ok': False, 'erro': erro, 'dica': dica(erro, alvo)}
    return {'ok': True, 'modelo': d['modelo'], 'resumo': f"{d['modelo']} · macOS {d['macos']} · CPU {d['cpu']}%"}


def dica(erro, alvo):
    if erro == t('sem chave SSH'):  # erro veio de coleta.erro_ssh, já no idioma
        return t('Instale a chave SSH neste Mac (botão "Instalar chave") e ative o Login Remoto nele.')
    if erro in (t('não respondeu'), t('recusou a conexão')):
        return t('Ative o Login Remoto no outro Mac: Ajustes do Sistema › Geral › Compartilhamento › Login Remoto.')
    if erro == t('host desconhecido'):
        return t('Confira o nome: use o nome da Tailscale, o IP ou um apelido do ~/.ssh/config.')
    return None


def ia_fontes(_):
    r = []
    if os.path.isdir(os.path.expanduser('~/.claude')):  # Claude Code sem Orca: o anel vem do statusline.py (README)
        r.append({'nome': 'Claude', 'ferramenta': 'Claude', 'fonte': 'claude-perfil', 'perfil': '',
                  'app': ['/Applications/Claude.app'], 'perguntar': 'claude-cli'})
    for p in sorted(glob.glob(f'{ORCA}/claude-profiles/*/home/deck-rate-limits.json')):
        perfil = p.split('/')[-3]
        r.append({'nome': f'Claude · {perfil[:8]}', 'ferramenta': 'Claude', 'fonte': 'claude-perfil', 'perfil': perfil,
                  'app': ['/Applications/Claude.app'], 'perguntar': 'claude-cli'})
    if glob.glob(os.path.expanduser('~/.codex/sessions/*')) or glob.glob(f'{ORCA}/codex-accounts/*/home/sessions/*'):
        r.append({'nome': 'ChatGPT', 'ferramenta': 'ChatGPT', 'fonte': 'codex-sessoes', 'perfil': '',
                  'app': ['/Applications/Codex.app', '/Applications/ChatGPT.app'], 'perguntar': 'codex-cli'})
    return {'contas': r}


def agentes_achados(_):
    r = []
    h = os.path.expanduser('~/.hermes')
    if os.path.isdir(h):
        r.append({'nome': 'Hermes', 'tipo': 'hermes', 'home': '~/.hermes', 'perfil': ''})
        g = json.load(open(f'{h}/gateway_state.json')) if os.path.exists(f'{h}/gateway_state.json') else {}
        servidos = set(g.get('served_profiles') or [])
        for p in sorted(glob.glob(f'{h}/profiles/*/')):
            nome = p.rstrip('/').split('/')[-1]
            if nome != 'default' and os.path.exists(p + 'config.yaml'):
                r.append({'nome': nome, 'tipo': 'hermes', 'home': '~/.hermes', 'perfil': nome, 'servido': nome in servidos})
    if os.path.isdir(os.path.expanduser('~/.openclaw')) or os.path.exists(os.path.expanduser('~/Library/LaunchAgents/ai.openclaw.gateway.plist')):
        r.append({'nome': 'OpenClaw', 'tipo': 'openclaw', 'porta': 18789, 'rotulo': 'ai.openclaw.gateway'})
    return {'agentes': r}


def agente_testar(_):
    import agentes
    a = json.load(sys.stdin)
    e = agentes.estado([a])[0]
    return {k: e.get(k) for k in ('estado', 'nivel', 'canais', 'problemas', 'erro', 'atividade', 'avatar')}


def email_contas(_):
    import caixas
    k = segredo('composio')
    if not k:
        return {'erro': t('sem chave do Composio'), 'contas': []}
    try:
        return {'contas': caixas.contas_composio(k)}
    except caixas.Falha as e:
        return {'erro': t(str(e)), 'contas': []}


def segredo_guardar(a):
    v = sys.stdin.read()
    if not v.strip() or not re.fullmatch(r'[\w:.@-]+', a.get('conta', '')):
        return {'ok': False}
    return {'ok': guardar_segredo(a['conta'], v)}


def composio_importar(_):
    try:
        k = json.load(open(os.path.expanduser('~/.composio/user_data.json')))['api_key']
    except (OSError, ValueError, KeyError):
        return {'ok': False, 'erro': t('não achei o login do Composio CLI (rode `composio login`)')}
    return {'ok': guardar_segredo('composio', k)}


CMDS = {'descobrir': descobrir, 'ssh-hosts': ssh_hosts, 'chave': chave, 'gerar-chave': gerar_chave, 'instalar-chave': instalar_chave,
        'testar': testar, 'ia-fontes': ia_fontes, 'agentes': agentes_achados, 'agente-testar': agente_testar, 'email-contas': email_contas,
        'segredo-guardar': segredo_guardar, 'segredo-apagar': lambda a: {'ok': apagar_segredo(a.get('conta', ''))},
        'segredo-existe': lambda a: {'existe': segredo(a.get('conta', '')) is not None}, 'composio-importar': composio_importar}


DEMO = {  # FROTA_DEMO=1: o assistente e os Ajustes mostram um Mac de mentira (pra print e pra experimentar sem configurar)
    'descobrir': {'nos': [{'nome': 'studio', 'host': 'studio', 'ip': '192.0.2.1', 'os': 'macOS', 'online': True, 'este': True},
                          {'nome': 'mbp', 'host': 'mbp', 'ip': '192.0.2.2', 'os': 'macOS', 'online': True, 'este': False},
                          {'nome': 'render-mini', 'host': 'render-mini', 'ip': '192.0.2.3', 'os': 'macOS', 'online': True, 'este': False},
                          {'nome': 'design-air', 'host': 'design-air', 'ip': '192.0.2.4', 'os': 'macOS', 'online': False, 'este': False},
                          {'nome': 'web-01', 'host': 'web-01', 'ip': '192.0.2.9', 'os': 'linux', 'online': True, 'este': False}]},
    'ssh-hosts': {'hosts': [{'apelido': 'web-01', 'host': 'web-01', 'usuario': 'deploy', 'porta': None},
                            {'apelido': 'db-01', 'host': 'db-01', 'usuario': 'deploy', 'porta': None},
                            {'apelido': 'edge-sp', 'host': 'edge-sp', 'usuario': 'deploy', 'porta': '2222'}]},
    'chave': {'existe': True, 'caminho': '~/.ssh/id_ed25519', 'digital': 'SHA256:3q2+7w…demo'},
    'ia-fontes': {'contas': [{'nome': 'Claude · Pro', 'ferramenta': 'Claude', 'fonte': 'claude-perfil', 'perfil': 'demo', 'app': ['/Applications/Claude.app'],
                              'perguntar': 'claude-cli'},
                             {'nome': 'ChatGPT', 'ferramenta': 'ChatGPT', 'fonte': 'codex-sessoes', 'perfil': '',
                              'app': ['/Applications/ChatGPT.app', '/Applications/Codex.app'], 'perguntar': 'codex-cli'}]},
    'agentes': {'agentes': [{'nome': 'Atlas', 'tipo': 'hermes', 'home': '~/.hermes', 'perfil': ''},
                            {'nome': 'research', 'tipo': 'hermes', 'home': '~/.hermes', 'perfil': 'research', 'servido': True},
                            {'nome': 'OpenClaw', 'tipo': 'openclaw', 'porta': 18789, 'rotulo': 'ai.openclaw.gateway'}]},
    'email-contas': {'contas': [{'id': 'ca_demo1', 'app': 'gmail', 'apelido': 'you@example.com', 'status': 'ACTIVE', 'usuario': 'u'},
                                {'id': 'ca_demo3', 'app': 'gmail', 'apelido': 'team@example.com', 'status': 'ACTIVE', 'usuario': 'u'},
                                {'id': 'ca_demo2', 'app': 'outlook', 'apelido': 'hello@example.net', 'status': 'EXPIRED', 'usuario': 'u'}]},
    'testar': {'ok': True, 'modelo': 'com.apple.macbookpro', 'resumo': 'MacBook Pro · macOS 26.3 · CPU 12%'},
    'segredo-existe': {'existe': True},
}


def main():
    argv = sys.argv[1:]
    if os.environ.get('FROTA_DEMO') and argv and argv[0] in DEMO:
        print(json.dumps(DEMO[argv[0]], ensure_ascii=False))
        return
    if not argv or argv[0] not in CMDS:
        print(json.dumps({'erro': 'uso: ajustes.py ' + '|'.join(CMDS)}))
        return
    a, i = {}, 1
    while i < len(argv):
        if argv[i].startswith('--'):
            tem = i + 1 < len(argv) and not argv[i + 1].startswith('--')
            a[argv[i][2:]] = argv[i + 1] if tem else True
            i += 2 if tem else 1
        else:
            i += 1
    try:
        r = CMDS[argv[0]](a)
    except Exception as e:  # nunca devolve traceback cru pra UI
        r = {'erro': f'{type(e).__name__}: {e}'[:200]}
    print(json.dumps(r, ensure_ascii=False))


if __name__ == '__main__':
    main()
