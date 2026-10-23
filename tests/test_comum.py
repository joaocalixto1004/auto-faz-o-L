"""Testes do redes/comum.py com objetos falsos no lugar da página do Playwright.

Nenhum navegador é aberto: a grade do perfil, a página e as ações são
imitações pequenas, e as pausas/esperas de vídeo viram funções vazias.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import Error as PlaywrightError

from automacao import banco
from automacao.abrir_redes import NavegadorFechado
from automacao.redes import comum


def config(**opcoes) -> comum.Config:
    base = dict(modo="quantidade", quantidade=3, semanas=2, comentario="Boa!!!",
                compartilhar_com="fulano", limite_diario=40, simular=False)
    base.update(opcoes)
    return comum.Config(**base)


# ------------------------------------------------------------------ Config

def test_config_corte_no_modo_quantidade_e_none():
    assert config(modo="quantidade").corte is None


def test_config_corte_no_modo_tempo():
    antes = datetime.now(timezone.utc) - timedelta(weeks=3)
    corte = config(modo="tempo", semanas=3).corte
    depois = datetime.now(timezone.utc) - timedelta(weeks=3)
    assert corte.tzinfo is not None
    assert antes <= corte <= depois


def test_config_descricao():
    assert config(modo="quantidade", quantidade=5).descricao() == "as 5 primeiras publicações de cada aba"
    assert config(modo="tempo", semanas=2).descricao() == "publicações das últimas 2 semana(s)"


# --------------------------------------------------------- coletar_da_grade

class GradeFalsa:
    """Imita a página com a grade de um perfil: cada rolagem mostra mais `por_vez` itens."""

    def __init__(self, hrefs, por_vez):
        self.hrefs = list(hrefs)
        self.por_vez = por_vez
        self.rolagens = 0
        self.esperas = []

    def ler_hrefs(self):
        return self.hrefs[: self.por_vez * (self.rolagens + 1)]

    def wait_for_timeout(self, ms):
        self.esperas.append(ms)


@pytest.fixture(autouse=True)
def rolar_falso(monkeypatch):
    """comum.rolar só conta a rolagem na grade falsa, sem mouse nem espera."""
    def rolar(pg, passos=(3, 6)):
        pg.rolagens += 1
    monkeypatch.setattr(comum, "rolar", rolar)


def extrair(href):
    achado = re.search(r"/p/([^/]+)/", href or "")
    return (achado.group(1), "https://www.instagram.com" + href) if achado else None


def links(*codigos):
    return [f"/p/{codigo}/" for codigo in codigos]


def coletar(grade, cfg, datas=None):
    datas = datas or {}
    return comum.coletar_da_grade(grade, grade.ler_hrefs, extrair, datas.get, cfg,
                                  "Instagram", "fulano", "posts")


def test_modo_quantidade_pega_as_primeiras_sem_repetir():
    grade = GradeFalsa(["/p/A/", "/p/A/", "/explore/", "/p/B/", "/p/C/", "/p/D/"], por_vez=10)
    pubs = coletar(grade, config(quantidade=3))
    assert [p.codigo for p in pubs] == ["A", "B", "C"]
    assert [p.posicao for p in pubs] == [1, 2, 3]
    assert pubs[0].url == "https://www.instagram.com/p/A/"
    assert {(p.rede, p.perfil, p.aba) for p in pubs} == {("Instagram", "fulano", "posts")}
    assert grade.rolagens == 0   # já tinha o suficiente, não rolou


def test_modo_quantidade_rola_ate_ter_o_suficiente():
    codigos = [f"c{i}" for i in range(1, 11)]
    grade = GradeFalsa(links(*codigos), por_vez=2)
    pubs = coletar(grade, config(quantidade=5))
    assert [p.codigo for p in pubs] == codigos[:5]
    assert grade.rolagens == 2
    assert grade.esperas == [1500, 1500]


def test_modo_quantidade_com_menos_publicacoes_que_o_pedido():
    grade = GradeFalsa(links("a", "b", "c", "d"), por_vez=10)
    pubs = coletar(grade, config(quantidade=10))
    assert [p.codigo for p in pubs] == ["a", "b", "c", "d"]
    assert grade.rolagens == 2   # para depois de duas rolagens sem nada novo


def test_modo_quantidade_nao_filtra_por_data():
    antiga = datetime(2020, 1, 1, tzinfo=timezone.utc)
    grade = GradeFalsa(links("a", "b"), por_vez=10)
    pubs = coletar(grade, config(quantidade=2), {"a": antiga, "b": antiga})
    assert [(p.codigo, p.data) for p in pubs] == [("a", antiga), ("b", antiga)]


def test_modo_tempo_tira_fixados_antigos_e_para_de_rolar_no_corte():
    agora = datetime.now(timezone.utc)
    fixados = [f"F{i}" for i in range(1, 4)]
    recentes = [f"R{i}" for i in range(1, 11)]
    velhos = [f"V{i}" for i in range(1, 31)]
    datas = {codigo: agora - timedelta(days=60) for codigo in fixados}
    datas |= {codigo: agora - timedelta(days=i) for i, codigo in enumerate(recentes, start=1)}
    datas |= {codigo: agora - timedelta(days=20 + i) for i, codigo in enumerate(velhos, start=1)}
    grade = GradeFalsa(links(*fixados, *recentes, *velhos), por_vez=6)

    pubs = coletar(grade, config(modo="tempo", semanas=2), datas)

    assert [p.codigo for p in pubs] == recentes
    assert [p.posicao for p in pubs] == list(range(4, 14))   # posição real na grade
    assert all(p.data == datas[p.codigo] for p in pubs)
    # com 18 itens carregados os 3 últimos já são velhos: para sem carregar os 43
    assert grade.rolagens == 2


def test_modo_tempo_mantem_publicacao_sem_data():
    agora = datetime.now(timezone.utc)
    grade = GradeFalsa(links("R1", "X", "R2"), por_vez=10)
    pubs = coletar(grade, config(modo="tempo", semanas=1),
                   {"R1": agora - timedelta(days=1), "R2": agora - timedelta(days=2)})
    assert [(p.codigo, p.data is None) for p in pubs] == [("R1", False), ("X", True), ("R2", False)]


def test_modo_tempo_para_quando_a_grade_acaba():
    agora = datetime.now(timezone.utc)
    codigos = [f"R{i}" for i in range(5)]
    grade = GradeFalsa(links(*codigos), por_vez=10)
    pubs = coletar(grade, config(modo="tempo", semanas=2), {c: agora for c in codigos})
    assert [p.codigo for p in pubs] == codigos
    assert grade.rolagens == 2


def test_modo_tempo_respeita_maximo_na_grade():
    agora = datetime.now(timezone.utc)
    codigos = [f"R{i}" for i in range(200)]
    # 12 por rolagem: 12, 24, 36, 48, 60 itens (o máximo atual é 60, múltiplo de 12)
    grade = GradeFalsa(links(*codigos), por_vez=12)
    pubs = coletar(grade, config(modo="tempo", semanas=2), {c: agora for c in codigos})
    assert len(pubs) == comum.MAXIMO_NA_GRADE
    assert [p.codigo for p in pubs] == codigos[:comum.MAXIMO_NA_GRADE]
    assert grade.rolagens == comum.MAXIMO_NA_GRADE // 12 - 1


def test_modo_tempo_nunca_devolve_mais_que_o_maximo():
    agora = datetime.now(timezone.utc)
    codigos = [f"R{i}" for i in range(200)]
    grade = GradeFalsa(links(*codigos), por_vez=25)
    pubs = coletar(grade, config(modo="tempo", semanas=2), {c: agora for c in codigos})
    assert len(pubs) <= comum.MAXIMO_NA_GRADE


# ---------------------------------------------------- processar_publicacao

class PaginaFalsa:
    def __init__(self, fechada=False):
        self.fechada = fechada

    def is_closed(self):
        return self.fechada


class AcaoFalsa:
    """Ação que conta as chamadas e devolve um Resultado (ou levanta um erro)."""

    def __init__(self, resultado=None, erro=None):
        self.resultado = resultado or comum.Resultado("feito")
        self.erro = erro
        self.chamadas = 0

    def __call__(self, pagina, cfg, pub):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.resultado


@pytest.fixture
def sem_espera(monkeypatch):
    """Troca pausas, vídeos e prints por funções que só anotam a chamada."""
    chamadas = {"pausa": [], "monitorar": [], "esperar_fim": [], "captura": []}
    monkeypatch.setattr(comum, "pausa", lambda intervalo=comum.PAUSA_ENTRE_ACOES: chamadas["pausa"].append(intervalo))
    monkeypatch.setattr(comum, "monitorar_video", lambda pg, token: chamadas["monitorar"].append(token))
    monkeypatch.setattr(comum, "esperar_fim_do_video", lambda pg, token: chamadas["esperar_fim"].append(token))
    monkeypatch.setattr(comum, "capturar_tela", lambda pg, pub, acao: chamadas["captura"].append(acao))
    return chamadas


class Cenario:
    """Roda processar_publicacao contando aberturas e fechamentos da publicação."""

    def __init__(self, conn):
        self.conn = conn
        self.aberturas = 0
        self.fechamentos = 0
        self.resumo = []

    def abrir(self):
        self.aberturas += 1

    def fechar(self):
        self.fechamentos += 1

    def rodar(self, cfg, pub, acoes, rodada_id=None, pagina=None, **extra):
        comum.processar_publicacao(self.conn, pagina or PaginaFalsa(), rodada_id, cfg, pub,
                                   abrir=self.abrir, acoes=acoes, resumo=self.resumo,
                                   fechar=self.fechar, **extra)


@pytest.fixture
def cenario(conn, sem_espera):
    return Cenario(conn)


DATA = datetime(2026, 10, 17, 18, 30, 55, tzinfo=timezone.utc)


def publicacao(codigo="ABC123", rede="Instagram", data=DATA) -> comum.Publicacao:
    return comum.Publicacao(rede, "fulano", "posts", 1, codigo, f"https://www.instagram.com/p/{codigo}/", data)


def acoes_no_banco(conn):
    return [tuple(linha) for linha in conn.execute("SELECT acao, status, detalhe FROM acoes ORDER BY id")]


def contar(conn, tabela):
    return conn.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


def ja_trabalhada_hoje(conn, codigo, rede="Instagram"):
    """Grava uma publicação com uma ação 'feito' hoje (conta para o limite diário)."""
    pub_id = banco.registrar_publicacao(conn, rede, codigo, "outro", "posts", 1, f"https://x/{codigo}")
    banco.registrar_acao(conn, pub_id, None, "curtir", "feito")
    return pub_id


def test_pula_publicacao_com_todas_as_acoes_concluidas(cenario, conn, sem_espera):
    pub = publicacao()
    pub_id = banco.registrar_publicacao(conn, pub.rede, pub.codigo, pub.perfil, pub.aba, pub.posicao, pub.url)
    banco.registrar_acao(conn, pub_id, None, "curtir", "feito")
    banco.registrar_acao(conn, pub_id, None, "comentar", "ja_estava_feito")
    banco.registrar_acao(conn, pub_id, None, "copiar_link", "nao_confirmado")
    acoes = {nome: AcaoFalsa() for nome in ("curtir", "comentar", "copiar_link")}

    cenario.rodar(config(), pub, list(acoes.items()))

    assert cenario.aberturas == 0
    assert all(acao.chamadas == 0 for acao in acoes.values())
    assert cenario.resumo[0].acoes == {nome: "já registrada" for nome in acoes}
    assert contar(conn, "acoes") == 3
    assert sem_espera["esperar_fim"] == []


def test_so_roda_acoes_pendentes(cenario, conn):
    pub = publicacao()
    pub_id = banco.registrar_publicacao(conn, pub.rede, pub.codigo, pub.perfil, pub.aba, pub.posicao, pub.url)
    banco.registrar_acao(conn, pub_id, None, "curtir", "feito")
    banco.registrar_acao(conn, pub_id, None, "comentar", "falhou")   # falhou: tenta de novo
    acoes = {nome: AcaoFalsa() for nome in ("curtir", "comentar", "copiar_link")}

    cenario.rodar(config(), pub, list(acoes.items()))

    assert (acoes["curtir"].chamadas, acoes["comentar"].chamadas, acoes["copiar_link"].chamadas) == (0, 1, 1)
    assert cenario.aberturas == 1 and cenario.fechamentos == 1
    assert cenario.resumo[0].acoes == {"curtir": "já registrada", "comentar": "feito", "copiar_link": "feito"}


def test_grava_status_detalhes_e_link(cenario, conn, sem_espera):
    pub = publicacao()
    rodada = banco.iniciar_rodada(conn)
    link = "https://www.instagram.com/p/ABC123/"
    acoes = [
        ("curtir", AcaoFalsa(comum.Resultado("feito"))),
        ("comentar", AcaoFalsa(comum.Resultado("ja_estava_feito", "já existe comentário"))),
        ("copiar_link", AcaoFalsa(comum.Resultado("feito", link=link))),
    ]

    cenario.rodar(config(), pub, acoes, rodada_id=rodada)

    assert acoes_no_banco(conn) == [
        ("curtir", "feito", None),
        ("comentar", "ja_estava_feito", "já existe comentário"),
        ("copiar_link", "feito", link),
    ]
    assert {linha[0] for linha in conn.execute("SELECT rodada_id FROM acoes")} == {rodada}
    linha = conn.execute("SELECT * FROM publicacoes").fetchone()
    assert (linha["rede"], linha["codigo"], linha["perfil"], linha["aba"]) == ("Instagram", "ABC123", "fulano", "posts")
    assert linha["link"] == link
    assert linha["publicada_em"] == DATA.isoformat(timespec="seconds")
    assert banco.acoes_concluidas(conn, "Instagram", "ABC123") == {"curtir", "comentar", "copiar_link"}
    assert sem_espera["esperar_fim"] == ["ABC123"]   # espera o vídeo terminar antes de sair
    assert cenario.fechamentos == 1


def test_nao_confirmado_e_gravado_e_nunca_repetido(cenario, conn):
    pub = publicacao()
    curtir = AcaoFalsa(erro=comum.NaoConfirmado("o botão não mudou para Descurtir"))

    cenario.rodar(config(), pub, [("curtir", curtir)])
    assert acoes_no_banco(conn) == [("curtir", "nao_confirmado", "o botão não mudou para Descurtir")]
    assert cenario.resumo[0].acoes == {"curtir": "nao_confirmado"}

    cenario.rodar(config(), pub, [("curtir", curtir)])
    assert curtir.chamadas == 1          # não clicou de novo
    assert cenario.aberturas == 1        # nem abriu a publicação de novo
    assert contar(conn, "acoes") == 1


def test_erro_generico_vira_falhou_e_e_tentado_de_novo(cenario, conn, sem_espera):
    pub = publicacao()
    comentar = AcaoFalsa(erro=RuntimeError("campo de comentário sumiu"))
    curtir = AcaoFalsa()

    cenario.rodar(config(), pub, [("comentar", comentar), ("curtir", curtir)])

    assert acoes_no_banco(conn) == [("comentar", "falhou", "campo de comentário sumiu"), ("curtir", "feito", None)]
    assert sem_espera["captura"] == ["comentar"]    # tirou print do erro
    assert curtir.chamadas == 1                      # o erro não parou as outras ações

    cenario.rodar(config(), pub, [("comentar", comentar), ("curtir", curtir)])
    assert comentar.chamadas == 2 and curtir.chamadas == 1
    assert cenario.aberturas == 2


def test_erro_do_playwright_com_pagina_aberta_vira_falhou(cenario, conn):
    erro = PlaywrightError("Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator")
    cenario.rodar(config(), publicacao(), [("curtir", AcaoFalsa(erro=erro))])
    assert acoes_no_banco(conn) == [("curtir", "falhou", "Timeout 10000ms exceeded.")]


def test_erro_do_playwright_com_pagina_fechada_levanta_navegador_fechado(cenario, conn):
    erro = PlaywrightError("Target page, context or browser has been closed")
    with pytest.raises(NavegadorFechado):
        cenario.rodar(config(), publicacao(), [("curtir", AcaoFalsa(erro=erro))],
                      pagina=PaginaFalsa(fechada=True))
    assert contar(conn, "acoes") == 0
    assert cenario.fechamentos == 1


def test_bloqueio_para_tudo_mas_guarda_o_que_ja_foi_feito(cenario, conn):
    copiar = AcaoFalsa()
    acoes = [("curtir", AcaoFalsa()), ("comentar", AcaoFalsa(erro=comum.Bloqueio("Ação bloqueada"))),
             ("copiar_link", copiar)]
    with pytest.raises(comum.Bloqueio, match="Ação bloqueada"):
        cenario.rodar(config(), publicacao(), acoes)
    assert acoes_no_banco(conn) == [("curtir", "feito", None)]
    assert copiar.chamadas == 0
    assert cenario.fechamentos == 1   # fecha a publicação mesmo com o erro


def test_conferir_que_falha_impede_a_acao(cenario, conn):
    def conferir():
        raise RuntimeError("o TikTok trocou de vídeo")
    curtir = AcaoFalsa()
    cenario.rodar(config(), publicacao(rede="TikTok"), [("curtir", curtir)], conferir=conferir)
    assert curtir.chamadas == 0
    assert acoes_no_banco(conn) == [("curtir", "falhou", "o TikTok trocou de vídeo")]


def test_erro_ao_fechar_e_ignorado(cenario, conn):
    def fechar():
        raise PlaywrightError("janela já fechada")
    comum.processar_publicacao(conn, PaginaFalsa(), None, config(), publicacao(), abrir=cenario.abrir,
                               acoes=[("curtir", AcaoFalsa())], resumo=[], fechar=fechar)
    assert acoes_no_banco(conn) == [("curtir", "feito", None)]


@pytest.mark.parametrize(("ver_midia", "pausas", "monitorados"), [
    (lambda pg, cfg, pub: "foto", [comum.PAUSA_FOTO, comum.PAUSA_ENTRE_ACOES], []),
    (None, [comum.PAUSA_LEITURA, comum.PAUSA_ENTRE_ACOES], ["MIDIA"]),            # sem ver_midia: vídeo
    (lambda pg, cfg, pub: "vídeo", [comum.PAUSA_LEITURA, comum.PAUSA_ENTRE_ACOES], ["MIDIA"]),
    (lambda pg, cfg, pub: "carrossel com 3 itens, visto até o fim", [comum.PAUSA_ENTRE_ACOES], []),
], ids=["foto", "padrao", "video", "carrossel"])
def test_tipo_de_midia_define_a_espera(cenario, sem_espera, ver_midia, pausas, monitorados):
    cenario.rodar(config(), publicacao("MIDIA"), [("curtir", AcaoFalsa())], ver_midia=ver_midia)
    assert sem_espera["pausa"] == pausas
    assert sem_espera["monitorar"] == monitorados


def test_erro_ao_abrir_publicacao_pula_sem_gravar_acoes(cenario, conn):
    curtir = AcaoFalsa()

    def abrir():
        raise PlaywrightError("net::ERR_TIMED_OUT\nmais detalhes")

    cenario.abrir = abrir
    cenario.rodar(config(), publicacao(), [("curtir", curtir)])
    assert curtir.chamadas == 0
    assert cenario.resumo[0].acoes == {"abrir": "falhou"}
    assert contar(conn, "acoes") == 0
    assert cenario.fechamentos == 1

    with pytest.raises(NavegadorFechado):
        cenario.rodar(config(), publicacao(), [("curtir", curtir)], pagina=PaginaFalsa(fechada=True))


def test_clique_enviado_e_depois_erro_vira_nao_confirmado(cenario, conn):
    def curtir(pagina, cfg, pub):
        comum.antes_de_clicar()          # o que as ações chamam logo antes do clique real
        raise RuntimeError("a página travou depois do clique")

    cenario.rodar(config(), publicacao(), [("curtir", curtir)])
    assert [status for _, status, _ in acoes_no_banco(conn)] == ["nao_confirmado", "nao_confirmado"]
    assert cenario.resumo[0].acoes == {"curtir": "nao_confirmado"}
    assert banco.acoes_concluidas(conn, "Instagram", "ABC123") == {"curtir"}   # nunca repete o clique


def test_antes_de_clicar_no_modo_simular_nao_grava(cenario, conn):
    def curtir(pagina, cfg, pub):
        comum.antes_de_clicar()
        return comum.Resultado("simulado")

    cenario.rodar(config(simular=True), publicacao(), [("curtir", curtir)])
    assert contar(conn, "acoes") == 0
    assert comum._ao_clicar is None


def test_limite_diario_atingido_levanta_e_nao_abre(cenario, conn):
    ja_trabalhada_hoje(conn, "P1")
    ja_trabalhada_hoje(conn, "P2")
    curtir = AcaoFalsa()
    with pytest.raises(comum.LimiteDiario, match="limite diário de 2"):
        cenario.rodar(config(limite_diario=2), publicacao("NOVA"), [("curtir", curtir)])
    assert issubclass(comum.LimiteDiario, comum.Bloqueio)
    assert cenario.aberturas == 0 and curtir.chamadas == 0
    assert contar(conn, "publicacoes") == 2


def test_limite_diario_abaixo_do_limite_continua(cenario, conn):
    ja_trabalhada_hoje(conn, "P1")
    cenario.rodar(config(limite_diario=2), publicacao("NOVA"), [("curtir", AcaoFalsa())])
    assert cenario.aberturas == 1
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram") == 2


def test_limite_diario_e_por_rede(cenario, conn):
    ja_trabalhada_hoje(conn, "T1", rede="TikTok")
    cenario.rodar(config(limite_diario=1), publicacao("NOVA", rede="Instagram"), [("curtir", AcaoFalsa())])
    assert cenario.aberturas == 1


def test_limite_diario_nao_bloqueia_repetir_publicacao_ja_contada_hoje(cenario, conn):
    pub = publicacao()
    pub_id = banco.registrar_publicacao(conn, pub.rede, pub.codigo, pub.perfil, pub.aba, pub.posicao, pub.url)
    banco.registrar_acao(conn, pub_id, None, "curtir", "feito")
    banco.registrar_acao(conn, pub_id, None, "comentar", "falhou")
    comentar = AcaoFalsa()
    cenario.rodar(config(limite_diario=1), pub, [("curtir", AcaoFalsa()), ("comentar", comentar)])
    assert comentar.chamadas == 1
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram") == 1


def test_simular_nao_grava_nada(cenario, conn, sem_espera):
    acoes = [
        ("curtir", AcaoFalsa(comum.Resultado("simulado"))),
        ("comentar", AcaoFalsa(comum.Resultado("simulado", "nenhum comentário seu encontrado"))),
        ("copiar_link", AcaoFalsa(erro=RuntimeError("botão não encontrado"))),
    ]
    cenario.rodar(config(simular=True), publicacao(), acoes, rodada_id=None)
    assert contar(conn, "publicacoes") == 0
    assert contar(conn, "acoes") == 0
    assert cenario.aberturas == 1
    assert cenario.resumo[0].acoes == {"curtir": "simulado", "comentar": "simulado", "copiar_link": "falhou"}
    assert sem_espera["esperar_fim"] == []


def test_simular_ignora_o_limite_diario(cenario, conn):
    ja_trabalhada_hoje(conn, "P1")
    cenario.rodar(config(simular=True, limite_diario=1), publicacao("NOVA"),
                  [("curtir", AcaoFalsa(comum.Resultado("simulado")))])
    assert cenario.aberturas == 1
    assert contar(conn, "publicacoes") == 1   # só a P1 gravada antes


# ---------------------------------------------------------- executar_acao

def test_executar_acao_junta_detalhe_e_link():
    acao = AcaoFalsa(comum.Resultado("feito", "usei o endereço do post", link="https://x/p/A/"))
    status, detalhe, link = comum.executar_acao(PaginaFalsa(), config(), publicacao(), "copiar_link", acao)
    assert (status, detalhe, link) == ("feito", "usei o endereço do post; https://x/p/A/", "https://x/p/A/")


# ------------------------------------------------------- sorteio de comentários

def _cfg_comentarios(*opcoes):
    return comum.Config("quantidade", 3, 2, opcoes[0] if opcoes else "Boa!!!", "x", 40, False,
                        comentarios=tuple(opcoes))


def test_comentarios_da_config_le_lista_em_json_e_usa_reserva():
    assert comum.comentarios_da_config({"comentarios": '["A", " ", "B"]', "comentario": "X"}) == ["A", "B"]
    assert comum.comentarios_da_config({"comentario": "So um"}) == ["So um"]
    assert comum.comentarios_da_config({"comentarios": "não é json", "comentario": "Y"}) == ["Y"]


def test_sortear_comentario_nunca_repete_o_anterior_e_usa_todos(monkeypatch):
    monkeypatch.setattr(comum, "_ultimo_comentario", None)
    cfg = _cfg_comentarios("A", "B", "C")
    sorteados = [comum.sortear_comentario(cfg) for _ in range(300)]
    assert set(sorteados) == {"A", "B", "C"}
    assert all(x != y for x, y in zip(sorteados, sorteados[1:]))


def test_sortear_comentario_com_uma_opcao_repete_ela(monkeypatch):
    monkeypatch.setattr(comum, "_ultimo_comentario", None)
    cfg = _cfg_comentarios("Unico")
    assert [comum.sortear_comentario(cfg) for _ in range(3)] == ["Unico"] * 3
    sem_lista = comum.Config("quantidade", 3, 2, "Boa!!!", "x", 40, False)
    assert comum.sortear_comentario(sem_lista) == "Boa!!!"


def test_sortear_comentario_usa_aleatoriedade_do_sistema():
    assert isinstance(comum._sorteio, comum.random.SystemRandom)
