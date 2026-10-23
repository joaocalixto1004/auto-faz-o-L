"""Testes da página de configuração (configuracao.py): validação, HTML e servidor HTTP local."""

import threading
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import json

import pytest

from automacao import banco, configuracao

NOVAS_OPCOES = {"seguir": "nao", "ver_stories": "nao", "repetir": "nao", "repetir_minutos": "20",
                "continuar_sem_login": "sim", "boost": "nao", "tetos": ""}

VALIDO = {
    "modo": "quantidade",
    "quantidade": "3",
    "semanas": "2",
    "comentario": "Boa!!!",
    "compartilhar_com": "fulano",
    "limite_diario": "40",
}


# ---------------------------------------------------------------- validar

def test_validar_aceita_formulario_valido():
    valores, erros = configuracao.validar(dict(VALIDO))
    assert erros == []
    assert valores == dict(VALIDO, comentarios='["Boa!!!"]', redes="ambas", anti_bloqueio="nao",
                             **NOVAS_OPCOES)


def test_validar_aceita_lista_de_comentarios_um_por_linha():
    formulario = dict(VALIDO, comentarios="Boa!!!\n\n  Top demais  \nShow")
    del formulario["comentario"]
    valores, erros = configuracao.validar(formulario)
    assert erros == []
    assert json.loads(valores["comentarios"]) == ["Boa!!!", "Top demais", "Show"]
    assert valores["comentario"] == "Boa!!!"


def test_validar_lista_de_comentarios_tem_limite():
    muitos = "\n".join(f"c{i}" for i in range(configuracao.MAXIMO_COMENTARIOS + 1))
    _, erros = configuracao.validar(dict(VALIDO, comentarios=muitos))
    assert len(erros) == 1 and "no máximo" in erros[0]


def test_validar_tira_arroba_e_espacos():
    formulario = dict(VALIDO, compartilhar_com="  @fulano.da.silva ", comentario="  Oi!  ",
                      quantidade=" 5 ", modo="tempo")
    valores, erros = configuracao.validar(formulario)
    assert erros == []
    assert valores["compartilhar_com"] == "fulano.da.silva"
    assert valores["comentario"] == "Oi!"
    assert valores["quantidade"] == "5"
    assert valores["modo"] == "tempo"


def test_validar_aceita_os_limites_extremos():
    for campo, (minimo, maximo) in configuracao.LIMITES.items():
        for valor in (minimo, maximo):
            _, erros = configuracao.validar(dict(VALIDO, **{campo: str(valor)}))
            assert erros == [], (campo, valor)
    _, erros = configuracao.validar(dict(VALIDO, comentario="x" * 300))
    assert erros == []


@pytest.mark.parametrize(("campo", "valor", "trecho"), [
    ("modo", "", "Escolha se o limite"),
    ("modo", "semanal", "Escolha se o limite"),
    ("quantidade", "0", "publicações por aba"),
    ("quantidade", "51", "publicações por aba"),
    ("quantidade", "abc", "publicações por aba"),
    ("quantidade", "", "publicações por aba"),
    ("quantidade", "-1", "publicações por aba"),
    ("quantidade", "2.5", "publicações por aba"),
    ("semanas", "0", "semanas precisa ser um número de 1 a 52"),
    ("semanas", "53", "semanas precisa ser um número de 1 a 52"),
    ("limite_diario", "0", "limite diário por rede"),
    ("limite_diario", "501", "limite diário por rede"),
    ("comentario", "", "pelo menos um comentário"),
    ("comentario", "   ", "pelo menos um comentário"),
    ("comentario", "x" * 301, "até 300 caracteres"),
    ("compartilhar_com", "fu lano", "usuário do Instagram"),
    ("compartilhar_com", "instagram.com/fulano", "usuário do Instagram"),
])
def test_validar_rejeita_campo_invalido(campo, valor, trecho):
    formulario = dict(VALIDO, modo="tempo") if campo == "semanas" else dict(VALIDO)  # semanas só vale no modo tempo
    formulario[campo] = valor
    _, erros = configuracao.validar(formulario)
    assert len(erros) == 1
    assert trecho in erros[0]


def test_validar_campo_ausente_e_erro():
    formulario = dict(VALIDO)
    del formulario["comentario"]
    _, erros = configuracao.validar(formulario)
    assert len(erros) == 1 and "comentário" in erros[0]


def test_validar_junta_todos_os_erros():
    _, erros = configuracao.validar({})
    # modo, quantidade, semanas (sem modo, os dois limites valem), limite diário e comentário
    assert len(erros) == 5


def test_validar_digito_unicode_vira_erro_e_nao_excecao():
    _, erros = configuracao.validar(dict(VALIDO, quantidade="²"))
    assert len(erros) == 1 and "publicações por aba" in erros[0]


# ----------------------------------------------------------------- pagina

def test_pagina_escapa_html_contra_xss():
    ataque = '"><script>alert(1)</script>'
    html = configuracao.pagina(dict(VALIDO, comentario=ataque, compartilhar_com="<b>eu</b>"))
    assert "<script>alert(1)</script>" not in html
    assert "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>eu</b>" not in html
    assert "&lt;b&gt;eu&lt;/b&gt;" in html


def test_pagina_escapa_erros_e_aviso():
    html = configuracao.pagina(VALIDO, erros=["<img src=x onerror=alert(1)>"])
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'role="alert"' in html

    html = configuracao.pagina(VALIDO, aviso="<i>salvo</i>")
    assert "<i>salvo</i>" not in html
    assert "&lt;i&gt;salvo&lt;/i&gt;" in html
    assert 'role="status"' in html


def test_pagina_marca_o_modo_e_mostra_o_resumo():
    html = configuracao.pagina(dict(VALIDO, modo="tempo", semanas="4"))
    assert 'value="tempo" checked' in html
    assert 'value="quantidade" >' in html
    assert "últimas 4 semana(s)" in html

    html = configuracao.pagina(VALIDO)
    assert 'value="quantidade" checked' in html
    assert "as 3 primeiras publicações" in html


# --------------------------------------------------------- servidor HTTP

# Sem proxy: as requisições vão direto para 127.0.0.1, mesmo se houver http_proxy no ambiente.
ABRIDOR = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    """Sobe o Manipulador numa porta livre, com o banco num arquivo temporário."""
    caminho = tmp_path / "config.db"
    monkeypatch.setattr(banco, "CAMINHO_BANCO", caminho)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), configuracao.Manipulador)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)


def requisitar(url: str, dados: dict | None = None, cabecalhos: dict | None = None) -> tuple[int, str, dict]:
    corpo = urlencode(dados).encode("utf-8") if dados is not None else None
    pedido = urllib.request.Request(url, data=corpo, headers=cabecalhos or {})
    try:
        with ABRIDOR.open(pedido, timeout=10) as resposta:
            return resposta.status, resposta.read().decode("utf-8"), dict(resposta.headers)
    except urllib.error.HTTPError as erro:
        with closing(erro):
            return erro.code, erro.read().decode("utf-8"), dict(erro.headers)


def configuracoes_salvas() -> dict[str, str]:
    """O que está gravado na tabela configuracoes do banco temporário do servidor."""
    with closing(banco.conectar()) as conn:
        return {chave: valor for chave, valor in conn.execute("SELECT chave, valor FROM configuracoes")}


def test_get_raiz_mostra_a_pagina(servidor):
    status, corpo, cabecalhos = requisitar(servidor + "/")
    assert status == 200
    assert cabecalhos["Content-Type"].startswith("text/html")
    assert cabecalhos["Cache-Control"] == "no-store"
    assert "Configuração das interações" in corpo
    assert banco.CONFIG_PADRAO["comentario"] in corpo


def test_get_raiz_com_query_string(servidor):
    status, _, _ = requisitar(servidor + "/?qualquer=coisa")
    assert status == 200


@pytest.mark.parametrize("caminho", ["/x", "/favicon.ico", "/automacao.db"])
def test_get_outro_caminho_da_404(servidor, caminho):
    status, corpo, _ = requisitar(servidor + caminho)
    assert status == 404
    assert "Não encontrado" in corpo


def test_post_valido_salva_no_banco(servidor):
    formulario = dict(VALIDO, modo="tempo", semanas="3", comentario="Show!", compartilhar_com="@ciclano")
    status, corpo, _ = requisitar(servidor + "/", formulario)
    assert status == 200
    assert "Configuração salva." in corpo
    assert configuracoes_salvas() == dict(formulario, compartilhar_com="ciclano", comentarios='["Show!"]',
                                          redes="ambas", anti_bloqueio="nao",
                             **NOVAS_OPCOES)

    # o GET seguinte já mostra os valores salvos
    _, corpo, _ = requisitar(servidor + "/")
    assert ">Show!</textarea>" in corpo and 'value="tempo" checked' in corpo


@pytest.mark.parametrize("origem", ["http://127.0.0.1:8765", "http://localhost:8765"])
def test_post_com_origem_local_e_aceito(servidor, origem):
    status, _, _ = requisitar(servidor + "/", VALIDO, {"Origin": origem})
    assert status == 200
    assert configuracoes_salvas()["comentario"] == VALIDO["comentario"]


def test_post_invalido_da_400_e_nao_salva(servidor):
    status, corpo, _ = requisitar(servidor + "/", dict(VALIDO, quantidade="999", comentario=""))
    assert status == 400
    assert "publicações por aba precisa ser um número de 1 a 50" in corpo
    assert "pelo menos um comentário" in corpo
    assert configuracoes_salvas() == {}


@pytest.mark.parametrize("origem", [
    "https://evil.example",
    "http://127.0.0.1.evil.example",
    "http://localhost.evil.example:8765",
    "null",
])
def test_post_de_outra_origem_da_403_e_nao_salva(servidor, origem):
    status, corpo, _ = requisitar(servidor + "/", VALIDO, {"Origin": origem})
    assert status == 403
    assert "Origem não permitida" in corpo
    assert configuracoes_salvas() == {}


def test_post_com_xss_e_salvo_mas_mostrado_escapado(servidor):
    ataque = '"><script>alert(1)</script>'
    status, corpo, _ = requisitar(servidor + "/", dict(VALIDO, comentario=ataque))
    assert status == 200
    assert configuracoes_salvas()["comentario"] == ataque
    _, corpo, _ = requisitar(servidor + "/")
    assert "<script>alert(1)</script>" not in corpo
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in corpo



@pytest.mark.parametrize(("valor", "esperado"), [("instagram", "instagram"), ("TikTok", "tiktok"),
                                                  ("ambas", "ambas"), ("", "ambas")])
def test_validar_escolha_de_redes(valor, esperado):
    valores, erros = configuracao.validar(dict(VALIDO, redes=valor))
    assert erros == [] and valores["redes"] == esperado


def test_validar_rede_desconhecida_e_erro():
    _, erros = configuracao.validar(dict(VALIDO, redes="facebook"))
    assert len(erros) == 1 and "redes" in erros[0]


@pytest.mark.parametrize(("valor", "esperado"), [("sim", "sim"), ("on", "sim"), ("1", "sim"),
                                                  ("nao", "nao"), ("", "nao"), ("qualquer", "nao")])
def test_validar_modo_anti_bloqueio(valor, esperado):
    valores, erros = configuracao.validar(dict(VALIDO, anti_bloqueio=valor))
    assert erros == [] and valores["anti_bloqueio"] == esperado



def test_validar_opcoes_de_seguir_stories_e_repeticao():
    valores, erros = configuracao.validar(dict(VALIDO, seguir="on", ver_stories="1", repetir="sim",
                                               repetir_minutos="45", continuar_sem_login="nao"))
    assert erros == []
    assert (valores["seguir"], valores["ver_stories"], valores["repetir"], valores["repetir_minutos"],
            valores["continuar_sem_login"]) == ("sim", "sim", "sim", "45", "nao")


@pytest.mark.parametrize("minutos", ["0", "1441", "abc", "²"])
def test_validar_intervalo_de_repeticao_invalido(minutos):
    _, erros = configuracao.validar(dict(VALIDO, repetir_minutos=minutos))
    assert len(erros) == 1 and "recomeçar" in erros[0]


# ------------------------------------------- campos que não estão em uso


@pytest.mark.parametrize("modo, campo_fora_de_uso", [("quantidade", "semanas"), ("tempo", "quantidade")])
@pytest.mark.parametrize("valor", ["", "0", "abc", "999"])
def test_validar_ignora_limite_do_outro_modo(modo, campo_fora_de_uso, valor):
    """Quem limita por tempo não precisa preencher a quantidade (e vice-versa):
    o valor errado fica de fora para não apagar o que estava salvo."""
    valores, erros = configuracao.validar(dict(VALIDO, modo=modo, **{campo_fora_de_uso: valor}))
    assert erros == []
    assert campo_fora_de_uso not in valores


def test_validar_grava_limite_do_outro_modo_quando_esta_certo():
    valores, erros = configuracao.validar(dict(VALIDO, modo="tempo", quantidade="7"))
    assert erros == []
    assert valores["quantidade"] == "7"


def test_validar_exige_limite_do_modo_escolhido():
    _, erros = configuracao.validar(dict(VALIDO, modo="tempo", semanas=""))
    assert len(erros) == 1 and "semanas" in erros[0]


@pytest.mark.parametrize("valor", ["", "  ", "@"])
def test_validar_aceita_compartilhar_com_vazio(valor):
    """A conta da configuração é só a reserva para quando a planilha não tem ninguém."""
    valores, erros = configuracao.validar(dict(VALIDO, compartilhar_com=valor))
    assert erros == []
    assert valores["compartilhar_com"] == ""
