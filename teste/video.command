#!/bin/zsh
# Prints + vídeo do README (o open -a Terminal não passa variável de ambiente; este atalho liga o vídeo)
VIDEO=1 exec "$(dirname "$0")/prints.command"
