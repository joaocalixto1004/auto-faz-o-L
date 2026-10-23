#!/usr/bin/env python3
"""Abre TikTok e Instagram em abas do Google Chrome e espera o login.

Na primeira execução, faça o login manualmente em cada aba. A sessão fica
salva na pasta perfil_navegador/, então nas próximas execuções as abas já
abrem logadas.

Para cada rede logada, o script também lê da página qual é a conta e mostra
uma linha como "[Instagram] Conta: @usuario". A conta fica gravada no banco
junto com o status do login.

Se a janela do navegador for fechada, ele reabre sozinho; a sessão só termina
pelo botão Parar da interface (ou Ctrl+C / Enter no terminal).

Uso (na raiz do projeto, com o Python do .venv):
    python -m automacao.abrir_redes [--timeout MINUTOS] [--sem-reabrir]
"""

import argparse
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from automacao import banco
from automacao.caminhos import PASTA_PERFIL
from automacao.terminal import preparar_console, vigiar_pedido_de_parada

INTERVALO_CHECAGEM = 2  # segundos entre cada verificação de login
INTERVALO_CONTA = 5  # segundos entre as tentativas de ler o nome da conta
TIMEOUT_NAVEGACAO = 60_000  # ms


# Funções JavaScript que leem a conta logada em cada rede. Devolvem o texto
# bruto (o href do link do perfil) ou null, e nunca
# levantam erro; extrair_conta() confere e converte o resultado. Os seletores
# dependem do layout de cada rede e podem mudar sem aviso.
JS_CONTA_INSTAGRAM = """() => {
    try {
        // Link "Perfil" da barra lateral: tem a foto do perfil e fica à esquerda.
        const fotos = document.querySelectorAll(
            'img[alt^="Foto do perfil de"], img[alt$="profile picture"]');
        for (const foto of fotos) {
            const link = foto.closest('a[href]');
            if (!link) continue;
            const caixa = link.getBoundingClientRect();
            if (caixa.width > 0 && caixa.height > 0 && caixa.x < 150) return link.getAttribute('href');
        }
    } catch (e) {}
    return null;
}"""

JS_CONTA_TIKTOK = """() => {
    try {
        const item = document.querySelector('[data-e2e="nav-profile"]');
        if (!item) return null;
        const link = item.matches('a[href]') ? item
            : (item.closest('a[href]') || item.querySelector('a[href]'));
        return link ? link.getAttribute('href') : null;
    } catch (e) { return null; }
}"""

RE_NOME_CONTA = re.compile(r"[A-Za-z0-9._]{1,30}")
# Primeiro trecho de links que não são de perfil (por garantia, se o seletor pegar o link errado).
CAMINHOS_QUE_NAO_SAO_CONTA = {
    "accounts", "compose", "direct", "explore", "home", "i", "login", "messages",
    "notifications", "p", "reel", "reels", "search", "settings", "stories",
}


@dataclass(frozen=True)
class Rede:
    nome: str
    url_inicial: str
    url_login: str
    dominio: str
    cookie_sessao: str  # cookie que só existe quando a conta está logada
    dica_login: str = "faça login manualmente na aba."
    # JavaScript que lê a conta logada (ver extrair_conta).
    js_conta: str | None = None


REDES = (
    Rede("TikTok", "https://www.tiktok.com/", "https://www.tiktok.com/login",
         "tiktok.com", cookie_sessao="sessionid", js_conta=JS_CONTA_TIKTOK),
    Rede("Instagram", "https://www.instagram.com/", "https://www.instagram.com/accounts/login/",
         "instagram.com", cookie_sessao="sessionid", js_conta=JS_CONTA_INSTAGRAM),
)


class NavegadorFechado(Exception):
    """O usuário fechou a janela do navegador."""


def abrir_navegador(p: Playwright, pasta_perfil: Path = PASTA_PERFIL,
                    headless: bool = False) -> BrowserContext:
    """Abre o Chrome com um perfil persistente (cookies e sessões ficam salvos)."""
    pasta_perfil.mkdir(parents=True, exist_ok=True)
    opcoes = dict(
        user_data_dir=str(pasta_perfil),
        headless=headless,
        no_viewport=True,
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        ignore_default_args=["--enable-automation"],
    )
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **opcoes)
    except PlaywrightError as erro:
        print(f"Não consegui abrir o Google Chrome ({erro.message.splitlines()[0]}).")
        print("Tentando o Chromium do Playwright...")
        return p.chromium.launch_persistent_context(**opcoes)


def garantir_aberto(contexto: BrowserContext) -> None:
    """Levanta NavegadorFechado se a janela do navegador foi fechada."""
    try:
        contexto.cookies()
    except PlaywrightError as erro:
        raise NavegadorFechado from erro


def redes_logadas(contexto: BrowserContext) -> set[str]:
    """Retorna o nome das redes logadas no navegador, pelos cookies de sessão."""
    try:
        cookies = contexto.cookies()
    except PlaywrightError as erro:
        raise NavegadorFechado from erro

    logadas = set()
    for rede in REDES:
        for cookie in cookies:
            dominio = cookie.get("domain", "").lstrip(".")
            mesmo_dominio = dominio == rede.dominio or dominio.endswith("." + rede.dominio)
            if mesmo_dominio and cookie.get("name") == rede.cookie_sessao and cookie.get("value"):
                logadas.add(rede.nome)
                break
    return logadas


def abas_da_rede(contexto: BrowserContext, rede: Rede) -> list[Page]:
    """Abas abertas no domínio da rede (ou num subdomínio dele)."""
    abas = []
    for pagina in contexto.pages:
        host = urlparse(pagina.url).hostname or ""
        if host == rede.dominio or host.endswith("." + rede.dominio):
            abas.append(pagina)
    return abas


def extrair_conta(rede: str, bruto: object) -> str | None:
    """Converte o link de perfil lido na página em "@usuario". None se não parecer uma conta."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    texto = bruto.strip()

    partes = [parte for parte in urlparse(texto).path.split("/") if parte]
    if not partes:
        return None
    nome = partes[0]
    if rede == "TikTok":
        if not nome.startswith("@"):
            return None
        nome = nome[1:]
    if nome.lower() in CAMINHOS_QUE_NAO_SAO_CONTA or not RE_NOME_CONTA.fullmatch(nome):
        return None
    return "@" + nome.lower()  # usuários do Instagram e do TikTok não diferenciam maiúsculas


def ler_conta(contexto: BrowserContext, rede: Rede) -> str | None:
    """Conta logada na rede, lida de uma aba aberta nela. None se não deu para ler.

    Nunca levanta erro: um seletor que mudou não pode atrapalhar o login.
    """
    if not rede.js_conta:
        return None
    try:
        for pagina in abas_da_rede(contexto, rede):
            try:
                conta = extrair_conta(rede.nome, pagina.evaluate(rede.js_conta))
            except Exception:  # noqa: BLE001 - aba recarregando, fechada etc.
                continue
            if conta:
                return conta
    except Exception:  # noqa: BLE001
        return None
    return None


class LeitorDeContas:
    """Lê, mostra e grava a conta de cada rede logada, uma vez por execução.

    Logo depois do login a página pode ainda não mostrar o perfil (a tela
    "Salvar suas informações de login?" do Instagram, por exemplo). Por isso
    `tentar` pode ser chamado a cada checagem: as redes ainda sem conta são
    lidas de novo a cada INTERVALO_CONTA segundos. Para cada conta lida, imprime
    "[Rede] Conta: @usuario" e chama `ao_ler(rede, conta)`.
    """

    def __init__(self, contexto: BrowserContext,
                 ao_ler: Callable[[str, str], None] | None = None) -> None:
        self.contexto = contexto
        self.ao_ler = ao_ler
        self.contas: dict[str, str] = {}
        self._proxima: dict[str, float] = {}

    def tentar(self, logadas: Iterable[str]) -> None:
        logadas = set(logadas)
        for rede in REDES:
            if rede.nome not in logadas or rede.nome in self.contas:
                continue
            agora = time.monotonic()
            if agora < self._proxima.get(rede.nome, 0):
                continue
            self._proxima[rede.nome] = agora + INTERVALO_CONTA
            conta = ler_conta(self.contexto, rede)
            if not conta:
                continue
            self.contas[rede.nome] = conta
            print(f"[{rede.nome}] Conta: {conta}")
            if self.ao_ler:
                try:
                    self.ao_ler(rede.nome, conta)
                except Exception as erro:  # noqa: BLE001 - não atrapalha o login
                    print(f"[{rede.nome}] Aviso: não consegui gravar a conta no banco ({erro}).")


def navegar(pagina: Page, url: str, nome: str) -> None:
    try:
        pagina.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_NAVEGACAO)
    except PlaywrightError as erro:
        if pagina.is_closed():
            raise NavegadorFechado from erro
        print(f"[{nome}] Aviso: a página não carregou direito ({erro.message.splitlines()[0]}). "
              "Recarregue a aba se ela ficar em branco.")


def abrir_abas(contexto: BrowserContext) -> set[str]:
    """Abre uma aba por rede: página inicial se já logado, tela de login se não.

    Retorna as redes que já estavam logadas ao abrir.
    """
    logadas = redes_logadas(contexto)
    paginas = [contexto.pages[0] if contexto.pages else contexto.new_page()]
    paginas += [contexto.new_page() for _ in REDES[1:]]

    for rede, pagina in zip(REDES, paginas):
        if rede.nome in logadas:
            navegar(pagina, rede.url_inicial, rede.nome)
        else:
            navegar(pagina, rede.url_login, rede.nome)

        if rede.nome in logadas:
            print(f"[{rede.nome}] Já logado ✔")
        else:
            print(f"[{rede.nome}] Não logado — {rede.dica_login}")

    # Traz para a frente a primeira aba que ainda precisa de login.
    pendentes = [pag for rede, pag in zip(REDES, paginas) if rede.nome not in logadas]
    (pendentes or paginas)[0].bring_to_front()
    return logadas


def esperar_login(contexto: BrowserContext, timeout_min: float, iniciais: set[str],
                  ao_detectar: Callable[[str], None] | None = None,
                  a_cada_checagem: Callable[[set[str]], None] | None = None) -> set[str]:
    """Verifica o login a cada poucos segundos até todas as redes estarem logadas.

    `iniciais` são as redes que já estavam logadas ao abrir as abas. Qualquer
    login feito depois disso, inclusive durante o carregamento das abas, é
    informado e repassado para `ao_detectar`. `a_cada_checagem` recebe as redes
    logadas depois de cada checagem.
    """
    logadas = set(iniciais)
    if len(logadas) < len(REDES):
        print(f"\nAguardando login (até {timeout_min:g} min)...")

    limite = time.monotonic() + timeout_min * 60
    while True:
        agora = redes_logadas(contexto)
        for nome in [r.nome for r in REDES if r.nome in agora - logadas]:
            print(f"[{nome}] Login detectado ✔")
            if ao_detectar:
                ao_detectar(nome)
        logadas = agora
        if a_cada_checagem:
            a_cada_checagem(logadas)
        if len(logadas) == len(REDES) or time.monotonic() >= limite:
            return logadas
        time.sleep(INTERVALO_CHECAGEM)


def aguardar_fechamento(contexto: BrowserContext,
                        a_cada_volta: Callable[[], None] | None = None) -> None:
    """Mantém o navegador aberto até o usuário apertar Enter ou fechar a janela.

    `a_cada_volta` é chamada a cada segundo enquanto espera.
    """
    enter = threading.Event()
    if sys.stdin.isatty():
        def ler_enter() -> None:
            try:
                input()
            except EOFError:
                return
            enter.set()

        threading.Thread(target=ler_enter, daemon=True).start()
        print("\nPressione Enter para encerrar. Se a janela do navegador for fechada, ela reabre.")
    else:
        print("\nO navegador fica aberto até você clicar em Parar na interface "
              "(se a janela for fechada, ela reabre).")

    while not enter.is_set():
        time.sleep(1)
        garantir_aberto(contexto)
        if a_cada_volta:
            a_cada_volta()


ESPERA_PARA_REABRIR_S = 3
MAXIMO_FALHAS_AO_ABRIR = 5


def navegador_vivo(contexto: BrowserContext) -> bool:
    try:
        contexto.cookies()
        return True
    except Exception:  # noqa: BLE001 - qualquer falha aqui significa navegador fechado
        return False


def abrir_com_insistencia(p: Playwright, pasta_perfil: Path | None = None) -> BrowserContext:
    """Abre o navegador no perfil; se a pasta ainda estiver sendo liberada, tenta de novo."""
    for tentativa in range(1, MAXIMO_FALHAS_AO_ABRIR + 1):
        try:
            return abrir_navegador(p, pasta_perfil or PASTA_PERFIL)
        except PlaywrightError as erro:
            if tentativa == MAXIMO_FALHAS_AO_ABRIR:
                raise
            print(f"Não consegui abrir o navegador ({erro.message.splitlines()[0]}). "
                  f"Tentando de novo em {ESPERA_PARA_REABRIR_S} s...")
            time.sleep(ESPERA_PARA_REABRIR_S)
    raise AssertionError("inalcançável")


def main() -> int:
    preparar_console()
    vigiar_pedido_de_parada()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=15,
                        help="minutos para esperar o login manual (padrão: 15)")
    parser.add_argument("--sem-reabrir", action="store_true",
                        help="encerra quando a janela do navegador for fechada "
                             "(padrão: reabre e só para pelo Parar da interface ou Ctrl+C)")
    parser.add_argument("--perfil", default=str(banco.PERFIL_PRINCIPAL), metavar="NOME_OU_ID",
                        help="perfil do navegador (uma conta de cada rede) em que fazer login "
                             "(padrão: o Principal)")
    args = parser.parse_args()

    with closing(banco.conectar()) as conn:
        perfil = banco.obter_perfil(conn, args.perfil)
        if perfil is None:
            parser.error(f"perfil {args.perfil!r} não encontrado (veja a aba Contas da interface)")
        pasta_perfil = banco.pasta_do_perfil(perfil)
        print(f"Perfil: {perfil['nome']}", flush=True)
        execucao = banco.iniciar_execucao(conn, perfil["id"])
        motivo = "erro"
        try:
            with sync_playwright() as p:
                while True:
                    contexto = abrir_com_insistencia(p, pasta_perfil)
                    try:
                        iniciais = abrir_abas(contexto)
                        for rede in REDES:
                            status = "ja_logado" if rede.nome in iniciais else "nao_logado"
                            banco.registrar_login(conn, execucao, rede.nome, status)

                        contas = LeitorDeContas(contexto, ao_ler=lambda nome, conta: banco.registrar_conta(
                            conn, execucao, nome, conta))
                        contas.tentar(iniciais)
                        logadas = esperar_login(
                            contexto, args.timeout, iniciais,
                            ao_detectar=lambda nome: banco.registrar_login(
                                conn, execucao, nome, "login_detectado"),
                            a_cada_checagem=contas.tentar,
                        )

                        print("\n" + "  ".join(
                            f"{'✔' if r.nome in logadas else '✘'} {r.nome}" for r in REDES))
                        if len(logadas) < len(REDES):
                            print("Tempo esgotado: ainda falta login nas redes marcadas com ✘.")

                        vistas = {"logadas": set(logadas)}

                        def acompanhar(vistas=vistas, contas=contas) -> None:
                            # Login feito depois do prazo também é registrado e lido.
                            atuais = redes_logadas(contexto)
                            for nome in [r.nome for r in REDES if r.nome in atuais - vistas["logadas"]]:
                                print(f"[{nome}] Login detectado ✔", flush=True)
                                banco.registrar_login(conn, execucao, nome, "login_detectado")
                            vistas["logadas"] = atuais
                            contas.tentar(atuais)

                        aguardar_fechamento(contexto, a_cada_volta=acompanhar)
                        motivo = "enter"
                        break
                    except (NavegadorFechado, PlaywrightError) as erro:
                        if isinstance(erro, PlaywrightError) and navegador_vivo(contexto):
                            raise  # erro de verdade, não janela fechada
                        if args.sem_reabrir:
                            motivo = "janela_fechada"
                            print("\nNavegador fechado.")
                            break
                        # A sessão só termina pelo Parar da interface (ou Ctrl+C).
                        print(f"\nA janela do navegador foi fechada. Reabrindo em {ESPERA_PARA_REABRIR_S} s "
                              "(para encerrar, use Parar na interface ou Ctrl+C)...", flush=True)
                        time.sleep(ESPERA_PARA_REABRIR_S)
                    finally:
                        try:
                            contexto.close()  # grava a sessão (logins) no perfil
                        except Exception:  # noqa: BLE001 - conexão com o driver já encerrada
                            pass
        except KeyboardInterrupt:
            motivo = "interrompido"
            print("\nInterrompido.")
        finally:
            banco.finalizar_execucao(conn, execucao, motivo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
