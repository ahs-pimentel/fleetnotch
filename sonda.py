#!/usr/bin/env python3
"""Sonda de um Mac. Roda local ou via `ssh host python3 - [--lento] < sonda.py` (nada fica instalado no remoto).
Sem sudo. Imprime um JSON. --lento inclui o que está conectado: monitores, USB, Thunderbolt, Bluetooth e áudio."""
import ctypes as C, ctypes.util, json, os, re, subprocess, sys, time


def sh(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ''


def sysctl(k):
    return sh(f'sysctl -n {k}').strip()


def cpu():
    # iostat: 2ª linha = média do último 1 s (a 1ª é desde o boot)
    lines = [l.split() for l in sh('iostat -n 0 -c 2 -w 1').splitlines() if re.match(r'\s*\d', l)]
    return 100 - int(lines[-1][2]) if lines else None


def gpu():
    m = re.search(r'"Device Utilization %"=(\d+)', sh('ioreg -r -d 1 -c IOAccelerator'))
    return int(m.group(1)) if m else None


def ram():
    out = sh('vm_stat')
    page = int(re.search(r'page size of (\d+)', out).group(1))
    v = {m.group(1): int(m.group(2)) for m in re.finditer(r'^(.+?):\s+(\d+)\.', out, re.M)}
    # ativa + wired + comprimida ≈ "Memória Usada" do Monitor de Atividade
    used = (v.get('Pages active', 0) + v.get('Pages wired down', 0) + v.get('Pages occupied by compressor', 0)) * page
    return {'usado': used, 'total': int(sysctl('hw.memsize'))}


def swap():
    m = re.search(r'total = ([\d.]+)M\s+used = ([\d.]+)M', sysctl('vm.swapusage'))
    return {'usado': float(m.group(2)) * 2**20, 'total': float(m.group(1)) * 2**20} if m else None


def temp():
    """Maior temperatura dos sensores de die da CPU (IOHID, sem sudo). None se não der."""
    try:
        cf, io, V = C.CDLL(ctypes.util.find_library('CoreFoundation')), C.CDLL(ctypes.util.find_library('IOKit')), C.c_void_p
        for lib, f, r, a in [(io, 'IOHIDEventSystemClientCreate', V, [V]), (io, 'IOHIDEventSystemClientSetMatching', C.c_int, [V, V]),
                             (io, 'IOHIDEventSystemClientCopyServices', V, [V]),
                             (io, 'IOHIDServiceClientCopyEvent', V, [V, C.c_int64, C.c_int32, C.c_int64]),
                             (io, 'IOHIDEventGetFloatValue', C.c_double, [V, C.c_int32]), (io, 'IOHIDServiceClientCopyProperty', V, [V, V]),
                             (cf, 'CFArrayGetCount', C.c_long, [V]), (cf, 'CFArrayGetValueAtIndex', V, [V, C.c_long]),
                             (cf, 'CFStringCreateWithCString', V, [V, C.c_char_p, C.c_uint32]), (cf, 'CFNumberCreate', V, [V, C.c_int, V]),
                             (cf, 'CFDictionaryCreate', V, [V, V, V, C.c_long, V, V]),
                             (cf, 'CFStringGetCString', C.c_bool, [V, C.c_char_p, C.c_long, C.c_uint32])]:
            getattr(lib, f).restype, getattr(lib, f).argtypes = r, a
        s = lambda x: cf.CFStringCreateWithCString(None, x.encode(), 0x08000100)

        def n(x):
            v = C.c_int(x)
            return cf.CFNumberCreate(None, 3, C.byref(v))
        cb = lambda name: C.addressof(C.c_void_p.in_dll(cf, name))
        match = cf.CFDictionaryCreate(None, (V * 2)(s('PrimaryUsagePage'), s('PrimaryUsage')), (V * 2)(n(0xff00), n(5)), 2,
                                      cb('kCFTypeDictionaryKeyCallBacks'), cb('kCFTypeDictionaryValueCallBacks'))
        client = io.IOHIDEventSystemClientCreate(None)
        io.IOHIDEventSystemClientSetMatching(client, match)
        svcs, buf, best = io.IOHIDEventSystemClientCopyServices(client), C.create_string_buffer(128), None
        for i in range(cf.CFArrayGetCount(svcs) if svcs else 0):
            svc = cf.CFArrayGetValueAtIndex(svcs, i)
            p = io.IOHIDServiceClientCopyProperty(svc, s('Product'))
            if not (p and cf.CFStringGetCString(p, buf, 128, 0x08000100) and b'tdie' in buf.value):
                continue
            ev = io.IOHIDServiceClientCopyEvent(svc, 15, 0, 0)  # 15 = kIOHIDEventTypeTemperature
            t = io.IOHIDEventGetFloatValue(ev, 15 << 16) if ev else 0
            if 0 < t < 150:
                best = max(best or 0, t)
        return round(best, 1) if best else None
    except Exception:
        return None


def discos():
    res, vistos = [], set()
    for line in sh('df -k').splitlines()[1:]:
        f = line.split(None, 8)
        if len(f) < 9 or not f[0].startswith('/dev/disk'):
            continue
        mnt = f[8]
        if mnt != '/System/Volumes/Data' and not mnt.startswith('/Volumes/'):
            continue
        total, livre = int(f[1]) * 1024, int(f[3]) * 1024
        base = re.match(r'/dev/disk\d+', f[0]).group()
        if total < 8 * 1024**3 or base in vistos:  # ignora DMG/pendrive minúsculo e volume irmão (ex.: Recovery) do mesmo contêiner
            continue
        vistos.add(base)
        res.append({'nome': 'Interno' if mnt == '/System/Volumes/Data' else mnt[9:], 'livre': livre, 'total': total})
    return res


def rede():
    m = re.search(r'interface: (\S+)', sh('route -n get default'))
    if not m:
        return None
    dev = m.group(1)
    ports = dict(re.findall(r'Hardware Port: (.+)\nDevice: (\S+)', sh('networksetup -listallhardwareports')))
    tipo = {v: k for k, v in ports.items()}.get(dev, dev)
    r = {'tipo': tipo, 'if': dev, 'ip': sh(f'ipconfig getifaddr {dev}').strip()}
    if tipo == 'Wi-Fi':
        r['wifi'] = wifi()
    else:
        r['link'] = link(sh(f'ifconfig {dev}'))
    return r


def link(ifconfig):
    """Velocidade do cabo em Mb/s, da linha "media: autoselect (1000baseT <full-duplex>)" (10Gbase-T = 10000)."""
    m = re.search(r'media: .*?\((\d+)(G?)base', ifconfig, re.I)
    return int(m.group(1)) * (1000 if m.group(2) else 1) if m else None


def contadores(dev):
    """(tempo, bytes recebidos, bytes enviados) da interface: a linha <Link#> do netstat -ibn (uma chamada, ~5 ms)."""
    for l in sh(f'netstat -ibn -I {dev}').splitlines():
        p = l.split()
        if len(p) >= 10 and p[2].startswith('<Link#') and p[-5].isdigit():
            return time.time(), int(p[-5]), int(p[-2])
    return None


def wifi():
    """Wi-Fi pelo CoreWLAN via ctypes (~15 ms, sem sudo, funciona pelo SSH): sinal e ruído (dBm), canal, banda, largura,
    padrão e velocidade do link. O nome da rede o macOS só entrega com permissão de Localização: aqui não vem."""
    try:
        objc = C.cdll.LoadLibrary(ctypes.util.find_library('objc'))
        C.cdll.LoadLibrary('/System/Library/Frameworks/CoreWLAN.framework/CoreWLAN')
        objc.objc_getClass.restype = C.c_void_p
        objc.sel_registerName.restype = C.c_void_p

        def msg(o, sel, tipo=C.c_void_p):
            return C.CFUNCTYPE(tipo, C.c_void_p, C.c_void_p)(('objc_msgSend', objc))(o, objc.sel_registerName(sel.encode()))
        i = msg(msg(objc.objc_getClass(b'CWWiFiClient'), 'sharedWiFiClient'), 'interface')
        if not i:
            return None
        ch = msg(i, 'wlanChannel')
        return {'rssi': msg(i, 'rssiValue', C.c_long) or None, 'ruido': msg(i, 'noiseMeasurement', C.c_long) or None,
                'taxa': round(msg(i, 'transmitRate', C.c_double)) or None,
                'canal': msg(ch, 'channelNumber', C.c_long) if ch else None,
                'banda': {1: '2,4 GHz', 2: '5 GHz', 3: '6 GHz'}.get(msg(ch, 'channelBand', C.c_long)) if ch else None,
                'largura': {1: 20, 2: 40, 3: 80, 4: 160, 5: 320}.get(msg(ch, 'channelWidth', C.c_long)) if ch else None,
                'padrao': {1: '802.11a', 2: '802.11b', 3: '802.11g', 4: 'Wi-Fi 4', 5: 'Wi-Fi 5', 6: 'Wi-Fi 6', 7: 'Wi-Fi 7'}.get(msg(i, 'activePHYMode', C.c_long))}
    except (OSError, AttributeError, TypeError):
        return None


def termico():
    return 'normal' if 'No thermal warning level' in sh('pmset -g therm') else 'limitando'


def top3():
    rows = []
    for line in sh('ps -Aceo pid=,pcpu=,comm= -r').splitlines()[:4]:
        pid, pct, nome = line.strip().split(None, 2)
        if int(pid) != os.getpid():  # a própria sonda não conta
            rows.append([nome, float(pct)])
    return rows[:3]


def curto(nome):
    """'Alex’s Magic Keyboard #2' → 'Magic Keyboard'; 'AirPods de Sam' → 'AirPods'."""
    nome = re.sub(r"^.+?[’']s ", '', nome)
    return re.sub(r'\s+(#\d+|de \S+.*)$', '', nome).strip()


def conectados():
    """Tudo que está plugado ou pareado e ligado, sem sudo: monitores, USB (hubs inclusos), Thunderbolt/USB4,
    Bluetooth com bateria e a saída de áudio. Uma chamada só do system_profiler (~0,3 s de CPU). Sem -detailLevel mini:
    o mini tira o endereço do Bluetooth, que é o que casa a bateria do ioreg (trackpad/mouse)."""
    d = json.loads(sh('system_profiler SPDisplaysDataType SPUSBHostDataType SPUSBDataType SPThunderboltDataType '
                      'SPBluetoothDataType SPAudioDataType -json', 20) or '{}')
    mon = []
    for g in d.get('SPDisplaysDataType', []):
        for x in g.get('spdisplays_ndrvs', []):
            r = re.match(r'(\d+) x (\d+)(?: @ ([\d.]+)Hz)?', x.get('_spdisplays_resolution') or x.get('spdisplays_resolution') or '')
            mon.append({'nome': 'Monitor' if x.get('_name', '').startswith('spdisplays') else x.get('_name', 'Monitor'),
                        'res': f'{r.group(1)}×{r.group(2)}' if r else None, 'hz': round(float(r.group(3))) if r and r.group(3) else None,
                        'principal': x.get('spdisplays_main') == 'spdisplays_yes',
                        'interno': x.get('spdisplays_connection_type') == 'spdisplays_internal'})

    def usb(itens):  # só o que é de fora (hub interno do próprio Mac e barramento não contam)
        for i in itens or []:
            if '_name' in i and i.get('USBKeyHardwareType') != 'Built-in' and not re.search(r'(Bus|bus_\d+)$', i['_name']):
                v = re.search(r'([\d.]+ [GM]b/s)', i.get('USBDeviceKeyLinkSpeed') or i.get('speed') or '')
                yield {'nome': i['_name'], 'marca': i.get('USBDeviceKeyVendorName') or i.get('manufacturer'),
                       'vel': v.group(1) if v else None, 'hub': bool(re.search(r'hub', i['_name'], re.I))}
            yield from usb(i.get('_items'))

    def tb(itens, raiz=True):  # o 1º nível é o próprio Mac (cada barramento); o que pendura nele é dock/monitor/disco
        for i in itens or []:
            if not raiz:
                yield {'nome': i.get('device_name_key') or i.get('_name'), 'marca': i.get('vendor_name_key')}
            yield from tb(i.get('_items'), False)

    bat = {}  # bateria de teclado/mouse/trackpad da Apple pelo ioreg (o system_profiler nem sempre traz)
    for bloco in sh('ioreg -r -k BatteryPercent -l').split('+-o'):
        a, p = re.search(r'"DeviceAddress" = "([^"]+)"', bloco), re.search(r'"BatteryPercent" = (\d+)', bloco)
        if a and p:
            bat[a.group(1).lower().replace('-', ':')] = int(p.group(1))
    bt = []
    for c in d.get('SPBluetoothDataType', []):
        for dev in c.get('device_connected', []):
            for nome, x in dev.items():
                niv = [int(v.rstrip('%')) for k, v in x.items() if k.startswith('device_batteryLevel') and k != 'device_batteryLevelCase']
                b = min(niv) if niv else bat.get((x.get('device_address') or '').lower().replace('-', ':'))
                tipo = (x.get('device_minorType') or '').lower()
                bt.append({'nome': curto(nome), 'bateria': b,
                           'tipo': next((t for k, t in (('keyboard', 'teclado'), ('trackpad', 'trackpad'), ('mouse', 'mouse'),
                                                         ('head', 'fones'), ('speaker', 'caixa')) if k in tipo), 'outro')})
    saida = next(({'nome': x.get('_name'), 'via': (x.get('coreaudio_device_transport') or '').replace('coreaudio_device_type_', '')}
                  for a in d.get('SPAudioDataType', []) for x in a.get('_items', [])
                  if x.get('coreaudio_default_audio_output_device') == 'spaudio_yes'), None)
    return {'monitores': mon, 'usb': list(usb(d.get('SPUSBHostDataType') or d.get('SPUSBDataType'))),
            'tb': list(tb(d.get('SPThunderboltDataType'))), 'bluetooth': bt, 'audio': saida}


def main():
    boot = int(re.search(r'sec = (\d+)', sysctl('kern.boottime')).group(1))
    r = rede()
    antes = contadores(r['if']) if r else None  # tráfego: os contadores antes e depois da sonda (que leva ~1 s, o iostat)
    out = {
        'modelo': sysctl('hw.model'),
        'macos': sh('sw_vers -productVersion').strip(),
        'uptime': int(time.time()) - boot,
        'carga': [float(x) for x in sysctl('vm.loadavg').strip('{} ').split()[:3]],
        'cpu': cpu(), 'gpu': gpu(), 'ram': ram(), 'swap': swap(),
        # kern.memorystatus_vm_pressure_level: 1 normal, 2 atenção, 4 crítica
        'pressao': {'1': 'normal', '2': 'atenção', '4': 'crítica'}.get(sysctl('kern.memorystatus_vm_pressure_level'), '?'),
        'temp': temp(), 'termico': termico(), 'top': top3(), 'discos': discos(), 'rede': r,
    }
    depois = contadores(r['if']) if antes else None
    if depois and depois[0] > antes[0]:  # bits por segundo, como o plano de internet e o teste de velocidade
        r['down'], r['up'] = (round(8 * max(0, depois[k] - antes[k]) / (depois[0] - antes[0])) for k in (1, 2))
    if '--lento' in sys.argv:
        out['plugados'] = conectados()
    print(json.dumps(out))


if __name__ == '__main__':
    main()
