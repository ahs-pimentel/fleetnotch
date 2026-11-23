#!/usr/bin/env python3
"""Ações da ilha num Mac. O MESMO arquivo roda no Mac do app (controlador) e, pelo stdin do SSH, no Mac alvo (igual à sonda).

    python3 acoes.py <acao> [--maquina NOME_TAILSCALE] [--ids a,b] [--volume NOME] [--pid N] [--alvo X]   → 1 JSON

Ações: cache-previa · cache-limpar --ids · tamanhos · lixeira-esvaziar · ejetar --volume · reiniciar --pid ·
       abrir --alvo monitor|downloads|terminal

Segurança (nada aqui pede sudo nem mexe em configuração):
- O que pode ser apagado é a lista FIXA abaixo (CACHES). A execução recebe só ids e recalcula cada caminho dela;
  caminho nenhum vem de fora. Raiz que é link, que sai do HOME ou cai num lugar protegido é pulada.
- Apagar = o CONTEÚDO da pasta de cache; link dentro dela é desfeito (o alvo do link nunca é tocado).
- Cache de app aberto é pulado (o app pode estar usando).
- Tudo que apaga vai pro ~/Library/Logs/fleetnotch/acoes.log do Mac onde foi apagado (e um resumo no do Mac do app).
- A UI só chama "limpar"/"esvaziar"/"ejetar"/"reiniciar" depois da prévia e do 2º clique."""
import json, os, plistlib, re, shutil, subprocess, sys, time

try:
    from comum import t
except ImportError:  # no Mac alvo (pelo SSH) não tem comum.py: o controlador põe a tabela do idioma (_T) no início do stdin
    def t(s, *a):
        r = globals().get('_T', {}).get(s) or (s.split('|', 1)[1] if '|' in s else s)
        for i, v in enumerate(a):
            r = r.replace('{%d}' % i, str(v))
        return r

HOME = os.path.realpath(os.path.expanduser(os.environ.get('FROTA_HOME') or '~'))  # FROTA_HOME: só o teste usa
LOG = os.path.join(HOME, 'Library/Logs/fleetnotch/acoes.log')
PATH_EXTRA = ['/opt/homebrew/bin', '/usr/local/bin', os.path.join(HOME, '.local/bin'), os.path.join(HOME, '.cargo/bin'),
              '/usr/bin', '/bin', '/usr/sbin', '/sbin']  # diskutil e lsof moram em /usr/sbin

# Nada aqui dentro é tocado, nem por link: dado, projeto, segredo, configuração.
PROTEGIDOS = ['Documents', 'Desktop', 'Projects', 'Pictures', 'Movies', 'Music', 'Downloads', 'orca', '.hermes', '.claude',
              '.ssh', '.gnupg', '.config', '.local/share', 'Library/Mail', 'Library/Messages', 'Library/Photos',
              'Library/Keychains', 'Library/Application Support', 'Library/Mobile Documents', 'Library/CloudStorage',
              'Library/Containers', 'Library/Group Containers', 'Library/Preferences']

# (id, nome, pasta relativa ao HOME, modo, trava). A trava é o que NÃO pode estar rodando (regex na linha de comando):
# instalação em andamento, não o uso comum (servidor MCP via `npm exec`/`bun` não usa o cache de pacotes).
#   conteudo: apaga o que tem dentro · uv: `uv cache prune` (só o que não está em uso; ele mesmo checa) · velhos: mantém
#   a versão mais nova de cada navegador headless
INSTALANDO = r'\s+(?:install|i|ci|add|update|up|upgrade|cache)\b'
CACHES = [
    ('npm', 'npm', '.npm/_cacache', 'conteudo', r'(?:^|/)npm(?:-cli\.js)?' + INSTALANDO),
    ('uv', t('uv (o que não está em uso)'), '.cache/uv', 'uv', None),
    ('pip', 'pip', 'Library/Caches/pip', 'conteudo', r'(?:^|/)pip3?\s+(?:install|download|wheel)\b|-m pip\s+install\b'),
    ('brew', t('Homebrew (downloads)'), 'Library/Caches/Homebrew', 'conteudo', r'brew\.sh'),
    ('bun', t('bun (pacotes)'), '.bun/install/cache', 'conteudo', r'(?:^|/)bun' + INSTALANDO),
    ('bun-lib', t('bun (transpilação)'), 'Library/Caches/bun', 'conteudo', r'(?:^|/)bun' + INSTALANDO),
    ('yarn', 'Yarn', 'Library/Caches/Yarn', 'conteudo', r'(?:^|/)yarn(?:\.js)?\b'),
    ('gyp', 'node-gyp (headers)', 'Library/Caches/node-gyp', 'conteudo', r'node-gyp'),
    ('electron', t('Electron (downloads)'), 'Library/Caches/electron', 'conteudo', r'electron-(?:builder|packager|rebuild)|@electron/get'),
    ('go', 'Go (build)', 'Library/Caches/go-build', 'conteudo', r'(?:^|/)go\s+(?:build|install|test|run|mod|vet)\b'),
    ('xcode', 'Xcode DerivedData', 'Library/Developer/Xcode/DerivedData', 'conteudo', r'Xcode\.app/Contents/MacOS/Xcode|xcodebuild'),
    ('chrome', t('Chrome (cache de páginas)'), 'Library/Caches/Google/Chrome', 'conteudo', r'Google Chrome\.app/Contents/MacOS/Google Chrome$'),
    ('headless', 'Chrome headless (cache)', 'Library/Caches/Google/Chrome-headless', 'conteudo', r'chrome-headless-shell|--headless'),
    ('puppeteer', t('Puppeteer (versões antigas)'), '.cache/puppeteer', 'velhos', None),
    ('playwright', t('Playwright (versões antigas)'), 'Library/Caches/ms-playwright', 'velhos', None),
]
# e mais um tipo, achado na hora: instaladores de atualização já baixados (Sparkle) de apps FECHADOS
SPARKLE = 'org.sparkle-project.Sparkle'
# apps que "reiniciar" nunca toca: o sistema, o que mantém a sessão/agentes vivos, segurança, rede, sincronização
NAO_REINICIA = {'Finder', 'Dock', 'SystemUIServer', 'ControlCenter', 'NotificationCenter', 'loginwindow', 'Orca', 'Terminal',
                'iTerm', 'iTerm2', 'Ghostty', 'Warp', 'WezTerm', 'Alacritty', 'kitty', 'Claude', 'Codex', 'FleetNotch',
                'Tailscale', '1Password', '1Password 7', 'Google Drive', 'Screen Sharing'}


def sh(cmd, timeout=30, entrada=None):
    try:
        # TERM: sem ele o binário do Tailscale.app (chamado de dentro do app) tenta abrir a GUI e não responde como CLI
        env = dict(os.environ, PATH=':'.join(PATH_EXTRA + [os.environ.get('PATH', '/usr/bin:/bin')]), TERM='xterm-256color')
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=entrada, env=env)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 124, '', type(e).__name__


def tamanho(p):
    """Bytes ocupados em disco (du, sem seguir link)."""
    rc, out, _ = sh(['du', '-sk', p], timeout=120)
    try:
        return int(out.split()[0]) * 1024
    except (ValueError, IndexError):
        return 0


def processos():
    """[(pid, uid, cpu, executável, linha de comando)] do Mac."""
    if os.environ.get('FROTA_TESTE_PROCS') == 'nenhum':  # só o teste: não depender do que está aberto neste Mac
        return []
    rc, out, _ = sh(['ps', '-axo', 'pid=,uid=,pcpu=,comm='])
    _, args, _ = sh(['ps', '-axo', 'pid=,args='])
    linhas = dict((l.split(None, 1) + [''])[:2] for l in args.splitlines() if l.strip())
    r = []
    for l in out.splitlines():
        m = re.match(r'\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(.+)', l)
        if m:
            r.append((int(m.group(1)), int(m.group(2)), float(m.group(3)), m.group(4).strip(), linhas.get(m.group(1), '')))
    return r


def rodando(trava, procs):
    """A trava casa com algum processo rodando? Devolve um nome curto pra dizer na UI ("Google Chrome aberto")."""
    if not trava:
        return None
    for _, uid, _, exe, args in procs:
        if re.search(trava, args or exe):
            app = re.search(r'([^/]+)\.app/', exe)
            return app.group(1) if app else os.path.basename(args.split()[0] if args else exe)
    return None


def abertos(procs):
    """Arquivos abertos agora pelos processos do usuário: {caminho: quem}. Um lsof só (~0,2 s). Cache com arquivo aberto
    é de alguém rodando (servidor MCP via uvx, script com uv run, app): apagar por baixo dele quebra o processo."""
    if os.environ.get('FROTA_TESTE_PROCS') == 'nenhum':  # só o teste
        return {}
    _, out, _ = sh(['lsof', '-n', '-P', '-u', str(os.getuid()), '-Fpcn'], timeout=60)
    if not out.strip():
        return None  # sem lsof não dá pra saber quem usa o quê: a prévia pula tudo (falha fechada)
    args = {x[0]: x[4] for x in procs}
    r, pid, cmd = {}, 0, '?'
    for l in out.splitlines():
        if l.startswith('p'):
            pid = int(l[1:]) if l[1:].isdigit() else 0
        elif l.startswith('c'):
            a = args.get(pid, '')
            m = re.search(r'([\w.-]+\.py)\b', a)  # script python: o nome do script diz mais que "python3.13"
            cmd = m.group(1) if m else os.path.basename(a.split()[-1]) if a.split() and not a.split()[-1].startswith('-') else l[1:]
        elif l.startswith('n/'):
            r.setdefault(l[1:], cmd)
    return r


def usado_por(raizes, ab):
    if ab is None:
        return t('quem? (não deu pra conferir)')
    pre = tuple(x.rstrip('/') + '/' for x in raizes)
    return next((c for p, c in ab.items() if p.startswith(pre)), None)


def protegido(caminho):
    rel = os.path.relpath(caminho, HOME)
    return rel == '.' or rel.startswith('..') or any(rel == p or rel.startswith(p + '/') for p in PROTEGIDOS)


def raiz_segura(rel):
    """Pasta de cache existente, de verdade dentro do HOME, sem link no caminho e fora dos lugares protegidos."""
    p = os.path.join(HOME, rel)
    if not os.path.isdir(p):
        return None, t('não existe')
    if os.path.realpath(p) != os.path.normpath(p):
        return None, t('é um link (não sigo)')
    if protegido(p):
        return None, t('lugar protegido')
    return p, None


def velhos(raiz):
    """Versões antigas de navegador headless: em cada família, tudo menos a mais nova (por data)."""
    familias = {}
    for e in os.scandir(raiz):
        if not e.is_dir(follow_symlinks=False):
            continue
        filhos = [f for f in os.scandir(e.path) if f.is_dir(follow_symlinks=False)]
        if re.search(r'-\d+$', e.name):  # playwright: <navegador>-<revisão>
            familias.setdefault(re.sub(r'-\d+$', '', e.name), []).append(e.path)
        else:  # puppeteer: <navegador>/<plataforma>-<versão>
            familias.setdefault(e.name, []).extend(f.path for f in filhos)
    r = []
    for vs in familias.values():
        r += sorted(vs, key=os.path.getmtime)[:-1]
    return r


def itens_cache(procs, ab=None):
    """A prévia: o que cada item da lista fixa apagaria agora, e o que fica de fora (e por quê)."""
    ab = abertos(procs) if ab is None else ab
    r = []
    for cid, nome, rel, modo, trava in CACHES:
        p, motivo = raiz_segura(rel)
        if p is None:
            continue  # não existe nesse Mac: nem aparece
        aberto = rodando(trava, procs)
        if modo == 'velhos':
            alvo = velhos(p)
            if not alvo:
                continue
            b = sum(tamanho(x) for x in alvo)
            u = usado_por(alvo, ab) or next((os.path.basename(a.split()[0]) for *_, a in procs if any(x in a for x in alvo)), None)
            r.append({'id': cid, 'nome': nome, 'caminho': '~/' + rel, 'bytes': b, 'pulado': t('em uso por {0}', u) if u else None})
        else:
            b = tamanho(p)
            if b < 1024 * 1024:
                continue  # menos de 1 MB: não vale a linha
            u = None if aberto else usado_por([p], ab)
            r.append({'id': cid, 'nome': nome, 'caminho': '~/' + rel, 'bytes': b,
                      'pulado': t('{0} rodando', aberto) if aberto else t('em uso por {0}', u) if u else None,
                      'nota': t('o uv decide o que não está em uso; pode liberar menos') if modo == 'uv' else None})
    for app_id, p in sparkles():
        b = tamanho(p)
        if b < 1024 * 1024:
            continue
        nome_app, exe = app_do_bundle(app_id)
        aberto = exe and rodando(r'/Contents/MacOS/' + re.escape(exe) + r'$', procs)
        u = None if aberto else usado_por([p], ab)
        r.append({'id': 'sparkle:' + app_id, 'nome': t('Atualização baixada · {0}', nome_app or app_id),
                  'caminho': '~/' + os.path.relpath(p, HOME), 'bytes': b,
                  'pulado': t('{0} aberto', nome_app) if aberto else t('em uso por {0}', u) if u else None})
    return r


def sparkles():
    base = os.path.join(HOME, 'Library/Caches')
    try:
        for e in os.scandir(base):
            p = os.path.join(e.path, SPARKLE)
            if e.is_dir(follow_symlinks=False) and os.path.isdir(p) and os.path.realpath(p) == p:
                yield e.name, p
    except OSError:
        return


def app_do_bundle(bundle_id):
    """Nome e executável do app dono de um bundle id (pra saber se está aberto). Sem app instalado: (None, None)."""
    rc, out, _ = sh(['mdfind', f"kMDItemCFBundleIdentifier == '{bundle_id}'"], timeout=10)
    for app in out.splitlines():
        try:
            info = plistlib.load(open(os.path.join(app, 'Contents/Info.plist'), 'rb'))
            return os.path.basename(app)[:-4], info.get('CFBundleExecutable')
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return None, None


def esvaziar(p):
    """Apaga o conteúdo de p. Link é desfeito sem seguir. Devolve (itens apagados, erros)."""
    n, erros = 0, []
    for e in list(os.scandir(p)):
        try:
            if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                os.unlink(e.path)
            else:
                shutil.rmtree(e.path)
            n += 1
        except OSError as x:
            erros.append(f'{e.name}: {x.strerror}')
    return n, erros


def registrar(linhas):
    # nome do Mac pelo LocalHostName (o hostname do DHCP vira "192.168…" e o log dizia "192")
    mac = sh(['scutil', '--get', 'LocalHostName'], timeout=5)[1].strip() or os.uname().nodename.split('.')[0]
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a') as f:
        for l in linhas:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} · {mac} · {l}\n")


def cache_limpar(ids):
    procs = processos()
    previa = {i['id']: i for i in itens_cache(procs)}
    r, log = [], []
    for cid in ids:
        item = previa.get(cid)
        if item is None:
            r.append({'id': cid, 'erro': t('não está na lista (ou sumiu)')})
            continue
        if item['pulado']:
            r.append({'id': cid, 'nome': item['nome'], 'pulado': item['pulado'], 'liberado': 0})
            log.append(f"cache · {item['nome']} ({item['caminho']}) · pulado: {item['pulado']}")
            continue
        if cid.startswith('sparkle:'):
            p, motivo = raiz_segura(item['caminho'][2:])
            modo = 'conteudo'
        else:
            _, _, rel, modo, _ = next(c for c in CACHES if c[0] == cid)
            p, motivo = raiz_segura(rel)
        if p is None:
            r.append({'id': cid, 'nome': item['nome'], 'pulado': motivo, 'liberado': 0})
            continue
        antes, erros, n = item['bytes'] if modo == 'velhos' else tamanho(p), [], 0
        if modo == 'uv':
            uv = shutil.which('uv', path=':'.join(PATH_EXTRA + ['/usr/bin']))
            if not uv:
                r.append({'id': cid, 'nome': item['nome'], 'pulado': t('uv não instalado'), 'liberado': 0})
                continue
            rc, out, err = sh([uv, 'cache', 'prune'], timeout=120)  # ponytail: nunca --force (apagaria por baixo de quem roda)
            if rc != 0:
                erros.append(t('o uv estava ocupado; tente de novo depois') if rc == 124 else
                             (err or out).strip().splitlines()[-1][:120] if (err or out).strip() else t('saída {0}', rc))
            depois = tamanho(p)
        elif modo == 'velhos':
            for v in velhos(p):
                if os.path.realpath(v).startswith(p + '/') and not protegido(v):
                    shutil.rmtree(v, ignore_errors=True)
                    n += 1
            depois = 0
            antes = item['bytes']
        else:
            n, erros = esvaziar(p)
            depois = tamanho(p)
        liberado = max(0, antes - depois)
        r.append({'id': cid, 'nome': item['nome'], 'liberado': liberado, 'erros': erros[:3]})
        log.append(f"cache · {item['nome']} ({item['caminho']}) · liberou {gb(liberado)}" + (f' · {len(erros)} erro(s)' if erros else ''))
    total = sum(x.get('liberado', 0) for x in r)
    registrar(log + [f'cache · total liberado {gb(total)}'])
    return {'itens': r, 'liberado': total}


def gb(b):
    return f'{b / 1e9:.1f} GB' if b >= 1e9 else f'{b / 1e6:.0f} MB' if b >= 1e6 or b == 0 else t('menos de 1 MB')


def lixeira():
    p = os.path.join(HOME, '.Trash')
    if not os.path.isdir(p) or os.path.realpath(p) != p:
        return None
    try:
        nomes = [e.name for e in os.scandir(p) if e.name != '.DS_Store']
    except OSError:
        return {'bytes': 0, 'itens': 0, 'semAcesso': True}
    return {'bytes': tamanho(p), 'itens': len(nomes)}


def lixeira_esvaziar(esperado):
    """Esvazia a Lixeira do usuário (só ~/.Trash; a das unidades externas fica). Não tem volta: a UI avisa antes, e só
    apaga se a Lixeira ainda tem os mesmos N itens que a pessoa viu na prévia (caiu coisa nova nela? confere de novo)."""
    p = os.path.join(HOME, '.Trash')
    if not os.path.isdir(p) or os.path.realpath(p) != p:
        return {'erro': t('Lixeira não encontrada')}
    nomes = [e.name for e in os.scandir(p) if e.name != '.DS_Store']
    if esperado is None or len(nomes) != esperado:
        return {'erro': t('a Lixeira mudou desde a prévia (agora {0} itens); confira de novo', len(nomes)), 'itens': len(nomes)}
    antes = tamanho(p)
    n, erros = esvaziar(p)
    depois = tamanho(p)
    registrar([f"lixeira · esvaziou {len(nomes)} item(ns), {gb(max(0, antes - depois))}: " + ', '.join(nomes[:40])
               + (f' (+{len(nomes) - 40})' if len(nomes) > 40 else '') + (f' · {len(erros)} erro(s)' if erros else '')])
    return {'liberado': max(0, antes - depois), 'itens': len(nomes), 'erros': erros[:3]}


def discos_externos():
    """Volumes em /Volumes que dá pra ejetar (externo, imagem de disco), com espaço livre."""
    r = []
    try:
        vols = sorted(os.listdir('/Volumes'))
    except OSError:
        return r
    for v in vols:
        p = os.path.join('/Volumes', v)
        if not os.path.ismount(p) or os.path.realpath(p) == '/':
            continue
        rc, out, _ = sh(['diskutil', 'info', '-plist', p], timeout=10)
        try:
            i = plistlib.loads(out.encode())
        except Exception:
            continue
        if i.get('Internal', True) and not i.get('Ejectable', False):
            continue
        st = os.statvfs(p)
        r.append({'nome': v, 'livre': st.f_bavail * st.f_frsize, 'total': st.f_blocks * st.f_frsize})
    return r


def ejetar(nome):
    if not nome or '/' in nome or nome.startswith('.'):
        return {'erro': t('nome de volume inválido')}
    if nome not in [d['nome'] for d in discos_externos()]:
        return {'erro': t('não é um disco externo montado')}
    rc, out, err = sh(['diskutil', 'eject', os.path.join('/Volumes', nome)], timeout=60)
    ok = rc == 0
    msg = (out + err).strip().splitlines()[-1] if (out + err).strip() else ''
    registrar([f"ejetar · {nome} · {'ok' if ok else 'falhou: ' + msg[:160]}"])
    return {'ok': ok, 'mensagem': msg[:200]}


def app_de(exe):
    """'/Applications/X.app/Contents/MacOS/X' → '/Applications/X.app': só o executável principal de app instalado no lugar
    de app (/Applications, /Applications/Utilities, ~/Applications). Fica de fora o que mora em outro lugar: XProtect e
    o resto do sistema, o Python das ferramentas, o host dos agentes do Orca, helpers dentro de outros apps."""
    pastas = ('/Applications/', '/Applications/Utilities/', os.path.join(HOME, 'Applications') + '/')
    m = re.match(r'^(/.+?\.app)/Contents/MacOS/[^/]+$', exe)
    return m.group(1) if m and os.path.dirname(m.group(1)) + '/' in pastas else None


def apps_reiniciaveis(procs):
    """Apps do usuário abertos que podem ser reiniciados (não os do sistema nem os que seguram a sessão), por CPU."""
    r = {}
    for pid, uid, cpu, exe, _ in procs:
        app = app_de(exe)
        if uid != os.getuid() or not app:
            continue
        nome = os.path.basename(app)[:-4]
        if nome in NAO_REINICIA or app.startswith('/System/'):
            continue
        if nome not in r or cpu > r[nome]['cpu']:
            r[nome] = {'nome': nome, 'pid': pid, 'app': app, 'cpu': cpu}
    return sorted(r.values(), key=lambda a: -a['cpu'])[:6]


def reiniciar(pid):
    """Fecha um app (TERM; se não fechar em 5 s, KILL) e abre de novo. Só app de usuário fora da lista NAO_REINICIA."""
    alvo = next((a for a in apps_reiniciaveis(processos()) if a['pid'] == pid), None)
    if alvo is None:
        return {'erro': t('esse processo não é um app que dá pra reiniciar (ou já fechou)')}
    try:
        os.kill(pid, 15)
        for _ in range(50):
            time.sleep(0.1)
            os.kill(pid, 0)
        os.kill(pid, 9)
        forcado = True
        time.sleep(0.5)
    except ProcessLookupError:
        forcado = False
    rc, _, err = sh(['open', '-a', alvo['app']], timeout=20)
    registrar([f"reiniciar · {alvo['nome']} (pid {pid}){' · forçado' if forcado else ''} · {'reaberto' if rc == 0 else 'não reabriu: ' + err[:120]}"])
    return {'ok': rc == 0, 'nome': alvo['nome'], 'forcado': forcado}


def velocidade(saida):
    """Do JSON do `networkQuality -c`: download e upload (bits/s) e responsividade (RPM, viagens de ida e volta por minuto
    com a rede ocupada: quanto maior, menos a chamada de vídeo engasga)."""
    try:
        j = json.loads(saida)
    except ValueError:
        return {'erro': t('o teste de velocidade não rodou')}
    if j.get('error_code'):
        return {'erro': t('o teste falhou ({0} {1})', j.get('error_domain', t('erro')), j['error_code'])}
    return {'down': j.get('dl_throughput'), 'up': j.get('ul_throughput'), 'rpm': j.get('responsiveness') or j.get('dl_responsiveness'),
            'interface': j.get('interface_name')}


def tamanhos():
    # ponytail: sem o tamanho do Downloads de propósito: ler ~/Downloads de dentro do app dispara o pedido de permissão do
    # macOS (Arquivos e Pastas) na cara de quem usa. A ação abre a pasta no Finder, que já tem acesso.
    return {'lixeira': lixeira(), 'discos': discos_externos(), 'apps': apps_reiniciaveis(processos())}


def local(acao, a):
    if acao == 'cache-previa':
        itens = itens_cache(processos())
        return {'itens': itens, 'total': sum(i['bytes'] for i in itens if not i['pulado'])}
    if acao == 'cache-limpar':
        return cache_limpar([i for i in (a.get('ids') or '').split(',') if i])
    if acao == 'tamanhos':
        return tamanhos()
    if acao == 'lixeira-esvaziar':
        return lixeira_esvaziar(int(a['itens']) if str(a.get('itens', '')).isdigit() else None)
    if acao == 'ejetar':
        return ejetar(a.get('volume'))
    if acao == 'reiniciar':
        return reiniciar(int(a.get('pid') or 0))
    if acao == 'velocidade':  # networkQuality nativo do macOS: consome banda, então só roda quando a pessoa clica
        rc, out, err = sh(['networkQuality', '-c', '-M', '30'], timeout=90)
        return velocidade(out)
    if acao == 'abrir':
        alvo = {'monitor': ['open', '-a', 'Activity Monitor'], 'downloads': ['open', os.path.join(HOME, 'Downloads')],
                'lixeira': ['open', os.path.join(HOME, '.Trash')]}.get(a.get('alvo'))
        if not alvo:
            return {'erro': t('alvo desconhecido')}
        rc, _, err = sh(alvo)
        return {'ok': rc == 0}
    return {'erro': t('ação desconhecida: {0}', acao)}


# --- controlador (o Mac do app): resolve a máquina e manda este mesmo arquivo pelo SSH ---

def controlador(acao, a):
    """Resolve a máquina como o coletor (nome na Tailscale, IP ou host do config) e manda este mesmo arquivo pelo SSH."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import coleta, comum
    m = next((x for x in coleta.CFG['maquinas'] if (x.get('host') or x.get('ts')) == a['maquina']), {'host': a['maquina'], 'nome': a['maquina']})
    no = coleta.resolver(a['maquina'], coleta.tailnet())
    if no.get('online') is False:
        return {'erro': t('máquina offline na Tailscale')}
    nome = m.get('nome') or a['maquina']
    if no['self']:
        r = local(acao, a)
    else:
        dest = coleta.destino(dict(no, usuario=m.get('usuario')))
        if acao == 'abrir' and a.get('alvo') == 'terminal':  # um Terminal neste Mac com SSH aberto naquele
            if not re.fullmatch(r'[\w.@:-]+', dest):
                return {'erro': t('host inválido')}
            arq = re.sub(r'[^\w.-]', '_', a['maquina'])
            p = os.path.join(coleta.CM, f'ssh-{arq}.command')
            open(p, 'w').write(f'#!/bin/zsh\nclear\nssh -t {dest}\n')
            os.chmod(p, 0o700)
            rc, _, _ = sh(['open', '-a', 'Terminal', p])
            return {'ok': rc == 0}
        args = [acao] + [x for k, v in a.items() if k in ('ids', 'volume', 'pid', 'alvo', 'itens') and v for x in (f'--{k}', str(v))]
        fonte = open(os.path.abspath(__file__)).read()
        tab = {k: v for k, v in comum._TAB.items() if k in fonte}  # lá não tem as tabelas: vão junto só as frases deste arquivo
        rc, out, err = sh([*coleta.SSH, dest, 'python3', '-', *args], timeout=900, entrada=f'_T = {tab!r}\n' + fonte)
        try:
            r = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {'erro': t('o Mac não respondeu') if rc else t('resposta inválida')}
        if acao in ('cache-limpar', 'lixeira-esvaziar', 'ejetar', 'reiniciar'):  # resumo também no log deste Mac
            registrar([f"{nome} (via SSH) · {acao} · " + json.dumps({k: r.get(k) for k in ('liberado', 'ok', 'nome', 'erro') if k in r},
                                                                    ensure_ascii=False)])
    r['maquina'] = nome
    return r


def main():
    argv = sys.argv[1:]
    if not argv:
        print(json.dumps({'erro': 'uso: acoes.py <acao> [--maquina X] …'}))
        return
    acao, a, i = argv[0], {}, 1
    while i < len(argv) - 1:
        if argv[i].startswith('--'):
            a[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    try:
        r = controlador(acao, a) if a.get('maquina') else local(acao, a)
    except Exception as e:  # nunca devolve traceback cru pra UI
        r = {'erro': f'{type(e).__name__}: {e}'[:200]}
    print(json.dumps(r, ensure_ascii=False))


if __name__ == '__main__':
    main()
