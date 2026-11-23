"""Detector de glitch quadro a quadro (streaming, aguenta 10 min a 60 fps).
Uso: python3 glitch.py sessao.mp4 [--log sessao.log] [--x0 480]
Mede em CADA quadro: (1) o notch está inteiro? (miolo preto entre os indicadores); (2) piscada: um quadro que difere dos
dois vizinhos enquanto os vizinhos são iguais entre si; (3) salto: mudança grande num quadro só, sem quadros de
animação em volta (algo que trocou sem interpolar). Gera folhas de contato dos suspeitos pra olhar."""
import json, subprocess, sys
import numpy as np
from PIL import Image

arq = sys.argv[1]
args = sys.argv[2:]
x0 = int(args[args.index('--x0') + 1]) if '--x0' in args else 480
info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=width,height',
                                  '-of', 'json', arq], capture_output=True, text=True).stdout)['streams'][0]
W, H = info['width'], info['height']
ts = [float(f['best_effort_timestamp_time']) for f in json.loads(subprocess.run(
    ['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time',
     '-of', 'json', arq], capture_output=True, text=True).stdout)['frames']]
p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', arq, '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                     stdout=subprocess.PIPE)
cx = 960 - x0  # centro do notch na região
miolo = (slice(4, 26), slice(cx - 18, cx + 20))  # entre a setinha de sincronização (esquerda) e os olhos/anéis (direita)
abaixo = slice(32, H)                            # tudo abaixo da barra de menus (o rótulo da barra muda sem animar: fora)
n, faltas, piscadas, saltos = 0, [], [], []
hist = []  # (t, reduzido, rgb) dos últimos 3 quadros
quadro = W * H * 3
while True:
    b = p.stdout.read(quadro)
    if len(b) < quadro:
        break
    f = np.frombuffer(b, np.uint8).reshape(H, W, 3)
    t = ts[n] if n < len(ts) else ts[-1]
    if (f[miolo].max(axis=-1) < 14).mean() < 0.97:
        faltas.append(t)
    red = f[abaixo][::4, ::4].mean(axis=-1).astype(np.float32)
    hist.append((t, red, f))
    if len(hist) > 3:
        hist.pop(0)
    if len(hist) == 3:
        (ta, a, fa), (tb, bb, fb), (tc, c, fc) = hist
        dab, dbc, dac = np.abs(bb - a).mean(), np.abs(c - bb).mean(), np.abs(c - a).mean()
        if dab > 0.6 and dbc > 0.6 and dac < 0.35 * min(dab, dbc):
            piscadas.append((tb, dab, dbc, dac, fa, fb, fc))
        # salto: mudança grande num quadro só, com o quadro anterior "parado" há > 80 ms e o seguinte idem
        if dab > 2.5 and tb - ta > 0.08 and tc - tb > 0.08:
            saltos.append((tb, dab, fa, fb))
    n += 1
p.wait()


def folha(nome, linhas):
    if not linhas:
        return
    w, h = W // 2, H // 2
    s = Image.new('RGB', (w * len(linhas[0]), h * len(linhas)), (255, 0, 255))
    for i, l in enumerate(linhas):
        for j, im in enumerate(l):
            s.paste(Image.fromarray(im).resize((w, h)), (j * w, i * h))
    s.save(nome)


def faixas(v):  # agrupa instantes vizinhos em intervalos
    r = []
    for t in v:
        if r and t - r[-1][1] < 0.12:
            r[-1][1] = t
        else:
            r.append([t, t])
    return r


print(f'quadros {n}  duração {ts[min(n, len(ts)) - 1]:.1f}s')
print('notch ausente/incompleto (intervalos):', [(round(a, 2), round(b, 2)) for a, b in faixas(faltas)])
print('piscadas de 1 quadro:', [(round(x[0], 2), round(x[1], 2), round(x[2], 2), round(x[3], 2)) for x in piscadas])
print('saltos sem animação:', [(round(x[0], 2), round(x[1], 2)) for x in saltos])
folha(arq + '.piscadas.png', [[x[4], x[5], x[6]] for x in piscadas[:12]])
folha(arq + '.saltos.png', [[x[2], x[3]] for x in saltos[:16]])
