#!/usr/bin/env python3
"""Pergunta rápida pra IA usando as contas do próprio usuário. Imprime 1 JSON: {resposta, segundos, modelo} ou {erro}.

    python3 pergunta.py --provedor claude-cli|codex-cli|anthropic-api|openai-api [--perfil ID] [--modelo M] < pergunta
    python3 pergunta.py --conta <id da conta em `ia` do config> < pergunta

Com --conta, se ela estiver sem login (ou o token venceu), tenta as outras contas da lista `ia`, na ordem, e devolve
também {conta: quem respondeu, nota: "Alice sem login, respondi pela Bia", semLogin: [nomes]}.

A pergunta vem pelo stdin (nunca na linha de comando, que aparece no `ps`). Seguro por construção:
- roda numa pasta temporária vazia, apagada no fim; nenhum arquivo do Mac é anexado;
- Claude Code: sem nenhuma ferramenta (--tools ""), sem MCP (--strict-mcp-config), sem configurações/hooks do usuário
  (--setting-sources project, numa pasta vazia), sem comandos e sem salvar sessão;
- Codex: sandbox só-leitura, sessão efêmera e TODA ferramenta desligada (shell, exec, code mode, imagem, navegador,
  apps, plugins, busca na web): o modelo só responde texto;
- API (chave do usuário no Keychain: "anthropic" ou "openai"): só texto, sem ferramenta nenhuma;
- tempo-limite de 90 s."""
import json, os, re, shutil, sys, tempfile, time, urllib.error, urllib.request

from comum import CACHE, N_, _iso, carregar, segredo, t

SISTEMA = t('Responda em português do Brasil, direto e curto (até 6 linhas). Pode usar Markdown simples.')
ORCA = os.path.expanduser('~/Library/Application Support/orca')
PATH = ':'.join([os.path.expanduser('~/.local/bin'), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin'])
DESLIGA_CODEX = ['shell_tool', 'unified_exec', 'code_mode_host', 'view_image', 'apps', 'browser_use', 'computer_use', 'image_generation',
                 'hooks', 'plugins', 'remote_plugin', 'multi_agent', 'in_app_browser']


def _cli(nome):
    return shutil.which(nome, path=PATH)


def _env(prov, perfil):
    env = dict(os.environ, PATH=PATH)
    if perfil and prov == 'claude-cli':  # conta de um perfil do Orca: a mesma pasta de configuração que o Orca usa pra ele
        env['CLAUDE_CONFIG_DIR'] = f'{ORCA}/claude-profiles/{perfil}/home'
    elif perfil and prov == 'codex-cli':
        env['CODEX_HOME'] = f'{ORCA}/codex-accounts/{perfil}/home'
    return env


# erro de login/token em qualquer provedor: vira "sem login" (nunca o texto cru do CLI, tipo "Please run /login").
# Os erros daqui saem como a chave em português (N_) e só viram texto do idioma em responder(), depois desse teste.
SEM_LOGIN = re.compile(r'not logged in|/login|log ?in again|sem login|sem chave|authenticat|unauthori[sz]ed|\b401\b|invalid.{0,12}(api.?key|token|bearer)'
                       r'|token.{0,20}(expired|revoked)|expired.{0,12}token|refresh.?token|invalid_grant', re.I)


# o Codex com token vencido avisa na hora pelo módulo de login (e depois insiste por ~18 s): essa linha encerra na hora.
# Só o login do próprio Codex ou o 401 da resposta dele (um 401 de servidor MCP não conta)
CODEX_SEM_LOGIN = re.compile(r'^(\S+Z )?ERROR:? (codex_login::.*(401|could not be refreshed|log ?in)|unexpected status 401)', re.I | re.M)
ANOTADO = os.path.join(CACHE, 'sem-login.json')  # conta → quando uma pergunta deu "sem login" (o coletor lê)


def anotado(prov, perfil=None):
    """Quando uma pergunta descobriu que a conta está sem login (0 = nunca). O teste barato não vê token vencido: o
    Codex diz "logado" e a API devolve 401. O coletor usa isto pra marcar "sem login" por 15 min."""
    try:
        return json.load(open(ANOTADO)).get(f'{prov}|{perfil or ""}', 0)
    except (OSError, ValueError, AttributeError):
        return 0


def anotar(cs, r):
    try:
        d = json.load(open(ANOTADO))
    except (OSError, ValueError):
        d = {}
    for c in cs:
        k = f"{c.get('perguntar')}|{c.get('perfil') or ''}"
        if nome(c) in r.get('semLogin', []):
            d[k] = time.time()
        elif nome(c) == r.get('conta') and 'resposta' in r:
            d.pop(k, None)
    try:
        os.makedirs(CACHE, exist_ok=True)
        json.dump(d, open(ANOTADO, 'w'))
    except OSError:
        pass


def logado(prov, perfil=None):
    """Teste barato de login, sem gastar pergunta (~0,07 s no Claude, ~0,01 s no Codex): True, False ou None (não sei).
    Só pergunta ao próprio CLI; nenhuma credencial é lida aqui. API: None (a chave mora no Keychain e não é tocada)."""
    exe = _cli({'claude-cli': 'claude', 'codex-cli': 'codex'}.get(prov, ''))
    if not exe:
        return None
    if prov == 'claude-cli':
        _, out, _ = run_env([exe, 'auth', 'status', '--json'], _env(prov, perfil), None, '', timeout=10)
        try:
            d = json.loads(out)
        except ValueError:
            return None
        vence = _iso(d.get('loginExpiresAt'))
        return bool(d.get('loggedIn')) and not (vence and vence < time.time())
    rc, out, err = run_env([exe, 'login', 'status'], _env(prov, perfil), None, '', timeout=10)
    return None if rc in (124, 127) else rc == 0 and 'not logged in' not in (out + err).lower()


def claude(p, perfil, modelo, pasta):
    exe = _cli('claude')
    if not exe:
        return {'erro': N_('Claude Code (CLI) não encontrado')}
    env = _env('claude-cli', perfil)
    rc, out, err = run_env([exe, '-p', '--model', modelo or 'haiku', '--tools', '', '--strict-mcp-config', '--setting-sources', 'project',
                            '--disable-slash-commands', '--no-session-persistence', '--output-format', 'json', '--system-prompt', SISTEMA],
                           env, pasta, p)
    try:
        d = json.loads(out)
    except ValueError:
        return {'erro': (err or out or 'sem resposta').strip().splitlines()[-1][:160] if (err or out).strip() else N_('sem resposta')}
    if d.get('is_error'):
        return {'erro': str(d.get('result') or N_('erro do Claude'))[:160]}
    return {'resposta': d.get('result', ''), 'modelo': next(iter(d.get('modelUsage') or {}), modelo or 'haiku'), 'custo': d.get('total_cost_usd')}


def codex(p, perfil, modelo, pasta):
    exe = _cli('codex')
    if not exe:
        return {'erro': N_('Codex (CLI) não encontrado')}
    env = _env('codex-cli', perfil)
    saida = os.path.join(pasta, '.resposta')
    cmd = [exe, 'exec', '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--color', 'never', '-C', pasta,
           '-c', 'model_reasoning_effort="low"', '-c', 'web_search="disabled"', '-o', saida]
    for f in DESLIGA_CODEX:
        cmd += ['--disable', f]
    if modelo:
        cmd += ['-m', modelo]
    rc, out, err = run_env(cmd + ['-'], env, pasta, SISTEMA + '\n\n' + p, corta=CODEX_SEM_LOGIN)
    if CODEX_SEM_LOGIN.search(err + out):
        return {'erro': N_('sem login (token vencido)')}
    try:
        resp = open(saida).read().strip()
    except OSError:
        resp = ''
    if rc != 0 or not resp:
        linhas = [l for l in (err + out).splitlines() if 'ERROR' in l or 'error' in l.lower()]
        return {'erro': (linhas[-1] if linhas else N_('Codex não respondeu'))[:160]}
    return {'resposta': resp, 'modelo': modelo or 'codex'}


def anthropic(p, modelo):
    chave = segredo('anthropic')
    if not chave:
        return {'erro': N_('sem chave da Anthropic (Ajustes › IA)')}
    corpo = {'model': modelo or 'claude-haiku-5-5', 'max_tokens': 600, 'system': SISTEMA, 'messages': [{'role': 'user', 'content': p}]}
    d = _post('https://api.anthropic.com/v1/messages', corpo, {'x-api-key': chave, 'anthropic-version': '2023-06-01'})
    if 'erro' in d:
        return d
    return {'resposta': ''.join(b.get('text', '') for b in d.get('content', []) if b.get('type') == 'text'), 'modelo': d.get('model')}


def openai(p, modelo):
    chave = segredo('openai')
    if not chave:
        return {'erro': N_('sem chave da OpenAI (Ajustes › IA)')}
    if not modelo:
        return {'erro': N_('escolha o modelo da OpenAI nos Ajustes')}
    d = _post('https://api.openai.com/v1/responses', {'model': modelo, 'instructions': SISTEMA, 'input': p}, {'Authorization': f'Bearer {chave}'})
    if 'erro' in d:
        return d
    txt = ''.join(c.get('text', '') for o in d.get('output', []) for c in o.get('content', []) or [] if c.get('type') == 'output_text')
    return {'resposta': txt, 'modelo': d.get('model')}


def _post(url, corpo, cab):
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), headers=dict(cab, **{'Content-Type': 'application/json'}))
    try:
        return json.load(urllib.request.urlopen(req, timeout=90))
    except urllib.error.HTTPError as e:
        return {'erro': t('a API respondeu {0}', e.code)}
    except (urllib.error.URLError, OSError, ValueError):
        return {'erro': N_('sem rede')}


def run_env(cmd, env, pasta, entrada, timeout=90, corta=None):
    """A pergunta entra pelo stdin do CLI: nunca fica na linha de comando (visível no `ps`). `corta`: linha (regex) que
    encerra o processo na hora; aí a saída vem junta (stdout + stderr) em `out`."""
    import subprocess, threading
    try:
        if not corta:
            p = subprocess.run(cmd, cwd=pasta, env=env, capture_output=True, text=True, timeout=timeout, input=entrada)
            return p.returncode, p.stdout, p.stderr
        p = subprocess.Popen(cmd, cwd=pasta, env=env, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        return 124, '', N_('demorou mais de 90 s')
    except OSError as e:
        return 127, '', type(e).__name__
    estourou = []
    t = threading.Timer(timeout, lambda: (estourou.append(1), p.kill()))
    t.start()
    try:
        p.stdin.write(entrada)
        p.stdin.close()
    except OSError:
        pass
    linhas = []
    for l in p.stdout:
        linhas.append(l)
        if corta.search(l):
            p.kill()
            break
    p.wait()
    t.cancel()
    return (124, '', 'demorou mais de 90 s') if estourou else (p.returncode, ''.join(linhas), '')


def uma(p, c, pasta):
    prov, perfil, modelo = c.get('perguntar'), c.get('perfil') or None, c.get('modelo') or None
    if prov == 'claude-cli':
        return claude(p, perfil, modelo, pasta)
    if prov == 'codex-cli':
        return codex(p, perfil, modelo, pasta)
    if prov == 'anthropic-api':
        return anthropic(p, modelo)
    if prov == 'openai-api':
        return openai(p, modelo)
    return {'erro': t('provedor desconhecido: {0}', prov)}


def contas(arg):
    """A conta escolhida primeiro, depois as outras da lista `ia` do config, na ordem. Sem --conta: só a dos argumentos."""
    ia = [c for c in carregar()['ia'] if c.get('perguntar')]
    eu = next((c for c in ia if c.get('id') == arg.get('conta')), None)
    if eu:
        return [eu] + [c for c in ia if c is not eu]
    return [{'nome': arg.get('nome', ''), 'perguntar': arg.get('provedor', 'claude-cli'), 'perfil': arg.get('perfil'), 'modelo': arg.get('modelo')}]


def nome(c):
    return c.get('nome') or c.get('ferramenta') or c.get('perguntar')


def juntar(nomes):
    return t('{0} e {1}', ', '.join(nomes[:-1]), nomes[-1]) if len(nomes) > 1 else nomes[0]


def responder(p, cs, pasta, logado=logado, uma=uma):
    """Pergunta pela 1ª conta logada. Sem login (teste barato antes, ou erro de login/token na resposta) = próxima."""
    sem = []
    for c in cs:
        r = {'erro': 'sem login'} if logado(c.get('perguntar'), c.get('perfil')) is False else uma(p, c, pasta)
        if not SEM_LOGIN.search(r.get('erro') or ''):
            r['conta'] = nome(c)
            if r.get('erro'):
                r['erro'] = t(r['erro'])
            if sem:
                r['semLogin'] = sem
                if 'resposta' in r:
                    r['nota'] = t('{0} sem login, respondi pela {1}', juntar(sem), nome(c))
            return r
        sem.append(nome(c))
    msg = (t('{0} está sem login. Clique no logo pra entrar pelo Orca.', sem[0]) if len(sem) == 1 else
           t('Nenhuma conta de IA está logada ({0}). Clique num logo pra entrar pelo Orca.', juntar(sem)))
    return {'erro': msg, 'semLogin': sem, 'conta': sem[0]}


def main():
    a = sys.argv[1:]
    arg = {a[i][2:]: a[i + 1] for i in range(0, len(a) - 1, 2) if a[i].startswith('--')}
    p = sys.stdin.read().strip()
    if not p:
        print(json.dumps({'erro': t('pergunta vazia')}))
        return
    t0 = time.time()
    pasta = tempfile.mkdtemp(prefix='pergunta-')
    try:
        cs = contas(arg)
        r = responder(p, cs, pasta)
        anotar(cs, r)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)
    r['segundos'] = round(time.time() - t0, 1)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == '__main__':
    main()
