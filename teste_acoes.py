"""Teste de segurança das ações num HOME FALSO: python3 teste_acoes.py
Só o que está na lista fixa some; dado, projeto, .hermes, .claude, alvo de link e versão nova ficam."""
import json, os, subprocess, sys, tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
h = tempfile.mkdtemp(prefix='frota-acoes-')
MB = b'\0' * (1024 * 1024)


def arq(rel, mb=2):
    p = os.path.join(h, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(MB * mb)
    return p


# caches de verdade (falsos)
arq('.npm/_cacache/content-v2/a'); arq('.npm/_cacache/index-v5/b')
arq('Library/Caches/Homebrew/downloads/x.tar.gz')
arq('Library/Caches/Google/Chrome/Default/Cache/data_1')
arq('.cache/puppeteer/chrome/mac_arm-1/chrome'); os.utime(os.path.join(h, '.cache/puppeteer/chrome/mac_arm-1'), (1, 1))
arq('.cache/puppeteer/chrome/mac_arm-2/chrome')
arq('Library/Caches/com.exemplo.semapp/org.sparkle-project.Sparkle/update.zip')
arq('.Trash/lixo.txt')
# o que NUNCA pode sumir
sentinelas = [arq('Documents/importante.txt', 1), arq('Projects/app/main.py', 1), arq('.hermes/state.db', 1),
              arq('.claude/settings.json', 1), arq('Downloads/nota.pdf', 1)]
# armadilhas: link dentro do cache apontando pra dado; raiz de cache que é link pra Documents
os.symlink(os.path.join(h, 'Documents/importante.txt'), os.path.join(h, '.npm/_cacache/link-pro-documento'))
os.makedirs(os.path.join(h, 'Library/Caches'), exist_ok=True)
os.symlink(os.path.join(h, 'Documents'), os.path.join(h, 'Library/Caches/pip'))
os.symlink(os.path.join(h, 'Projects'), os.path.join(h, '.cache/puppeteer/chrome-link'))


def acao(*a):
    p = subprocess.run([sys.executable, os.path.join(AQUI, 'acoes.py'), *a], capture_output=True, text=True,
                       env=dict(os.environ, FROTA_HOME=h, FROTA_TESTE_PROCS='nenhum'))
    return json.loads(p.stdout)


previa = acao('cache-previa')
ids = {i['id']: i for i in previa['itens']}
assert set(ids) >= {'npm', 'brew', 'chrome', 'puppeteer', 'sparkle:com.exemplo.semapp'}, ids
assert 'pip' not in ids, 'raiz de cache que é link não pode aparecer'
assert ids['puppeteer']['bytes'] >= 2 * 1024 * 1024 and ids['npm']['bytes'] >= 4 * 1024 * 1024
assert previa['total'] == sum(i['bytes'] for i in previa['itens'] if not i['pulado'])
print('ok prévia', {k: round(v['bytes'] / 1e6, 1) for k, v in ids.items()})

r = acao('cache-limpar', '--ids', ','.join(ids) + ',documents,../Documents')
assert any(i.get('erro') for i in r['itens'] if i['id'] == 'documents'), 'id fora da lista tem que ser recusado'
assert r['liberado'] >= 8 * 1024 * 1024, r
for s in sentinelas:
    assert os.path.exists(s), f'APAGOU O QUE NÃO PODIA: {s}'
assert os.path.isdir(os.path.join(h, 'Documents')) and os.path.islink(os.path.join(h, 'Library/Caches/pip')), 'link-raiz não pode ser seguido'
assert os.listdir(os.path.join(h, '.npm/_cacache')) == [], 'o cache do npm tinha que estar vazio'
assert os.path.exists(os.path.join(h, 'Documents/importante.txt')), 'link dentro do cache: some o link, nunca o alvo'
assert os.listdir(os.path.join(h, 'Library/Caches/Homebrew')) == [] and os.listdir(os.path.join(h, 'Library/Caches/Google/Chrome')) == []
assert not os.path.exists(os.path.join(h, '.cache/puppeteer/chrome/mac_arm-1')), 'versão velha do headless tinha que sair'
assert os.path.exists(os.path.join(h, '.cache/puppeteer/chrome/mac_arm-2/chrome')), 'a versão mais nova fica'
assert os.path.exists(os.path.join(h, 'Projects/app/main.py')) and os.path.islink(os.path.join(h, '.cache/puppeteer/chrome-link'))
assert os.listdir(os.path.join(h, 'Library/Caches/com.exemplo.semapp/org.sparkle-project.Sparkle')) == []
log = open(os.path.join(h, 'Library/Logs/fleetnotch/acoes.log')).read()
assert 'npm' in log and 'total liberado' in log, log
print('ok limpeza', round(r['liberado'] / 1e6, 1), 'MB; sentinelas e alvos de link intactos')

t = acao('tamanhos')
assert t['lixeira']['itens'] == 1 and 'downloads' not in t, t  # Downloads nunca é lido (pedido de permissão do macOS)
assert 'erro' in acao('lixeira-esvaziar') and 'erro' in acao('lixeira-esvaziar', '--itens', '2'), 'sem a contagem da prévia não esvazia'
assert os.listdir(os.path.join(h, '.Trash')) == ['lixo.txt']
lx = acao('lixeira-esvaziar', '--itens', '1')
assert lx['itens'] == 1 and os.listdir(os.path.join(h, '.Trash')) == [] and 'lixo.txt' in open(os.path.join(h, 'Library/Logs/fleetnotch/acoes.log')).read()
for s in sentinelas:
    assert os.path.exists(s), f'APAGOU O QUE NÃO PODIA: {s}'
print('ok lixeira')

assert 'erro' in acao('ejetar', '--volume', '../..') and 'erro' in acao('ejetar', '--volume', 'NaoExiste')
assert 'erro' in acao('reiniciar', '--pid', '1')  # launchd não é app de usuário
print('ok recusas (ejetar/reiniciar fora do permitido)')

# app aberto: o cache dele é pulado (troca a lista de processos por uma com o Chrome aberto)
sys.path.insert(0, AQUI)
os.environ['FROTA_HOME'] = h
import acoes  # noqa: E402
arq('Library/Caches/Google/Chrome/Default/Cache/data_2')
falsos = [(10, os.getuid(), 1.0, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
          (11, os.getuid(), 0.0, 'node', 'npm exec @x/mcp-server')]  # npm exec (servidor MCP) NÃO trava o cache do npm
acoes.processos = lambda: falsos
it = {i['id']: i for i in acoes.itens_cache(falsos)}
assert it['chrome']['pulado'] == 'Google Chrome rodando', it['chrome']
r = acoes.cache_limpar(['chrome'])
assert r['liberado'] == 0 and os.path.exists(os.path.join(h, 'Library/Caches/Google/Chrome/Default/Cache/data_2'))
assert acoes.rodando(acoes.CACHES[0][4], falsos) is None, 'npm exec não é instalação'
assert acoes.rodando(acoes.CACHES[0][4], [(12, 0, 0, 'node', 'node /opt/homebrew/bin/npm install')]) == 'node'
# cache com arquivo aberto por alguém (servidor MCP via uvx, script com uv run) é pulado, mesmo sem trava
arq('.npm/_cacache/content-v2/c')
ab = {os.path.join(os.path.realpath(h), '.npm/_cacache/content-v2/c'): 'faces.py'}  # lsof devolve o caminho real
it = {i['id']: i for i in acoes.itens_cache([], ab)}
assert it['npm']['pulado'] == 'em uso por faces.py', it['npm']
acoes.abertos = lambda procs: ab
assert acoes.cache_limpar(['npm'])['liberado'] == 0 and os.path.exists(os.path.join(h, '.npm/_cacache/content-v2/c'))
print('ok app aberto e cache em uso são pulados')

# só app instalado no lugar de app pode ser reiniciado; sem lsof, a prévia pula tudo (falha fechada)
assert acoes.app_de('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') == '/Applications/Google Chrome.app'
assert acoes.app_de('/Applications/Utilities/Activity Monitor.app/Contents/MacOS/Activity Monitor')
for exe in ('/Library/Apple/System/Library/CoreServices/XProtect.app/Contents/MacOS/XProtect',
            acoes.HOME + '/Library/Application Support/orca/daemon/Orca Agents.app/Contents/MacOS/Orca Agents',
            '/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python',
            '/Applications/X.app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper'):
    assert acoes.app_de(exe) is None, exe
import importlib
importlib.reload(acoes)  # desfaz os remendos de cima
real_sh = acoes.sh
acoes.sh = lambda cmd, timeout=30, entrada=None: (1, '', '') if cmd[0] == 'lsof' else real_sh(cmd, timeout, entrada)
assert acoes.abertos([]) is None
it = acoes.itens_cache([])
assert it and all(i['pulado'] for i in it), it
print('ok só apps de verdade; sem lsof não apaga nada')
