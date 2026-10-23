"""Ações no Instagram: curtir, repostar, comentar, compartilhar e copiar o link.

Antes de cada ação, o script confere na própria página se aquilo já foi feito
(post já curtido, já repostado, comentário seu já presente...), para não
repetir o que você fez à mão antes do sistema existir.

Os seletores usam os rótulos de acessibilidade da interface em português, com
o equivalente em inglês como reserva.
"""

import random
import re
import sqlite3
from datetime import datetime, timezone

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from automacao import banco
from automacao.redes.comum import (Acao, Bloqueio, Config, LinhaResumo,
                                   NaoConfirmado, Publicacao, Resultado, antes_de_clicar, clicar,
                                   coletar_da_grade, digitar, esperar_ate, ler_clipboard, monitorar_video,
                                   pausa, preparar_copia, processar_publicacao, rolar, sortear_comentario,
                                   tem_video_visivel, ver_item)
from automacao.redes.protecao import PularAcao, controlar_ritmo

REDE = "Instagram"
BASE = "https://www.instagram.com"
ABAS = (("posts", ""), ("reels", "reels/"), ("reposts", "reposts/"))  # (nome, sufixo da URL do perfil)

SEL_LINK_POST = 'main a[href*="/p/"], main a[href*="/reel/"]'
RE_CODIGO = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
RE_BLOQUEIO = re.compile(
    r"Tente novamente mais tarde|Ação bloqueada|Try again later|Action blocked|comportamento automatizado|"
    r"automated behavior|Confirme que é você|Confirm it's you|atividade incomum|unusual activity", re.I)
ALFABETO_CODIGO = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
EPOCA_INSTAGRAM_MS = 1314220021721


def _svg(*rotulos: str) -> str:
    return ", ".join(f'svg[aria-label="{r}"]' for r in rotulos)


SVG_CURTIR = _svg("Curtir", "Like")
SVG_DESCURTIR = _svg("Descurtir", "Unlike")
SVG_REPOSTAR = _svg("Repostar", "Repost")
SVG_COMPARTILHAR = _svg("Compartilhar", "Share")
SVG_MAIS_OPCOES = _svg("Mais opções", "More options")
SVG_MAIS_COMENTARIOS = _svg("Carregar mais comentários", "Load more comments")
CAMPO_COMENTARIO = ('textarea[aria-label="Adicione um comentário..."], '
                    'textarea[aria-label="Add a comment…"], textarea[aria-label="Add a comment..."]')
CAMPO_PESQUISA = 'input[placeholder="Pesquisar"], input[placeholder="Search"]'
BOTAO_AVANCAR = 'main button[aria-label="Avançar"], main button[aria-label="Next"]'
# Começo do desenho (atributo "d") do ícone de repostar em cada estado. O nome do
# botão continua "Repostar" depois do repost; só o ícone muda.
ICONE_REPOST_ATIVO = "M16 6.001a1 1 0 0 0 .924-1.382"
ICONE_REPOST_INATIVO = "M19.998 9.497a1 1 0 0 0-1 1v4.228"
MAXIMO_ITENS_CARROSSEL = 25

_meu_usuario: str | None = None


def definir_meu_usuario(usuario: str | None) -> None:
    """Informa a conta logada (sem @) ao trocar de perfil; None faz ler de novo da página."""
    global _meu_usuario, _meus_reposts
    _meu_usuario = usuario.lstrip("@") if usuario else None
    _meus_reposts = None  # a aba Reposts é de cada conta
_meus_reposts: set[str] | None = None            # códigos na aba Reposts da sua conta


# ------------------------------------------------------------------ apoio

def data_publicacao(codigo: str) -> datetime | None:
    """Data de publicação embutida no código do post (os 41 bits altos do id)."""
    if len(codigo) != 11 or any(c not in ALFABETO_CODIGO for c in codigo):
        return None
    numero = 0
    for letra in codigo:
        numero = numero * 64 + ALFABETO_CODIGO.index(letra)
    return datetime.fromtimestamp(((numero >> 23) + EPOCA_INSTAGRAM_MS) / 1000, timezone.utc)


def meu_usuario(pg: Page) -> str | None:
    """Usuário logado, lido do link de perfil na barra lateral."""
    global _meu_usuario
    if _meu_usuario is None:
        _meu_usuario = pg.evaluate("""() => {
            const img = [...document.querySelectorAll('a[href] img[alt]')].find(i =>
                /^(Foto do perfil de|.*profile picture)/i.test(i.alt) && i.getBoundingClientRect().x < 150);
            const href = img?.closest('a')?.getAttribute('href') || '';
            return href.replaceAll('/', '') || null;
        }""")
    return _meu_usuario


def barra_acoes(pg: Page) -> Locator:
    """A barra com Curtir, Comentar, Repostar, Compartilhar e Salvar do post aberto."""
    return pg.locator("main section").filter(has=pg.locator(SVG_COMPARTILHAR)).first


def verificar_bloqueio(pg: Page) -> None:
    aviso = pg.locator('[role="dialog"]').filter(has_text=RE_BLOQUEIO)
    if aviso.count():
        raise Bloqueio(f"o Instagram bloqueou a ação: {aviso.first.inner_text()[:150]!r}")


def fechar_janelas(pg: Page) -> None:
    """Fecha menus e janelas que tenham ficado abertos sobre o post."""
    for _ in range(3):
        dialogos = pg.locator('[role="dialog"]')
        if not dialogos.count():
            return
        fechar = dialogos.last.get_by_role("button", name=re.compile(r"^(Fechar|Close|Cancelar|Cancel)$"))
        if fechar.count():
            clicar(pg, fechar.first)
        else:
            pg.keyboard.press("Escape")
            pg.mouse.click(10, 10)
        pg.wait_for_timeout(700)


# ---------------------------------------------------------------- navegação

def listar_publicacoes(pg: Page, cfg: Config, perfil: str, aba: str, sufixo: str) -> list[Publicacao] | None:
    """Publicações da aba dentro do limite configurado. None se a aba não existir."""
    pg.goto(f"{BASE}/{perfil}/{sufixo}", wait_until="domcontentloaded")
    try:
        pg.wait_for_selector(SEL_LINK_POST, timeout=20_000)
    except PlaywrightTimeout:
        return [] if sufixo in pg.url else None
    if sufixo and sufixo.rstrip("/") not in pg.url:
        return None  # o Instagram redirecionou: o perfil não tem essa aba
    meu_usuario(pg)
    pg.wait_for_timeout(1500)  # deixa a grade terminar de montar

    def ler_hrefs() -> list[str]:
        return pg.eval_on_selector_all(SEL_LINK_POST, "els => els.map(e => e.getAttribute('href'))")

    def extrair(href: str) -> tuple[str, str] | None:
        # Abre sempre o endereço padrão do post (/p/CODIGO/). O da grade traz o
        # perfil antes do código, e em posts de colaboração o Instagram
        # redireciona esse formato para a página do perfil.
        achado = RE_CODIGO.search(href or "")
        return (achado.group(1), f"{BASE}/p/{achado.group(1)}/") if achado else None

    return coletar_da_grade(pg, ler_hrefs, extrair, data_publicacao, cfg, REDE, perfil, aba)


def abrir_publicacao(pg: Page, pub: Publicacao) -> None:
    pg.goto(pub.url, wait_until="domcontentloaded")
    barra_acoes(pg).wait_for(state="visible", timeout=20_000)
    verificar_bloqueio(pg)


def conferir_publicacao(pg: Page, pub: Publicacao) -> None:
    """Garante que a aba ainda mostra a publicação certa (alguém pode ter navegado)."""
    verificar_bloqueio(pg)
    if f"/{pub.codigo}/" not in pg.url and not pg.url.rstrip("/").endswith(pub.codigo):
        raise RuntimeError(f"a aba saiu da publicação {pub.codigo} (endereço atual: {pg.url})")


def ver_midia(pg: Page, cfg: Config, pub: Publicacao) -> str:
    """Passa o carrossel até o último item, com pausas (e vídeos até o fim)."""
    avancar = pg.locator(BOTAO_AVANCAR).first
    if not avancar.is_visible():
        return "vídeo" if tem_video_visivel(pg) else "foto"
    itens = 1
    while itens < MAXIMO_ITENS_CARROSSEL:
        ver_item(pg, cfg, f"{pub.codigo}-{itens}")
        if not avancar.is_visible():
            break
        clicar(pg, avancar)
        itens += 1
        pg.wait_for_timeout(700)
    return f"carrossel com {itens} itens, visto até o fim"


# ------------------------------------------------------------------- ações

def curtir(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    barra = barra_acoes(pg)
    if barra.locator(SVG_DESCURTIR).count():
        return Resultado("ja_estava_feito", "o post já estava curtido")
    # Só clica se o botão disser explicitamente "Curtir" (nunca às cegas).
    botao = barra.locator(SVG_CURTIR).first
    botao.wait_for(state="visible", timeout=10_000)
    if cfg.simular:
        return Resultado("simulado")
    def ainda_nao_curtido() -> None:
        if barra.locator(SVG_DESCURTIR).count() or not barra.locator(SVG_CURTIR).count():
            raise RuntimeError("o estado do botão de curtir mudou antes do clique; não cliquei")

    antes_de_clicar()
    clicar(pg, botao, conferir_antes=ainda_nao_curtido)
    if not esperar_ate(lambda: barra.locator(SVG_DESCURTIR).count() > 0, 10):
        verificar_bloqueio(pg)
        raise NaoConfirmado("o botão não mudou para Descurtir")
    return Resultado("feito")


def _rotulos_barra(barra: Locator) -> list[str]:
    return barra.locator("svg[aria-label]").evaluate_all(
        "els => els.map(e => e.getAttribute('aria-label'))")


def _estado_repost(barra: Locator) -> str:
    """'ativo', 'inativo' ou 'desconhecido', pelo desenho do ícone de repostar."""
    icone = barra.locator(SVG_REPOSTAR).first
    if not icone.count():
        return "desconhecido"
    desenho = icone.evaluate("e => [...e.querySelectorAll('path')].map(p => p.getAttribute('d') || '').join(' ')")
    if desenho.startswith(ICONE_REPOST_ATIVO):
        return "ativo"
    if desenho.startswith(ICONE_REPOST_INATIVO):
        return "inativo"
    return "desconhecido"


def meus_reposts(pg: Page) -> set[str]:
    """Códigos da aba Reposts da sua conta (carregada uma vez por execução)."""
    global _meus_reposts
    if _meus_reposts is None:
        usuario = meu_usuario(pg)
        _meus_reposts = set()
        if usuario:
            atual = pg.url
            pg.goto(f"{BASE}/{usuario}/reposts/", wait_until="domcontentloaded")
            try:
                pg.wait_for_selector(SEL_LINK_POST, timeout=15_000)
                for _ in range(3):
                    hrefs = pg.eval_on_selector_all(SEL_LINK_POST, "els => els.map(e => e.getAttribute('href'))")
                    _meus_reposts.update(m.group(1) for h in hrefs if (m := RE_CODIGO.search(h or "")))
                    rolar(pg)
                    pg.wait_for_timeout(1200)
            except PlaywrightTimeout:
                pass
            pg.goto(atual, wait_until="domcontentloaded")
            barra_acoes(pg).wait_for(state="visible", timeout=20_000)
    return _meus_reposts


def repostar(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    barra = barra_acoes(pg)
    estado = _estado_repost(barra)
    if estado == "ativo":
        return Resultado("ja_estava_feito", "o ícone de repostar já está marcado")
    if estado == "desconhecido":
        # O Instagram mudou o ícone: confere na aba Reposts da sua conta.
        if pub.codigo in meus_reposts(pg):
            return Resultado("ja_estava_feito", "o post já está na aba Reposts da sua conta")
        raise RuntimeError(f"não consegui saber se o post já foi repostado (botões: {_rotulos_barra(barra)}); "
                           "não cliquei para não desfazer um repost")
    if cfg.simular:
        return Resultado("simulado")

    def ainda_nao_repostado() -> None:
        if _estado_repost(barra) != "inativo":
            raise RuntimeError("o estado do botão de repostar mudou antes do clique; não cliquei")

    antes_de_clicar()
    clicar(pg, barra.locator(SVG_REPOSTAR).first, conferir_antes=ainda_nao_repostado)

    # O Instagram pode repostar direto ou pedir confirmação numa janela.
    # A confirmação recebe um clique só.
    confirmar = pg.locator('[role="dialog"]').get_by_role(
        "button", name=re.compile(r"^(Repostar|Repost)$"))
    if esperar_ate(lambda: confirmar.count() > 0, 2.5):
        clicar(pg, confirmar.first)

    if not esperar_ate(lambda: _estado_repost(barra) == "ativo", 10):
        verificar_bloqueio(pg)
        raise NaoConfirmado("o ícone de repostar não ficou marcado")
    return Resultado("feito")


JS_MEUS_COMENTARIOS = """(usuario) => [...document.querySelectorAll(`main a[href="/${usuario}/"]`)]
    .filter(a => { let e = a;
        for (let i = 0; i < 7 && e; i++, e = e.parentElement) {
            if (e.querySelector('svg[aria-label="Compartilhar"], svg[aria-label="Share"]')) return false;
            if (/Responder|Reply/.test(e.innerText)) return true;
        }
        return false; }).length"""

JS_CAIXA_COMENTARIOS = """() => {
    const links = [...document.querySelectorAll('main a[href^="/"]')]
        .filter(a => /^\\/[^/]+\\/$/.test(a.getAttribute('href')));
    let c = links[links.length - 1];
    while (c && !(c.scrollHeight > c.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(c).overflowY)))
        c = c.parentElement;
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2, fim: c.scrollTop + c.clientHeight >= c.scrollHeight - 5};
}"""


JS_QUANTOS_COMENTARIOS = """() => new Set([...document.querySelectorAll('main a[href^="/"]')]
    .filter(a => /^\\/[^/]+\\/$/.test(a.getAttribute('href')))
    .map(a => a.closest('li, div[role="button"], ul > div') || a)).size"""


def ja_comentei(pg: Page, usuario: str, rodadas: int = 8) -> bool:
    """Procura um comentário seu no post, carregando mais comentários algumas vezes.

    Em posts com milhares de comentários, só os primeiros carregados são vistos.
    """
    for _ in range(rodadas):
        if pg.evaluate(JS_MEUS_COMENTARIOS, usuario):
            return True
        mais = pg.locator(SVG_MAIS_COMENTARIOS)
        if mais.count() and mais.first.is_visible():
            clicar(pg, mais.first)
        else:
            caixa = pg.evaluate(JS_CAIXA_COMENTARIOS)
            if not caixa or caixa["fim"]:
                return False
            pg.mouse.move(caixa["x"], caixa["y"], steps=random.randint(6, 14))
            pg.mouse.wheel(0, random.randint(500, 900))
        pg.wait_for_timeout(random.randint(900, 1600))
    return bool(pg.evaluate(JS_MEUS_COMENTARIOS, usuario))


def comentar(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    vistos = None
    if cfg.verificar_comentarios:  # só nos testes, para não acumular comentários
        usuario = meu_usuario(pg)
        if not usuario:
            raise RuntimeError("não consegui identificar seu usuário para procurar comentários antigos")
        if ja_comentei(pg, usuario):
            return Resultado("ja_estava_feito", f"já existe comentário de @{usuario} no post")
        vistos = pg.evaluate(JS_QUANTOS_COMENTARIOS)
    extra = f" (nenhum comentário seu entre os ~{vistos} carregados)" if vistos is not None else ""
    campo = pg.locator(CAMPO_COMENTARIO).first
    campo.wait_for(state="visible", timeout=10_000)
    if cfg.simular:
        return Resultado("simulado", extra.strip(" ()") or None)
    texto = sortear_comentario(cfg)
    clicar(pg, campo)
    digitar(pg, texto)
    formulario = campo.locator("xpath=ancestor::form[1]")
    postar = formulario.get_by_role("button", name=re.compile(r"^(Postar|Publicar|Post)$"))
    if not postar.count():
        postar = pg.get_by_role("button", name=re.compile(r"^(Postar|Publicar|Post)$"))
    antes_de_clicar()
    clicar(pg, postar.first)
    # Depois de publicar, o Instagram limpa o campo e mostra o comentário.
    if not esperar_ate(lambda: pg.locator(CAMPO_COMENTARIO).first.input_value() == "", 15):
        verificar_bloqueio(pg)
        raise NaoConfirmado("o campo de comentário não foi limpo depois de postar")
    return Resultado("feito", f"{texto}{extra}")


def compartilhar(pg: Page, cfg: Config, pub: Publicacao, destino: str) -> Resultado:
    clicar(pg, barra_acoes(pg).locator(SVG_COMPARTILHAR).first)
    dialogo = pg.locator('[role="dialog"]').filter(has=pg.locator(CAMPO_PESQUISA)).last
    dialogo.wait_for(state="visible", timeout=10_000)
    try:
        clicar(pg, dialogo.locator(CAMPO_PESQUISA).first)
        digitar(pg, destino)
        conta = dialogo.get_by_text(destino, exact=True).first
        conta.wait_for(state="visible", timeout=15_000)
        if cfg.simular:
            return Resultado("simulado", f"conta {destino} encontrada")
        clicar(pg, conta)
        enviar = dialogo.get_by_role("button", name=re.compile(r"^(Enviar|Send)$")).first
        enviar.wait_for(state="visible", timeout=10_000)
        pausa((0.8, 1.8))
        antes_de_clicar()
        clicar(pg, enviar)
        if not esperar_ate(lambda: not dialogo.is_visible(), 15):
            verificar_bloqueio(pg)
            raise NaoConfirmado("a janela de compartilhar não fechou depois de enviar")
        return Resultado("feito", f"enviado para {destino}")
    finally:
        fechar_janelas(pg)


def copiar_link(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    clicar(pg, pg.locator(SVG_MAIS_OPCOES).first)
    botao = pg.get_by_role("button", name=re.compile(r"^(Copiar link|Copy link)$")).first
    try:
        botao.wait_for(state="visible", timeout=10_000)
        if cfg.simular:
            return Resultado("simulado")
        preparar_copia(pg)
        clicar(pg, botao)
        link = ler_clipboard(pg)
    finally:
        fechar_janelas(pg)
    if link and pub.codigo in link:
        return Resultado("feito", link=link)
    reserva = f"{BASE}/p/{pub.codigo}/"
    return Resultado("feito", f"não consegui ler o link copiado ({link!r}), usei o endereço do post",
                     link=reserva)


def acoes(cfg: Config) -> list[tuple[str, Acao]]:
    """As ações na ordem pedida, com um compartilhamento por destinatário."""
    lista: list[tuple[str, Acao]] = [("curtir", curtir), ("repostar", repostar), ("comentar", comentar)]
    for destino in cfg.destinatarios.get(REDE, []):
        lista.append((f"compartilhar:{destino}",
                      lambda pg, cfg, pub, destino=destino: compartilhar(pg, cfg, pub, destino)))
    lista.append(("copiar_link", copiar_link))
    return lista


# ------------------------------------------------------------- seguir

BOTOES_CABECALHO = "main header button, main header [role=button]"
RE_SEGUIR = re.compile(r"^\s*(Seguir|Seguir de volta|Follow|Follow Back)\s*$")
RE_JA_SEGUE = re.compile(r"^\s*(Seguindo|Solicitado|Following|Requested)\s*$")


def abrir_perfil(pg: Page, perfil: str) -> None:
    pg.goto(f"{BASE}/{perfil}/", wait_until="domcontentloaded")
    pg.locator("main header").first.wait_for(state="visible", timeout=20_000)
    verificar_bloqueio(pg)


# Texto visível (innerText) dos botões do cabeçalho. O textContent não serve: depois
# de seguir, o botão ganha um ícone cujo título ("Ícone de seta para baixo") entra nele.
JS_BOTOES_CABECALHO = """() => [...document.querySelectorAll('main header button, main header [role="button"]')]
    .map(b => (b.innerText || '').trim())"""


def _estado_seguir(pg: Page) -> tuple[str | None, int | None]:
    """('segue' | 'seguir' | None, índice do botão no cabeçalho)."""
    textos = pg.evaluate(JS_BOTOES_CABECALHO)
    for indice, texto in enumerate(textos):
        if RE_JA_SEGUE.match(texto):
            return "segue", indice
    for indice, texto in enumerate(textos):
        if RE_SEGUIR.match(texto):
            return "seguir", indice
    return None, None


def seguir(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    """Segue o perfil se ainda não segue. Nunca clica em "Seguindo" (deixaria de seguir)."""
    esperar_ate(lambda: _estado_seguir(pg)[0] is not None, 8)  # os botões aparecem depois do cabeçalho
    estado, indice = _estado_seguir(pg)
    if estado == "segue":
        return Resultado("ja_estava_feito", "já segue o perfil")
    if estado is None:
        raise RuntimeError("não encontrei o botão Seguir do perfil; não cliquei")
    if cfg.simular:
        return Resultado("simulado")
    botao = pg.locator(BOTOES_CABECALHO).nth(indice)

    def ainda_nao_segue() -> None:
        if _estado_seguir(pg) != ("seguir", indice):
            raise RuntimeError("o botão de seguir mudou antes do clique; não cliquei")

    antes_de_clicar()
    clicar(pg, botao, conferir_antes=ainda_nao_segue)
    if not esperar_ate(lambda: _estado_seguir(pg)[0] == "segue", 15):
        verificar_bloqueio(pg)
        raise NaoConfirmado("o botão não mudou para Seguindo")
    return Resultado("feito")


# ------------------------------------------------------------ stories

# O anel em volta da foto do perfil: colorido = stories novos; cinza = já vistos.
JS_ANEL_STORIES = """() => {
    const img = document.querySelector('main header img');
    const botao = img && img.closest('[role="button"]');
    const anel = botao && botao.querySelector('canvas');
    if (!anel) return 'nenhum';
    try {
        const c = anel.getContext('2d'), w = anel.width, h = anel.height;
        const pontos = [[w / 2, 2], [2, h / 2], [w / 2, h - 3], [w - 3, h / 2]];
        for (const [x, y] of pontos) {
            const [r, g, b, a] = c.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
            if (a > 0 && Math.max(r, g, b) - Math.min(r, g, b) > 60) return 'novos';
        }
        return 'vistos';
    } catch (e) { return 'novos'; }
}"""
MAXIMO_STORIES = 60


def ver_stories(conn, pg: Page, rodada_id: int | None, cfg: Config, perfil: str,
                conta: str | None, resumo: list[LinhaResumo]) -> None:
    """Assiste os stories ainda não vistos do perfil e curte os que não estão curtidos.

    Só abre se o anel da foto estiver colorido. Para quando o visualizador sai
    dos stories deste perfil (ele passa sozinho para o próximo da bandeja).
    """
    abrir_perfil(pg, perfil)
    estado = pg.evaluate(JS_ANEL_STORIES)
    if estado != "novos":
        print(f"[{REDE}] @{perfil}: {'nenhum story novo' if estado == 'vistos' else 'sem stories'}.")
        return
    clicar(pg, pg.locator("main header img").first)
    if not esperar_ate(lambda: f"/stories/{perfil.lower()}/" in pg.url.lower(), 10):
        print(f"[{REDE}] @{perfil}: os stories não abriram.")
        return
    vistos = curtidos = 0
    ultimo = None
    while vistos < MAXIMO_STORIES and f"/stories/{perfil.lower()}/" in pg.url.lower():
        verificar_bloqueio(pg)
        atual = pg.url
        if atual == ultimo:
            break  # não avançou: fim dos stories
        ultimo = atual
        vistos += 1
        achado = re.search(r"/stories/[^/]+/(\d+)", atual)
        codigo = f"story:{achado.group(1)}" if achado else None
        video = monitorar_video(pg, f"story-{vistos}")
        pausa((2.0, 4.5))  # assiste um pouco antes de curtir
        if codigo and not cfg.simular:
            curtidos += _curtir_story(conn, pg, rodada_id, cfg, perfil, conta, codigo, atual)
        # Espera o story acabar (o visualizador avança sozinho) ou avança à mão.
        limite = (video["duracao"] - video["tempo"] + 3) if video and video.get("duracao") else 8
        if not esperar_ate(lambda: pg.url != atual, min(max(limite, 4), 70), 0.5):
            avancar = pg.locator('svg[aria-label="Avançar"], svg[aria-label="Next"]').first
            if not avancar.count() or not avancar.is_visible():
                break
            clicar(pg, avancar)
            esperar_ate(lambda: pg.url != atual, 5, 0.3)
    fechar = pg.locator('svg[aria-label="Fechar"], svg[aria-label="Close"]').first
    if fechar.count() and fechar.is_visible():
        clicar(pg, fechar)
    linha = LinhaResumo(Publicacao(REDE, perfil, "stories", 0, f"stories:@{perfil}", f"{BASE}/stories/{perfil}/"))
    linha.acoes = {"stories vistos": str(vistos), "stories curtidos": str(curtidos)}
    resumo.append(linha)
    print(f"[{REDE}] @{perfil}: {vistos} stories vistos, {curtidos} curtidos.")


def _curtir_story(conn, pg: Page, rodada_id: int | None, cfg: Config, perfil: str,
                  conta: str | None, codigo: str, url: str) -> int:
    """Curte o story na tela se ainda não estiver curtido. Devolve 1 se curtiu.

    O visualizador avança sozinho: se o story na tela não for mais o `url`
    (o que estava sendo assistido), não curte, para não curtir outro story
    (talvez de outro perfil) e gravar no lugar errado.
    """
    def mesmo_story() -> bool:
        return pg.url == url

    if not mesmo_story() or banco.acoes_concluidas(conn, REDE, codigo, conta):
        return 0
    if pg.locator(SVG_DESCURTIR).count():
        return 0  # já curtido (talvez à mão)
    coracao = pg.locator(SVG_CURTIR).last
    if not coracao.count() or not coracao.is_visible():
        return 0
    try:
        controlar_ritmo(conn, cfg.protecao, REDE, conta, "curtir_story", pode_esperar=False)
    except PularAcao as erro:
        print(f"    {'story':<13} sem curtir ({erro})")
        return 0
    if not mesmo_story():
        return 0
    marcado: dict[str, int] = {}

    def ainda_nao_curtido() -> None:
        if not mesmo_story():
            raise RuntimeError("o story mudou antes do clique; não cliquei")
        if pg.locator(SVG_DESCURTIR).count():
            raise RuntimeError("o story já está curtido; não cliquei")
        # Tudo conferido, com o mouse parado no coração: a marca vai para o banco
        # agora, logo antes do clique (como em comum.processar_publicacao).
        marcado["id"] = banco.registrar_publicacao(conn, REDE, codigo, perfil, "stories", 0, url)
        banco.registrar_acao(conn, marcado["id"], rodada_id, "curtir", "nao_confirmado",
                             "clique enviado, aguardando confirmação", conta=conta, alvo=perfil)

    try:
        clicar(pg, coracao, conferir_antes=ainda_nao_curtido)
    except RuntimeError:
        return 0  # não clicou nem marcou: o story pode ser curtido numa próxima visita
    except PlaywrightError as erro:
        if "id" not in marcado:
            return 0
        # O clique pode ter saído ou não: a marca fica (nunca repete) e, se o
        # coração já aparece curtido no mesmo story, conta como feito.
        if not (mesmo_story() and pg.locator(SVG_DESCURTIR).count()):
            print(f"    {'story':<13} curtida não confirmada ({erro.message.splitlines()[0]})")
            return 0
    if esperar_ate(lambda: pg.locator(SVG_DESCURTIR).count() > 0, 6):
        banco.registrar_acao(conn, marcado["id"], rodada_id, "curtir", "feito", "story", conta=conta, alvo=perfil)
        return 1
    return 0


# ------------------------------------------------------------- perfil-alvo

def processar_perfil(conn: sqlite3.Connection, pg: Page, rodada_id: int | None, cfg: Config,
                     perfil: str, resumo: list[LinhaResumo], conta: str | None = None) -> None:
    """Trabalha o perfil-alvo `perfil` com a conta logada `conta` ("@usuario").

    Na ordem: seguir (se ligado), stories (se ligado) e as abas Posts, Reels e
    Reposts. Um reel que também aparece em Posts é trabalhado uma vez só (o
    banco pula as ações já feitas naquela publicação).
    """
    if cfg.seguir:
        alvo = Publicacao(REDE, perfil, "perfil", 0, f"@{perfil.lower()}", f"{BASE}/{perfil}/")
        processar_publicacao(conn, pg, rodada_id, cfg, alvo, abrir=lambda: abrir_perfil(pg, perfil),
                             acoes=[("seguir", seguir)], resumo=resumo, fechar=lambda: fechar_janelas(pg),
                             ver_midia=lambda *_: "perfil", conta=conta)
    if cfg.ver_stories:
        try:
            ver_stories(conn, pg, rodada_id, cfg, perfil, conta, resumo)
        except PlaywrightTimeout as erro:
            print(f"[{REDE}] @{perfil}: não consegui ver os stories ({erro.message.splitlines()[0]}).")
    for aba, sufixo in ABAS:
        publicacoes = listar_publicacoes(pg, cfg, perfil, aba, sufixo)
        if publicacoes is None:
            print(f"[{REDE}] @{perfil}: o perfil não tem a aba {aba}, pulando.")
            continue
        if not publicacoes:
            print(f"[{REDE}] @{perfil}: nenhuma publicação na aba {aba} dentro do limite.")
            continue
        print(f"[{REDE}] @{perfil}: {len(publicacoes)} publicações na aba {aba}.")
        for pub in publicacoes:
            processar_publicacao(conn, pg, rodada_id, cfg, pub,
                                 abrir=lambda pub=pub: abrir_publicacao(pg, pub),
                                 acoes=acoes(cfg), resumo=resumo, fechar=lambda: fechar_janelas(pg),
                                 conferir=lambda pub=pub: conferir_publicacao(pg, pub),
                                 ver_midia=ver_midia, conta=conta)
            pausa(cfg.protecao.pausa_entre_publicacoes)
