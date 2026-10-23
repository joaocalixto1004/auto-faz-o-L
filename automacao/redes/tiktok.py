"""Ações no TikTok: curtir, comentar, adicionar aos favoritos e copiar o link.

Antes de cada ação, o script confere na própria página se aquilo já foi feito
(vídeo já curtido, já nos favoritos, comentário seu já presente), para não
repetir o que você fez à mão antes do sistema existir.

Os vídeos são abertos clicando na grade do perfil, porque o TikTok recusa a
abertura direta pelo endereço do vídeo quando o navegador é automatizado.
"""

import random
import re
import sqlite3
from datetime import datetime, timezone

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from automacao.redes.comum import (Acao, Bloqueio,
                                   Config, LinhaResumo, NaoConfirmado, Publicacao, Resultado,
                                   antes_de_clicar, clicar, coletar_da_grade, digitar, esperar_ate, esperar_um_pouco,
                                   ler_clipboard, pausa, preparar_copia, processar_publicacao, rolar,
                                   sortear_comentario, tem_video_visivel)

REDE = "TikTok"
BASE = "https://www.tiktok.com"
# (nome da aba, data-e2e da aba, data-e2e dos itens da grade)
ABAS = (("videos", "videos-tab", "user-post-item"),
        ("republicacoes", "repost-tab", "user-repost-item"))
RE_CODIGO = re.compile(r"/(?:video|photo)/(\d+)")
SEL_CAPTCHA = ("#captcha-verify-container-main-page, .captcha-verify-container, "
               "#tiktok-verify-ele, [id^='captcha_container']")

BOTAO_CURTIR = 'button:has([data-e2e="browse-like-icon"])'
TRACOS_FAVORITO = '[data-e2e="browse-favorite-icon"] svg path'
BOTAO_FAVORITO = 'button:has([data-e2e="browse-favorite-icon"])'
CAMPO_COMENTARIO = '[data-e2e="comment-input"] [contenteditable="true"]'
CAMPO_PROCURAR = 'input[placeholder="Procurar"], input[placeholder="Search"]'

_meu_usuario: str | None = None


def definir_meu_usuario(usuario: str | None) -> None:
    """Informa a conta logada (sem @) ao trocar de perfil; None faz ler de novo da página."""
    global _meu_usuario
    _meu_usuario = usuario.lstrip("@") if usuario else None


# ------------------------------------------------------------------ apoio

def data_publicacao(codigo: str) -> datetime | None:
    """O id do vídeo do TikTok guarda o horário de criação nos 32 bits altos."""
    try:
        return datetime.fromtimestamp(int(codigo) >> 32, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def meu_usuario(pg: Page) -> str | None:
    """Usuário logado, lido do link "Perfil" do menu lateral."""
    global _meu_usuario
    if _meu_usuario is None:
        href = pg.locator('[data-e2e="nav-profile"]').first.get_attribute("href", timeout=5000) or ""
        _meu_usuario = href.split("@", 1)[1].strip("/") if "@" in href else None
    return _meu_usuario


def esperar_captcha(pg: Page) -> None:
    """Um captcha é o TikTok desconfiando: conta como aviso de bloqueio e para tudo.

    (O nome ficou da versão antiga, que esperava a pessoa resolver.)
    """
    if pg.locator(SEL_CAPTCHA).first.is_visible():
        raise Bloqueio("o TikTok pediu uma verificação (captcha)")


# ---------------------------------------------------------------- navegação

def abrir_aba(pg: Page, perfil: str, tab: str, item: str) -> bool:
    """Abre o perfil na aba pedida. False se o perfil não tiver essa aba."""
    pg.goto(f"{BASE}/@{perfil}", wait_until="domcontentloaded")
    esperar_um_pouco(pg, 2000)
    esperar_captcha(pg)
    pg.locator('[data-e2e="videos-tab"]').first.wait_for(state="visible", timeout=20_000)
    try:
        meu_usuario(pg)
    except PlaywrightTimeout:
        pass
    # A página tem cópias ocultas das abas; só vale a que aparece na tela.
    aba = pg.locator(f'[data-e2e="{tab}"] >> visible=true').first
    try:
        aba.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeout:
        return False
    clicar(pg, aba)
    try:
        pg.locator(f'[data-e2e="{item}"] a').first.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeout:
        pass  # aba vazia
    return True


def listar_publicacoes(pg: Page, cfg: Config, perfil: str, aba: str, item: str) -> list[Publicacao]:
    def ler_hrefs() -> list[str]:
        return pg.locator(f'[data-e2e="{item}"] a').evaluate_all(
            "els => els.map(e => e.getAttribute('href'))")

    def extrair(href: str) -> tuple[str, str] | None:
        achado = RE_CODIGO.search(href or "")
        if not achado:
            return None
        return achado.group(1), href if href.startswith("http") else BASE + href

    return coletar_da_grade(pg, ler_hrefs, extrair, data_publicacao, cfg, REDE, perfil, aba)


def abrir_publicacao(pg: Page, pub: Publicacao, tab: str, item: str) -> None:
    """Clica no vídeo na grade, reabrindo o perfil e rolando se for preciso."""
    seletor = f'[data-e2e="{item}"] a[href*="{pub.codigo}"]'
    if not pg.locator(seletor).count():
        abrir_aba(pg, pub.perfil, tab, item)
        for _ in range(10):
            if pg.locator(seletor).count():
                break
            rolar(pg)
            esperar_um_pouco(pg, 1200)
    clicar(pg, pg.locator(seletor).first, timeout=15_000)
    esperar_um_pouco(pg, 1500)
    esperar_captcha(pg)
    pg.locator(BOTAO_CURTIR).first.wait_for(state="visible", timeout=20_000)


RE_AVISO = re.compile(r"rápido demais|too fast|Tente novamente mais tarde|Try again later|"
                      r"atividade incomum|unusual activity", re.I)


def verificar_aviso(pg: Page) -> None:
    """Avisos de limite do TikTok aparecem em balões (toast) sobre a página."""
    aviso = pg.locator('[role="alert"], [class*="toast" i]').filter(has_text=RE_AVISO)
    if aviso.count() and aviso.first.is_visible():
        raise Bloqueio(f"o TikTok avisou: {aviso.first.inner_text()[:120]!r}")


def confirmar(pg: Page, condicao, timeout_s: float) -> bool:
    """Espera a confirmação de um clique vigiando os avisos da rede.

    Os balões de "rápido demais" somem em poucos segundos: olhar só no fim da
    espera deixaria passar o aviso. Levanta Bloqueio se um aparecer.
    """
    def checar() -> bool:
        verificar_aviso(pg)
        return condicao()

    resultado = esperar_ate(checar, timeout_s)
    verificar_aviso(pg)
    return resultado


def conferir_publicacao(pg: Page, pub: Publicacao) -> None:
    """Garante que o painel ainda mostra o mesmo vídeo (o TikTok pode avançar sozinho)."""
    esperar_captcha(pg)
    verificar_aviso(pg)
    if pub.codigo not in pg.url:
        raise RuntimeError(f"o TikTok trocou de vídeo (endereço atual: {pg.url})")


def fechar_publicacao(pg: Page) -> None:
    fechar = pg.locator('[data-e2e="browse-close"]').first
    if fechar.is_visible():
        clicar(pg, fechar)
        esperar_um_pouco(pg, 1500)


# Posts de fotos do TikTok são um carrossel (Swiper) com setas pequenas dos
# dois lados dos pontinhos. O total vem dos slides que não são cópias.
JS_CARROSSEL = """() => {
    const visivel = e => { const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
    const cont = [...document.querySelectorAll('[class*="DivSwiperContainer"]')].find(visivel);
    if (!cont) return null;
    const sw = cont.querySelector('.swiper');
    const total = cont.querySelectorAll('.swiper-slide:not(.swiper-slide-duplicate)').length;
    const atual = sw && sw.swiper ? sw.swiper.realIndex : null;
    let raiz = cont;
    for (let i = 0; i < 3 && raiz.parentElement; i++) raiz = raiz.parentElement;
    const setas = [...raiz.querySelectorAll('button')].filter(b => {
        const r = b.getBoundingClientRect(); return r.width > 10 && r.width < 40 && r.height < 40; })
        .sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
    const prox = setas.length >= 2 ? setas[setas.length - 1].getBoundingClientRect() : null;
    return {total, atual, proximo: prox ? {x: prox.x, y: prox.y, w: prox.width, h: prox.height} : null};
}"""
MAXIMO_ITENS_CARROSSEL = 35


def ver_midia(pg: Page, cfg: Config, pub: Publicacao) -> str:
    """Em posts de fotos, passa por todas as imagens com pausas. Vídeos: só informa."""
    info = pg.evaluate(JS_CARROSSEL)
    if not info or info["total"] <= 1:
        return "vídeo" if tem_video_visivel(pg) else "foto"
    total = info["total"]
    vistos = {info["atual"] if info["atual"] is not None else 0}
    cliques = 0
    # O carrossel também avança sozinho com a música; segue até ter visto todas.
    while len(vistos) < total and cliques < min(total * 2, MAXIMO_ITENS_CARROSSEL):
        pausa(cfg.protecao.pausa_item_carrossel)
        info = pg.evaluate(JS_CARROSSEL)
        if not info or not info["proximo"]:
            break
        seta = info["proximo"]
        x = seta["x"] + seta["w"] * random.uniform(0.3, 0.7)
        y = seta["y"] + seta["h"] * random.uniform(0.3, 0.7)
        pg.mouse.move(x, y, steps=random.randint(10, 22))
        pg.mouse.click(x, y, delay=random.randint(50, 150))
        cliques += 1
        esperar_um_pouco(pg, 800)
        info = pg.evaluate(JS_CARROSSEL)
        vistos.add(info["atual"] if info and info["atual"] is not None else len(vistos))
    pausa(cfg.protecao.pausa_item_carrossel)
    return f"carrossel de fotos com {total} itens, visto até o fim"


# ------------------------------------------------------------------- ações

def curtir(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    botao = pg.locator(BOTAO_CURTIR).first
    estado = botao.get_attribute("aria-pressed")
    if estado == "true":
        return Resultado("ja_estava_feito", "o vídeo já estava curtido")
    if estado != "false":
        raise RuntimeError(f"não consegui saber se o vídeo já foi curtido (aria-pressed={estado!r}); "
                           "não cliquei para não desfazer uma curtida")
    if cfg.simular:
        return Resultado("simulado")
    def ainda_nao_curtido() -> None:
        conferir_publicacao(pg, pub)  # o TikTok pode ter trocado de vídeo durante o movimento
        if botao.get_attribute("aria-pressed") != "false":
            raise RuntimeError("o estado do botão de curtir mudou antes do clique; não cliquei")

    antes_de_clicar()
    clicar(pg, botao, conferir_antes=ainda_nao_curtido)
    if not confirmar(pg, lambda: botao.get_attribute("aria-pressed") == "true", 10):
        esperar_captcha(pg)
        raise NaoConfirmado("o botão de curtir não ficou marcado")
    return Resultado("feito")


JS_CAIXA_COMENTARIOS = """() => {
    let c = document.querySelector('[data-e2e="comment-level-1"]');
    while (c && !(c.scrollHeight > c.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(c).overflowY)))
        c = c.parentElement;
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2, fim: c.scrollTop + c.clientHeight >= c.scrollHeight - 5};
}"""


def quantos_comentarios(pg: Page) -> int:
    return pg.locator('[data-e2e^="comment-username"]').count()


def voltar_ao_topo_do_painel(pg: Page) -> None:
    """Rola o painel da direita de volta ao topo (onde ficam curtir, favoritos e compartilhar)."""
    botao = pg.get_by_role("button", name=re.compile(r"Voltar para o topo|Back to top", re.I))
    if not botao.count():
        botao = pg.get_by_text(re.compile(r"^(Voltar para o topo|Back to top)$", re.I))
    if botao.count() and botao.first.is_visible():
        clicar(pg, botao.first)
        esperar_um_pouco(pg, 900)
        return
    caixa = pg.evaluate(JS_CAIXA_COMENTARIOS)
    if caixa:
        pg.mouse.move(caixa["x"], caixa["y"], steps=random.randint(6, 14))
        for _ in range(6):
            pg.mouse.wheel(0, -random.randint(1500, 2500))
            esperar_um_pouco(pg, 250, 500)


def ja_comentei(pg: Page, usuario: str, rodadas: int = 8) -> bool:
    """Procura um comentário seu no vídeo, rolando a lista de comentários algumas vezes.

    No fim, volta o painel ao topo: em vídeos com muitos comentários a rolagem
    tira da tela os botões de favoritos, compartilhar e copiar link.
    """
    try:
        return _procurar_meu_comentario(pg, usuario, rodadas)
    finally:
        voltar_ao_topo_do_painel(pg)


def _procurar_meu_comentario(pg: Page, usuario: str, rodadas: int) -> bool:
    seletor = f'[data-e2e^="comment-username"] a[href="/@{usuario}"], a[data-e2e^="comment-avatar"][href="/@{usuario}"]'
    for _ in range(rodadas):
        if pg.locator(seletor).count():
            return True
        caixa = pg.evaluate(JS_CAIXA_COMENTARIOS)
        if not caixa or caixa["fim"]:
            return False
        pg.mouse.move(caixa["x"], caixa["y"], steps=random.randint(6, 14))
        pg.mouse.wheel(0, random.randint(500, 900))
        esperar_um_pouco(pg, 900, 1600)
    return pg.locator(seletor).count() > 0


def comentar(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    vistos = None
    if cfg.verificar_comentarios:  # só nos testes, para não acumular comentários
        usuario = meu_usuario(pg)
        if not usuario:
            raise RuntimeError("não consegui identificar seu usuário para procurar comentários antigos")
        if ja_comentei(pg, usuario):
            return Resultado("ja_estava_feito", f"já existe comentário de @{usuario} no vídeo")
        vistos = quantos_comentarios(pg)
    extra = f" (nenhum comentário seu entre os {vistos} carregados)" if vistos is not None else ""
    caixa = pg.locator('[data-e2e="comment-input"]').first
    caixa.wait_for(state="visible", timeout=10_000)
    if cfg.simular:
        return Resultado("simulado", extra.strip(" ()") or None)
    # A caixa visível fica por cima do editor; clica nela e confere o foco.
    clicar(pg, caixa)
    campo = pg.locator(CAMPO_COMENTARIO).first
    focado = "e => e === document.activeElement || e.contains(document.activeElement)"
    if not esperar_ate(lambda: campo.evaluate(focado), 3):
        campo.focus()
    texto = sortear_comentario(cfg)
    digitar(pg, texto)
    antes_de_clicar()   # também confere que o TikTok não trocou de vídeo durante a digitação
    clicar(pg, pg.locator('[data-e2e="comment-post"]').first)
    # Depois de publicar, o TikTok limpa o campo.
    if not confirmar(pg, lambda: pg.locator(CAMPO_COMENTARIO).first.inner_text().strip() == "", 15):
        esperar_captcha(pg)
        raise NaoConfirmado("o campo de comentário não foi limpo depois de publicar")
    return Resultado("feito", f"{texto}{extra}")


RE_RGB = re.compile(r"rgba?\(\s*(\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)\s*[,/)]")


def _rgb(cor: str) -> tuple[float, float, float] | None:
    """(r, g, b) de uma cor "rgb(...)"/"rgba(...)"; None para qualquer outro formato."""
    achou = RE_RGB.match(cor.strip())
    return tuple(float(n) for n in achou.groups()) if achou else None


def _amarelo(cor: str) -> bool:
    rgb = _rgb(cor)
    return rgb is not None and rgb[0] > 200 and rgb[1] > 150 and rgb[2] < 120


def _cores_favorito(pg: Page) -> list[str]:
    """Cores de preenchimento dos traços do ícone de favoritos."""
    return pg.locator(TRACOS_FAVORITO).evaluate_all("els => els.map(e => getComputedStyle(e).fill)")


def _favoritado(pg: Page) -> bool | None:
    """True/False pelo ícone; None se o ícone não estiver na tela ou se uma cor não der para ler.

    Nos favoritos, um dos traços do ícone fica amarelo (#FACE15); fora dele,
    todos usam a cor do texto. A cor do ícone em si não serve: ela muda com o
    mouse em cima. Só diz "não favoritado" depois de ler a cor de todos os
    traços (os sem preenchimento não contam): um formato desconhecido
    (gradiente, color(), oklch...) poderia esconder o amarelo, e clicar
    desfaria o favorito.
    """
    cores = _cores_favorito(pg)
    if any(_amarelo(cor) for cor in cores):
        return True
    pintadas = [cor for cor in cores if cor.strip() != "none"]
    if not pintadas or any(_rgb(cor) is None for cor in pintadas):
        return None
    return False


def favoritar(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    estado = _favoritado(pg)
    if estado is True:
        return Resultado("ja_estava_feito", "o vídeo já estava nos favoritos")
    if estado is None:
        raise RuntimeError(f"não consegui ler o ícone de favoritos (cores: {_cores_favorito(pg)}); "
                           "não cliquei para não desfazer um favorito")
    if cfg.simular:
        return Resultado("simulado", f"cores do ícone: {_cores_favorito(pg)}")
    def ainda_nao_favoritado() -> None:
        conferir_publicacao(pg, pub)
        if _favoritado(pg) is not False:
            raise RuntimeError("o estado do botão de favoritos mudou antes do clique; não cliquei")

    antes_de_clicar()
    clicar(pg, pg.locator(BOTAO_FAVORITO).first, conferir_antes=ainda_nao_favoritado)
    if not confirmar(pg, lambda: _favoritado(pg) is True, 10):
        esperar_captcha(pg)
        raise NaoConfirmado("o ícone de favoritos não ficou amarelo")
    return Resultado("feito")


def compartilhar(pg: Page, cfg: Config, pub: Publicacao, destino: str) -> Resultado:
    """Envia o vídeo por mensagem pelo botão "Enviar para amigos"."""
    painel = pg.locator('[role="dialog"]').filter(has=pg.locator(CAMPO_PROCURAR)).last
    for tentativa in range(3):  # abrir a janela não muda nada na rede; pode tentar de novo
        clicar(pg, pg.locator('[data-e2e="video-share-message"]').first)
        if esperar_ate(painel.is_visible, 6):
            break
        pausa((1.0, 2.0))
    painel.wait_for(state="visible", timeout=5_000)
    try:
        clicar(pg, painel.locator(CAMPO_PROCURAR).first)
        digitar(pg, destino)
        conta = painel.locator('[class*="DivUserItemContainer"]').filter(
            has=pg.get_by_text(destino, exact=True)).first
        conta.wait_for(state="visible", timeout=15_000)
        if cfg.simular:
            return Resultado("simulado", f"conta {destino} encontrada")
        clicar(pg, conta)
        enviar = painel.get_by_role("button", name=re.compile(r"^(Enviar|Send)$")).first
        enviar.wait_for(state="visible", timeout=10_000)
        pausa((0.8, 1.8))
        antes_de_clicar()   # também confere que o vídeo na tela ainda é o certo
        clicar(pg, enviar)
        if not confirmar(pg, lambda: not painel.is_visible(), 15):
            esperar_captcha(pg)
            raise NaoConfirmado("a janela de envio não fechou depois de enviar")
        return Resultado("feito", f"enviado para {destino}")
    finally:
        fechar = painel.locator('button[aria-label="close"]').first
        if painel.is_visible() and fechar.count():
            clicar(pg, fechar)


def copiar_link(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    botao = pg.locator('[data-e2e="browse-copy"]').first
    botao.wait_for(state="visible", timeout=10_000)
    texto_tela = pg.locator('[data-e2e="browse-video-link"]').first.inner_text().strip()
    if cfg.simular:
        return Resultado("simulado", texto_tela)
    preparar_copia(pg)
    clicar(pg, botao)
    link = ler_clipboard(pg)
    if link and pub.codigo in link:
        return Resultado("feito", link=link)
    reserva = texto_tela if pub.codigo in texto_tela else pub.url
    return Resultado("feito", f"não consegui ler o link copiado ({link!r}), usei o link da tela",
                     link=reserva)


def acoes(cfg: Config) -> list[tuple[str, Acao]]:
    """As ações na ordem pedida, com um compartilhamento por destinatário."""
    lista: list[tuple[str, Acao]] = [("curtir", curtir), ("comentar", comentar), ("favoritar", favoritar)]
    for destino in cfg.destinatarios.get(REDE, []):
        lista.append((f"compartilhar:{destino}",
                      lambda pg, cfg, pub, destino=destino: compartilhar(pg, cfg, pub, destino)))
    lista.append(("copiar_link", copiar_link))
    return lista


# ------------------------------------------------------------- seguir

RE_SEGUIR = re.compile(r"^\s*(Seguir|Seguir de volta|Follow|Follow back)\s*$", re.I)
RE_JA_SEGUE = re.compile(r"^\s*(Seguindo|Amigos|Solicitado|Following|Friends|Requested)\s*$", re.I)
# O botão do perfil fica no cabeçalho; os outros "Seguir" da página são sugestões.
BOTAO_SEGUIR_PERFIL = '.user-profile-header [data-e2e="follow-button"], [data-e2e="user-page"] [data-e2e="follow-button"]'


def _botao_seguir(pg: Page):
    botao = pg.locator(BOTAO_SEGUIR_PERFIL).first
    if botao.count():
        return botao
    # Reserva: o primeiro botão de seguir do topo da página (as sugestões ficam mais abaixo).
    candidatos = pg.locator('[data-e2e="follow-button"]')
    for i in range(min(candidatos.count(), 3)):
        caixa = candidatos.nth(i).bounding_box()
        if caixa and caixa["y"] < 300:
            return candidatos.nth(i)
    return None


def abrir_perfil(pg: Page, perfil: str) -> None:
    pg.goto(f"{BASE}/@{perfil}", wait_until="domcontentloaded")
    esperar_um_pouco(pg, 2000)
    esperar_captcha(pg)
    pg.locator('[data-e2e="videos-tab"]').first.wait_for(state="visible", timeout=20_000)


def seguir(pg: Page, cfg: Config, pub: Publicacao) -> Resultado:
    """Segue o perfil se ainda não segue. Nunca clica em "Seguindo"/"Amigos" (deixaria de seguir)."""
    botao = _botao_seguir(pg)
    if botao is None:
        raise RuntimeError("não encontrei o botão Seguir do perfil; não cliquei")
    texto = botao.inner_text().strip()
    if RE_JA_SEGUE.match(texto):
        return Resultado("ja_estava_feito", f"já segue (botão: {texto})")
    if not RE_SEGUIR.match(texto):
        raise RuntimeError(f"botão de seguir com texto inesperado ({texto!r}); não cliquei")
    if cfg.simular:
        return Resultado("simulado")

    def ainda_nao_segue() -> None:
        verificar_aviso(pg)
        if not RE_SEGUIR.match(botao.inner_text().strip()):
            raise RuntimeError("o botão de seguir mudou antes do clique; não cliquei")

    antes_de_clicar()
    clicar(pg, botao, conferir_antes=ainda_nao_segue)
    if not confirmar(pg, lambda: bool(RE_JA_SEGUE.match(botao.inner_text().strip())), 10):
        esperar_captcha(pg)
        verificar_aviso(pg)
        raise NaoConfirmado("o botão não mudou para Seguindo")
    return Resultado("feito", botao.inner_text().strip())


def processar_perfil(conn: sqlite3.Connection, pg: Page, rodada_id: int | None, cfg: Config,
                     perfil: str, resumo: list[LinhaResumo], conta: str | None = None) -> None:
    """Trabalha o perfil-alvo `perfil` com a conta logada `conta` ("@usuario").

    Segue o perfil (se ligado) e depois trabalha as abas Vídeos e Republicações.
    Stories do TikTok não aparecem na versão web; a opção vale só no Instagram.
    """
    if cfg.seguir:
        alvo = Publicacao(REDE, perfil, "perfil", 0, f"@{perfil.lower()}", f"{BASE}/@{perfil}")
        processar_publicacao(conn, pg, rodada_id, cfg, alvo, abrir=lambda: abrir_perfil(pg, perfil),
                             acoes=[("seguir", seguir)], resumo=resumo,
                             ver_midia=lambda *_: "perfil", conta=conta)
    for aba, tab, item in ABAS:
        if not abrir_aba(pg, perfil, tab, item):
            print(f"[{REDE}] @{perfil}: o perfil não tem a aba {aba}, pulando.")
            continue
        publicacoes = listar_publicacoes(pg, cfg, perfil, aba, item)
        if not publicacoes:
            print(f"[{REDE}] @{perfil}: nenhuma publicação na aba {aba} dentro do limite.")
            continue
        print(f"[{REDE}] @{perfil}: {len(publicacoes)} publicações na aba {aba}.")
        for pub in publicacoes:
            processar_publicacao(conn, pg, rodada_id, cfg, pub,
                                 abrir=lambda pub=pub: abrir_publicacao(pg, pub, tab, item),
                                 acoes=acoes(cfg), resumo=resumo, fechar=lambda: fechar_publicacao(pg),
                                 conferir=lambda pub=pub: conferir_publicacao(pg, pub),
                                 ver_midia=ver_midia, conta=conta)
            pausa(cfg.protecao.pausa_entre_publicacoes)
