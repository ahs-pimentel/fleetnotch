"""O que o coletor, os agentes, o e-mail e os ajustes usam em comum: config, comando com tempo-limite, leitura
incremental de log, segredo do Keychain e textos curtos ("há 3 min")."""
import json, os, re, subprocess, time

APP = os.environ.get('FROTA_APP') or 'FleetNotch'  # nome do app: pasta em Application Support, serviço no Keychain, cache
CONFIG = os.environ.get('FROTA_CONFIG') or os.path.expanduser(f'~/Library/Application Support/{APP}/config.json')
CACHE = os.path.expanduser(f'~/Library/Caches/{APP}')

IDIOMA = os.environ.get('FROTA_LANG') or 'pt'  # o app passa o idioma da interface; sem app (terminal), português


def _tabela(c):
    try:
        return json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'i18n', c + '.json')))
    except (OSError, ValueError):
        return {}


_TAB = {} if IDIOMA == 'pt' else _tabela(IDIOMA)


def t(s, *a):
    """Tradução: a chave é o texto em português; {0}, {1}… são os argumentos. "contexto|texto" desambigua palavra curta
    (em português aparece o que vem depois da barra). Tabelas em i18n/<código>.json, as mesmas do app. See i18n/README.md."""
    r = _TAB.get(s) or (s.split('|', 1)[1] if '|' in s else s)
    for i, v in enumerate(a):
        r = r.replace('{%d}' % i, str(v))
    return r


def decimal(s):
    """Separador decimal do idioma: ponto em inglês, vírgula em português, espanhol e francês ('4.1' → '4,1')."""
    return s if IDIOMA == 'en' else s.replace('.', ',')


def data_curta(ts):
    """Dia e mês na ordem do idioma: 23/11 (pt, es, fr) ou 11/23 (en), com a hora."""
    return time.strftime('%m/%d %H:%M' if IDIOMA == 'en' else '%d/%m %H:%M', time.localtime(ts))


def N_(s):
    """Marca um texto pra tradução sem traduzir agora (valor que é traduzido depois com t(x))."""
    return s


MODULOS = {'macs': True, 'servidores': True, 'email': True, 'ia': True, 'agentes': True, 'sincronizacao': True, 'acoes': True,
           'orca': True, 'pergunta': True}
LIMITES = {'cpu': [60, 85], 'gpu': [60, 85], 'ram': [80, 92], 'temp': [85, 95], 'disco': [10, 5], 'ia': [80, 95]}


def carregar(caminho=None):
    """config.json do usuário (sem segredo nenhum). Sem arquivo = config vazia: o app abre o assistente."""
    try:
        c = json.load(open(caminho or CONFIG))
    except (OSError, ValueError):
        c = {}
    c.setdefault('intervalo', 20)
    c.setdefault('lento', 60)
    c['modulos'] = dict(MODULOS, **(c.get('modulos') or {}))
    ap = c.setdefault('aparencia', {})
    ap['limites'] = dict(LIMITES, **(ap.get('limites') or {}))
    for k in ('maquinas', 'servidores', 'ia', 'agentes'):
        c[k] = [x for x in c.get(k) or [] if x.get('visivel', True)]
    c['email'] = dict({'intervalo': 60, 'contas': []}, **(c.get('email') or {}))
    c['email']['contas'] = [x for x in c['email']['contas'] if x.get('visivel', True)]
    return c


def run(cmd, stdin=None, timeout=15):
    try:
        # TERM: sem ele o binário do Tailscale.app acha que é pra abrir a GUI
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, TERM='xterm-256color'))
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'
    except OSError as e:
        return 127, '', type(e).__name__


def segredo(conta):
    """Segredo do Keychain (serviço = nome do app). Sempre pelo /usr/bin/security: assim nenhum rebuild do app pede
    permissão de novo. Nunca vai pra log, tela ou arquivo."""
    rc, out, _ = run(['/usr/bin/security', 'find-generic-password', '-s', APP, '-a', conta, '-w'], timeout=5)
    return out.rstrip('\n') if rc == 0 and out.strip() else None


def pct(a, b):
    return round(100 * a / b) if b else 0


def dur(s):
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return t('{0}d {1}h', d, h) if d else t('{0}h {1}min', h, m) if h else t('{0}min', m)


def quando(ts, agora):
    return None if not ts else {'hora': time.strftime('%H:%M', time.localtime(ts)), 'ha': dur(max(0, int(agora - ts)))}


def _json(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def _iso(s):
    """ISO ('2026-11-22T05:07:47.162566+00:00' ou '-03:00') → epoch; None se não der."""
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError, AttributeError):
        return None


LIDO = {}   # log → até onde já foi lido: cada ciclo lê só o que entrou depois (reler 400 KB a cada 20 s custava CPU)
ACHADO = {}  # log → último evento achado nele (persiste entre ciclos)
JSONS = {}  # arquivo → (mtime, conteúdo): JSON grande só é relido quando muda


def _novas(p, n=400_000):
    """Linhas inteiras que entraram no log desde a última leitura (1ª vez: o rabo de n bytes; girou/truncou: relê o rabo)."""
    try:
        tam = os.path.getsize(p)
        ini = LIDO.get(p, -1)
        if ini < 0 or tam < ini:
            ini = max(0, tam - n)
        with open(p, 'rb') as f:
            f.seek(ini)
            b = f.read(tam - ini)
        b = b[:b.rfind(b'\n') + 1]  # linha pela metade fica pro próximo ciclo
        LIDO[p] = ini + len(b)
        return b.decode(errors='ignore').splitlines()
    except OSError:
        return []


def _json_m(p):
    try:
        m = os.path.getmtime(p)
    except OSError:
        return None
    if p not in JSONS or JSONS[p][0] != m:
        JSONS[p] = (m, _json(p))
    return JSONS[p][1]


def _limpa(t, n=72):
    """Resumo de linha de log sem dado sensível: some caminho, id longo, número de telefone/chat, e-mail."""
    t = re.sub(r'/Users/\S+', '…', t)
    t = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '…', t)
    t = re.sub(r'[A-Za-z0-9_\-]{24,}', '…', t)
    t = re.sub(r'\b\d{6,}\b(@\S+)?', '…', t)
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


def _rabo_curto(p, n=8000):
    try:
        with open(p, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode(errors='ignore').splitlines()
    except (OSError, TypeError):
        return []


LCTL = {'quando': 0, 'saida': ''}


def _launchctl():
    """`launchctl list` uma vez por ciclo (agentes e backups leem do mesmo)."""
    if time.time() - LCTL['quando'] > 5:
        LCTL['saida'], LCTL['quando'] = run(['launchctl', 'list'], timeout=5)[1], time.time()
    return LCTL['saida']


def servicos(prefixo):
    """LaunchAgents cujo rótulo começa com o prefixo (ou é igual a ele): [{nome, rotulo, pid, saida}]."""
    r = []
    for l in _launchctl().splitlines():
        p = l.split('\t')
        if len(p) == 3 and prefixo and (p[2] == prefixo or p[2].startswith(prefixo.rstrip('.') + '.')):
            r.append({'nome': p[2][len(prefixo):].lstrip('.') or p[2], 'rotulo': p[2], 'pid': p[0] != '-',
                      'saida': int(p[1]) if p[1].lstrip('-').isdigit() else None})
    return r


def _aspas(v):
    return '"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"'


def guardar_segredo(conta, valor):
    """Grava (ou troca) o segredo no Keychain. O valor vai pelo stdin do `security -i`, nunca pela linha de comando."""
    valor = valor.strip().replace('\n', '')
    cmd = f"add-generic-password -U -s {_aspas(APP)} -a {_aspas(conta)} -l {_aspas(f'{APP} ({conta})')} -w {_aspas(valor)}\n"
    rc, _, err = run(['/usr/bin/security', '-i'], stdin=cmd, timeout=10)
    return rc == 0 and 'error' not in err.lower()


def apagar_segredo(conta):
    return run(['/usr/bin/security', 'delete-generic-password', '-s', APP, '-a', conta], timeout=5)[0] == 0
