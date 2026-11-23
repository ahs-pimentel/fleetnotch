#!/bin/zsh
# Prints + vídeo do README PÚBLICO (inglês, modo demonstração) numa pasta irmã com sufixo -en.
# Rodar pelo Terminal.app:  open -a Terminal teste/publico.command   (o open não passa variável de ambiente)
cd "$(dirname "$0")"
BASE=$(cat .saida 2>/dev/null || echo "$HOME/Library/Caches/fleetnotch/testes")
IDIOMA=en VIDEO=1 OUT_DIR="$BASE-en" exec ./prints.command
