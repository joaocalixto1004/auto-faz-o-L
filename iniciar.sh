#!/usr/bin/env bash
# Abre a automação das redes sociais (Linux).
#
# Na primeira vez, instala sozinho o que falta: o uv (que baixa o Python 3.12),
# as dependências e, se o Google Chrome não estiver instalado, o Chromium do
# Playwright, e cria o ícone "Automação das redes sociais" no menu de
# aplicativos e na Área de Trabalho. Nas outras vezes só confere (é rápido) e
# abre a janela.
#
# Uso:
#   ./iniciar.sh              prepara o que faltar e abre a interface
#   ./iniciar.sh --verificar  só confere o que falta (não instala, não cria o ícone, não abre nada)
#   ./iniciar.sh --preparar   prepara o que faltar (e o ícone), sem abrir a interface
#
# O ícone é recriado se sumir do menu ou se a pasta do projeto mudar de lugar
# (o mesmo que rodar ./instalar_atalho.sh).

set -uo pipefail

RAIZ=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P) || exit 1
cd "$RAIZ" || exit 1

PYTHON="$RAIZ/.venv/bin/python"
PASTA_LOGS="$RAIZ/logs"
LOG="$PASTA_LOGS/instalacao.log"
LOG_INTERFACE="$PASTA_LOGS/interface.log"
URL_UV="https://astral.sh/uv/install.sh"
UV=""
NOME_ATALHO="automacao-redes-sociais.desktop"   # o mesmo do instalar_atalho.sh

ajuda() {
    cat <<'FIM'
Uso: ./iniciar.sh [--verificar | --preparar]
  (sem opção)   prepara o que faltar e abre a interface
  --verificar   só confere o que falta (não instala, não cria o ícone, não abre nada)
  --preparar    prepara o que faltar (e o ícone do menu), sem abrir a interface
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
    local codigo=${PIPESTATUS[0]}
    registrar "uv venv terminou com o código $codigo."
    if [ "$codigo" -ne 0 ]; then
        # O motivo de verdade é o que o uv escreveu (na tela, logo acima, e no log).
        falhar "Não consegui criar o ambiente Python (.venv): o uv terminou com o código $codigo. O motivo está na mensagem do uv logo acima. Causas comuns: sem internet para baixar o Python, disco cheio ou pasta sem permissão de escrita."
    fi
}

preparar() {
    mkdir -p "$PASTA_LOGS" || falhar "Não consegui criar a pasta $PASTA_LOGS."
    registrar "iniciar.sh: preparando ($(uname -srm))."
    avisar "Preparando a automação (só demora na primeira vez)..."
    encontrar_uv || instalar_uv
    registrar "uv: $UV ($("$UV" --version 2>&1))"
    if ! { [ -x "$PYTHON" ] && "$PYTHON" -c "pass" >/dev/null 2>&1; }; then
        criar_venv
    fi
    "$PYTHON" -m automacao.instalacao --preparar --uv "$UV" || falhar "A preparação não terminou (veja a mensagem acima)."
}

atalho_do_menu() {  # onde fica o ícone do menu de aplicativos (como no instalar_atalho.sh)
    printf '%s/applications/%s' "${XDG_DATA_HOME:-${HOME:-}/.local/share}" "$NOME_ATALHO"
}

atalho_em_dia() {  # o ícone do menu existe e abre o iniciar.sh desta pasta?
    local atalho
    atalho=$(atalho_do_menu)
    [ -f "$atalho" ] && grep -qxF -- "Exec=bash \"${RAIZ//%/%%}/iniciar.sh\"" "$atalho"
}

garantir_atalho() {
    # Só o ícone do menu é conferido: se a pessoa apagou o da Área de Trabalho de
    # propósito, ele não volta. Um problema aqui nunca impede a janela de abrir.
    [ -n "${HOME:-}" ] || return 0
    atalho_em_dia && return 0
    if bash "$RAIZ/instalar_atalho.sh" --silencioso; then
        registrar "iniciar.sh: ícone criado em $(atalho_do_menu)."
        avisar "✔ Atalho criado: procure \"Automação das redes sociais\" no menu ou na Área de Trabalho."
    else
        registrar "iniciar.sh: não consegui criar o ícone."
        avisar "Aviso: não consegui criar o ícone no menu (a janela abre mesmo assim; para tentar de novo: ./instalar_atalho.sh)."
    fi
    return 0
}

abrir_interface() {
    mkdir -p "$PASTA_LOGS"
    registrar "iniciar.sh: abrindo a interface."
    printf '\n[%s] iniciar.sh: abrindo a interface.\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$LOG_INTERFACE" 2>/dev/null
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

informar_atalho() {  # para o --verificar: só diz, não cria nada
    if atalho_em_dia; then
        avisar "✔ Ícone no menu de aplicativos: $(atalho_do_menu)"
    else
        avisar "• Ícone no menu de aplicativos: ainda não existe ou é de outra pasta (o ./iniciar.sh cria sozinho)."
    fi
}

# Carregado com "source" (os testes fazem isso), só define as funções acima.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

case "$modo" in
    verificar)
        if [ ! -x "$PYTHON" ]; then
            avisar "✘ Ambiente Python: a pasta .venv ainda não existe (rode ./iniciar.sh para instalar)."
            informar_atalho
            exit 1
        fi
        "$PYTHON" -m automacao.instalacao --verificar
        codigo=$?
        informar_atalho  # o ícone não muda o resultado da conferência
        exit $codigo
        ;;
    preparar)
        if pronto; then
            avisar "✔ Tudo pronto; nada a instalar."
        else
            preparar
        fi
        garantir_atalho
        ;;
    abrir)
        pronto || preparar
        garantir_atalho
        abrir_interface
        ;;
esac
exit 0
