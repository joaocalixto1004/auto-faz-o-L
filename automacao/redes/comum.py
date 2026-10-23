"""Peças compartilhadas pelas automações do Instagram e do TikTok.

Inclui o ritmo "de pessoa": o mouse anda até o botão antes de clicar, o texto
é digitado letra a letra com velocidade variável, há pausas aleatórias de
leitura entre as ações e os vídeos são assistidos até o fim.
"""

import json
import random
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from automacao import banco
from automacao.abrir_redes import NavegadorFechado
from automacao.caminhos import PASTA_CAPTURAS
from automacao.terminal import dormir
from automacao.redes.protecao import (PROTECAO_NORMAL, Bloqueio, LimiteDiario, Protecao, PularAcao,
                                      controlar_ritmo, tipo_da_acao)

# Pausas aleatórias, em segundos (mínimo, máximo).
# Valores do modo normal (os do modo anti-bloqueio ficam em protecao.py). As
# funções usam cfg.protecao; estes nomes ficam para quem lê e para os testes.
PAUSA_LEITURA = PROTECAO_NORMAL.pausa_leitura                  # depois de abrir um vídeo
PAUSA_FOTO = PROTECAO_NORMAL.pausa_foto                        # olhando uma foto antes das ações
PAUSA_ITEM_CARROSSEL = PROTECAO_NORMAL.pausa_item_carrossel    # em cada item de um carrossel
PAUSA_ENTRE_ACOES = PROTECAO_NORMAL.pausa_entre_acoes
PAUSA_ENTRE_PUBLICACOES = PROTECAO_NORMAL.pausa_entre_publicacoes
PAUSA_ENTRE_PERFIS = PROTECAO_NORMAL.pausa_entre_perfis
PAUSA_ENTRE_CONTAS = PROTECAO_NORMAL.pausa_entre_contas        # ao trocar de perfil do navegador
DIGITACAO_POR_LETRA = (0.07, 0.25)

MAXIMO_NA_GRADE = 60          # no modo tempo, nunca olha mais que isso por aba
VIDEO_ESPERA_MAXIMA_S = 20 * 60


@dataclass(frozen=True)
class Config:
    modo: str              # "quantidade" ou "tempo"
    quantidade: int
    semanas: int
    comentario: str
    compartilhar_com: str
    limite_diario: int
    simular: bool
    destinatarios: dict[str, list[str]] = field(default_factory=dict)  # {rede: [usuários]}
    comentarios: tuple[str, ...] = ()   # opções sorteadas; vazio = só `comentario`
    # Modo teste: procurar um comentário seu antes de comentar (em produção, comenta direto).
    verificar_comentarios: bool = False
    # Redes em que a automação age (configuração "redes": só Instagram, só TikTok ou as duas).
    redes: tuple[str, ...] = ("Instagram", "TikTok")
    # Ritmo e limites: normal ou anti-bloqueio (configuração "anti_bloqueio").
    protecao: Protecao = PROTECAO_NORMAL
    # Ações extras no perfil-alvo (configurações "seguir" e "ver_stories").
    seguir: bool = False
    ver_stories: bool = False

    @property
    def corte(self) -> datetime | None:
        """Data mínima de publicação no modo tempo."""
        if self.modo != "tempo":
            return None
        return datetime.now(timezone.utc) - timedelta(weeks=self.semanas)

    def descricao(self) -> str:
        if self.modo == "tempo":
            return f"publicações das últimas {self.semanas} semana(s)"
        return f"as {self.quantidade} primeiras publicações de cada aba"


@dataclass(frozen=True)
class Publicacao:
    rede: str
    perfil: str
    aba: str
    posicao: int                 # posição na grade da aba (1 = primeira)
    codigo: str
    url: str
    data: datetime | None = None  # data de publicação


@dataclass
class Resultado:
    status: str  # feito, ja_estava_feito ou simulado
    detalhe: str | None = None
    link: str | None = None


@dataclass
class LinhaResumo:
    publicacao: Publicacao
    acoes: dict[str, str] = field(default_factory=dict)


class NaoConfirmado(Exception):
    """O script clicou, mas não conseguiu confirmar o resultado na tela."""


def comentarios_da_config(salvas: dict[str, str]) -> list[str]:
    """Lista de comentários salva pela configuração (com o campo antigo como reserva)."""
    try:
        lista = json.loads(salvas.get("comentarios") or "[]")
    except json.JSONDecodeError:
        lista = []
    lista = [str(c).strip() for c in lista if str(c).strip()] if isinstance(lista, list) else []
    return lista or [salvas.get("comentario", "Boa!!!")]


_sorteio = random.SystemRandom()      # usa a aleatoriedade do sistema operacional
_ultimo_comentario: str | None = None


_textos_evitar: set[str] = set()  # comentários usados recentemente pela conta (anti-bloqueio)


def sortear_comentario(cfg: "Config") -> str:
    """Sorteia um comentário da lista, sem repetir o da publicação anterior.

    No modo anti-bloqueio, também não usa os textos que a conta comentou nos
    últimos dias; se todos já foram usados, o comentário fica para depois.
    """
    global _ultimo_comentario
    opcoes = [c for c in dict.fromkeys(cfg.comentarios or [cfg.comentario]) if c not in _textos_evitar]
    if not opcoes:
        raise PularAcao("todos os comentários da lista já foram usados por esta conta nos últimos "
                        f"{cfg.protecao.comentario_repetido_dias} dias; cadastre mais variações")
    if len(opcoes) > 1 and _ultimo_comentario in opcoes:
        opcoes.remove(_ultimo_comentario)
    _ultimo_comentario = _sorteio.choice(opcoes)
    return _ultimo_comentario


# Uma ação recebe a aba do navegador, a configuração e a publicação.
Acao = Callable[[Page, Config, Publicacao], Resultado]


# ------------------------------------------------------------ ritmo humano

def pausa(intervalo: tuple[float, float] = PAUSA_ENTRE_ACOES) -> None:
    """Espera um tempo aleatório dentro do intervalo."""
    dormir(random.uniform(*intervalo))


def clicar(pg: Page, alvo: Locator, timeout: int = 10_000,
           conferir_antes: Callable[[], None] | None = None) -> None:
    """Leva o mouse até um ponto aleatório do elemento, em trajetória, e clica.

    `conferir_antes` roda com o mouse já parado sobre o elemento, imediatamente
    antes do clique; se levantar erro, o clique não acontece. Serve para
    reconferir o estado de botões de liga/desliga (curtir, favoritar...).
    """
    alvo.wait_for(state="visible", timeout=timeout)
    alvo.scroll_into_view_if_needed(timeout=timeout)
    caixa = alvo.bounding_box()
    if not caixa:
        # Elemento sem posição (sendo redesenhado): mesmo assim, reconfere antes.
        if conferir_antes:
            conferir_antes()
        alvo.click(timeout=timeout)
        return
    rx = caixa["width"] * random.uniform(0.35, 0.65)
    ry = caixa["height"] * random.uniform(0.35, 0.65)
    pg.mouse.move(caixa["x"] + rx, caixa["y"] + ry, steps=random.randint(12, 28))
    time.sleep(random.uniform(0.2, 0.7))
    if conferir_antes:
        conferir_antes()
    alvo.click(position={"x": rx, "y": ry}, delay=random.randint(50, 120), timeout=timeout)


def digitar(pg: Page, texto: str) -> None:
    """Digita letra a letra, com intervalo variável, no campo que estiver em foco."""
    for letra in texto:
        pg.keyboard.type(letra)
        time.sleep(random.uniform(*DIGITACAO_POR_LETRA))
    time.sleep(random.uniform(0.4, 1.0))


def rolar(pg: Page, passos: tuple[int, int] = (3, 6)) -> None:
    """Rola a página para baixo com a roda do mouse, em pequenos passos."""
    largura, altura = pg.evaluate("() => [innerWidth, innerHeight]")
    pg.mouse.move(largura * random.uniform(0.4, 0.6), altura * random.uniform(0.4, 0.6),
                  steps=random.randint(5, 12))
    for _ in range(random.randint(*passos)):
        pg.mouse.wheel(0, random.randint(250, 600))
        time.sleep(random.uniform(0.3, 0.9))


def esperar_ate(condicao: Callable[[], bool], timeout_s: float, intervalo: float = 0.4) -> bool:
    """Repete `condicao` até ela ser verdadeira ou o tempo acabar."""
    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        try:
            if condicao():
                return True
        except PlaywrightError:
            pass
        time.sleep(intervalo)
    return False


# Guarda o texto que a própria página copiar (botão "Copiar link"), sem depender
# de a janela estar em foco para ler a área de transferência depois.
JS_ESCUTAR_COPIA = """() => {
    window.__ultimoCopiado = null;
    if (navigator.clipboard && !navigator.clipboard.__escutado) {
        const original = navigator.clipboard.writeText.bind(navigator.clipboard);
        navigator.clipboard.writeText = t => { window.__ultimoCopiado = String(t); return original(t); };
        navigator.clipboard.__escutado = true;
    }
    if (!window.__escutaCopy) {
        document.addEventListener('copy', e => {
            const s = (e.clipboardData && e.clipboardData.getData('text/plain')) ||
                      String(document.getSelection() || '');
            if (s) window.__ultimoCopiado = s;
        }, true);
        window.__escutaCopy = true;
    }
}"""


def preparar_copia(pagina: Page) -> None:
    """Chamar antes de clicar em "Copiar link"."""
    try:
        pagina.evaluate(JS_ESCUTAR_COPIA)
    except PlaywrightError:
        pass


def ler_clipboard(pagina: Page) -> str | None:
    """O texto copiado: pela escuta na página, pela API do navegador ou pelo sistema."""
    pagina.wait_for_timeout(700)
    try:
        texto = pagina.evaluate("() => window.__ultimoCopiado")
        if texto and texto.strip():
            return texto.strip()
    except PlaywrightError:
        pass
    try:
        pagina.bring_to_front()
        texto = pagina.evaluate("() => navigator.clipboard.readText()")
        if texto and texto.strip():
            return texto.strip()
    except PlaywrightError:
        pass
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: sem piscar console
    for comando in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"],
                    ["powershell", "-NoProfile", "-Command",
                     "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard"]):
        if shutil.which(comando[0]):
            try:
                saida = subprocess.run(comando, capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=5, creationflags=sem_janela)
                if saida.returncode == 0 and saida.stdout.strip():
                    return saida.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
    return None


# ------------------------------------------------------ cliques que mudam algo

_ao_clicar: Callable[[], None] | None = None


def antes_de_clicar() -> None:
    """Chamar imediatamente antes de um clique que muda algo na rede.

    Grava a ação como "nao_confirmado" no banco antes do clique. Assim, se a
    execução cair entre o clique e a confirmação (Ctrl+C, janela fechada...),
    a ação já conta como feita e nunca é repetida. No TikTok, também confere
    que o vídeo na tela ainda é o certo.
    """
    if _ao_clicar:
        _ao_clicar()


# ------------------------------------------------------------------ vídeos

# Um vídeo só conta como visível se pelo menos metade dele aparece na tela,
# descontando o que fica cortado por contêineres (itens escondidos de carrossel).
JS_VISIVEL = """v => {
    const r = v.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    let l = r.left, t = r.top, d = r.right, b = r.bottom;
    for (let e = v.parentElement; e; e = e.parentElement) {
        const s = getComputedStyle(e);
        if (s.overflowX !== 'visible' || s.overflowY !== 'visible') {
            const q = e.getBoundingClientRect();
            l = Math.max(l, q.left); t = Math.max(t, q.top); d = Math.min(d, q.right); b = Math.min(b, q.bottom);
        }
    }
    l = Math.max(l, 0); t = Math.max(t, 0); d = Math.min(d, innerWidth); b = Math.min(b, innerHeight);
    return Math.max(0, d - l) * Math.max(0, b - t) > 0.5 * r.width * r.height;
}"""

JS_MONITORAR_VIDEO = """(token) => {
    const visivel = """ + JS_VISIVEL + """;
    const videos = [...document.querySelectorAll('video')].filter(visivel)
        .sort((a, b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height);
    const v = videos[0];
    if (!v) return null;
    window.__videos = window.__videos || {};
    const est = window.__videos[token] = {terminou: false, ultimo: v.currentTime, duracao: v.duration || 0, v};
    v.addEventListener('ended', () => { est.terminou = true; });
    v.addEventListener('timeupdate', () => {
        if (v.duration) est.duracao = v.duration;
        if (v.duration && v.currentTime >= v.duration - 0.4) est.terminou = true;
        if (v.currentTime + 1 < est.ultimo) est.terminou = true;   // recomeçou: vídeo em loop
        est.ultimo = v.currentTime;
    });
    return {duracao: v.duration || 0, tempo: v.currentTime};
}"""

JS_ESTADO_VIDEO = """(token) => {
    const est = (window.__videos || {})[token];
    if (!est) return null;
    const v = est.v;
    if (!v.isConnected) return {terminou: true, motivo: 'o vídeo saiu da tela'};
    if (v.paused && !est.terminou) {          // retoma se pausou sozinho
        v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });
    }
    return {terminou: est.terminou, tempo: v.currentTime, duracao: est.duracao};
}"""


def monitorar_video(pg: Page, token: str) -> dict | None:
    """Começa a acompanhar o vídeo visível. None se a publicação não for vídeo."""
    try:
        return pg.evaluate(JS_MONITORAR_VIDEO, token)
    except PlaywrightError:
        return None


def tem_video_visivel(pg: Page) -> bool:
    try:
        return pg.evaluate(f"() => [...document.querySelectorAll('video')].some({JS_VISIVEL})")
    except PlaywrightError:
        return False


def ver_item(pg: Page, cfg: Config, token: str) -> None:
    """Olha o item que está na tela: assiste até o fim se for vídeo, senão pausa."""
    if monitorar_video(pg, token) and not cfg.simular:
        esperar_fim_do_video(pg, token)
    else:
        pausa(cfg.protecao.pausa_item_carrossel)


def esperar_fim_do_video(pg: Page, token: str) -> None:
    """Só retorna quando o vídeo acompanhado chegar ao fim."""
    try:
        estado = pg.evaluate(JS_ESTADO_VIDEO, token)
    except PlaywrightError:
        return
    if not estado or estado.get("terminou"):
        return
    restante = max(estado["duracao"] - estado["tempo"], 0)
    print(f"    vídeo         aguardando terminar (~{restante:.0f}s)")
    limite = time.monotonic() + min(restante + 30, VIDEO_ESPERA_MAXIMA_S)
    while time.monotonic() < limite:
        time.sleep(1)
        try:
            estado = pg.evaluate(JS_ESTADO_VIDEO, token)
        except PlaywrightError:
            return
        if not estado or estado.get("terminou"):
            return
    print("    vídeo         não terminou no tempo esperado, seguindo")


# ------------------------------------------------------------------- grade

def coletar_da_grade(pg: Page, ler_hrefs: Callable[[], list[str]],
                     extrair: Callable[[str], tuple[str, str] | None],
                     data_de: Callable[[str], datetime | None], cfg: Config,
                     rede: str, perfil: str, aba: str) -> list[Publicacao]:
    """Lê a grade do perfil e devolve as publicações dentro do limite configurado.

    No modo quantidade, pega as N primeiras. No modo tempo, rola a grade até as
    últimas publicações carregadas serem mais antigas que o corte, e fica com as
    que foram publicadas depois dele (posts fixados antigos ficam de fora).
    """
    corte = cfg.corte
    ordem: dict[str, str] = {}
    sem_novidade = 0
    while True:
        for href in ler_hrefs():
            achado = extrair(href)
            if achado and achado[0] not in ordem:
                ordem[achado[0]] = achado[1]
        if cfg.modo == "quantidade" and len(ordem) >= cfg.quantidade:
            break
        if cfg.modo == "tempo" and len(ordem) >= MAXIMO_NA_GRADE:
            break
        if corte is not None:
            datas = [data_de(codigo) for codigo in ordem]
            cauda = [d for d in datas if d][-3:]
            if len(cauda) == 3 and all(d < corte for d in cauda):
                break
        antes = len(ordem)
        rolar(pg)
        pg.wait_for_timeout(1500)
        for href in ler_hrefs():
            achado = extrair(href)
            if achado and achado[0] not in ordem:
                ordem[achado[0]] = achado[1]
        sem_novidade = sem_novidade + 1 if len(ordem) == antes else 0
        if sem_novidade >= 2:
            break

    itens = list(ordem.items())
    if cfg.modo == "tempo":
        itens = itens[:MAXIMO_NA_GRADE]
    publicacoes = []
    for posicao, (codigo, url) in enumerate(itens, start=1):
        data = data_de(codigo)
        if corte is not None and data is not None and data < corte:
            continue
        publicacoes.append(Publicacao(rede, perfil, aba, posicao, codigo, url, data))
        if cfg.modo == "quantidade" and len(publicacoes) == cfg.quantidade:
            break
    return publicacoes


# ------------------------------------------------------------- execução

def capturar_tela(pagina: Page, pub: Publicacao, acao: str) -> Path | None:
    """Salva um print da tela quando uma ação falha, para ajudar a entender o erro."""
    try:
        PASTA_CAPTURAS.mkdir(exist_ok=True)
        # Sem ":" e outros caracteres que o Windows não aceita em nomes de arquivo.
        acao_segura = re.sub(r"[^A-Za-z0-9._-]+", "_", acao)
        nome = f"{datetime.now():%Y%m%d-%H%M%S}-{pub.rede}-{pub.codigo}-{acao_segura}.png"
        caminho = PASTA_CAPTURAS / nome
        pagina.screenshot(path=str(caminho))
        return caminho
    except (PlaywrightError, OSError):
        return None  # sem print; o erro original da ação continua registrado


def _trabalhada_hoje(conn: sqlite3.Connection, rede: str, codigo: str, conta: str | None = None) -> bool:
    """A publicação já teve alguma ação feita pelo script hoje (por esta conta)?"""
    filtro_conta, parametros = "", [rede, codigo]
    if conta:
        filtro_conta = "AND a.conta = ?"
        parametros.append(conta)
    return conn.execute(
        f"""
        SELECT 1 FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
        WHERE p.rede = ? AND p.codigo = ? AND a.status IN ('feito', 'nao_confirmado') {filtro_conta}
          AND substr(a.registrado_em, 1, 10) = date('now', 'localtime')
        LIMIT 1
        """,
        parametros,
    ).fetchone() is not None


def processar_publicacao(conn: sqlite3.Connection, pagina: Page, rodada_id: int | None,
                         cfg: Config, pub: Publicacao, abrir: Callable[[], None],
                         acoes: list[tuple[str, Acao]], resumo: list[LinhaResumo],
                         fechar: Callable[[], None] | None = None,
                         conferir: Callable[[], None] | None = None,
                         ver_midia: Callable[[Page, Config, Publicacao], str] | None = None,
                         conta: str | None = None) -> None:
    """Abre a publicação e faz só as ações que esta conta ainda não concluiu nela.

    O banco evita repetir o que o próprio script já fez. Cada ação ainda
    confere na tela se aquilo já estava feito (inclusive por você, à mão).
    `conferir` roda antes de cada ação e falha se a página trocou de publicação.
    `ver_midia` olha a publicação antes das ações (passa o carrossel até o fim)
    e devolve o tipo: começa com "carrossel", "vídeo" ou "foto".
    `conta` ("@usuario") é a conta logada que age: o registro e o limite diário
    são por conta, então outra conta pode agir na mesma publicação.
    """
    data = f", publicada em {pub.data.astimezone():%d/%m/%Y}" if pub.data else ""
    rotulo = f"[{pub.rede}] @{pub.perfil} {pub.aba} #{pub.posicao} ({pub.codigo}{data})"
    linha = LinhaResumo(pub)
    resumo.append(linha)

    concluidas = banco.acoes_concluidas(conn, pub.rede, pub.codigo, conta)
    pendentes = [(nome, fn) for nome, fn in acoes if nome not in concluidas]
    if not pendentes:
        print(f"{rotulo}: já processada antes, pulando.")
        linha.acoes = {nome: "já registrada" for nome, _ in acoes}
        return
    for nome in concluidas:
        linha.acoes[nome] = "já registrada"

    # O limite conta publicações novas do dia: retomar uma que já foi trabalhada
    # hoje (para refazer uma ação que falhou) não aumenta o total.
    if (not cfg.simular and not _trabalhada_hoje(conn, pub.rede, pub.codigo, conta)
            and banco.publicacoes_trabalhadas_hoje(conn, pub.rede, conta) >= cfg.limite_diario):
        raise LimiteDiario(f"limite diário de {cfg.limite_diario} publicações atingido"
                           + (f" para {conta}" if conta else ""))

    protecao = cfg.protecao
    if (protecao.max_publicacoes_por_alvo_dia and not cfg.simular
            and not _trabalhada_hoje(conn, pub.rede, pub.codigo, conta)
            and banco.publicacoes_do_alvo_hoje(conn, pub.rede, pub.perfil, conta)
            >= protecao.max_publicacoes_por_alvo_dia):
        print(f"{rotulo}: já foram {protecao.max_publicacoes_por_alvo_dia} publicações deste perfil hoje; "
              "fica para amanhã (anti-bloqueio).")
        linha.acoes = {"perfil-alvo": "limite do dia"}
        return

    global _textos_evitar
    _textos_evitar = (banco.textos_comentados(conn, conta, protecao.comentario_repetido_dias, pub.rede)
                      if protecao.comentario_repetido_dias and not cfg.simular else set())

    print(f"{rotulo}: abrindo ({len(pendentes)} ações pendentes)")
    try:
        abrir()
        tipo = ver_midia(pagina, cfg, pub) if ver_midia else "vídeo"
    except PlaywrightError as erro:
        if pagina.is_closed():
            raise NavegadorFechado from erro
        print(f"    não consegui abrir a publicação: {erro.message.splitlines()[0]}")
        linha.acoes["abrir"] = "falhou"
        if fechar:
            try:
                fechar()
            except PlaywrightError:
                pass
        return
    print(f"    {'mídia':<13} {tipo}")
    if tipo.startswith("vídeo"):
        monitorar_video(pagina, pub.codigo)   # assiste enquanto age; espera o fim depois
        pausa(cfg.protecao.pausa_leitura)
    elif tipo.startswith("foto"):
        pausa(cfg.protecao.pausa_foto)
    elif tipo.startswith("perfil"):
        pausa(cfg.protecao.pausa_leitura)  # olhando o perfil antes de seguir
    publicacao_id = None
    if not cfg.simular:
        publicada_em = pub.data.isoformat(timespec="seconds") if pub.data else None
        publicacao_id = banco.registrar_publicacao(conn, pub.rede, pub.codigo, pub.perfil, pub.aba,
                                                   pub.posicao, pub.url, publicada_em)

    global _ao_clicar
    try:
        for nome, fn in pendentes:
            marcado = {"sim": False}

            def marcar(nome: str = nome, marcado: dict = marcado) -> None:
                if conferir:
                    conferir()
                if publicacao_id is not None:
                    banco.registrar_acao(conn, publicacao_id, rodada_id, nome, "nao_confirmado",
                                         "clique enviado, aguardando confirmação", conta=conta,
                                         alvo=pub.perfil)
                marcado["sim"] = True

            _ao_clicar = None if cfg.simular else marcar
            try:
                if not cfg.simular:
                    if (tipo_da_acao(nome) == "comentar" and protecao.comentario_por_alvo_dias
                            and banco.comentou_no_alvo(conn, pub.rede, pub.perfil, conta,
                                                       protecao.comentario_por_alvo_dias)):
                        raise PularAcao(f"esta conta já comentou neste perfil nos últimos "
                                        f"{protecao.comentario_por_alvo_dias} dias")
                    controlar_ritmo(conn, protecao, pub.rede, conta, nome)
                status, detalhe, link = executar_acao(pagina, cfg, pub, nome, fn, conferir)
            except PularAcao as erro:
                status, detalhe, link = "pulado", str(erro), None
            finally:
                _ao_clicar = None
            if status == "falhou" and marcado["sim"]:
                status = "nao_confirmado"  # o clique já foi enviado: nunca repetir
            linha.acoes[nome] = status
            print(f"    {nome:<13} {status}" + (f"  ({detalhe})" if detalhe else ""))
            if status == "pulado":
                continue  # não grava: a ação fica para outro dia
            if publicacao_id is not None and status != "simulado":
                banco.registrar_acao(conn, publicacao_id, rodada_id, nome, status, detalhe, conta=conta,
                                     alvo=pub.perfil)
                if link:
                    banco.salvar_link(conn, publicacao_id, link)
            pausa(protecao.pausa_entre_acoes)
        if not cfg.simular:
            esperar_fim_do_video(pagina, pub.codigo)
    finally:
        if fechar:
            try:
                fechar()
            except PlaywrightError:
                pass


def executar_acao(pagina: Page, cfg: Config, pub: Publicacao, nome: str, fn: Acao,
                  conferir: Callable[[], None] | None = None) -> tuple[str, str | None, str | None]:
    """Roda uma ação e traduz o resultado (ou o erro) em status para o banco."""
    try:
        if conferir:
            conferir()
        resultado = fn(pagina, cfg, pub)
        detalhe = resultado.detalhe
        if resultado.link:
            detalhe = f"{detalhe}; {resultado.link}" if detalhe else resultado.link
        return resultado.status, detalhe, resultado.link
    except NaoConfirmado as erro:
        return "nao_confirmado", str(erro), None
    except PularAcao as erro:
        return "pulado", str(erro), None
    except Bloqueio:
        raise
    except PlaywrightError as erro:
        if pagina.is_closed():
            raise NavegadorFechado from erro
        captura = capturar_tela(pagina, pub, nome)
        mensagem = erro.message.splitlines()[0]
        return "falhou", mensagem + (f" | print: {captura.name}" if captura else ""), None
    except Exception as erro:  # noqa: BLE001 - qualquer erro numa ação não pode parar as outras
        captura = capturar_tela(pagina, pub, nome)
        return "falhou", f"{erro}" + (f" | print: {captura.name}" if captura else ""), None
