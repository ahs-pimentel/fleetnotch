#!/bin/sh
# Sonda de um servidor Linux. Roda pelo SSH, sem instalar nada lá:
#   ssh host sh -s -- [--docker] [serviço …] < sonda_linux.sh
# POSIX sh (dash, bash, busybox), sem sudo, só leitura: /proc, df, ps, systemctl is-active e docker ps. Imprime um JSON.
docker=0
servicos=""
for a in "$@"; do
  case "$a" in
    --docker) docker=1 ;;
    *) servicos="$servicos $a" ;;
  esac
done
esc() { printf '%s' "$1" | tr -d '\n\r\t' | sed 's/\\/\\\\/g; s/"/\\"/g'; }

# CPU: duas leituras do /proc/stat com 1 s entre elas (ocupado = user+nice+system+irq+softirq+steal)
cpu_agora() { awk '/^cpu /{print $2+$3+$4+$7+$8+$9, $5+$6}' /proc/stat; }
set -- $(cpu_agora); o1=$1; i1=$2
sleep 1
set -- $(cpu_agora); o2=$1; i2=$2
cpu=$(awk -v a="$((o2 - o1))" -v b="$((i2 - i1))" 'BEGIN { t = a + b; print (t > 0) ? int(100 * a / t + 0.5) : 0 }')

# memória: usado = total - disponível (MemAvailable; kernel antigo: livre + buffers + cache)
mem=$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} /^MemFree:/{f=$2} /^Buffers:/{b=$2} /^Cached:/{c=$2}
  /^SwapTotal:/{st=$2} /^SwapFree:/{sf=$2}
  END { if (a == "") a = f + b + c; printf "{\"total\":%.0f,\"usado\":%.0f},\"swap\":{\"total\":%.0f,\"usado\":%.0f}", t*1024, (t-a)*1024, st*1024, (st-sf)*1024 }' /proc/meminfo)

# discos de verdade (/dev/…), um por dispositivo, de 1 GB pra cima
discos=$(df -kP 2>/dev/null | awk 'NR > 1 && $1 ~ /^\/dev\// && $2 >= 1048576 && !visto[$1]++ {
  gsub(/"/, "", $6); printf "%s{\"nome\":\"%s\",\"total\":%.0f,\"livre\":%.0f}", (n++ ? "," : ""), $6, $2*1024, $4*1024 }')

carga=$(awk '{printf "%s,%s,%s", $1, $2, $3}' /proc/loadavg)
ligado=$(awk '{printf "%d", $1}' /proc/uptime)
so=$(awk -F= '/^PRETTY_NAME=/{gsub(/"/, "", $2); print $2}' /etc/os-release 2>/dev/null)
nucleos=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)

# 3 processos que mais usam CPU (ps do procps; o do busybox não ordena: fica vazio)
top=$(ps -eo comm=,pcpu= --sort=-pcpu 2>/dev/null | head -3 | awk '{gsub(/"/, "", $1); printf "%s[\"%s\",%s]", (n++ ? "," : ""), $1, $2 + 0}')

# serviços escolhidos: active, inactive, failed, activating… (sem systemd: "sem systemd")
sv=""
for s in $servicos; do
  if command -v systemctl >/dev/null 2>&1; then e=$(systemctl is-active "$s" 2>/dev/null | head -1); else e="sem systemd"; fi
  sv="$sv${sv:+,}{\"nome\":\"$(esc "$s")\",\"estado\":\"$(esc "${e:-desconhecido}")\"}"
done

# containers (só com --docker): nome, estado, status ("Up 3 hours (healthy)") e imagem
dk="null"
if [ "$docker" = 1 ]; then
  if ! command -v docker >/dev/null 2>&1; then
    dk="{\"erro\":\"docker não instalado\"}"
  elif saida=$(docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}' 2>/dev/null); then
    lista=$(printf '%s\n' "$saida" | awk -F'|' 'NF >= 4 { for (i = 1; i <= 4; i++) gsub(/["\\]/, "", $i)
      printf "%s{\"nome\":\"%s\",\"estado\":\"%s\",\"status\":\"%s\",\"imagem\":\"%s\"}", (n++ ? "," : ""), $1, $2, $3, $4 }')
    dk="{\"containers\":[$lista]}"
  else
    dk="{\"erro\":\"sem acesso ao docker\"}"
  fi
fi

printf '{"so":"%s","kernel":"%s","host":"%s","nucleos":%s,"uptime":%s,"carga":[%s],"cpu":%s,"ram":%s,"discos":[%s],"top":[%s],"servicos":[%s],"docker":%s}\n' \
  "$(esc "$so")" "$(esc "$(uname -r)")" "$(esc "$(uname -n)")" "${nucleos:-0}" "${ligado:-0}" "$carga" "${cpu:-0}" "$mem" "$discos" "$top" "$sv" "$dk"
