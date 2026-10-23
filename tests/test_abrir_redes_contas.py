"""Testes da leitura da conta logada (abrir_redes.extrair_conta, ler_conta e LeitorDeContas).

Os seletores rodam no navegador e não dá para testá-los aqui; os testes usam
abas falsas cujo evaluate() devolve o que o JavaScript devolveria.
"""

import io

import pytest
from playwright.sync_api import Error as PlaywrightError

from automacao import abrir_redes
from automacao.abrir_redes import LeitorDeContas, extrair_conta, ler_conta

REDE = {rede.nome: rede for rede in abrir_redes.REDES}


class AbaFalsa:
    def __init__(self, url, valor=None, erro=None):
        self.url = url
        self.valor = valor
        self.erro = erro
        self.avaliacoes = 0

    def evaluate(self, js):
        self.avaliacoes += 1
        if self.erro:
            raise self.erro
        return self.valor


class ContextoFalso:
    def __init__(self, abas=(), cookies=()):
        self.pages = list(abas)
        self._cookies = list(cookies)

    def cookies(self):
        return list(self._cookies)


# ------------------------------------------------------------ extrair_conta

@pytest.mark.parametrize(("rede", "bruto", "esperado"), [
    ("Instagram", "/fulano.da_silva/", "@fulano.da_silva"),
    ("Instagram", "https://www.instagram.com/fulano/", "@fulano"),
    ("TikTok", "/@fulano", "@fulano"),
    ("TikTok", "https://www.tiktok.com/@fulano?lang=pt-BR", "@fulano"),
    ("Instagram", "/Fulano_123/", "@fulano_123"),  # sempre em minúsculas
])
def test_extrair_conta_reconhece(rede, bruto, esperado):
    assert extrair_conta(rede, bruto) == esperado


@pytest.mark.parametrize(("rede", "bruto"), [
    ("Instagram", None),
    ("Instagram", ""),
    ("Instagram", 123),
    ("Instagram", "/"),
    ("Instagram", "/explore/"),
    ("Instagram", "/direct/inbox/"),
    ("Instagram", "/nome com espaço/"),
    ("TikTok", "/login"),           # sem @: não é perfil
    ("TikTok", "/@"),
    ("Instagram", "/reels/"),
    ("Instagram", "/accounts/login/"),
])
def test_extrair_conta_recusa_o_que_nao_e_conta(rede, bruto):
    assert extrair_conta(rede, bruto) is None


# ------------------------------------------------------------ ler_conta

def test_ler_conta_usa_so_abas_da_rede():
    outra = AbaFalsa("https://www.tiktok.com/", "/@tiktoker")
    parecida = AbaFalsa("https://instagram.com.evil.example/", "/golpe/")
    certa = AbaFalsa("https://www.instagram.com/", "/fulano/")
    contexto = ContextoFalso([outra, parecida, certa])
    assert ler_conta(contexto, REDE["Instagram"]) == "@fulano"
    assert outra.avaliacoes == 0 and parecida.avaliacoes == 0


def test_ler_conta_tenta_a_proxima_aba_se_uma_falhar():
    quebrada = AbaFalsa("https://www.instagram.com/", erro=PlaywrightError("Execution context was destroyed"))
    vazia = AbaFalsa("https://www.instagram.com/accounts/login/", None)
    boa = AbaFalsa("https://www.instagram.com/", "/fulano/")
    assert ler_conta(ContextoFalso([quebrada, vazia, boa]), REDE["Instagram"]) == "@fulano"


@pytest.mark.parametrize("erro", [PlaywrightError("Target closed"), RuntimeError("qualquer coisa"),
                                  ValueError("x")])
def test_ler_conta_nunca_levanta_erro(erro):
    aba = AbaFalsa("https://www.tiktok.com/", erro=erro)
    assert ler_conta(ContextoFalso([aba]), REDE["TikTok"]) is None


def test_ler_conta_com_contexto_quebrado_devolve_none():
    class ContextoQuebrado:
        @property
        def pages(self):
            raise PlaywrightError("Browser has been closed")

    assert ler_conta(ContextoQuebrado(), REDE["TikTok"]) is None


def test_ler_conta_sem_aba_da_rede():
    assert ler_conta(ContextoFalso([AbaFalsa("about:blank")]), REDE["TikTok"]) is None


def test_todas_as_redes_tem_javascript_de_conta():
    assert all(rede.js_conta for rede in abrir_redes.REDES)


# ------------------------------------------------------------ LeitorDeContas

def test_leitor_imprime_grava_e_nao_repete(capsys):
    gravadas = []
    abas = [AbaFalsa("https://www.tiktok.com/", "/@fulano"), AbaFalsa("https://www.instagram.com/", "/ciclano/")]
    leitor = LeitorDeContas(ContextoFalso(abas), ao_ler=lambda rede, conta: gravadas.append((rede, conta)))

    leitor.tentar({"TikTok", "Instagram"})
    leitor.tentar({"TikTok", "Instagram"})

    assert gravadas == [("TikTok", "@fulano"), ("Instagram", "@ciclano")]
    saida = capsys.readouterr().out.splitlines()
    assert saida == ["[TikTok] Conta: @fulano", "[Instagram] Conta: @ciclano"]
    assert abas[0].avaliacoes == 1  # já lida: não lê de novo


def test_leitor_ignora_redes_nao_logadas():
    aba = AbaFalsa("https://www.instagram.com/", "/fulano/")
    leitor = LeitorDeContas(ContextoFalso([aba]))
    leitor.tentar({"TikTok"})
    assert aba.avaliacoes == 0 and leitor.contas == {}


def test_leitor_tenta_de_novo_so_depois_do_intervalo(monkeypatch):
    relogio = [1000.0]
    monkeypatch.setattr(abrir_redes.time, "monotonic", lambda: relogio[0])
    aba = AbaFalsa("https://www.instagram.com/accounts/onetap/", None)  # perfil ainda não aparece
    leitor = LeitorDeContas(ContextoFalso([aba]))

    leitor.tentar({"Instagram"})
    leitor.tentar({"Instagram"})
    assert aba.avaliacoes == 1

    relogio[0] += abrir_redes.INTERVALO_CONTA
    aba.valor = "/fulano/"
    leitor.tentar({"Instagram"})
    assert aba.avaliacoes == 2
    assert leitor.contas == {"Instagram": "@fulano"}


def test_erro_ao_gravar_a_conta_nao_atrapalha(capsys):
    def falha(rede, conta):
        raise RuntimeError("database is locked")

    leitor = LeitorDeContas(ContextoFalso([AbaFalsa("https://www.instagram.com/", "/fulano/")]), ao_ler=falha)
    leitor.tentar({"Instagram"})
    saida = capsys.readouterr().out
    assert "[Instagram] Conta: @fulano" in saida
    assert "Aviso: não consegui gravar a conta" in saida


# ------------------------------------------------------------ ganchos nos laços de espera

def test_esperar_login_chama_a_cada_checagem():
    cookies = [{"domain": ".tiktok.com", "name": "sessionid", "value": "a"},
               {"domain": ".instagram.com", "name": "sessionid", "value": "a"}]
    chamadas = []
    logadas = abrir_redes.esperar_login(ContextoFalso([], cookies), 0, {"TikTok"},
                                        a_cada_checagem=chamadas.append)
    assert logadas == {"TikTok", "Instagram"}
    assert chamadas == [logadas]


def test_esperar_login_continua_funcionando_sem_o_gancho(capsys):
    logadas = abrir_redes.esperar_login(ContextoFalso(), 0, set())
    assert logadas == set()


def test_aguardar_fechamento_chama_a_cada_volta(monkeypatch):
    monkeypatch.setattr(abrir_redes.time, "sleep", lambda s: None)
    monkeypatch.setattr(abrir_redes.sys, "stdin", io.StringIO())  # não é terminal: espera fechar
    voltas = []

    def volta():
        voltas.append(1)
        if len(voltas) == 3:
            raise abrir_redes.NavegadorFechado

    with pytest.raises(abrir_redes.NavegadorFechado):
        abrir_redes.aguardar_fechamento(ContextoFalso(), a_cada_volta=volta)
    assert len(voltas) == 3
