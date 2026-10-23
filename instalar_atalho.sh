#!/usr/bin/env bash
# Cria um ícone para abrir a automação com dois cliques (Linux).
#
# Grava o atalho no menu de aplicativos (~/.local/share/applications) e na Área
# de Trabalho, apontando para o iniciar.sh desta pasta. Se a pasta do projeto
# mudar de lugar, rode este script de novo.
#
# O iniciar.sh chama este script sozinho (com --silencioso) quando o ícone do
# menu falta ou aponta para outra pasta; rodar à mão serve para recriar o ícone.
#
# Uso: ./instalar_atalho.sh [--silencioso]   (ou: bash instalar_atalho.sh)
#   --silencioso   não mostra o que foi criado (os erros continuam aparecendo)

set -uo pipefail

SILENCIOSO=0
case "${1:-}" in
    "") ;;
    --silencioso) SILENCIOSO=1 ;;
    -h | --help | --ajuda) echo "Uso: ./instalar_atalho.sh [--silencioso]"; exit 0 ;;
    *) echo "Opção desconhecida: $1 (use: ./instalar_atalho.sh [--silencioso])" >&2; exit 2 ;;
esac

informar() { [ "$SILENCIOSO" -eq 1 ] || printf '%s\n' "$*"; }

RAIZ=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P) || exit 1
LANCADOR="$RAIZ/iniciar.sh"
NOME_ARQUIVO="automacao-redes-sociais.desktop"
PASTA_MENU="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

if [ ! -f "$LANCADOR" ]; then
    echo "✘ Não encontrei $LANCADOR." >&2
    exit 1
fi
# No arquivo .desktop, aspas, crase, cifrão, barra invertida e quebra de linha
# precisariam de escapes em camadas; um caminho com eles é raro, então só recusamos.
case "$LANCADOR" in
    *[\"\`\$\\]* | *$'\n'*)
        echo "✘ O caminho da pasta tem aspas, crase, \$, barra invertida ou quebra de linha: $RAIZ" >&2
        echo "  Mova o projeto para uma pasta sem esses caracteres e rode de novo." >&2
        exit 1
        ;;
esac
chmod +x "$LANCADOR" "$RAIZ/instalar_atalho.sh" 2>/dev/null || true

# Exec com "bash" funciona mesmo se o disco não permitir executar arquivos.
# O % é o único caractere especial do Exec que pode aparecer aqui (vira %%).
conteudo="[Desktop Entry]
Type=Application
Version=1.0
Name=Automação das redes sociais
Comment=Abre a automação do Instagram e do TikTok (na primeira vez, instala o que faltar)
Exec=bash \"${LANCADOR//%/%%}\"
Path=$RAIZ
Icon=applications-internet
Terminal=true
Categories=Utility;
StartupNotify=false
"

instalar() {  # instalar <pasta>: grava o atalho e o marca como confiável
    local destino="$1/$NOME_ARQUIVO"
    mkdir -p "$1" || return 1
    printf '%s' "$conteudo" >"$destino" || return 1
    chmod +x "$destino"
    # GNOME: sem isto, o ícone da Área de Trabalho pede "Permitir execução".
    if command -v gio >/dev/null 2>&1; then
        gio set "$destino" metadata::trusted true 2>/dev/null || true
    fi
    informar "✔ Atalho criado: $destino"
}

instalar "$PASTA_MENU" || { echo "✘ Não consegui gravar em $PASTA_MENU." >&2; exit 1; }
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$PASTA_MENU" >/dev/null 2>&1 || true
fi

AREA_DE_TRABALHO=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    AREA_DE_TRABALHO=$(xdg-user-dir DESKTOP 2>/dev/null)
fi
[ -n "$AREA_DE_TRABALHO" ] || AREA_DE_TRABALHO="$HOME/Desktop"
if [ "$AREA_DE_TRABALHO" != "$HOME" ] && [ -d "$AREA_DE_TRABALHO" ]; then
    instalar "$AREA_DE_TRABALHO" || echo "Aviso: não consegui criar o ícone em $AREA_DE_TRABALHO." >&2
fi

informar ""
informar "Procure \"Automação das redes sociais\" no menu de aplicativos ou use o ícone da Área de Trabalho."
informar "Se o ícone da Área de Trabalho não abrir, clique nele com o botão direito e escolha \"Permitir execução\"."
