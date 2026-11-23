"""E-mail: quantos não lidos, quando chegou o último e o cabeçalho dele (de, assunto, hora). NUNCA o corpo.

Composio (padrão; o usuário traz a própria chave, guardada no Keychain como conta "composio"):
  - status das contas (ativa, expirada…): 1 chamada de gerenciamento pra todas, a cada 10 min (não é chamada de ferramenta)
  - não lidos: 1 chamada de ferramenta por conta a cada `intervalo` min (GMAIL_GET_LABEL / OUTLOOK_GET_MAIL_FOLDER)
  - último cabeçalho: só quando o app pede (a página de e-mail abriu) e só pelo PROXY com a lista de campos fechada
    (`fields=` no Gmail, `$select=` no Outlook): as ferramentas prontas do Composio devolvem 200 caracteres do corpo.
  No plano grátis (Hobby) do Composio: 10 mil execuções/mês fora de sessão e 1 mil chamadas de proxy; o app mostra a conta
  em Ajustes › E-mail. Chave de projeto (ak_…) vai em x-api-key; chave de usuário (uak_…, a do `composio login`) resolve
  a organização e o projeto "consumer" sozinha.
IMAP (opcional, de graça): senha de app no Keychain (conta "imap:<usuario>@<servidor>"); STATUS + BODY.PEEK só dos
  campos From/Subject/Date, com a caixa aberta só pra leitura."""
import email.header, email.utils, imaplib, json, os, re, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

from comum import CACHE, N_, data_curta, dur, segredo, t

BASE = 'https://backend.composio.dev'
ARQ = os.path.join(CACHE, 'email.json')  # o que já se sabe de cada conta: um reinício do coletor não gasta chamada à toa
E = {'status_em': 0, 'status': {}, 'contas': {}, 'pausa': 0, 'erro': None}
SESSAO = {}  # chave → cabeçalhos HTTP já resolvidos (org/projeto da chave de usuário)


def _ler_cache():
    try:
        d = json.load(open(ARQ))
        E.update(contas=d.get('contas', {}), status=d.get('status', {}), status_em=d.get('status_em', 0))
    except (OSError, ValueError):
        pass


def _gravar_cache():
    os.makedirs(CACHE, exist_ok=True)
    tmp = ARQ + '.tmp'
    json.dump({'contas': E['contas'], 'status': E['status'], 'status_em': E['status_em']}, open(tmp, 'w'))
    os.replace(tmp, ARQ)


class Falha(Exception):
    """A mensagem é a chave em português (N_): é comparada e gravada no cache assim; vira texto do idioma onde aparece (t)."""


def _req(metodo, caminho, cab, corpo=None, timeout=20):
    req = urllib.request.Request(BASE + caminho, method=metodo, data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers=dict(cab, **{'Accept': 'application/json', 'Content-Type': 'application/json',
                                                      'User-Agent': 'notch-status-app'}))
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read(4000)).get('error') or {}
        except ValueError:
            err = {}
        txt = err.get('message', '') if isinstance(err, dict) else str(err)
        if e.code in (401, 403):
            raise Falha(N_('chave do Composio recusada'))
        if e.code in (402, 429) or 'limit' in txt.lower():
            raise Falha(N_('limite do Composio'))
        raise Falha(f'Composio {e.code}')
    except (urllib.error.URLError, OSError, ValueError):
        raise Falha(N_('sem rede'))


def cabecalhos(chave):
    if chave in SESSAO:
        return SESSAO[chave]
    if chave.startswith('uak_'):
        h = {'x-user-api-key': chave}
        info = _req('GET', '/api/v3/auth/session/info', h)
        h['x-org-id'] = ((info.get('project') or {}).get('org') or {}).get('id') or info.get('org_id') or ''
        h['x-project-id'] = _req('POST', '/api/v3/org/consumer/project/resolve', h)['project_nano_id']
    else:
        h = {'x-api-key': chave}
    SESSAO[chave] = h
    return h


def contas_composio(chave):
    """Contas de e-mail (Gmail, Outlook) conectadas no Composio do usuário: id, app, apelido, status. Só lista."""
    r = _req('GET', '/api/v3.1/connected_accounts?toolkit_slugs=gmail,outlook&limit=200', cabecalhos(chave))
    return [{'id': x['id'], 'app': (x.get('toolkit') or {}).get('slug'), 'apelido': x.get('alias') or x.get('word_id') or x['id'],
             'status': x.get('status'), 'usuario': x.get('user_id')} for x in r.get('items', [])]


def _ferramenta(chave, slug, c, args):
    r = _req('POST', f'/api/v3.1/tools/execute/{slug}', cabecalhos(chave),
             {'connected_account_id': c['conta'], 'user_id': E['status'].get(c['conta'], {}).get('usuario'), 'arguments': args})
    if not r.get('successful', True):
        raise Falha(str(r.get('error') or N_('ferramenta falhou'))[:80])
    return r.get('data') or {}


def _proxy(chave, c, url):
    r = _req('POST', '/api/v3.1/tools/execute/proxy', cabecalhos(chave), {'endpoint': url, 'method': 'GET', 'connected_account_id': c['conta']})
    if int(r.get('status') or 200) >= 400:
        raise Falha(t('provedor {0}', r.get('status')))
    return r.get('data') or {}


def _achar(d, chave):
    """Valor de uma chave em qualquer nível do JSON (o formato da resposta muda de ferramenta pra ferramenta)."""
    if isinstance(d, dict):
        if chave in d:
            return d[chave]
        for v in d.values():
            x = _achar(v, chave)
            if x is not None:
                return x
    elif isinstance(d, list):
        for v in d:
            x = _achar(v, chave)
            if x is not None:
                return x
    return None


def contar(chave, c):
    """(não lidos, total) da caixa de entrada: 1 chamada de ferramenta."""
    if E['status'].get(c['conta'], {}).get('app') == 'outlook':
        d = _ferramenta(chave, 'OUTLOOK_GET_MAIL_FOLDER', c, {'mail_folder_id': 'inbox', 'select': ['unreadItemCount', 'totalItemCount']})
        return int(_achar(d, 'unreadItemCount') or 0), int(_achar(d, 'totalItemCount') or 0)
    d = _ferramenta(chave, 'GMAIL_GET_LABEL', c, {'id': 'INBOX'})
    return int(d.get('messagesUnread') or 0), int(d.get('messagesTotal') or 0)


def _de(v):
    nome, end = email.utils.parseaddr(v or '')
    return nome or end or '—'


def cabecalho(chave, c):
    """Último e-mail da caixa de entrada: {de, assunto, ts}. Só os campos pedidos voltam (nada do corpo)."""
    if E['status'].get(c['conta'], {}).get('app') == 'outlook':
        q = urllib.parse.urlencode({'$top': 1, '$select': 'from,subject,receivedDateTime', '$orderby': 'receivedDateTime desc'})
        m = ((_proxy(chave, c, 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?' + q) or {}).get('value') or [None])[0]
        if not m:
            return None
        de = ((m.get('from') or {}).get('emailAddress') or {})
        from comum import _iso
        return {'de': de.get('name') or de.get('address') or '—', 'assunto': m.get('subject') or t('(sem assunto)'),
                'ts': _iso(m.get('receivedDateTime')) or time.time()}
    g = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
    ids = (_proxy(chave, c, g + '?maxResults=1&labelIds=INBOX&fields=messages/id') or {}).get('messages') or []
    if not ids:
        return None
    m = _proxy(chave, c, f"{g}/{ids[0]['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
                         '&fields=internalDate,payload/headers')
    hs = {h['name'].lower(): h['value'] for h in (m.get('payload') or {}).get('headers') or []}
    return {'de': _de(hs.get('from')), 'assunto': hs.get('subject') or t('(sem assunto)'), 'ts': int(m.get('internalDate') or 0) / 1000}


def _decod(v):
    return ''.join(p.decode(cod or 'utf-8', 'replace') if isinstance(p, bytes) else p for p, cod in email.header.decode_header(v or ''))


def imap(c, com_cabecalho):
    """(não lidos, total, cabeçalho|None) por IMAP, com a caixa aberta só pra leitura e só os campos do cabeçalho."""
    senha = segredo(f"imap:{c['usuario']}@{c['servidor']}")
    if not senha:
        raise Falha(N_('sem senha (Ajustes › E-mail)'))
    try:
        M = imaplib.IMAP4_SSL(c['servidor'], int(c.get('porta') or 993), timeout=15)
    except (OSError, imaplib.IMAP4.error):
        raise Falha(N_('servidor IMAP não respondeu'))
    try:
        try:
            M.login(c['usuario'], senha)
        except imaplib.IMAP4.error:
            raise Falha(N_('usuário ou senha de app recusados'))
        st = M.status('INBOX', '(MESSAGES UNSEEN)')[1][0].decode()
        total, nao = int(re.search(r'MESSAGES (\d+)', st).group(1)), int(re.search(r'UNSEEN (\d+)', st).group(1))
        ult = None
        if com_cabecalho and total:
            M.select('INBOX', readonly=True)
            d = M.fetch(str(total), '(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')[1]
            bruto = next((x[1] for x in d if isinstance(x, tuple)), b'')
            msg = email.message_from_bytes(bruto)
            data = re.search(rb'INTERNALDATE "([^"]+)"', d[0][0] if isinstance(d[0], tuple) else b'')
            ts = time.mktime(imaplib.Internaldate2tuple(b'INTERNALDATE "' + data.group(1) + b'"')) if data else time.time()
            ult = {'de': _de(_decod(msg.get('From'))), 'assunto': _decod(msg.get('Subject')) or t('(sem assunto)'), 'ts': ts}
        return nao, total, ult
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _atualiza(c, chave, agora, intervalo, pedido):
    """Uma conta: conta os não lidos se venceu o intervalo; busca o cabeçalho se foi pedido (ou, no IMAP, se chegou e-mail)."""
    k = c['id']
    st = E['contas'].setdefault(k, {})
    try:
        if c.get('provedor') == 'imap':
            if agora - st.get('contado', 0) >= 300 or pedido:
                novo = st.get('total') is None
                nao, tot, _ = imap(c, False)
                quer = novo or tot != st.get('total') or (pedido and agora - st.get('cabecalho_em', 0) > 600)
                if quer:
                    nao, tot, ult = imap(c, True)
                    st.update(ultimo=ult, cabecalho_em=agora)
                if not novo and tot > (st.get('total') or 0):
                    st['chegou'] = agora
                st.update(naoLidos=nao, total=tot, contado=agora, erro=None)
            return
        if not chave:
            st['erro'] = N_('sem chave do Composio (Ajustes › E-mail)')
            return
        if E['status'].get(c['conta'], {}).get('status') not in (None, 'ACTIVE'):
            return  # conexão expirada/falhou: nem tenta (gastaria chamada à toa)
        if agora - st.get('contado', 0) >= intervalo * 60:
            nao, tot = contar(chave, c)
            if st.get('total') is not None and (tot > st['total'] or nao > (st.get('naoLidos') or 0)):
                st['chegou'] = agora  # chegou e-mail entre a verificação anterior e esta
            st.update(naoLidos=nao, total=tot, contado=agora, erro=None)
        if pedido and agora - st.get('cabecalho_em', 0) > 600 and st.get('total') != st.get('total_cab'):
            st.update(ultimo=cabecalho(chave, c), cabecalho_em=agora, total_cab=st.get('total'))
    except Falha as e:
        st['erro'] = str(e)
        if str(e) == 'limite do Composio':
            E['pausa'] = agora + 3600


def estado(cfg, agora=None, pedido=False):
    agora = agora or time.time()
    contas = cfg.get('contas') or []
    if not contas:
        return None
    if not E['contas'] and not E['status']:
        _ler_cache()
    comp = [c for c in contas if c.get('provedor', 'composio') == 'composio']
    if comp and 'chave' not in E:  # lida uma vez por processo: mudou nos Ajustes, o app reinicia o coletor
        E['chave'] = segredo('composio')
    chave = E.get('chave') if comp else None
    E['erro'] = None
    if comp and chave and agora >= E['pausa'] and agora - E['status_em'] >= 600:
        try:
            E['status'] = {x['id']: x for x in contas_composio(chave)}
            E['status_em'] = agora
        except Falha as e:
            E['erro'] = str(e)
            if str(e) == 'limite do Composio':
                E['pausa'] = agora + 3600
    antes = json.dumps(E['contas'], sort_keys=True)
    if agora >= E['pausa']:
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(lambda c: _atualiza(c, chave, agora, int(cfg.get('intervalo') or 60), pedido), contas))
    if json.dumps(E['contas'], sort_keys=True) != antes or agora - E['status_em'] < 1:
        _gravar_cache()
    saida = []
    for c in contas:
        st, info = E['contas'].get(c['id'], {}), E['status'].get(c.get('conta'), {})
        s = info.get('status')
        estado_c = 'expirada' if s in ('EXPIRED', 'INACTIVE') else 'falhou' if s in ('FAILED',) else 'erro' if st.get('erro') else \
            'ok' if st.get('contado') else 'verificando'
        ult = st.get('ultimo')
        erro = {'expirada': N_('conexão expirada: reconecte no Composio'), 'falhou': N_('conexão falhou no Composio')}.get(estado_c) or st.get('erro')
        saida.append({'id': c['id'], 'nome': c.get('nome') or info.get('apelido') or c.get('usuario') or t('e-mail'),
                      'endereco': c.get('endereco') or (c.get('usuario') if c.get('provedor') == 'imap' else ''),
                      'provedor': c.get('provedor', 'composio'), 'app': info.get('app') or ('imap' if c.get('provedor') == 'imap' else None),
                      'estado': estado_c, 'nivel': 1 if estado_c in ('expirada', 'falhou', 'erro') else 0,
                      'erro': t(erro) if erro else None,
                      'naoLidos': st.get('naoLidos'), 'chegou': dur(int(agora - st['chegou'])) if st.get('chegou') else None,
                      'verificado': dur(int(agora - st['contado'])) if st.get('contado') else None,
                      'ultimo': {'de': ult['de'], 'assunto': ult['assunto'], 'ha': dur(max(0, int(agora - ult['ts']))),
                                 'hora': data_curta(ult['ts'])} if ult else None,
                      'cabecalhoHa': dur(int(agora - st['cabecalho_em'])) if st.get('cabecalho_em') else None})
    ruins = [x for x in saida if x['nivel']]
    prob = t(E['erro']) if E['erro'] else (t('{0} contas com problema', len(ruins)) if len(ruins) > 1 else
                                           f"{ruins[0]['nome']}: {ruins[0]['erro']}" if ruins else None)
    if agora < E['pausa']:
        prob = t('limite do Composio: pausado até {0}', time.strftime('%H:%M', time.localtime(E['pausa'])))
    chegou = max((x for x in saida if x['chegou']), key=lambda x: E['contas'][x['id']]['chegou'], default=None)
    return {'contas': saida, 'naoLidos': sum(x['naoLidos'] or 0 for x in saida), 'nivel': 1 if prob else 0, 'problema': prob,
            'chegou': {'conta': chegou['nome'], 'ha': chegou['chegou']} if chegou else None,
            'intervalo': int(cfg.get('intervalo') or 60)}
