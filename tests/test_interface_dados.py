"""Testes da parte da interface que não depende do Tkinter (interface_dados.py)."""

from datetime import datetime

import pytest

from automacao import banco, configuracao, planilhas
from automacao.interface import dados
from automacao.interface.dados import EventoLogin, SituacaoLogin
from automacao.redes import comum


# ------------------------------------------------------------ linhas da saída

@pytest.mark.parametrize(("linha", "esperado"), [
    ("[TikTok] Já logado ✔", [EventoLogin("TikTok", True, "ja_logado")]),
    ("[Instagram] Não logado — faça login manualmente na aba.",
     [EventoLogin("Instagram", False, "nao_logado")]),
    ("[TikTok] Login detectado ✔", [EventoLogin("TikTok", True, "login_detectado")]),
    ("  [Instagram] Já logado ✔  ", [EventoLogin("Instagram", True, "ja_logado")]),
    ("[Instagram] Conta: @fulano.silva", [EventoLogin("Instagram", conta="@fulano.silva")]),
    ("[TikTok] Conta: @fulano_tt", [EventoLogin("TikTok", conta="@fulano_tt")]),
    ("✔ TikTok  ✘ Instagram", [EventoLogin("TikTok", True), EventoLogin("Instagram", False)]),
])
def test_interpretar_linha_reconhece(linha, esperado):
    assert dados.interpretar_linha(linha) == esperado


@pytest.mark.parametrize("linha", [
    "",
    "Aguardando login (até 15 min)...",
    "[Instagram] Aviso: a página não carregou direito (Timeout). Recarregue a aba se ela ficar em branco.",
    "[Instagram] Não está logado. Rode abrir_redes.py e faça login antes. Pulando @fulano.",
    "[Facebook] Já logado ✔",                 # rede desconhecida
    "[Facebook] Conta: @fulano",
    "✔ Facebook  ✘ Orkut",
    "  Instagram @fulano posts #1 (ABC): curtir=feito",
    "Navegador fechado.",
])
def test_interpretar_linha_ignora_o_resto(linha):
    assert dados.interpretar_linha(linha) == []


@pytest.mark.parametrize(("linha", "estilo"), [
    ("[TikTok] Já logado ✔", "ok"),
    ("[TikTok] Conta: @fulano", "ok"),
    ("[TikTok] Não logado — faça login", "aviso"),
    ("[Instagram] Aviso: a página não carregou direito", "aviso"),
    ("✔ TikTok  ✘ X", "aviso"),
    ("Traceback (most recent call last):", "erro"),
    ("ValueError: Erro qualquer", "erro"),
    ("Perfis: 3. Limite: as 3 primeiras.", None),
])
def test_estilo_da_linha(linha, estilo):
    assert dados.estilo_da_linha(linha) == estilo


# ------------------------------------------------------------ situação dos logins

def test_situacao_logins_de_banco_vazio(conn):
    situacoes = dados.situacao_logins(conn)
    assert list(situacoes) == ["TikTok", "Instagram"]
    assert all(s.status is None for s in situacoes.values())
    assert dados.texto_situacao(situacoes["TikTok"]) == ("Nunca verificado", "suave")


def test_situacao_logins_do_banco(conn):
    execucao = banco.iniciar_execucao(conn)
    banco.registrar_login(conn, execucao, "Instagram", "login_detectado")
    banco.registrar_conta(conn, execucao, "Instagram", "@fulano")
    banco.registrar_login(conn, execucao, "TikTok", "nao_logado")
    situacoes = dados.situacao_logins(conn)
    assert situacoes["Instagram"].logado and situacoes["Instagram"].conta == "@fulano"
    assert dados.texto_situacao(situacoes["Instagram"]) == ("Logado", "ok")
    assert dados.texto_situacao(situacoes["TikTok"]) == ("Não logado", "erro")
    assert situacoes["TikTok"].verificado_em


def test_aplicar_eventos_ao_vivo():
    situacoes = {nome: SituacaoLogin(nome, verificando=True) for nome in dados.NOMES_REDES}
    for linha in ("[Instagram] Não logado — ...", "[Instagram] Login detectado ✔",
                  "[Instagram] Conta: @fulano", "✔ TikTok  ✔ Instagram"):
        for evento in dados.interpretar_linha(linha):
            dados.aplicar_evento(situacoes, evento, momento="2026-10-23T12:00:00-03:00")

    instagram = situacoes["Instagram"]
    assert (instagram.status, instagram.conta, instagram.verificando) == ("login_detectado", "@fulano", False)
    assert situacoes["TikTok"].logado and situacoes["TikTok"].status == "login_detectado"
    assert situacoes["TikTok"].verificado_em == "2026-10-23T12:00:00-03:00"


def test_resumo_nao_troca_o_status_de_quem_ja_estava_logado():
    situacoes = {"TikTok": SituacaoLogin("TikTok", status="ja_logado")}
    dados.aplicar_evento(situacoes, EventoLogin("TikTok", True))
    assert situacoes["TikTok"].status == "ja_logado"


@pytest.mark.parametrize(("situacao", "texto"), [
    (SituacaoLogin("TikTok", "ja_logado", conta="@fulano"), "@fulano"),
    (SituacaoLogin("Instagram", "ja_logado"), "—"),
    (SituacaoLogin("Instagram", "ja_logado", ultima_conta="@antigo"), "@antigo (última vista)"),
    (SituacaoLogin("TikTok", "nao_logado"), "—"),
])
def test_texto_conta(situacao, texto):
    assert dados.texto_conta(situacao) == texto


def test_texto_situacao_enquanto_verifica():
    assert dados.texto_situacao(SituacaoLogin("TikTok", "ja_logado", verificando=True))[0] == "Verificando…"


@pytest.mark.parametrize(("iso", "texto"), [
    (None, "—"),
    ("", "—"),
    ("2026-10-23T14:05:00", "hoje às 14:05"),
    ("2026-10-22T09:30:00", "ontem às 09:30"),
    ("2026-10-01T18:00:00", "01/10/2026 às 18:00"),
    ("não é data", "não é data"),
])
def test_formatar_momento(iso, texto):
    assert dados.formatar_momento(iso, agora=datetime(2026, 10, 23, 20, 0)) == texto


def test_formatar_momento_com_fuso_converte_para_hora_local():
    momento = datetime(2026, 10, 23, 15, 0).astimezone()
    assert dados.formatar_momento(momento.isoformat(), agora=datetime(2026, 10, 23, 20, 0)) == "hoje às 15:00"


# ------------------------------------------------------------ configuração e planilhas

def test_resumo_configuracao():
    valores = dict(banco.CONFIG_PADRAO)
    assert dict(dados.resumo_configuracao(valores)) == {
        "Redes": "as duas (Instagram e TikTok)",
        "Publicações": "as 3 primeiras de cada aba (modo quantidade)",
        "Comentários": "1 comentário: “Boa!!!”",
        "Ações extras": "Seguir: não · Stories: não · Recomeçar: não · Conta sem login: pula",
        "Limite diário": "40 publicações por conta, em cada rede",  # o limite é por conta
        "Proteção anti-bloqueio": "desligada",
    }
    valores.update(modo="tempo", semanas="4")
    assert dict(dados.resumo_configuracao(valores))["Publicações"].startswith("as das últimas 4 semana(s)")


def test_resumo_configuracao_com_lista_de_comentarios_salva(conn):
    """Salva pelo mesmo caminho da aba Configurações e confere o resumo da aba Início."""
    campos = dict(modo="quantidade", quantidade="3", semanas="2", compartilhar_com="fulano",
                  limite_diario="40", comentarios="Boa!!!\n\n  Top demais  \nShow\nIncrível")
    valores, erros = configuracao.validar(campos)
    assert erros == []
    banco.salvar_configuracoes(conn, valores)
    resumo = dict(dados.resumo_configuracao(banco.ler_configuracoes(conn)))
    # A aba Início mostra só os 2 primeiros (o resumo tem de caber na janela mínima).
    assert resumo["Comentários"] == ("4 comentários, sorteados a cada publicação: "
                                     "“Boa!!!”, “Top demais” e mais 2")


def test_resumo_comentarios_encurta_os_longos():
    texto = dados.resumo_comentarios(["a" * 100, "b"], tamanho=10)
    assert texto == "2 comentários, sorteados a cada publicação: “aaaaaaaaa…”, “b”"
    assert dados.resumo_comentarios([]) == "nenhum"


@pytest.mark.parametrize(("texto", "esperado"), [
    ("Boa!!!", "Boa!!!"),
    ("  Que   legal \n demais ", "Que legal demais"),
    ("x" * 300, "x" * 300),
])
def test_validar_comentario_aceita(texto, esperado):
    assert dados.validar_comentario(texto, []) == (esperado, None)


@pytest.mark.parametrize(("texto", "existentes", "trecho"), [
    ("", [], "Digite"),
    ("   \n ", [], "Digite"),
    ("x" * 301, [], "até 300 caracteres"),
    ("boa!!!", ["Boa!!!"], "já está na lista"),
    ("novo", [f"c{i}" for i in range(50)], "no máximo 50"),
])
def test_validar_comentario_recusa(texto, existentes, trecho):
    valor, erro = dados.validar_comentario(texto, existentes)
    assert valor is None and trecho in erro


def test_editar_comentario_com_a_lista_cheia_e_permitido():
    outros = [f"c{i}" for i in range(49)]  # a lista tem 50: o editado mais 49
    assert dados.validar_comentario("novo texto", outros, editando=True) == ("novo texto", None)


def test_lista_validada_aqui_passa_no_configuracao_validar():
    lista = []
    for texto in ("Boa!!!", "Top", "boa!!!", "x" * 301, "Show"):
        valor, erro = dados.validar_comentario(texto, lista)
        if valor:
            lista.append(valor)
    campos = dict(modo="tempo", quantidade="3", semanas="2", compartilhar_com="fulano",
                  limite_diario="40", comentarios="\n".join(lista))
    valores, erros = configuracao.validar(campos)
    assert erros == []
    assert comum.comentarios_da_config(valores) == ["Boa!!!", "Top", "Show"]


def test_resumo_perfis_sem_planilha():
    resumo = dados.resumo_perfis()  # o conftest aponta para um arquivo que não existe
    assert not resumo.existe and resumo.total == 0


def test_resumo_perfis_conta_como_o_interagir(tmp_path):
    caminho = tmp_path / "pa.xlsx"
    planilhas.salvar_perfis({
        "instagram": ["https://www.instagram.com/fulano/", "instagram.com/fulano",   # repetido
                      "https://www.tiktok.com/@na_aba_errada", "https://www.instagram.com/p/ABC123/"],
        "tiktok": ["https://www.tiktok.com/@ciclano", "texto qualquer"],
    }, caminho)
    resumo = dados.resumo_perfis(caminho)
    assert resumo.existe
    assert resumo.por_rede == {"Instagram": 1, "TikTok": 2}
    assert resumo.invalidos == ["https://www.instagram.com/p/ABC123/"]


def test_resumo_destinatarios_das_duas_redes(tmp_path):
    caminho = tmp_path / "pc.xlsx"
    planilhas.salvar_destinatarios({"instagram": ["fulano"], "tiktok": ["ciclano", "beltrano"]}, caminho)
    assert dados.resumo_destinatarios("@padrao", caminho) == {
        "Instagram": (["fulano"], False), "TikTok": (["ciclano", "beltrano"], False)}


@pytest.mark.parametrize("conta", ["", "  ", "@"])
def test_resumo_destinatarios_sem_conta_reserva_fica_vazio(tmp_path, conta):
    """Sem conta na configuração e sem ninguém na planilha, o Instagram não compartilha."""
    assert dados.resumo_destinatarios(conta, tmp_path / "nao_existe.xlsx") == {
        "Instagram": ([], False), "TikTok": ([], False)}


def test_resumo_destinatarios_usa_a_configuracao_no_instagram_vazio(tmp_path):
    caminho = tmp_path / "pc.xlsx"
    planilhas.salvar_destinatarios({"instagram": [], "tiktok": ["ciclano"]}, caminho)
    assert dados.resumo_destinatarios("@padrao", caminho) == {
        "Instagram": (["padrao"], True), "TikTok": (["ciclano"], False)}
    assert dados.resumo_destinatarios("padrao") == {  # sem planilha
        "Instagram": (["padrao"], True), "TikTok": ([], False)}


# ------------------------------------------------------------ validação do que é digitado

@pytest.mark.parametrize(("texto", "aba", "link"), [
    ("https://www.instagram.com/fulano/?igsh=abc", "instagram", "https://www.instagram.com/fulano/"),
    ("instagram.com/fulano", "instagram", "https://www.instagram.com/fulano/"),
    ("  www.tiktok.com/@ciclano?lang=pt-BR ", "tiktok", "https://www.tiktok.com/@ciclano"),
])
def test_validar_novo_perfil_aceita_e_padroniza(texto, aba, link):
    assert dados.validar_novo_perfil(texto, aba, []) == (link, None)


@pytest.mark.parametrize(("texto", "aba", "trecho"), [
    ("", "instagram", "Digite"),
    ("fulano", "instagram", "não é um link de perfil"),
    ("https://x.com/fulano", "instagram", "não é um link de perfil"),
    ("https://www.instagram.com/p/ABC123/", "instagram", "não é um link de perfil"),
    ("https://www.tiktok.com/@ciclano", "instagram", "esta aba é do Instagram"),
    ("https://www.instagram.com/fulano/", "tiktok", "esta aba é do TikTok"),
])
def test_validar_novo_perfil_recusa(texto, aba, trecho):
    valor, erro = dados.validar_novo_perfil(texto, aba, [])
    assert valor is None and trecho in erro


def test_validar_novo_perfil_recusa_repetido_mesmo_com_link_diferente():
    existentes = ["instagram.com/Fulano"]
    valor, erro = dados.validar_novo_perfil("https://www.instagram.com/fulano/", "instagram", existentes)
    assert valor is None and "já está na lista" in erro


def test_link_gravado_e_lido_igual_pelo_interagir():
    from automacao.interagir import interpretar_perfil
    link, _ = dados.validar_novo_perfil("tiktok.com/@ciclano/video/123", "tiktok", [])
    assert planilhas.eh_link_de_perfil(link)
    assert interpretar_perfil(link) == ("TikTok", "ciclano")


@pytest.mark.parametrize(("texto", "usuario"), [("@fulano", "fulano"), ("  ciclano.silva_2 ", "ciclano.silva_2")])
def test_validar_novo_destinatario_aceita(texto, usuario):
    assert dados.validar_novo_destinatario(texto, []) == (usuario, None)


@pytest.mark.parametrize("texto", ["", "@", "nome com espaço", "fulano!", "a" * 31,
                                   "https://www.instagram.com/fulano/"])
def test_validar_novo_destinatario_recusa(texto):
    valor, erro = dados.validar_novo_destinatario(texto, [])
    assert valor is None and erro


def test_validar_novo_destinatario_recusa_repetido():
    valor, erro = dados.validar_novo_destinatario("@Fulano", ["fulano"])
    assert valor is None and "já está na lista" in erro


def test_observacoes_das_linhas_ja_gravadas():
    assert dados.observacao_perfil("https://www.instagram.com/fulano/", "instagram", []) == ""
    assert dados.observacao_perfil("texto", "instagram", []) == "não é link (ignorado)"
    assert dados.observacao_perfil("https://www.instagram.com/p/ABC/", "instagram", []).startswith("inválido")
    assert dados.observacao_perfil("https://www.tiktok.com/@a", "instagram", []) == "perfil do TikTok"
    assert dados.observacao_perfil("instagram.com/fulano", "instagram",
                                   ["https://www.instagram.com/fulano/"]) == "repetido"
    assert dados.observacao_destinatario("fulano", []) == ""
    assert dados.observacao_destinatario("@Fulano", ["fulano"]) == "repetido"
    assert dados.observacao_destinatario("não vale", []) == "inválido (ignorado)"
