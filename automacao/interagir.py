#!/usr/bin/env python3
"""Interage com as publicações de perfis do Instagram e do TikTok.

Instagram, nas abas Posts, Reels e Reposts: curtir, repostar, comentar, compartilhar
por mensagem e copiar o link (pelo menu "...").
TikTok, nas abas Vídeos e Republicações: curtir, comentar, adicionar aos
favoritos, compartilhar por mensagem e copiar o link.

De onde vêm os dados:
- Perfis: data/para_acessar.xlsx (uma aba por rede), ou links na linha de
  comando, ou --arquivo/--planilha.
- Com quem compartilhar: data/para_compartilhar.xlsx (uma aba por rede). Cada
  publicação é enviada para cada conta da aba da sua rede.
- Limite (N primeiras publicações ou últimas X semanas), comentários e limite
  diário: aba Configurações da interface (ou a página antiga do
  configuracao.py), salvos no banco. As opções abaixo têm prioridade.

Com as duas redes escolhidas, o Instagram e o TikTok agem ao mesmo tempo, cada
um na sua janela do mesmo Chrome (veja processar_em_paralelo e
docs/adr/0002-redes-em-paralelo.md); --uma-rede-por-vez volta ao jeito antigo.
O ritmo e os tetos vêm de redes/protecao.py (normal, anti-bloqueio ou boost).

Curtir, repostar e favoritar só são clicados quando a página mostra que ainda
não foram feitos, para nunca desfazer uma ação (inclusive uma feita à mão).
Tudo fica gravado no SQLite (automacao.db), e uma ação concluída numa
publicação não é repetida pelo script.

Faça o login antes (python -m automacao.abrir_redes). Exemplos, na raiz do
projeto e com o Python do .venv:
    python -m automacao.interagir                          # perfis da planilha
    python -m automacao.interagir --simular                # só confere, não clica
    python -m automacao.interagir https://www.tiktok.com/@perfil --semanas 2 --trace
"""

import argparse
import random
import sys
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from automacao import banco, planilhas
from automacao.abrir_redes import (ESPERA_PARA_REABRIR_S, REDES, NavegadorFechado, abrir_com_insistencia,
                                   endereco_de_controle, extrair_conta, navegador_vivo, redes_logadas)
from automacao.caminhos import PASTA_TRACES, TRAVA_AUTOMACAO
from automacao.redes import instagram, tiktok
from automacao.redes.protecao import LimiteDiario, protecao_da_config, registrar_aviso
from automacao.redes.comum import (Bloqueio, Config, LinhaResumo,
                                   comentarios_da_config, pausa, sortear_quantidades)
from automacao.terminal import (PARADA, SaidaComRotulos, definir_rotulo, dormir, obter_trava, preparar_console,
                                vigiar_pedido_de_parada)

# Primeiros trechos de endereço do Instagram que não são perfis.
RESERVADOS_INSTAGRAM = {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct"}
MODULOS = {"Instagram": instagram, "TikTok": tiktok}
# Com as duas redes escolhidas, agem ao mesmo tempo: a primeira na janela de
# sempre e a segunda numa janela própria do mesmo Chrome.
REDES_EM_PARALELO = ("Instagram", "TikTok")


def interpretar_perfil(texto: str) -> tuple[str, str]:
    """Converte um link de perfil em (rede, nome do perfil)."""
    texto = texto.strip()
    url = urlparse(texto if "://" in texto else "https://" + texto)
    host = (url.hostname or "").removeprefix("www.").removeprefix("m.")
    partes = [parte for parte in url.path.split("/") if parte]
    if host == "instagram.com" and partes and partes[0].lower() not in RESERVADOS_INSTAGRAM:
        return "Instagram", partes[0]
    if host == "tiktok.com" and partes and partes[0].startswith("@") and len(partes[0]) > 1:
        return "TikTok", partes[0][1:]
    raise ValueError(f"não reconheci o perfil {texto!r}: use o link do Instagram ou do TikTok")


def ler_perfis(args: argparse.Namespace) -> list[tuple[str, str]]:
    textos = list(args.perfis)
    planilha = args.planilha
    if planilha is None and not textos and not args.arquivo and planilhas.PLANILHA_PERFIS.exists():
        planilha = planilhas.PLANILHA_PERFIS  # padrão: data/para_acessar.xlsx
    if planilha:
        textos += planilhas.ler_perfis(Path(planilha))
    if args.arquivo:
        for linha in Path(args.arquivo).read_text(encoding="utf-8").splitlines():
            linha = linha.split("#", 1)[0].strip()
            if linha:
                textos.append(linha)
    perfis = []
    for texto in textos:
        try:
            perfil = interpretar_perfil(texto)
        except ValueError as erro:
            if texto in args.perfis:
                raise  # link digitado na linha de comando: erro de uso
            print(f"Aviso: {erro}. Linha ignorada.")
            continue
        if perfil not in perfis:
            perfis.append(perfil)
    return perfis


def quantidade_minima(salvas: dict[str, str]) -> int | None:
    """O mínimo da faixa do modo quantidade, se estiver salvo e couber (1 até o máximo)."""
    texto = str(salvas.get("quantidade_min", "")).strip()
    if not (texto.isascii() and texto.isdecimal()):
        return None
    minimo, maximo = int(texto), int(salvas["quantidade"])
    return minimo if 1 <= minimo <= maximo else None


def montar_config(salvas: dict[str, str], args: argparse.Namespace) -> Config:
    """Junta as configurações da página com as opções da linha de comando."""
    modo = salvas["modo"]
    if args.quantidade is not None:
        modo = "quantidade"
    elif args.semanas is not None:
        modo = "tempo"
    comentarios = [c.strip() for c in (args.comentario or []) if c.strip()] or comentarios_da_config(salvas)
    # Usuários do Instagram/TikTok não diferenciam maiúsculas: a chave da ação
    # ("compartilhar:usuario") precisa ser sempre a mesma.
    compartilhar_com = (args.compartilhar_com or salvas["compartilhar_com"]).lstrip("@").lower()
    destinatarios: dict[str, list[str]] = {"Instagram": [], "TikTok": []}
    caminho = Path(args.planilha_compartilhar) if args.planilha_compartilhar else None
    if caminho and caminho.exists():
        destinatarios.update(planilhas.ler_destinatarios(caminho))
    if compartilhar_com and (args.compartilhar_com or not destinatarios["Instagram"]):
        destinatarios["Instagram"] = [compartilhar_com]
    return Config(
        modo=modo,
        quantidade=args.quantidade or int(salvas["quantidade"]),
        quantidade_min=None if args.quantidade else quantidade_minima(salvas),
        semanas=args.semanas or int(salvas["semanas"]),
        comentario=comentarios[0],
        comentarios=tuple(comentarios),
        compartilhar_com=compartilhar_com,
        limite_diario=args.limite_diario or int(salvas["limite_diario"]),
        simular=args.simular,
        destinatarios=destinatarios,
        verificar_comentarios=getattr(args, "verificar_comentarios", False),
        redes=(banco.REDES_ESCOLHA[args.redes] if getattr(args, "redes", None)
               else banco.redes_escolhidas(salvas)),
        protecao=protecao_da_config(salvas, getattr(args, "anti_bloqueio", False), getattr(args, "boost", False)),
        seguir=getattr(args, "seguir", False) or salvas.get("seguir") == "sim",
        ver_stories=getattr(args, "ver_stories", False) or salvas.get("ver_stories") == "sim",
    )


def minutos_para_repetir(salvas: dict[str, str], args: argparse.Namespace) -> int | None:
    """Intervalo para recomeçar a lista, ou None se a repetição estiver desligada."""
    if getattr(args, "repetir", None):
        return args.repetir
    if getattr(args, "sem_repetir", False) or salvas.get("repetir") != "sim":
        return None
    try:
        return max(1, int(salvas.get("repetir_minutos", "20")))
    except ValueError:
        return 20


def imprimir_resumo(resumo: list[LinhaResumo]) -> None:
    if not resumo:
        return
    print("\nResumo:")
    for linha in resumo:
        pub = linha.publicacao
        acoes = ", ".join(f"{nome}={status}" for nome, status in linha.acoes.items())
        print(f"  {pub.rede:<9} @{pub.perfil} {pub.aba} #{pub.posicao} ({pub.codigo}): {acoes}")


def main() -> int:
    preparar_console()
    vigiar_pedido_de_parada()
    parser = argparse.ArgumentParser(
        description="Curte, comenta e compartilha publicações de perfis do Instagram e do TikTok.")
    parser.add_argument("perfis", nargs="*",
                        help="links dos perfis (sem nada, usa data/para_acessar.xlsx)")
    parser.add_argument("--planilha", help="planilha .xlsx com os perfis (uma aba por rede)")
    parser.add_argument("--arquivo", help="arquivo de texto com um link de perfil por linha")
    parser.add_argument("--planilha-compartilhar", default=str(planilhas.PLANILHA_DESTINATARIOS),
                        help="planilha com quem compartilhar (padrão: data/para_compartilhar.xlsx)")
    limite = parser.add_mutually_exclusive_group()
    limite.add_argument("--quantidade", type=int,
                        help="pega as N primeiras publicações de cada aba")
    limite.add_argument("--semanas", type=int,
                        help="pega as publicações das últimas N semanas")
    parser.add_argument("--comentario", action="append",
                        help="texto do comentário; repita a opção para sortear entre vários")
    parser.add_argument("--compartilhar-com",
                        help="conta do Instagram que recebe o compartilhamento (no lugar da planilha)")
    parser.add_argument("--limite-diario", type=int,
                        help="máximo de publicações trabalhadas por rede por dia")
    parser.add_argument("--simular", action="store_true",
                        help="só abre as publicações e confere os botões, sem clicar nem gravar nada")
    parser.add_argument("--trace", action="store_true",
                        help="grava um trace do Playwright em traces/ para rever a execução")
    parser.add_argument("--verificar-comentarios", action="store_true",
                        help="modo teste: antes de comentar, procura um comentário seu no post "
                             "(em produção o script comenta direto)")
    parser.add_argument("--sem-reabrir", action="store_true",
                        help="encerra se a janela do navegador for fechada "
                             "(padrão: reabre e continua de onde parou)")
    parser.add_argument("--seguir", action="store_true",
                        help="segue os perfis da lista que a conta ainda não segue")
    parser.add_argument("--ver-stories", action="store_true",
                        help="assiste e curte os stories ainda não vistos (Instagram)")
    repeticao = parser.add_mutually_exclusive_group()
    repeticao.add_argument("--repetir", type=int, metavar="MINUTOS",
                           help="ao terminar a lista, recomeça depois de MINUTOS (até Parar/Ctrl+C)")
    repeticao.add_argument("--sem-repetir", action="store_true",
                           help="não recomeça a lista, mesmo se a configuração mandar")
    parser.add_argument("--anti-bloqueio", action="store_true",
                        help="liga o modo anti-bloqueio (bem mais lento e cauteloso) nesta execução")
    parser.add_argument("--boost", action="store_true",
                        help="modo boost nesta execução: ignora os tetos por hora e por dia e o limite "
                             "diário (mais risco de bloqueio); o anti-bloqueio, se ligado, vale mais")
    parser.add_argument("--uma-rede-por-vez", action="store_true",
                        help="com as duas redes, uma depois da outra numa janela só (padrão: as duas "
                             "ao mesmo tempo, cada uma na sua janela)")
    parser.add_argument("--redes", choices=sorted(banco.REDES_ESCOLHA),
                        help="em quais redes agir (padrão: o que está nas configurações)")
    parser.add_argument("--perfil", action="append", dest="perfis_navegador", metavar="NOME_OU_ID",
                        help="perfil do navegador (conta) que age; repita para vários "
                             "(padrão: todos os perfis ativos, um depois do outro)")
    args = parser.parse_args()

    try:
        perfis = ler_perfis(args)
    except (ValueError, OSError) as erro:
        parser.error(str(erro))
    if not perfis:
        parser.error("informe perfis (links), --arquivo ou --planilha, ou crie data/para_acessar.xlsx")
    for nome in ("quantidade", "semanas", "limite_diario"):
        valor = getattr(args, nome)
        if valor is not None and valor < 1:
            parser.error(f"--{nome.replace('_', '-')} precisa ser pelo menos 1")

    if args.repetir is not None and args.repetir < 1:
        parser.error("--repetir precisa ser pelo menos 1 minuto")
    trava = obter_trava(TRAVA_AUTOMACAO)
    if trava is None:
        print("Já existe uma automação rodando neste computador. Pare a outra antes de iniciar esta.")
        return 1

    with closing(banco.conectar()) as conn, sync_playwright() as p:
        volta = 0
        while True:
            volta += 1
            salvas = banco.ler_configuracoes(conn)  # relida a cada volta: vale o que mudou na interface
            cfg = montar_config(salvas, args)
            repetir = minutos_para_repetir(salvas, args)
            if volta > 1 and not (args.perfis or args.arquivo or args.planilha):
                perfis = ler_perfis(args)        # a planilha pode ter mudado durante a espera
            try:
                perfis_nav = escolher_perfis_navegador(conn, args.perfis_navegador)
            except ValueError as erro:
                if volta == 1:
                    parser.error(str(erro))
                print(f"Não há conta para usar: {erro}.")
                return 0
            try:
                status = executar_volta(p, conn, cfg, perfis, perfis_nav, args, volta, repetir)
            except KeyboardInterrupt:
                return 0  # Parar/Ctrl+C: a volta já registrou "interrompida" e mostrou o resumo
            if status != "concluida" or repetir is None:
                return 0
            print(f"\nLista concluída. Recomeçando em {repetir} min "
                  "(para encerrar, use Parar na interface ou Ctrl+C).", flush=True)
            try:
                dormir(repetir * 60 + random.uniform(0, 60))
            except KeyboardInterrupt:
                print("\nInterrompido.")
                return 0
            if minutos_para_repetir(banco.ler_configuracoes(conn), args) is None:
                print("A repetição foi desligada nas configurações. Encerrando.")
                return 0


def executar_volta(p, conn, cfg: Config, perfis, perfis_nav, args: argparse.Namespace,
                   volta: int, repetir: int | None) -> str:
    """Uma passada por todos os perfis-alvo, com cada conta. Devolve o status da rodada."""
    resumo: list[LinhaResumo] = []
    alvos = [(rede, perfil) for rede, perfil in perfis if rede in cfg.redes]
    compartilhar = "; ".join(f"{rede}: {', '.join('@' + u for u in cfg.destinatarios.get(rede, [])) or 'ninguém'}"
                             for rede in cfg.redes)
    if volta > 1:
        print(f"\n========== Volta {volta} ==========")
    print(f"Redes: {', '.join(cfg.redes)}. Perfis-alvo: {len(alvos)}. Limite: {cfg.descricao()}. "
          f"Comentários: {len(cfg.comentarios)} (sorteados). "
          f"Limite diário: {cfg.limite_diario} por conta e rede.")
    print(f"Contas (perfis do navegador): {', '.join(p_['nome'] for p_ in perfis_nav)}. "
          f"Proteção: {cfg.protecao.descricao}.")
    extras = [nome for nome, ligado in (("seguir", cfg.seguir), ("ver stories", cfg.ver_stories)) if ligado]
    print(f"Compartilhar com: {compartilhar}. Extras: {', '.join(extras) or 'nenhum'}. "
          f"Recomeçar a lista: {f'a cada {repetir} min' if repetir else 'não'}.")
    if cfg.verificar_comentarios:
        print("Modo teste: antes de comentar, procura um comentário seu no post.")
    if cfg.simular:
        print("Modo simulação: nada será clicado nem gravado.")
    print()
    if not alvos:
        print("Nenhum perfil-alvo nas redes escolhidas. Confira a planilha e a escolha de redes.")
        return "concluida"

    continuar = getattr(args, "continuar_sem_login", None)
    if continuar is None:
        continuar = banco.ler_configuracoes(conn).get("continuar_sem_login", "sim") == "sim"
    rodada_id = None if cfg.simular else banco.iniciar_rodada(conn)
    status = "erro"
    aviso = None
    usadas = 0
    try:
        for ordem, perfil_nav in enumerate(perfis_nav):
            if ordem and usadas:
                print("\nPausa antes da próxima conta...", flush=True)
                pausa(cfg.protecao.pausa_entre_contas)
            if not args.perfis_navegador:
                atual = banco.obter_perfil(conn, perfil_nav["id"])
                if atual is None or not atual["ativo"]:
                    print(f"\n[{perfil_nav['nome']}] Conta removida ou desativada durante a execução; pulando.")
                    continue
            print(f"\n===== Perfil {perfil_nav['nome']} =====", flush=True)
            try:
                if rodar_perfil_navegador(p, conn, rodada_id, cfg, perfil_nav, alvos, resumo, args):
                    usadas += 1
                    continue
                motivo = "nenhuma rede escolhida está logada com conta identificada"
            except PlaywrightError as erro:
                motivo = f"não consegui abrir o navegador deste perfil ({erro.message.splitlines()[0]})"
            if not continuar:
                print(f"[{perfil_nav['nome']}] {motivo}. Parando, como pedido na configuração.")
                status = "interrompida"
                return status
            print(f"[{perfil_nav['nome']}] {motivo}. Pulando esta conta.")
        if not usadas:
            print("\nNenhuma conta pôde ser usada. Faça o login pela aba Contas da interface.")
        status = "concluida"
    except ParadaPorAviso as erro:
        status = "interrompida"
        aviso = erro
        print(f"\n⛔ Aviso de bloqueio — {erro}. A automação parou inteira (todas as redes e contas) e "
              "não recomeça sozinha. Confira a conta no app antes de iniciar de novo.", flush=True)
    except JanelaFechada:
        status = "janela_fechada"
        print("\nNavegador fechado.")
    except KeyboardInterrupt:
        status = "interrompida"
        print("\nInterrompido.")
        raise
    finally:
        if rodada_id is not None:
            banco.finalizar_rodada(conn, rodada_id, status)
        imprimir_resumo(resumo)
    return "aviso" if aviso else status


class ParadaPorAviso(Exception):
    """Uma rede mostrou um aviso de bloqueio: a automação inteira para (todas as redes e contas)."""


class JanelaFechada(Exception):
    """A janela foi fechada e a opção --sem-reabrir pediu para encerrar."""


def escolher_perfis_navegador(conn, referencias: list[str] | None) -> list:
    """Perfis do navegador que vão agir: os pedidos (nome ou id) ou todos os ativos."""
    if referencias:
        escolhidos = []
        for referencia in referencias:
            perfil = banco.obter_perfil(conn, referencia)
            if perfil is None:
                raise ValueError(f"perfil {referencia!r} não encontrado (veja a aba Contas da interface)")
            if perfil["id"] not in [p_["id"] for p_ in escolhidos]:
                escolhidos.append(perfil)
        return escolhidos
    ativos = banco.listar_perfis(conn, somente_ativos=True)
    if not ativos:
        raise ValueError("nenhum perfil ativo: ative pelo menos uma conta na aba Contas da interface")
    return ativos


def ler_conta_na_pagina(pagina, rede, tentativas: int = 10):
    """Abre a página inicial da rede e lê a conta logada ("@usuario"). None se não der."""
    try:
        pagina.goto(rede.url_inicial, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightError as erro:
        if pagina.is_closed():
            raise NavegadorFechado from erro
        return None
    for _ in range(tentativas):
        try:
            conta = extrair_conta(rede.nome, pagina.evaluate(rede.js_conta))
        except PlaywrightError:
            conta = None
        if conta:
            return conta
        try:
            pagina.wait_for_timeout(1500)
        except PlaywrightError as erro:
            raise NavegadorFechado from erro  # a janela foi fechada no meio da leitura
    return None


def descobrir_contas(contexto, redes: tuple[str, ...]) -> dict[str, str]:
    """Qual conta está logada em cada rede escolhida, neste perfil do navegador.

    Só entram as redes logadas cuja conta foi identificada: sem saber a conta,
    não dá para registrar quem agiu nem aplicar o limite diário dela.
    """
    logadas = redes_logadas(contexto)
    pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
    contas: dict[str, str] = {}
    for rede in REDES:
        if rede.nome not in redes:
            continue
        if rede.nome not in logadas:
            print(f"[{rede.nome}] Não logado — faça o login desta conta pela aba Contas da interface.")
            continue
        conta = ler_conta_na_pagina(pagina, rede)
        MODULOS[rede.nome].definir_meu_usuario(conta)
        if conta:
            contas[rede.nome] = conta
            print(f"[{rede.nome}] Conta: {conta}")
        else:
            print(f"[{rede.nome}] Não consegui identificar a conta logada; pulando esta rede neste perfil.")
    return contas


def rodar_perfil_navegador(p, conn, rodada_id: int | None, cfg: Config, perfil_nav, alvos,
                           resumo: list[LinhaResumo], args: argparse.Namespace) -> bool:
    """Processa todos os perfis-alvo com as contas de um perfil do navegador.

    Devolve False se nenhuma rede escolhida estiver logada (com conta
    identificada) neste perfil.

    Se a janela for fechada, reabre e continua do perfil-alvo em que parou
    (as publicações já feitas ficam no banco e são puladas).
    """
    pasta = banco.pasta_do_perfil(perfil_nav)
    paralelo = pode_rodar_em_paralelo(cfg, args)
    proximo, bloqueadas, contas, anteriores = 0, set(), None, None
    proximos = dict.fromkeys(REDES_EM_PARALELO, 0)   # no modo paralelo, cada rede tem a sua lista
    cfg_conta = None   # cfg com a quantidade sorteada para cada conta (faixa do modo quantidade)
    while True:
        aberto_em = time.time()
        contexto = abrir_com_insistencia(p, pasta, depuracao=True) if paralelo else abrir_com_insistencia(p, pasta)
        try:
            contexto.grant_permissions(["clipboard-read", "clipboard-write"])
            if args.trace:
                # Só imagens e ações: guardar as páginas inteiras (com os vídeos)
                # gera arquivos de centenas de MB.
                contexto.tracing.start(screenshots=True, snapshots=False)
            if contas is None:
                novas = descobrir_contas(contexto, cfg.redes)
                if anteriores is not None and novas != anteriores:
                    # Outra conta assumiu este perfil: recomeça a lista com ela (o banco
                    # pula o que ela já fez) e esquece as paradas da conta anterior.
                    print(f"As contas mudaram ({anteriores} -> {novas}); recomeçando a lista com elas.")
                    proximo, bloqueadas = 0, set()
                    proximos = dict.fromkeys(REDES_EM_PARALELO, 0)
                if cfg_conta is None or novas != anteriores:
                    # Uma vez por conta nesta volta: reabrir o navegador não sorteia de novo.
                    cfg_conta = sortear_quantidades(cfg, novas)
                contas = anteriores = novas
            if not contas:
                return False
            endereco = None
            if paralelo and all(rede in contas and any(r == rede for r, _ in alvos) for rede in REDES_EM_PARALELO):
                endereco = endereco_de_controle(pasta, aberto_em)
                if endereco is None:
                    print("Não consegui abrir a segunda janela; as redes vão uma depois da outra.")
            if endereco:
                proximos = processar_em_paralelo(conn, contexto, endereco, rodada_id, cfg_conta, alvos, proximos,
                                                 bloqueadas, resumo, contas, reabrir=not args.sem_reabrir)
            else:
                proximo = processar_perfis(conn, contexto, rodada_id, cfg_conta, alvos, proximo, bloqueadas,
                                           resumo, contas)
            return True
        except NavegadorFechado as erro:
            proximo = getattr(erro, "proximo", proximo)
            proximos = getattr(erro, "proximos", proximos)
            if args.sem_reabrir:
                raise JanelaFechada from erro
            # Só para pelo Parar da interface (ou Ctrl+C).
            print(f"\nA janela do navegador foi fechada. Reabrindo em {ESPERA_PARA_REABRIR_S} s "
                  "e continuando de onde parou (para encerrar, use Parar na interface ou Ctrl+C)...",
                  flush=True)
            salvar_trace(contexto, args.trace)
            fechar_contexto(contexto)
            contexto = None
            contas = None  # ao reabrir, confere de novo qual conta está logada
            dormir(ESPERA_PARA_REABRIR_S)
        finally:
            if contexto is not None:
                salvar_trace(contexto, args.trace)
                fechar_contexto(contexto)


def pode_rodar_em_paralelo(cfg: Config, args: argparse.Namespace) -> bool:
    """As duas redes ao mesmo tempo? Só com as duas escolhidas, sem --uma-rede-por-vez
    e sem --trace (o trace só grava o que passa pela janela principal)."""
    return (all(rede in cfg.redes for rede in REDES_EM_PARALELO)
            and not getattr(args, "uma_rede_por_vez", False) and not getattr(args, "trace", False))


def nova_janela(navegador, contexto):
    """Abre uma janela nova do Chrome no mesmo perfil e devolve a página dela.

    Janela, e não aba: uma aba que não está na frente fica "escondida" para o
    site, que pausa o vídeo e deixa de carregar a grade de publicações.
    """
    sessao = navegador.new_browser_cdp_session()
    try:
        with contexto.expect_page(timeout=30_000) as nova:
            sessao.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
    finally:
        sessao.detach()
    return nova.value


def processar_rede_com_reabertura(conn, contexto, rodada_id: int | None, cfg: Config, rede: str,
                                  lista: list[tuple[str, str]], inicio: int, bloqueadas: set[str],
                                  resumo: list[LinhaResumo], contas: dict[str, str],
                                  nova_pagina, vivo, reabrir: bool = True) -> int:
    """processar_perfis numa página só desta rede. Devolve o índice seguinte ao último.

    Se só a janela desta rede for fechada (o navegador continua aberto), abre
    outra e continua do perfil-alvo em que parou. Se o navegador inteiro
    fechou, levanta NavegadorFechado com `.proximo`.
    """
    pagina = nova_pagina(True)
    while True:
        try:
            return processar_perfis(conn, contexto, rodada_id, cfg, lista, inicio, bloqueadas, resumo,
                                    contas, pagina=pagina)
        except NavegadorFechado as erro:
            inicio = getattr(erro, "proximo", inicio)
            causa = erro
        except PlaywrightError as erro:
            if vivo() and not pagina.is_closed():
                raise  # erro de verdade, não uma janela fechada
            causa = erro
        if not reabrir or not vivo():
            fechado = NavegadorFechado()
            fechado.proximo = inicio
            raise fechado from causa
        origem = causa.__cause__ or causa
        motivo = getattr(origem, "message", None) or str(origem) or type(origem).__name__
        print(f"A janela do {rede} foi fechada ({motivo.splitlines()[0][:120]}). Abrindo outra em "
              f"{ESPERA_PARA_REABRIR_S} s e continuando de onde parou...", flush=True)
        dormir(ESPERA_PARA_REABRIR_S)
        pagina = nova_pagina(False)


def _rede_em_outra_janela(endereco: str, rodada_id: int | None, cfg: Config, rede: str,
                          lista: list[tuple[str, str]], inicio: int, bloqueadas: set[str],
                          resumo: list[LinhaResumo], contas: dict[str, str], reabrir: bool,
                          resultado: dict) -> None:
    """Corpo da linha de execução da segunda rede: conexão própria ao mesmo Chrome e ao banco.

    A API do Playwright não pode ser usada por duas linhas de execução, então
    esta abre a sua (pelo endereço local do Chrome). O que acontecer vai para
    `resultado`: "proximo", e "fechado", "parado" ou "erro".
    """
    definir_rotulo(rede)
    resultado["proximo"] = inicio
    try:
        with sync_playwright() as p, closing(banco.conectar()) as conn:
            navegador = p.chromium.connect_over_cdp(endereco)
            contexto = navegador.contexts[0]
            janela = {}

            def abrir_janela(_primeira: bool):
                janela["pagina"] = nova_janela(navegador, contexto)
                return janela["pagina"]

            resultado["proximo"] = processar_rede_com_reabertura(
                conn, contexto, rodada_id, cfg, rede, lista, inicio, bloqueadas, resumo, contas,
                nova_pagina=abrir_janela, vivo=navegador.is_connected, reabrir=reabrir)
            print(f"Lista do {rede} terminada.", flush=True)
            try:
                janela["pagina"].close()   # a janela vazia não fica sobrando
            except (KeyError, PlaywrightError):
                pass
    except NavegadorFechado as erro:
        resultado["proximo"] = getattr(erro, "proximo", resultado["proximo"])
        resultado["fechado"] = True
    except ParadaPorAviso as erro:
        resultado["aviso"] = erro
        PARADA.set()                   # a rede principal também para, na próxima espera
    except KeyboardInterrupt:
        resultado["parado"] = True
    except PlaywrightError as erro:
        resultado["fechado"] = True    # a conexão com o navegador caiu
        print(f"Perdi a conexão com o navegador ({erro.message.splitlines()[0]}).", flush=True)
    except Exception as erro:  # noqa: BLE001 - quem decide o que fazer é a linha principal
        resultado["erro"] = erro
    finally:
        definir_rotulo(None)
        if isinstance(sys.stdout, SaidaComRotulos):
            sys.stdout.descarregar()


def processar_em_paralelo(conn, contexto, endereco: str, rodada_id: int | None, cfg: Config,
                          alvos: list[tuple[str, str]], proximos: dict[str, int], bloqueadas: set[str],
                          resumo: list[LinhaResumo], contas: dict[str, str],
                          reabrir: bool = True) -> dict[str, int]:
    """Instagram nesta janela e TikTok numa janela própria, ao mesmo tempo.

    Cada rede segue a sua parte da lista; a conta só termina quando as duas
    terminarem. As linhas do log ganham "[Instagram]" ou "[TikTok]" na frente.
    Devolve {rede: próximo índice da lista dela}. Se o navegador inteiro
    fechar, levanta NavegadorFechado com `.proximos`.
    """
    principal, outra = REDES_EM_PARALELO
    listas = {rede: [alvo for alvo in alvos if alvo[0] == rede] for rede in REDES_EM_PARALELO}
    proximos = dict(proximos)
    resultado: dict = {}
    print(f"{principal} e {outra} ao mesmo tempo: o {outra} abre numa janela própria do Chrome.", flush=True)
    trabalhador = threading.Thread(
        target=_rede_em_outra_janela, name=outra, daemon=True,
        args=(endereco, rodada_id, cfg, outra, listas[outra], proximos[outra], bloqueadas, resumo, contas,
              reabrir, resultado))
    original = sys.stdout
    sys.stdout = saida = SaidaComRotulos(original)
    definir_rotulo(principal)
    fechado = False
    try:
        trabalhador.start()
        try:
            proximos[principal] = processar_rede_com_reabertura(
                conn, contexto, rodada_id, cfg, principal, listas[principal], proximos[principal], bloqueadas,
                resumo, contas,
                nova_pagina=lambda primeira: (contexto.pages[0] if primeira and contexto.pages
                                              else contexto.new_page()),
                vivo=lambda: navegador_vivo(contexto), reabrir=reabrir)
            if trabalhador.is_alive():
                print(f"Lista do {principal} terminada; esperando o {outra} terminar.", flush=True)
        except NavegadorFechado as erro:
            proximos[principal] = getattr(erro, "proximo", proximos[principal])
            fechado = True
        # Espera em pedaços curtos para o Ctrl+C (Parar) continuar funcionando.
        limite = time.monotonic() + 15 if fechado else None
        while trabalhador.is_alive() and (limite is None or time.monotonic() < limite):
            trabalhador.join(0.5)
    except ParadaPorAviso:
        PARADA.set()                   # aviso nesta rede: a outra para na próxima espera
        trabalhador.join(15)
        raise
    except KeyboardInterrupt:
        PARADA.set()                   # a outra rede para na próxima espera
        trabalhador.join(10)
        if "aviso" in resultado:       # foi a outra rede que levou o aviso e pediu para parar
            raise resultado["aviso"] from None
        raise
    finally:
        definir_rotulo(None)
        saida.descarregar()
        sys.stdout = original
    if "aviso" in resultado:
        raise resultado["aviso"]
    if "erro" in resultado:
        print(f"[{outra}] Erro inesperado: {resultado['erro']}. As ações do {outra} pararam nesta conta.")
    proximos[outra] = resultado.get("proximo", proximos[outra])
    if resultado.get("parado") and not fechado:
        raise KeyboardInterrupt
    if fechado or resultado.get("fechado"):
        erro = NavegadorFechado()
        erro.proximos = proximos
        raise erro
    return proximos


def processar_perfis(conn, contexto, rodada_id: int | None, cfg: Config,
                     perfis: list[tuple[str, str]], inicio: int, bloqueadas: set[str],
                     resumo: list[LinhaResumo], contas: dict[str, str] | None = None,
                     pagina=None) -> int:
    """Processa os perfis-alvo a partir de `inicio`. Devolve o índice seguinte ao último.

    `contas` é {rede: "@conta"} das contas logadas que agem neste perfil do
    navegador; uma rede fora dele é pulada. Sem `pagina`, usa a primeira aba.
    """
    logadas = redes_logadas(contexto)
    if pagina is None:
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
    pagina.bring_to_front()
    for indice in range(inicio, len(perfis)):
        rede, perfil = perfis[indice]
        if indice > inicio or inicio > 0:
            pausa(cfg.protecao.pausa_entre_perfis)
        if rede not in logadas:
            print(f"[{rede}] Não está logado. Faça o login antes (botão de login da interface "
                  f"ou python -m automacao.abrir_redes). Pulando @{perfil}.")
            continue
        if contas is not None and rede not in contas:
            continue  # conta não identificada neste perfil (já avisado)
        if rede in bloqueadas:
            print(f"[{rede}] Pulando @{perfil}: as ações desta conta pararam nesta execução.")
            continue
        conta = contas.get(rede) if contas else None
        if not cfg.simular:
            pausa_vigente = banco.pausa_ativa(conn, rede, conta)
            if pausa_vigente is not None:
                print(f"[{rede}] {conta or 'Conta'} em pausa até {pausa_vigente['fim'][:16].replace('T', ' ')} "
                      f"({pausa_vigente['motivo']}). Pulando as ações desta rede.")
                bloqueadas.add(rede)
                continue
        try:
            MODULOS[rede].processar_perfil(conn, pagina, rodada_id, cfg, perfil, resumo, conta=conta)
        except LimiteDiario as erro:
            print(f"[{rede}] Parando as ações desta conta hoje: {erro}")
            bloqueadas.add(rede)
        except Bloqueio as erro:
            # Qualquer aviso de bloqueio para TUDO, em qualquer modo (inclusive o boost):
            # a conta fica em pausa nesta rede e a automação encerra, sem recomeçar a lista.
            print(f"[{rede}] A rede avisou: {erro}")
            if not cfg.simular:
                print(f"[{rede}] {registrar_aviso(conn, cfg.protecao, rede, conta, str(erro))}. "
                      "Resolva qualquer verificação à mão no app ou no navegador.")
            bloqueadas.add(rede)
            raise ParadaPorAviso(f"{rede} ({conta or 'conta'}): {erro}") from erro
        except NavegadorFechado as erro:
            erro.proximo = indice  # recomeça por este perfil-alvo
            raise
        except PlaywrightError as erro:
            if pagina.is_closed():
                raise NavegadorFechado from erro
            print(f"[{rede}] Erro inesperado em @{perfil}: {erro.message.splitlines()[0]}. "
                  "Seguindo para o próximo perfil.")
        except Exception as erro:  # noqa: BLE001 - um perfil com problema não para os outros
            print(f"[{rede}] Erro inesperado em @{perfil}: {erro}. Seguindo para o próximo perfil.")
    return len(perfis)


def salvar_trace(contexto, ligado: bool) -> None:
    if not ligado:
        return
    PASTA_TRACES.mkdir(exist_ok=True)
    caminho = PASTA_TRACES / f"interagir-{datetime.now():%Y%m%d-%H%M%S}.zip"
    try:
        contexto.tracing.stop(path=str(caminho))
        print(f"\nTrace salvo. Para ver: \"{sys.executable}\" -m playwright show-trace \"{caminho}\"")
    except PlaywrightError:
        pass  # navegador já fechado ou trace já salvo


def fechar_contexto(contexto) -> None:
    try:
        contexto.close()
    except Exception:  # noqa: BLE001 - navegador ou conexão com o driver já encerrados
        pass


if __name__ == "__main__":
    sys.exit(main())
