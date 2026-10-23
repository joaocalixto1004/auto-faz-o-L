"""Testes da detecção de login (abrir_redes.redes_logadas) com um contexto falso do navegador."""

import pytest
from playwright.sync_api import Error as PlaywrightError

from automacao import abrir_redes
from automacao.abrir_redes import NavegadorFechado, redes_logadas


class PaginaFalsa:
    """Aba falsa: `evaluate` devolve o estado pedido (ou levanta o erro)."""

    def __init__(self, url, estado="logado", erro=None):
        self.url = url
        self.estado = estado
        self.erro = erro
        self.avaliacoes = 0

    def evaluate(self, js):
        self.avaliacoes += 1
        if self.erro:
            raise self.erro
        return self.estado


class ContextoFalso:
    """Contexto falso: só cookies() e pages, que é o que redes_logadas usa."""

    def __init__(self, cookies=(), paginas=(), erro=None):
        self._cookies = list(cookies)
        self.pages = list(paginas)
        self.erro = erro

    def cookies(self):
        if self.erro:
            raise self.erro
        return [dict(c) for c in self._cookies]


def cookie(dominio, nome="sessionid", valor="abc123"):
    return {"domain": dominio, "name": nome, "value": valor}


def test_sem_cookies_nem_abas_nada_logado():
    assert redes_logadas(ContextoFalso()) == set()


@pytest.mark.parametrize(("dominio", "nome", "rede"), [
    (".tiktok.com", "sessionid", "TikTok"),
    ("tiktok.com", "sessionid", "TikTok"),
    ("www.tiktok.com", "sessionid", "TikTok"),
    (".instagram.com", "sessionid", "Instagram"),
    ("i.instagram.com", "sessionid", "Instagram"),
])
def test_cookie_de_sessao_no_dominio_ou_subdominio(dominio, nome, rede):
    assert redes_logadas(ContextoFalso([cookie(dominio, nome)])) == {rede}


@pytest.mark.parametrize(("dominio", "nome"), [
    ("notiktok.com", "sessionid"),
    (".notiktok.com", "sessionid"),
    ("faketiktok.com", "sessionid"),
    ("tiktok.com.evil.example", "sessionid"),
    ("evilinstagram.com", "sessionid"),
    ("instagram.com.br", "sessionid"),
    ("netflix.com", "auth_token"),
    ("box.com", "auth_token"),
    ("x.com.evil.example", "auth_token"),
])
def test_dominio_parecido_nao_conta(dominio, nome):
    assert redes_logadas(ContextoFalso([cookie(dominio, nome)])) == set()


def test_cookie_vazio_ou_com_outro_nome_nao_conta():
    cookies = [
        cookie(".tiktok.com", valor=""),
        cookie(".instagram.com", nome="csrftoken"),
        cookie(".tiktok.com", nome="auth_token"),    # o TikTok usa sessionid
        {"name": "sessionid", "value": "abc"},      # sem domínio
    ]
    assert redes_logadas(ContextoFalso(cookies)) == set()


def test_varias_redes_ao_mesmo_tempo():
    cookies = [cookie(".tiktok.com"), cookie(".instagram.com"), cookie(".x.com", "auth_token"),
               cookie(".google.com", "SID")]
    assert redes_logadas(ContextoFalso(cookies)) == {"TikTok", "Instagram"}


def test_so_tiktok_e_instagram():
    assert [rede.nome for rede in abrir_redes.REDES] == ["TikTok", "Instagram"]


def test_abas_abertas_nao_contam_sem_cookie():
    paginas = [PaginaFalsa("https://www.instagram.com/"), PaginaFalsa("https://www.tiktok.com/")]
    assert redes_logadas(ContextoFalso(paginas=paginas)) == set()
    assert all(pagina.avaliacoes == 0 for pagina in paginas)


def test_navegador_fechado_ao_ler_cookies():
    contexto = ContextoFalso(erro=PlaywrightError("Target page, context or browser has been closed"))
    with pytest.raises(NavegadorFechado):
        redes_logadas(contexto)
    with pytest.raises(NavegadorFechado):
        abrir_redes.garantir_aberto(contexto)


def test_garantir_aberto_com_navegador_aberto():
    abrir_redes.garantir_aberto(ContextoFalso())   # não levanta nada
