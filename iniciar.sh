#!/usr/bin/env bash
# Abre a automação das redes sociais (Linux).
#
# Na primeira vez, instala sozinho o que falta: o uv (que baixa o Python 3.12),
# as dependências e, se o Google Chrome não estiver instalado, o Chromium do
# Playwright. Nas outras vezes só confere (é rápido) e abre a janela.
#
# Uso:
#   ./iniciar.sh              prepara o que faltar e abre a interface
#   ./iniciar.sh --verificar  só confere o que falta (não instala nem abre nada)
#   ./iniciar.sh --preparar   prepara o que faltar, sem abrir a interface
#
# Para ter um ícone de dois cliques no menu e na Área de Trabalho: ./instalar_atalho.sh

set -uo pipefail

RAIZ=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P) || exit 1
cd "$RAIZ" || exit 1

PYTHON="$RAIZ/.venv/bin/python"
PASTA_LOGS="$RAIZ/logs"
LOG="$PASTA_LOGS/instalacao.log"
LOG_INTERFACE="$PASTA_LOGS/interface.log"
URL_UV="https://astral.sh/uv/install.sh"
UV=""

ajuda() {
    cat <<'FIM'
Uso: ./iniciar.sh [--verificar | --preparar]
  (sem opção)   prepara o que faltar e abre a interface
  --verificar   só confere o que falta (não instala nem abre nada)
  --preparar    prepara o que faltar, sem abrir a interface
FIM
}

modo=abrir
case "${1:-}" in
    "") ;;
    --verificar) modo=verificar ;;
    --preparar) modo=preparar ;;
    -h | --help | --ajuda) ajuda; exit 0 ;;
    *) echo "Opção desconhecida: $1" >&2; ajuda >&2; exit 2 ;;
esac

avisar() { printf '%s\n' "$*"; }

registrar() {  # grava no log, com data e hora
    [ -d "$PASTA_LOGS" ] || return 0
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"
}

pausar() {
    # No modo de dois cliques, dá tempo de ler a mensagem antes de o terminal fechar.
    if [ "$modo" = abrir ] && [ -t 0 ]; then
        read -r -p "Pressione Enter para fechar..." _ || true
    fi
}

falhar() {
    avisar ""
    avisar "✘ $*"
    registrar "ERRO: $*"
    [ -f "$LOG" ] && avisar "Detalhes em: $LOG"
    pausar
    exit 1
}

pronto() {  # tudo instalado? (conferência rápida, sem instalar nada)
    [ -x "$PYTHON" ] && "$PYTHON" -m automacao.instalacao --verificar --silencioso >/dev/null 2>&1
}

encontrar_uv() {
    local candidato
    for candidato in "$(command -v uv 2>/dev/null)" "${XDG_BIN_HOME:-$HOME/.local/bin}/uv" \
                     "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -n "$candidato" ] && [ -f "$candidato" ] && [ -x "$candidato" ]; then
            UV=$candidato
            return 0
        fi
    done
    return 1
}

instalar_uv() {
    local baixar
    if command -v curl >/dev/null 2>&1; then
        baixar=(curl -LsSf "$URL_UV")
    elif command -v wget >/dev/null 2>&1; then
        baixar=(wget -qO- "$URL_UV")
    else
        falhar "Para instalar o uv é preciso o curl ou o wget. Instale um deles (ex.: sudo apt install curl) e abra de novo."
    fi
    avisar "Instalando o uv, que baixa o Python e as dependências (só desta vez)..."
    registrar "Instalando o uv com o instalador oficial ($URL_UV)."
    if ! { "${baixar[@]}" | sh; } 2>&1 | tee -a "$LOG"; then
        falhar "Não consegui instalar o uv. Confira a conexão com a internet e abra de novo."
    fi
    encontrar_uv || falhar "O uv foi instalado, mas não o encontrei. Feche o terminal e abra de novo."
}

criar_venv() {
    # Python do próprio uv (--managed-python), que já traz o Tkinter. Versões antigas
    # do uv não têm essa opção; aí vale o Python que o uv escolher. O UV_VENV_CLEAR
    # recria um .venv quebrado (o uv só apaga a pasta se ela for mesmo um ambiente).
    local opcoes=(venv --python 3.12)
    if "$UV" venv --help 2>/dev/null | grep -q -- "--managed-python"; then
        opcoes+=(--managed-python)
    fi
    avisar "Criando o ambiente Python em .venv (o uv baixa o Python 3.12 se preciso)..."
    UV_VENV_CLEAR=1 "$UV" "${opcoes[@]}" "$RAIZ/.venv" 2>&1 | tee -a "$LOG"
}

preparar() {
    mkdir -p "$PASTA_LOGS" || falhar "Não consegui criar a pasta $PASTA_LOGS."
    registrar "iniciar.sh: preparando ($(uname -srm))."
    avisar "Preparando a automação (só demora na primeira vez)..."
    encontrar_uv || instalar_uv
    registrar "uv: $UV ($("$UV" --version 2>&1))"
    if ! { [ -x "$PYTHON" ] && "$PYTHON" -c "pass" >/dev/null 2>&1; }; then
        criar_venv || falhar "Não consegui criar o ambiente Python (.venv). Confira a internet e abra de novo."
    fi
    "$PYTHON" -m automacao.instalacao --preparar --uv "$UV" || falhar "A preparação não terminou (veja a mensagem acima)."
}

abrir_interface() {
    mkdir -p "$PASTA_LOGS"
    registrar "iniciar.sh: abrindo a interface."
    avisar "Abrindo a janela da automação..."
    # Numa sessão própria (setsid) e sem ligação com o terminal: fechar o terminal
    # não fecha a janela. O Parar da interface continua funcionando (usa sinais do Linux).
    if command -v setsid >/dev/null 2>&1; then
        setsid "$PYTHON" -m automacao.interface </dev/null >>"$LOG_INTERFACE" 2>&1 &
    else
        nohup "$PYTHON" -m automacao.interface </dev/null >>"$LOG_INTERFACE" 2>&1 &
    fi
    local pid=$! codigo
    # Se a janela fechar logo de cara (sem tela, erro...), mostra o motivo em vez de sumir.
    for _ in 1 2 3 4 5 6; do
        sleep 0.5
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid"
            codigo=$?
            [ "$codigo" -eq 0 ] && return 0
            avisar "A janela fechou logo depois de abrir (código $codigo). Últimas linhas de $LOG_INTERFACE:"
            tail -n 20 "$LOG_INTERFACE"
            falhar "A interface não abriu."
        fi
    done
    avisar "Pronto. Pode fechar este terminal: a janela continua aberta."
}

case "$modo" in
    verificar)
        if [ ! -x "$PYTHON" ]; then
            avisar "✘ Ambiente Python: a pasta .venv ainda não existe (rode ./iniciar.sh para instalar)."
            exit 1
        fi
        "$PYTHON" -m automacao.instalacao --verificar
        exit $?
        ;;
    preparar)
        if pronto; then
            avisar "✔ Tudo pronto; nada a instalar."
        else
            preparar
        fi
        ;;
    abrir)
        pronto || preparar
        abrir_interface
        ;;
esac
exit 0
