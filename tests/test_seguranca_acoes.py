"""Testes da regra de segurança mais importante: nunca desfazer uma ação.

Curtir, repostar, favoritar e seguir são botões de liga/desliga: clicar num que
já está ligado desfaz a ação (inclusive uma feita à mão). Por isso cada ação:

- só clica quando a página mostra, com certeza, que ainda não foi feita;
- reconfere o estado com o mouse já parado sobre o botão, imediatamente antes
  do clique (comum.clicar com conferir_antes), em todos os caminhos;
- grava a marca "nao_confirmado" no banco antes do clique (antes_de_clicar) e,
  se o resultado não aparecer na tela, termina como NaoConfirmado e nunca é
  tentada de novo.

Nenhum navegador é aberto: as páginas são imitações pequenas que guardam o
estado de cada botão, contam os cliques e implementam só os métodos do
Playwright que as funções usam (um seletor desconhecido faz o teste falhar). O
relógio de comum também é falso, então as esperas de confirmação passam na hora.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from automacao import banco
from automacao.redes import comum, instagram, tiktok
from automacao.redes.protecao import Bloqueio

CONTA = "@eu"


def config(**opcoes) -> comum.Config:
    base = dict(modo="quantidade", quantidade=3, semanas=2, comentario="Boa!!!",
                compartilhar_com="fulano", limite_diario=40, simular=False)
    base.update(opcoes)
    return comum.Config(**base)


def acoes_no_banco(conn):
    return [tuple(linha) for linha in conn.execute("SELECT acao, status, detalhe FROM acoes ORDER BY id")]


# ------------------------------------------------------- relógio e acaso falsos

class RelogioFalso:
    """Fica no lugar do módulo time dentro de comum: sleep só avança o relógio."""

    def __init__(self):
        self.agora = 1000.0

    def monotonic(self):
        return self.agora

    def sleep(self, segundos):
        self.agora += segundos


def _sem_efeito(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    """Deixa tudo instantâneo: relógio falso, acaso fixo, sem pausas, vídeos, prints nem ritmo."""
    relogio = RelogioFalso()
    monkeypatch.setattr(comum, "time", relogio)
    monkeypatch.setattr(comum, "random", SimpleNamespace(uniform=lambda a, b: (a + b) / 2,
                                                         randint=lambda a, b: a))
    monkeypatch.setattr(comum, "_ao_clicar", None)
    for nome in ("pausa", "monitorar_video", "esperar_fim_do_video", "capturar_tela", "controlar_ritmo"):
        monkeypatch.setattr(comum, nome, _sem_efeito)
    for nome in ("pausa", "monitorar_video", "controlar_ritmo"):
        monkeypatch.setattr(instagram, nome, _sem_efeito)
    # A aba Reposts da sua conta já foi lida e não tem o post (assim nada navega).
    monkeypatch.setattr(instagram, "_meus_reposts", set())
    return relogio


# ------------------------------------------------------------- página falsa

CAIXA = {"x": 100.0, "y": 200.0, "width": 40.0, "height": 30.0}


@dataclass
class Elemento:
    """Um elemento da página falsa; o que ele mostra vem do estado da página."""
    nome: str
    indice: int = 0
    papel: str | None = None
    texto: str = ""
    filhos: list["Elemento"] = field(default_factory=list)

    @property
    def rotulo(self) -> str:
        return f"{self.nome}[{self.indice}]" if self.indice else self.nome


def _combina(padrao, texto: str) -> bool:
    """Como has_text/name do Playwright: regex procura; texto é um trecho, sem diferenciar maiúsculas."""
    if isinstance(padrao, re.Pattern):
        return padrao.search(texto) is not None
    return padrao.lower() in texto.lower()


class Localizador:
    """Imita o Locator do Playwright: preguiçoso, procura de novo na página a cada uso."""

    def __init__(self, pagina, buscar: Callable[[], list[Elemento]], descricao: str):
        self._pagina = pagina
        self._buscar = buscar
        self._descricao = descricao

    def _derivado(self, buscar, sufixo):
        return Localizador(self._pagina, buscar, f"{self._descricao} >> {sufixo}")

    def _um(self, operacao):
        elementos = self._buscar()
        if not elementos:
            raise PlaywrightTimeout(f"Timeout em {operacao}: nada encontrado para {self._descricao!r}")
        return elementos[0]

    # composição
    @property
    def first(self):
        return self._derivado(lambda: self._buscar()[:1], "first")

    @property
    def last(self):
        return self._derivado(lambda: self._buscar()[-1:], "last")

    def nth(self, indice):
        return self._derivado(lambda: self._buscar()[indice:indice + 1], f"nth={indice}")

    def locator(self, seletor):
        return self._derivado(lambda: self._pagina.buscar(seletor) if self._buscar() else [], seletor)

    def filter(self, has=None, has_text=None):
        def buscar():
            if has is not None and not has.count():
                return []
            elementos = self._buscar()
            if has_text is not None:
                elementos = [e for e in elementos if _combina(has_text, self._pagina.texto(e))]
            return elementos
        return self._derivado(buscar, "filter")

    def get_by_role(self, papel, name=None, **_):
        def buscar():
            return [f for e in self._buscar() for f in e.filhos
                    if f.papel == papel and (name is None or _combina(name, self._pagina.texto(f)))]
        return self._derivado(buscar, f"role={papel}")

    # leitura
    def count(self):
        return len(self._buscar())

    def is_visible(self):
        elementos = self._buscar()
        return bool(elementos) and self._pagina.visivel(elementos[0])

    def get_attribute(self, nome, timeout=None):
        return self._pagina.atributo(self._um("get_attribute"), nome)

    def inner_text(self, timeout=None):
        return self._pagina.texto(self._um("inner_text"))

    def evaluate(self, js, arg=None):
        return self._pagina.avaliar(self._um("evaluate"), js)

    def evaluate_all(self, js):
        return self._pagina.avaliar_todos(self._buscar(), js)

    def bounding_box(self):
        elementos = self._buscar()
        return self._pagina.caixa(elementos[0]) if elementos else None

    # espera e clique
    def wait_for(self, state="visible", timeout=None):
        elementos = self._buscar()
        if not elementos or (state == "visible" and not self._pagina.visivel(elementos[0])):
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded: {self._descricao!r} não ficou {state}")

    def scroll_into_view_if_needed(self, timeout=None):
        self._um("scroll_into_view_if_needed")
        self._pagina.anotar("rolar", self._pagina.ao_rolar)

    def click(self, position=None, delay=None, timeout=None):
        self._pagina.clicar(self._um("click"), position=position, delay=delay, timeout=timeout)


class MouseFalso:
    def __init__(self, pagina):
        self.pagina = pagina

    def move(self, x, y, steps=1):
        self.pagina.anotar("mover", self.pagina.ao_mover)

    def click(self, x, y, **_):
        # Um clique solto na tela também conta: nenhum pode passar despercebido.
        self.pagina.cliques.append(f"mouse({x:.0f}, {y:.0f})")
        self.pagina.eventos.append(("clique", "mouse"))


class PaginaFalsa:
    """Base das páginas falsas: anota eventos, conta cliques e aplica o efeito de cada clique.

    `ao_mover` e `ao_rolar` são ganchos que rodam quando o mouse anda até o botão
    ou a página rola até ele: servem para mudar o estado entre a primeira
    conferência e o clique (corrida). `no_clique` roda no instante do clique.
    """

    def __init__(self, relogio, url, muda_no_clique=True):
        self.relogio = relogio
        self.url = url
        self.muda_no_clique = muda_no_clique   # False: o clique acontece, mas a tela não muda
        self.sem_caixa = False                 # True: bounding_box() devolve None (botão redesenhando)
        self.cliques: list[str] = []
        self.detalhes_cliques: list[dict] = []
        self.eventos: list = []
        self.ao_mover: Callable[[], None] | None = None
        self.ao_rolar: Callable[[], None] | None = None
        self.no_clique: list[Callable[[], None]] = []
        self.mouse = MouseFalso(self)

    def anotar(self, evento, gancho=None):
        self.eventos.append(evento)
        if gancho:
            gancho()

    def locator(self, seletor):
        return Localizador(self, lambda: self.buscar(seletor), seletor)

    def buscar(self, seletor) -> list[Elemento]:
        raise AssertionError(f"a página falsa não conhece o seletor {seletor!r}")

    def evaluate(self, js, arg=None):
        raise AssertionError(f"a página falsa não conhece o script {js[:70]!r}")

    def goto(self, url, **_):
        self.url = url

    def wait_for_timeout(self, ms):
        self.relogio.sleep(ms / 1000)

    def is_closed(self):
        return False

    # o que cada elemento mostra (as subclasses completam)
    def visivel(self, elemento):
        return True

    def caixa(self, elemento):
        return None if self.sem_caixa else dict(CAIXA)

    def texto(self, elemento):
        return elemento.texto

    def atributo(self, elemento, nome):
        raise AssertionError(f"atributo {nome!r} inesperado em {elemento.rotulo!r}")

    def avaliar(self, elemento, js):
        raise AssertionError(f"script inesperado em {elemento.rotulo!r}: {js[:70]!r}")

    def avaliar_todos(self, elementos, js):
        raise AssertionError(f"script inesperado: {js[:70]!r}")

    def clicar(self, elemento, **detalhes):
        for funcao in self.no_clique:
            funcao()
        self.cliques.append(elemento.rotulo)
        self.detalhes_cliques.append(detalhes)
        self.eventos.append(("clique", elemento.rotulo))
        self.efeito_do_clique(elemento)

    def efeito_do_clique(self, elemento):
        pass


# ---------------------------------------------------------- Instagram falso

CODIGO_IG = "ABCdef12345"
URL_POST = f"https://www.instagram.com/p/{CODIGO_IG}/"
URL_PERFIL_IG = "https://www.instagram.com/fulano/"
URL_STORY = "https://www.instagram.com/stories/fulano/3456789012345678901/"
URL_OUTRO_STORY = "https://www.instagram.com/stories/fulano/3456789012345678902/"
CODIGO_STORY = "story:3456789012345678901"
SVG_AVANCAR_STORY = 'svg[aria-label="Avançar"], svg[aria-label="Next"]'
SVG_FECHAR_STORY = 'svg[aria-label="Fechar"], svg[aria-label="Close"]'

# Atributo "d" dos traços do ícone de repostar, juntados como _estado_repost lê.
DESENHO_REPOST = {
    "ativo": instagram.ICONE_REPOST_ATIVO + "H8a1 1 0 0 0 0 2h8Z M4 12a1 1 0 0 0 1 1",
    "inativo": instagram.ICONE_REPOST_INATIVO + "a3 3 0 0 1-3 3H5 M5 8.5a1 1 0 0 0 1-1",
    "estranho": "M0 0h24v24H0z M12 2a10 10 0 1 0 0 20",  # o Instagram mudou o ícone
}


class PaginaInstagram(PaginaFalsa):
    """Post (ou perfil) aberto no Instagram, com a barra de ações e o cabeçalho do perfil."""

    ROTULOS = {instagram.SVG_CURTIR: "Curtir", instagram.SVG_DESCURTIR: "Descurtir",
               instagram.SVG_REPOSTAR: "Repostar", instagram.SVG_COMPARTILHAR: "Compartilhar"}

    def __init__(self, relogio, url=URL_POST, curtido=False, repost="inativo",
                 cabecalho=("Seguir", "Enviar mensagem"), pede_confirmacao=False,
                 depois_de_seguir="Seguindo", **opcoes):
        super().__init__(relogio, url, **opcoes)
        self.curtido = curtido               # True, False ou None (nenhum coração na tela)
        self.repost = repost                 # "ativo", "inativo", "estranho" ou None (sem ícone)
        self.cabecalho = list(cabecalho)     # innerText dos botões do cabeçalho do perfil
        self.pede_confirmacao = pede_confirmacao
        self.depois_de_seguir = depois_de_seguir
        self.dialogos: list[Elemento] = []

    def _svgs(self):
        rotulos = {True: ["Descurtir"], False: ["Curtir"], None: []}[self.curtido] + ["Comentar"]
        if self.repost is not None:
            rotulos.append("Repostar")
        rotulos.append("Compartilhar")
        return [Elemento(rotulo) for rotulo in rotulos]

    def buscar(self, seletor):
        if seletor == "main section":
            return [Elemento("barra de ações")]
        if seletor in self.ROTULOS:
            return [e for e in self._svgs() if e.nome == self.ROTULOS[seletor]]
        if seletor == "svg[aria-label]":
            return self._svgs()
        if seletor == '[role="dialog"]':
            return list(self.dialogos)
        if seletor == instagram.BOTOES_CABECALHO:
            return [Elemento("botão do cabeçalho", i) for i in range(len(self.cabecalho))]
        return super().buscar(seletor)

    def texto(self, elemento):
        if elemento.nome == "botão do cabeçalho":
            return self.cabecalho[elemento.indice]
        return super().texto(elemento)

    def evaluate(self, js, arg=None):
        if js == instagram.JS_BOTOES_CABECALHO:
            return list(self.cabecalho)
        return super().evaluate(js, arg)

    def avaliar(self, elemento, js):
        if elemento.nome == "Repostar" and "querySelectorAll('path')" in js:
            return DESENHO_REPOST[self.repost]
        return super().avaliar(elemento, js)

    def avaliar_todos(self, elementos, js):
        if "getAttribute('aria-label')" in js:
            return [e.nome for e in elementos]
        return super().avaliar_todos(elementos, js)

    def efeito_do_clique(self, elemento):
        if elemento.nome == "Repostar" and self.pede_confirmacao:
            botao = Elemento("confirmar repost", papel="button", texto="Repostar")
            self.dialogos = [Elemento("janela", texto="Repostar esta publicação?", filhos=[botao])]
            return  # o repost só acontece na confirmação
        if elemento.nome == "confirmar repost":
            self.dialogos = []
        if not self.muda_no_clique:
            return
        # Liga e desliga de verdade: um clique a mais desfaria a ação.
        if elemento.nome in ("Curtir", "Descurtir"):
            self.curtido = not self.curtido
        elif elemento.nome in ("Repostar", "confirmar repost"):
            self.repost = "inativo" if self.repost == "ativo" else "ativo"
        elif elemento.nome == "botão do cabeçalho":
            texto = self.cabecalho[elemento.indice]
            if instagram.RE_SEGUIR.match(texto):
                self.cabecalho[elemento.indice] = self.depois_de_seguir
            elif instagram.RE_JA_SEGUE.match(texto):
                self.cabecalho[elemento.indice] = "Seguir"


class PaginaStories(PaginaInstagram):
    """O perfil com stories novos e, depois de clicar na foto, o visualizador de stories."""

    def __init__(self, relogio, url=URL_STORY, curtido=False, coracao_visivel=True, **opcoes):
        super().__init__(relogio, url, curtido=curtido, repost=None, **opcoes)
        self.coracao_visivel = coracao_visivel

    def buscar(self, seletor):
        if seletor == "main header":
            return [Elemento("cabeçalho do perfil")]
        if seletor == "main header img":
            return [Elemento("foto do perfil")]
        if seletor in (SVG_AVANCAR_STORY, SVG_FECHAR_STORY):
            return []
        return super().buscar(seletor)

    def visivel(self, elemento):
        if elemento.nome in ("Curtir", "Descurtir"):
            return self.coracao_visivel
        return True

    def evaluate(self, js, arg=None):
        if js == instagram.JS_ANEL_STORIES:
            return "novos"
        return super().evaluate(js, arg)

    def efeito_do_clique(self, elemento):
        if elemento.nome == "foto do perfil":
            self.url = URL_STORY
            return
        super().efeito_do_clique(elemento)


# ------------------------------------------------------------- TikTok falso

CODIGO_TT = "7699507358707305746"
URL_VIDEO = f"https://www.tiktok.com/@fulano/video/{CODIGO_TT}"
URL_OUTRO_VIDEO = "https://www.tiktok.com/@fulano/video/7573525231633976588"
URL_PERFIL_TT = "https://www.tiktok.com/@fulano"
SELETOR_AVISOS = '[role="alert"], [class*="toast" i]'      # o de tiktok.verificar_aviso
SELETOR_BOTOES_SEGUIR = '[data-e2e="follow-button"]'        # a reserva de tiktok._botao_seguir
AMARELO = "rgb(250, 206, 21)"           # #FACE15: o traço que fica amarelo nos favoritos
BRANCO = "rgba(255, 255, 255, 0.9)"     # cor do texto: fora dos favoritos
AVISO_RAPIDO = "Você está indo rápido demais. Tente novamente mais tarde."


class PaginaTikTok(PaginaFalsa):
    """Vídeo aberto no painel do TikTok (ou o perfil, para seguir)."""

    def __init__(self, relogio, url=URL_VIDEO, curtido="false", cores=(BRANCO, BRANCO),
                 seguir=(("Seguir", 180),), seguir_no_cabecalho=True, **opcoes):
        super().__init__(relogio, url, **opcoes)
        self.aria_pressed = curtido                       # valor do aria-pressed do botão de curtir
        self.cores = list(cores)                          # fill de cada traço do ícone de favoritos
        self.botoes_seguir = [list(b) for b in seguir]    # [texto, y] de cada follow-button, na ordem
        self.seguir_no_cabecalho = seguir_no_cabecalho    # o primeiro fica no cabeçalho do perfil
        self.avisos: list[tuple[str, float, float]] = []  # (texto, aparece, some) no relógio falso
        self.captcha = False

    def aviso(self, texto=AVISO_RAPIDO, daqui=0.0, dura=2.0):
        """Mostra um balão que aparece daqui a `daqui` segundos e some `dura` segundos depois."""
        inicio = self.relogio.agora + daqui
        self.avisos.append((texto, inicio, inicio + dura))

    def buscar(self, seletor):
        if seletor == tiktok.BOTAO_CURTIR:
            return [Elemento("curtir")]
        if seletor == tiktok.TRACOS_FAVORITO:
            return [Elemento("traço do favorito", i) for i in range(len(self.cores))]
        if seletor == tiktok.BOTAO_FAVORITO:
            return [Elemento("favoritar")]
        if seletor == tiktok.SEL_CAPTCHA:
            return [Elemento("captcha")] if self.captcha else []
        if seletor == SELETOR_AVISOS:
            agora = self.relogio.agora
            return [Elemento("aviso", i) for i, (_, inicio, fim) in enumerate(self.avisos) if inicio <= agora < fim]
        if seletor == tiktok.BOTAO_SEGUIR_PERFIL:
            return [Elemento("seguir")] if self.seguir_no_cabecalho and self.botoes_seguir else []
        if seletor == SELETOR_BOTOES_SEGUIR:
            return [Elemento("seguir", i) for i in range(len(self.botoes_seguir))]
        return super().buscar(seletor)

    def texto(self, elemento):
        if elemento.nome == "seguir":
            return self.botoes_seguir[elemento.indice][0]
        if elemento.nome == "aviso":
            return self.avisos[elemento.indice][0]
        return super().texto(elemento)

    def caixa(self, elemento):
        caixa = super().caixa(elemento)
        if caixa and elemento.nome == "seguir":
            caixa["y"] = self.botoes_seguir[elemento.indice][1]
        return caixa

    def atributo(self, elemento, nome):
        if elemento.nome == "curtir" and nome == "aria-pressed":
            return self.aria_pressed
        return super().atributo(elemento, nome)

    def avaliar_todos(self, elementos, js):
        if "getComputedStyle(e).fill" in js:
            return [self.cores[e.indice] for e in elementos]
        return super().avaliar_todos(elementos, js)

    def efeito_do_clique(self, elemento):
        if not self.muda_no_clique:
            return
        # Liga e desliga de verdade: um clique a mais desfaria a ação.
        if elemento.nome == "curtir":
            self.aria_pressed = "false" if self.aria_pressed == "true" else "true"
        elif elemento.nome == "favoritar":
            self.cores = [BRANCO, BRANCO] if AMARELO in self.cores else [BRANCO, AMARELO]
        elif elemento.nome == "seguir":
            botao = self.botoes_seguir[elemento.indice]
            botao[0] = "Seguindo" if tiktok.RE_SEGUIR.match(botao[0]) else "Seguir"


# -------------------------------------------- as ações de liga/desliga

PUB_IG = comum.Publicacao("Instagram", "fulano", "posts", 1, CODIGO_IG, URL_POST)
PERFIL_IG = comum.Publicacao("Instagram", "fulano", "perfil", 0, "@fulano", URL_PERFIL_IG)
PUB_TT = comum.Publicacao("TikTok", "fulano", "videos", 1, CODIGO_TT, URL_VIDEO)
PERFIL_TT = comum.Publicacao("TikTok", "fulano", "perfil", 0, "@fulano", URL_PERFIL_TT)


def _ig_curtir(relogio, estado, **opcoes):
    return PaginaInstagram(relogio, curtido={"feito": True, "pendente": False, "desconhecido": None}[estado],
                           **opcoes)


def _ig_repostar(relogio, estado, **opcoes):
    repost = {"feito": "ativo", "pendente": "inativo", "desconhecido": "estranho"}[estado]
    return PaginaInstagram(relogio, repost=repost, **opcoes)


def _ig_seguir(relogio, estado, **opcoes):
    cabecalho = {"feito": ["Seguindo", "Enviar mensagem"], "pendente": ["Seguir", "Enviar mensagem"],
                 "desconhecido": ["Enviar mensagem"]}[estado]
    return PaginaInstagram(relogio, url=URL_PERFIL_IG, cabecalho=cabecalho, **opcoes)


def _tt_curtir(relogio, estado, **opcoes):
    return PaginaTikTok(relogio, curtido={"feito": "true", "pendente": "false", "desconhecido": None}[estado],
                        **opcoes)


def _tt_favoritar(relogio, estado, **opcoes):
    cores = {"feito": [BRANCO, AMARELO], "pendente": [BRANCO, BRANCO], "desconhecido": []}[estado]
    return PaginaTikTok(relogio, cores=cores, **opcoes)


def _tt_seguir(relogio, estado, **opcoes):
    botoes = {"feito": [("Seguindo", 180)], "pendente": [("Seguir", 180)], "desconhecido": []}[estado]
    return PaginaTikTok(relogio, url=URL_PERFIL_TT, seguir=botoes, **opcoes)


def _ig_curtir_por_fora(pg):
    pg.curtido = True


def _ig_repostar_por_fora(pg):
    pg.repost = "ativo"


def _ig_seguir_por_fora(pg):
    pg.cabecalho[0] = "Seguindo"


def _tt_curtir_por_fora(pg):
    pg.aria_pressed = "true"


def _tt_favoritar_por_fora(pg):
    pg.cores = [BRANCO, AMARELO]


def _tt_seguir_por_fora(pg):
    pg.botoes_seguir[0][0] = "Seguindo"


@dataclass(frozen=True)
class Caso:
    """Uma ação de liga/desliga e como montar a página falsa em cada estado."""
    rede: str
    nome: str                                    # nome da ação no banco
    acao: Callable                               # a função real da rede
    pub: comum.Publicacao
    alvo: str                                    # rótulo do botão de liga/desliga na página falsa
    montar: Callable[..., PaginaFalsa]           # montar(relogio, "feito" | "pendente" | "desconhecido")
    fazer_por_fora: Callable[[PaginaFalsa], None]  # liga a ação sem o script (você, à mão, ou a página)
    ligado: Callable[[PaginaFalsa], bool]        # a página mostra a ação feita?
    erro_desconhecido: type[Exception] = RuntimeError
    conferir: Callable | None = None             # o `conferir` que processar_perfil passa ao motor


CASOS = [
    Caso("Instagram", "curtir", instagram.curtir, PUB_IG, "Curtir", _ig_curtir, _ig_curtir_por_fora,
         lambda pg: pg.curtido is True,
         # Sem "Curtir" nem "Descurtir", a espera pelo botão "Curtir" estoura (nunca clica às cegas).
         erro_desconhecido=PlaywrightTimeout, conferir=instagram.conferir_publicacao),
    Caso("Instagram", "repostar", instagram.repostar, PUB_IG, "Repostar", _ig_repostar, _ig_repostar_por_fora,
         lambda pg: pg.repost == "ativo", conferir=instagram.conferir_publicacao),
    Caso("Instagram", "seguir", instagram.seguir, PERFIL_IG, "botão do cabeçalho", _ig_seguir,
         _ig_seguir_por_fora, lambda pg: pg.cabecalho[0] == "Seguindo"),
    Caso("TikTok", "curtir", tiktok.curtir, PUB_TT, "curtir", _tt_curtir, _tt_curtir_por_fora,
         lambda pg: pg.aria_pressed == "true", conferir=tiktok.conferir_publicacao),
    Caso("TikTok", "favoritar", tiktok.favoritar, PUB_TT, "favoritar", _tt_favoritar, _tt_favoritar_por_fora,
         lambda pg: AMARELO in pg.cores, conferir=tiktok.conferir_publicacao),
    Caso("TikTok", "seguir", tiktok.seguir, PERFIL_TT, "seguir", _tt_seguir, _tt_seguir_por_fora,
         lambda pg: pg.botoes_seguir[0][0] == "Seguindo"),
]
IDS = [f"{caso.rede.lower()}-{caso.nome}" for caso in CASOS]
CASOS_TIKTOK = [caso for caso in CASOS if caso.rede == "TikTok"]
IDS_TIKTOK = [f"tiktok-{caso.nome}" for caso in CASOS_TIKTOK]


class Execucao:
    """Roda comum.processar_publicacao com a ação real e a página falsa, contando as aberturas."""

    def __init__(self, conn, pg, caso, simular=False):
        self.conn, self.pg, self.caso, self.simular = conn, pg, caso, simular
        self.aberturas = 0

    def abrir(self):
        self.aberturas += 1

    def rodar(self) -> dict[str, str]:
        resumo = []
        conferir = (lambda: self.caso.conferir(self.pg, self.caso.pub)) if self.caso.conferir else None
        comum.processar_publicacao(self.conn, self.pg, None, config(simular=self.simular), self.caso.pub,
                                   abrir=self.abrir, acoes=[(self.caso.nome, self.caso.acao)], resumo=resumo,
                                   conferir=conferir, conta=CONTA)
        return resumo[0].acoes


def test_toda_acao_de_liga_desliga_das_redes_tem_caso_aqui():
    """Se alguém acrescentar uma ação nova nas redes, ela precisa entrar nos casos acima."""
    nao_sao_liga_desliga = {"comentar", "copiar_link"}
    cobertas = {(caso.rede, caso.nome): caso.acao for caso in CASOS}
    cfg = config(destinatarios={"Instagram": ["amigo"], "TikTok": ["amigo"]})
    for modulo in (instagram, tiktok):
        for nome, funcao in modulo.acoes(cfg):
            if nome in nao_sao_liga_desliga or nome.startswith("compartilhar:"):
                continue
            assert cobertas.get((modulo.REDE, nome)) is funcao, f"{modulo.REDE}/{nome} sem teste de segurança"
        assert cobertas[(modulo.REDE, "seguir")] is modulo.seguir


# ------------------------------------------------- comum.clicar e a marca

class PaginaSimples(PaginaFalsa):
    def __init__(self, relogio):
        super().__init__(relogio, "https://exemplo/")

    def buscar(self, seletor):
        return [Elemento("botão")] if seletor == "button" else super().buscar(seletor)


def test_clicar_reconfere_com_o_mouse_parado_imediatamente_antes_do_clique(relogio):
    pg = PaginaSimples(relogio)
    comum.clicar(pg, pg.locator("button"), timeout=5000, conferir_antes=lambda: pg.eventos.append("conferir"))
    assert pg.eventos == ["rolar", "mover", "conferir", ("clique", "botão")]
    # Clica num ponto do botão (o meio, com o acaso fixo), com atraso de pessoa.
    assert pg.detalhes_cliques == [{"position": {"x": 20.0, "y": 15.0}, "delay": 50, "timeout": 5000}]


def test_clicar_sem_posicao_ainda_reconfere_imediatamente_antes_do_clique(relogio):
    pg = PaginaSimples(relogio)
    pg.sem_caixa = True   # botão sendo redesenhado: bounding_box() devolve None
    comum.clicar(pg, pg.locator("button"), timeout=5000, conferir_antes=lambda: pg.eventos.append("conferir"))
    assert pg.eventos == ["rolar", "conferir", ("clique", "botão")]
    assert pg.detalhes_cliques == [{"position": None, "delay": None, "timeout": 5000}]


@pytest.mark.parametrize("sem_posicao", [False, True], ids=["com_posicao", "sem_posicao"])
def test_clicar_nao_clica_se_a_reconferencia_levanta(relogio, sem_posicao):
    pg = PaginaSimples(relogio)
    pg.sem_caixa = sem_posicao

    def ja_ligado():
        raise RuntimeError("o estado mudou; não cliquei")

    with pytest.raises(RuntimeError, match="não cliquei"):
        comum.clicar(pg, pg.locator("button"), conferir_antes=ja_ligado)
    assert pg.cliques == []


def test_clicar_sem_elemento_visivel_nao_reconfere_nem_clica(relogio):
    pg = PaginaSimples(relogio)
    conferidas = []
    with pytest.raises(PlaywrightTimeout):
        comum.clicar(pg, pg.locator("button").filter(has_text="não existe"),
                     conferir_antes=lambda: conferidas.append(1))
    assert conferidas == [] and pg.cliques == []


def test_antes_de_clicar_so_chama_o_gancho_quando_ha_um(monkeypatch):
    comum.antes_de_clicar()   # sem gancho (fora do motor ou no modo simular): não faz nada
    chamadas = []
    monkeypatch.setattr(comum, "_ao_clicar", lambda: chamadas.append("marca"))
    comum.antes_de_clicar()
    assert chamadas == ["marca"]


# ------------------------------------- regra geral, para cada ação de liga/desliga

@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_ja_feito_devolve_ja_estava_feito_sem_clicar(caso, relogio):
    pg = caso.montar(relogio, "feito")
    resultado = caso.acao(pg, config(), caso.pub)
    assert resultado.status == "ja_estava_feito"
    assert pg.cliques == []
    assert caso.ligado(pg)   # nada foi desfeito


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_pendente_clica_uma_vez_depois_da_marca_e_confirma(caso, relogio, monkeypatch):
    pg = caso.montar(relogio, "pendente")
    monkeypatch.setattr(comum, "_ao_clicar", lambda: pg.eventos.append("marca"))
    resultado = caso.acao(pg, config(), caso.pub)
    assert resultado.status == "feito"
    assert pg.cliques == [caso.alvo]
    assert caso.ligado(pg)
    # A marca "nao_confirmado" vem antes do clique, e o mouse anda até o botão antes de clicar.
    assert pg.eventos.index("marca") < pg.eventos.index("mover") < pg.eventos.index(("clique", caso.alvo))


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_estado_desconhecido_levanta_sem_clicar_nem_marcar(caso, relogio, monkeypatch):
    pg = caso.montar(relogio, "desconhecido")
    monkeypatch.setattr(comum, "_ao_clicar", lambda: pg.eventos.append("marca"))
    with pytest.raises(caso.erro_desconhecido):
        caso.acao(pg, config(), caso.pub)
    assert pg.cliques == []
    assert "marca" not in pg.eventos   # nada foi enviado: pode tentar de novo noutra execução


@pytest.mark.parametrize("sem_posicao", [False, True], ids=["com_posicao", "sem_posicao"])
@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_estado_liga_entre_a_conferencia_e_o_clique_nao_clica(caso, sem_posicao, relogio):
    """Corrida: a ação fica feita enquanto o mouse anda até o botão (ou com o botão sem posição)."""
    pg = caso.montar(relogio, "pendente")
    pg.sem_caixa = sem_posicao
    if sem_posicao:
        pg.ao_rolar = lambda: caso.fazer_por_fora(pg)
    else:
        pg.ao_mover = lambda: caso.fazer_por_fora(pg)
    with pytest.raises(RuntimeError, match="mudou antes do clique; não cliquei"):
        caso.acao(pg, config(), caso.pub)
    assert pg.cliques == []
    assert caso.ligado(pg)   # continua feito: o script não desfez
    assert ("mover" in pg.eventos) is not sem_posicao


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_clique_sem_mudanca_na_tela_vira_nao_confirmado_sem_insistir(caso, relogio):
    pg = caso.montar(relogio, "pendente", muda_no_clique=False)
    with pytest.raises(comum.NaoConfirmado):
        caso.acao(pg, config(), caso.pub)
    assert pg.cliques == [caso.alvo]   # um clique só, nunca um segundo


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_nao_confirmado_fica_marcado_antes_do_clique_e_nunca_e_repetido(caso, relogio, conn):
    pg = caso.montar(relogio, "pendente", muda_no_clique=False)
    no_instante_do_clique = []
    pg.no_clique.append(lambda: no_instante_do_clique.append(
        banco.acoes_concluidas(conn, caso.rede, caso.pub.codigo, CONTA)))
    execucao = Execucao(conn, pg, caso)

    assert execucao.rodar() == {caso.nome: "nao_confirmado"}
    assert no_instante_do_clique == [{caso.nome}]   # a marca já estava no banco quando clicou
    registros = acoes_no_banco(conn)
    assert [(acao, status) for acao, status, _ in registros] == [(caso.nome, "nao_confirmado")] * 2
    assert registros[0][2] == "clique enviado, aguardando confirmação"

    # Outra execução: nem abre a publicação, nem clica de novo.
    assert execucao.rodar() == {caso.nome: "já registrada"}
    assert pg.cliques == [caso.alvo]
    assert execucao.aberturas == 1
    assert len(acoes_no_banco(conn)) == 2


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_corrida_no_motor_nao_clica_nem_agora_nem_depois(caso, relogio, conn):
    pg = caso.montar(relogio, "pendente")
    pg.ao_mover = lambda: caso.fazer_por_fora(pg)
    execucao = Execucao(conn, pg, caso)
    execucao.rodar()
    execucao.rodar()
    assert pg.cliques == []
    assert caso.ligado(pg)
    assert caso.nome in banco.acoes_concluidas(conn, caso.rede, caso.pub.codigo, CONTA)


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_estado_desconhecido_no_motor_vira_falhou_sem_marca(caso, relogio, conn):
    pg = caso.montar(relogio, "desconhecido")
    assert Execucao(conn, pg, caso).rodar() == {caso.nome: "falhou"}
    assert pg.cliques == []
    assert [status for _, status, _ in acoes_no_banco(conn)] == ["falhou"]
    assert banco.acoes_concluidas(conn, caso.rede, caso.pub.codigo, CONTA) == set()


@pytest.mark.parametrize(("estado", "status"), [("pendente", "simulado"), ("feito", "ja_estava_feito")])
@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_simular_nunca_clica(caso, estado, status, relogio):
    pg = caso.montar(relogio, estado)
    assert caso.acao(pg, config(simular=True), caso.pub).status == status
    assert pg.cliques == []


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_simular_no_motor_nao_clica_nem_grava(caso, relogio, conn):
    pg = caso.montar(relogio, "pendente")
    assert Execucao(conn, pg, caso, simular=True).rodar() == {caso.nome: "simulado"}
    assert pg.cliques == []
    assert acoes_no_banco(conn) == []


# --------------------------------------------------------- Instagram: detalhes

def test_instagram_repostar_sem_icone_levanta_sem_clicar(relogio):
    pg = PaginaInstagram(relogio, repost=None)
    with pytest.raises(RuntimeError, match="não cliquei para não desfazer um repost"):
        instagram.repostar(pg, config(), PUB_IG)
    assert pg.cliques == []


def test_instagram_repostar_icone_estranho_mas_post_na_aba_reposts_nao_clica(relogio, monkeypatch):
    monkeypatch.setattr(instagram, "_meus_reposts", {CODIGO_IG})
    pg = PaginaInstagram(relogio, repost="estranho")
    resultado = instagram.repostar(pg, config(), PUB_IG)
    assert resultado.status == "ja_estava_feito"
    assert pg.cliques == []


def test_instagram_repostar_icone_redesenhado_durante_o_movimento_nao_clica(relogio):
    pg = PaginaInstagram(relogio, repost="inativo")
    pg.ao_mover = lambda: setattr(pg, "repost", "estranho")   # não é mais "inativo" com certeza
    with pytest.raises(RuntimeError, match="não cliquei"):
        instagram.repostar(pg, config(), PUB_IG)
    assert pg.cliques == []


def test_instagram_repostar_com_janela_de_confirmacao_clica_uma_vez_em_cada(relogio):
    pg = PaginaInstagram(relogio, repost="inativo", pede_confirmacao=True)
    assert instagram.repostar(pg, config(), PUB_IG).status == "feito"
    assert pg.cliques == ["Repostar", "confirmar repost"]
    assert pg.repost == "ativo"


@pytest.mark.parametrize(("texto", "status", "cliques"), [
    ("Seguir de volta", "feito", ["botão do cabeçalho"]),
    ("Follow Back", "feito", ["botão do cabeçalho"]),
    ("Solicitado", "ja_estava_feito", []),     # conta privada: o pedido já foi enviado
    ("Following", "ja_estava_feito", []),
    ("Requested", "ja_estava_feito", []),
])
def test_instagram_seguir_pelos_textos_do_botao(relogio, texto, status, cliques):
    pg = PaginaInstagram(relogio, url=URL_PERFIL_IG, cabecalho=[texto, "Enviar mensagem"])
    assert instagram.seguir(pg, config(), PERFIL_IG).status == status
    assert pg.cliques == cliques


def test_instagram_seguir_conta_privada_confirma_com_solicitado(relogio):
    pg = PaginaInstagram(relogio, url=URL_PERFIL_IG, cabecalho=["Seguir"], depois_de_seguir="Solicitado")
    assert instagram.seguir(pg, config(), PERFIL_IG).status == "feito"
    assert pg.cliques == ["botão do cabeçalho"]


def test_instagram_seguir_botao_muda_de_posicao_antes_do_clique_nao_clica(relogio):
    pg = PaginaInstagram(relogio, url=URL_PERFIL_IG, cabecalho=["Seguir"])
    # Um botão novo aparece antes: o nth(0) que seria clicado já não é o "Seguir".
    pg.ao_mover = lambda: pg.cabecalho.insert(0, "Enviar mensagem")
    with pytest.raises(RuntimeError, match="não cliquei"):
        instagram.seguir(pg, config(), PERFIL_IG)
    assert pg.cliques == []


# ---------------------------------------------------------- Instagram: stories

def curtir_story(conn, pg, url=URL_STORY):
    return instagram._curtir_story(conn, pg, None, config(), "fulano", CONTA, CODIGO_STORY, url)


def test_story_nao_curtido_curte_uma_vez_e_grava_marca_antes(conn, relogio):
    pg = PaginaStories(relogio)
    no_instante_do_clique = []
    pg.no_clique.append(lambda: no_instante_do_clique.append(acoes_no_banco(conn)))
    assert curtir_story(conn, pg) == 1
    assert pg.cliques == ["Curtir"]
    assert pg.curtido is True
    assert no_instante_do_clique == [[("curtir", "nao_confirmado", "clique enviado, aguardando confirmação")]]
    assert [status for _, status, _ in acoes_no_banco(conn)] == ["nao_confirmado", "feito"]


def test_story_ja_curtido_nao_clica(conn, relogio):
    pg = PaginaStories(relogio, curtido=True)
    assert curtir_story(conn, pg) == 0
    assert pg.cliques == []
    assert pg.curtido is True
    assert acoes_no_banco(conn) == []


def test_story_ja_registrado_no_banco_nao_clica(conn, relogio):
    pub_id = banco.registrar_publicacao(conn, "Instagram", CODIGO_STORY, "fulano", "stories", 0, URL_STORY)
    banco.registrar_acao(conn, pub_id, None, "curtir", "feito", conta=CONTA)
    pg = PaginaStories(relogio)
    assert curtir_story(conn, pg) == 0
    assert pg.cliques == []


@pytest.mark.parametrize(("curtido", "coracao_visivel"), [(None, True), (False, False)],
                         ids=["sem_coracao", "coracao_escondido"])
def test_story_sem_coracao_visivel_nao_clica(conn, relogio, curtido, coracao_visivel):
    pg = PaginaStories(relogio, curtido=curtido, coracao_visivel=coracao_visivel)
    assert curtir_story(conn, pg) == 0
    assert pg.cliques == []
    assert acoes_no_banco(conn) == []


def test_story_que_ja_passou_nao_e_curtido(conn, relogio):
    pg = PaginaStories(relogio, url=URL_OUTRO_STORY)   # o visualizador já avançou sozinho
    assert curtir_story(conn, pg) == 0
    assert pg.cliques == []
    assert acoes_no_banco(conn) == []


@pytest.mark.parametrize("mudanca", ["avancou", "curtido"])
def test_story_muda_durante_o_movimento_nao_clica(conn, relogio, mudanca):
    pg = PaginaStories(relogio)
    if mudanca == "avancou":
        pg.ao_mover = lambda: setattr(pg, "url", URL_OUTRO_STORY)   # o próximo pode já estar curtido
    else:
        pg.ao_mover = lambda: setattr(pg, "curtido", True)
    assert curtir_story(conn, pg) == 0
    assert pg.cliques == []
    # A marca gravada antes fica: este story nunca recebe outro clique.
    assert banco.acoes_concluidas(conn, "Instagram", CODIGO_STORY, CONTA) == {"curtir"}


def test_story_sem_confirmacao_fica_marcado_e_nunca_e_repetido(conn, relogio):
    pg = PaginaStories(relogio, muda_no_clique=False)
    assert curtir_story(conn, pg) == 0
    assert pg.cliques == ["Curtir"]
    assert acoes_no_banco(conn) == [("curtir", "nao_confirmado", "clique enviado, aguardando confirmação")]
    assert curtir_story(conn, pg) == 0   # volta ao mesmo story noutra visita
    assert pg.cliques == ["Curtir"]


@pytest.mark.parametrize(("simular", "curtidas"), [(True, []), (False, ["Curtir"])], ids=["simular", "normal"])
def test_ver_stories_so_curte_fora_do_modo_simular(conn, relogio, simular, curtidas):
    pg = PaginaStories(relogio, url="about:blank")
    resumo = []
    instagram.ver_stories(conn, pg, None, config(simular=simular), "fulano", CONTA, resumo)
    assert pg.cliques == ["foto do perfil", *curtidas]   # abrir os stories não muda nada na rede
    assert resumo[-1].acoes == {"stories vistos": "1", "stories curtidos": str(len(curtidas))}
    if simular:
        assert acoes_no_banco(conn) == []


# ------------------------------------------------------------- TikTok: detalhes

@pytest.mark.parametrize("valor", [None, "", "mixed", "True"])
def test_tiktok_curtir_aria_pressed_inesperado_nao_clica(relogio, valor):
    pg = PaginaTikTok(relogio, curtido=valor)
    with pytest.raises(RuntimeError, match="não cliquei para não desfazer uma curtida"):
        tiktok.curtir(pg, config(), PUB_TT)
    assert pg.cliques == []


@pytest.mark.parametrize("caso", [c for c in CASOS_TIKTOK if c.nome != "seguir"],
                         ids=[i for i in IDS_TIKTOK if i != "tiktok-seguir"])
def test_tiktok_trocou_de_video_durante_o_movimento_nao_clica(caso, relogio):
    pg = caso.montar(relogio, "pendente")
    pg.ao_mover = lambda: setattr(pg, "url", URL_OUTRO_VIDEO)   # o TikTok avançou sozinho
    with pytest.raises(RuntimeError, match="trocou de vídeo"):
        caso.acao(pg, config(), caso.pub)
    assert pg.cliques == []


@pytest.mark.parametrize("caso", CASOS_TIKTOK, ids=IDS_TIKTOK)
def test_tiktok_aviso_durante_o_movimento_vira_bloqueio_sem_clicar(caso, relogio):
    pg = caso.montar(relogio, "pendente")
    pg.ao_mover = lambda: pg.aviso()
    with pytest.raises(Bloqueio, match="rápido demais"):
        caso.acao(pg, config(), caso.pub)
    assert pg.cliques == []


@pytest.mark.parametrize(("cores", "status", "cliques"), [
    ([BRANCO, BRANCO], "feito", ["favoritar"]),
    (["rgb(22, 24, 35)"], "feito", ["favoritar"]),                       # tema claro
    (["rgb(254, 44, 85)", BRANCO], "feito", ["favoritar"]),              # vermelho não é amarelo
    ([BRANCO, AMARELO], "ja_estava_feito", []),
    (["rgb(255, 194, 0)"], "ja_estava_feito", []),                       # outro tom de amarelo
])
def test_tiktok_favoritar_pelas_cores_do_icone(relogio, cores, status, cliques):
    pg = PaginaTikTok(relogio, cores=cores)
    assert tiktok.favoritar(pg, config(), PUB_TT).status == status
    assert pg.cliques == cliques


@pytest.mark.parametrize("cores", [
    ["none"],                                    # só traços sem preenchimento
    [BRANCO, "url(#degrade)"],                   # gradiente: o amarelo pode estar nele
    ["color(srgb 0.98 0.81 0.08)"],              # amarelo em outro formato (antes lia "0, 98, 0")
    [BRANCO, "oklch(0.87 0.17 95)"],
    [BRANCO, ""],
])
def test_tiktok_favoritar_cor_que_nao_da_para_ler_nao_clica(relogio, cores):
    pg = PaginaTikTok(relogio, cores=cores)
    with pytest.raises(RuntimeError, match="não cliquei"):
        tiktok.favoritar(pg, config(), PUB_TT)
    assert pg.cliques == []


def test_tiktok_favoritar_traco_sem_preenchimento_nao_atrapalha(relogio):
    assert tiktok.favoritar(PaginaTikTok(relogio, cores=[BRANCO, "none"]), config(), PUB_TT).status == "feito"
    pg = PaginaTikTok(relogio, cores=["none", "rgba(250, 206, 21, 1)"])
    assert tiktok.favoritar(pg, config(), PUB_TT).status == "ja_estava_feito" and pg.cliques == []


def test_tiktok_favoritar_icone_some_durante_o_movimento_nao_clica(relogio):
    pg = PaginaTikTok(relogio, cores=[BRANCO, BRANCO])
    pg.ao_mover = lambda: setattr(pg, "cores", [])   # estado vira desconhecido
    with pytest.raises(RuntimeError, match="não cliquei"):
        tiktok.favoritar(pg, config(), PUB_TT)
    assert pg.cliques == []


@pytest.mark.parametrize(("texto", "status", "cliques"), [
    ("Seguir de volta", "feito", ["seguir"]),
    ("Follow back", "feito", ["seguir"]),
    ("Amigos", "ja_estava_feito", []),       # vocês se seguem: clicar deixaria de seguir
    ("Friends", "ja_estava_feito", []),
    ("Following", "ja_estava_feito", []),
    ("Solicitado", "ja_estava_feito", []),
])
def test_tiktok_seguir_pelos_textos_do_botao(relogio, texto, status, cliques):
    pg = PaginaTikTok(relogio, url=URL_PERFIL_TT, seguir=[(texto, 180)])
    assert tiktok.seguir(pg, config(), PERFIL_TT).status == status
    assert pg.cliques == cliques


@pytest.mark.parametrize("texto", ["Mensagem", "", "Editar perfil"])
def test_tiktok_seguir_texto_inesperado_nao_clica(relogio, texto):
    pg = PaginaTikTok(relogio, url=URL_PERFIL_TT, seguir=[(texto, 180)])
    with pytest.raises(RuntimeError, match="texto inesperado"):
        tiktok.seguir(pg, config(), PERFIL_TT)
    assert pg.cliques == []


def test_tiktok_seguir_sem_cabecalho_usa_o_botao_do_topo_e_ignora_sugestoes(relogio):
    pg = PaginaTikTok(relogio, url=URL_PERFIL_TT, seguir=[("Seguindo", 900), ("Seguir", 150)],
                      seguir_no_cabecalho=False)
    assert tiktok.seguir(pg, config(), PERFIL_TT).status == "feito"
    assert pg.cliques == ["seguir[1]"]
    assert pg.botoes_seguir[0][0] == "Seguindo"   # a sugestão de baixo continua como estava


def test_tiktok_seguir_so_com_sugestoes_abaixo_nao_clica(relogio):
    pg = PaginaTikTok(relogio, url=URL_PERFIL_TT, seguir=[("Seguir", 900), ("Seguir", 1200)],
                      seguir_no_cabecalho=False)
    with pytest.raises(RuntimeError, match="não encontrei o botão Seguir"):
        tiktok.seguir(pg, config(), PERFIL_TT)
    assert pg.cliques == []


# ------------------------------------------------- TikTok: confirmar() e avisos

def test_confirmar_aviso_passageiro_durante_a_espera_vira_bloqueio(relogio):
    pg = PaginaTikTok(relogio)
    inicio = relogio.agora
    pg.aviso(daqui=1.0, dura=1.5)   # some bem antes do fim da espera
    with pytest.raises(Bloqueio, match="rápido demais"):
        tiktok.confirmar(pg, lambda: False, 10)
    assert relogio.agora - inicio < 10   # percebido durante a espera, não no fim
    relogio.agora = inicio + 10
    tiktok.verificar_aviso(pg)           # no fim da espera ele já sumiu: olhar só no fim deixaria passar


def test_confirmar_sem_aviso_devolve_a_condicao(relogio):
    pg = PaginaTikTok(relogio)
    pg.aviso("Link copiado")              # balão que não é aviso de limite
    inicio = relogio.agora
    assert tiktok.confirmar(pg, lambda: relogio.agora - inicio >= 2, 10) is True
    assert tiktok.confirmar(pg, lambda: False, 3) is False


def test_tiktok_aviso_logo_depois_do_clique_vira_bloqueio_e_nao_repete(conn, relogio):
    caso = next(c for c in CASOS_TIKTOK if c.nome == "curtir")
    pg = caso.montar(relogio, "pendente", muda_no_clique=False)
    pg.no_clique.append(lambda: pg.aviso(daqui=1.0, dura=2.0))
    execucao = Execucao(conn, pg, caso)
    with pytest.raises(Bloqueio, match="rápido demais"):
        execucao.rodar()
    assert pg.cliques == ["curtir"]
    assert acoes_no_banco(conn) == [("curtir", "nao_confirmado", "clique enviado, aguardando confirmação")]

    execucao.rodar()   # outra execução, depois do bloqueio
    assert pg.cliques == ["curtir"]
    assert execucao.aberturas == 1
