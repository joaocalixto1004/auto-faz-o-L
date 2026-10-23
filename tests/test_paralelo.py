"""Instagram e TikTok ao mesmo tempo, modo boost e a aba Reels, sem abrir o navegador.

O Playwright é trocado por objetos falsos: o que se testa aqui é a divisão da
lista entre as duas redes, as linhas de execução, a saída com rótulos, a
reabertura de uma janela fechada e o Parar.
"""

import argparse
from types import SimpleNamespace
import io
import os
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from automacao import abrir_redes, interagir, terminal
from automacao.abrir_redes import NavegadorFechado
from automacao.redes import comum, instagram, protecao
from automacao.redes.protecao import PROTECAO_ANTI_BLOQUEIO, PROTECAO_BOOST, PROTECAO_NORMAL, controlar_ritmo
from automacao.terminal import SaidaComRotulos

ALVOS = [("Instagram", "a"), ("Instagram", "b"), ("TikTok", "c"), ("Instagram", "d"), ("TikTok", "e")]
CONTAS = {"Instagram": "@ig", "TikTok": "@tt"}


@pytest.fixture(autouse=True)
def parada_limpa():
    terminal.PARADA.clear()
    yield
    terminal.PARADA.clear()


def config(**opcoes) -> comum.Config:
    base = dict(modo="quantidade", quantidade=3, semanas=2, comentario="Boa!!!", compartilhar_com="",
                limite_diario=40, simular=False, redes=("Instagram", "TikTok"))
    base.update(opcoes)
    return comum.Config(**base)


# ------------------------------------------------------- Playwright falso

class PaginaFalsa:
    def __init__(self, nome):
        self.nome = nome
        self.fechada = False

    def is_closed(self):
        return self.fechada

    def close(self):
        self.fechada = True

    def bring_to_front(self):
        pass


class ContextoFalso:
    def __init__(self):
        self.pages = [PaginaFalsa("principal")]
        self.vivo = True

    def cookies(self):
        if not self.vivo:
            raise RuntimeError("fechado")
        return []

    def new_page(self):
        pagina = PaginaFalsa(f"aba{len(self.pages)}")
        self.pages.append(pagina)
        return pagina


class NavegadorFalso:
    def __init__(self, contexto):
        self.contexts = [contexto]
        self.conectado = True

    def is_connected(self):
        return self.conectado


@pytest.fixture
def chrome(monkeypatch):
    """Um 'Chrome' compartilhado: a linha da segunda rede se conecta a ele."""
    contexto = ContextoFalso()
    navegador = NavegadorFalso(contexto)
    conexoes = []

    @contextmanager
    def sync_playwright_falso():
        conexoes.append(threading.current_thread().name)
        p = argparse.Namespace(chromium=argparse.Namespace(connect_over_cdp=lambda endereco: navegador))
        yield p

    janelas = []

    def nova_janela(nav, ctx):
        pagina = PaginaFalsa(f"janela{len(janelas)}")
        janelas.append(pagina)
        return pagina

    monkeypatch.setattr(interagir, "sync_playwright", sync_playwright_falso)
    monkeypatch.setattr(interagir, "nova_janela", nova_janela)
    monkeypatch.setattr(interagir, "navegador_vivo", lambda ctx: ctx.vivo)
    monkeypatch.setattr(interagir, "dormir", lambda segundos: None)
    return argparse.Namespace(contexto=contexto, navegador=navegador, conexoes=conexoes, janelas=janelas)


class ProcessarFalso:
    """No lugar de interagir.processar_perfis: anota quem chamou e com o quê."""

    def __init__(self, comportamento=None):
        self.chamadas = []
        self.trava = threading.Lock()
        self.comportamento = comportamento or {}

    def __call__(self, conn, contexto, rodada_id, cfg, perfis, inicio, bloqueadas, resumo, contas=None,
                 pagina=None):
        rede = perfis[0][0] if perfis else "?"
        with self.trava:
            self.chamadas.append((threading.current_thread().name, rede, [p for _, p in perfis], inicio,
                                  pagina.nome if pagina else None))
            vez = sum(1 for c in self.chamadas if c[1] == rede)
        print(f"    curtir        feito  ({rede})")
        acao = self.comportamento.get((rede, vez))
        if acao:
            return acao()
        return len(perfis)


def rodar_em_paralelo(conn, chrome, processar, monkeypatch, proximos=None, reabrir=True):
    monkeypatch.setattr(interagir, "processar_perfis", processar)
    return interagir.processar_em_paralelo(conn, chrome.contexto, "http://127.0.0.1:1", None, config(), ALVOS,
                                           proximos or {"Instagram": 0, "TikTok": 0}, set(), [], CONTAS,
                                           reabrir=reabrir)


# ------------------------------------------------------- divisão e linhas

def test_cada_rede_roda_a_sua_parte_da_lista_em_linhas_separadas(conn, chrome, monkeypatch, capsys):
    processar = ProcessarFalso()
    original = sys.stdout
    proximos = rodar_em_paralelo(conn, chrome, processar, monkeypatch)
    assert sys.stdout is original                         # a saída volta ao normal
    assert proximos == {"Instagram": 3, "TikTok": 2}
    por_rede = {c[1]: c for c in processar.chamadas}
    assert por_rede["Instagram"][0] == threading.main_thread().name
    assert por_rede["Instagram"][2] == ["a", "b", "d"] and por_rede["Instagram"][4] == "principal"
    assert por_rede["TikTok"][0] == "TikTok"              # outra linha de execução...
    assert por_rede["TikTok"][2] == ["c", "e"] and por_rede["TikTok"][4] == "janela0"   # ...e outra janela
    assert chrome.conexoes == ["TikTok"]                  # com a sua própria conexão ao Chrome
    assert chrome.janelas[0].fechada                      # a janela da segunda rede fecha ao terminar
    saida = capsys.readouterr().out
    assert "[Instagram]     curtir        feito  (Instagram)" in saida
    assert "[TikTok]     curtir        feito  (TikTok)" in saida


def test_continua_de_onde_cada_rede_parou(conn, chrome, monkeypatch):
    processar = ProcessarFalso()
    rodar_em_paralelo(conn, chrome, processar, monkeypatch, proximos={"Instagram": 2, "TikTok": 1})
    assert sorted((c[1], c[3]) for c in processar.chamadas) == [("Instagram", 2), ("TikTok", 1)]


def test_a_conta_so_termina_quando_as_duas_redes_terminam(conn, chrome, monkeypatch):
    def tiktok_demora():
        time.sleep(0.5)
        return 2

    processar = ProcessarFalso({("TikTok", 1): tiktok_demora})
    inicio = time.monotonic()
    assert rodar_em_paralelo(conn, chrome, processar, monkeypatch) == {"Instagram": 3, "TikTok": 2}
    assert time.monotonic() - inicio >= 0.5


# ------------------------------------------------------- janelas fechadas

def fechou(proximo):
    def levantar():
        erro = NavegadorFechado()
        erro.proximo = proximo
        raise erro
    return levantar


def test_janela_so_do_tiktok_fechada_reabre_outra_e_continua(conn, chrome, monkeypatch):
    processar = ProcessarFalso({("TikTok", 1): fechou(1)})
    assert rodar_em_paralelo(conn, chrome, processar, monkeypatch) == {"Instagram": 3, "TikTok": 2}
    tiktok = [c for c in processar.chamadas if c[1] == "TikTok"]
    assert [(c[3], c[4]) for c in tiktok] == [(0, "janela0"), (1, "janela1")]   # outra janela, do perfil 1


def test_aba_so_do_instagram_fechada_abre_outra_no_mesmo_chrome(conn, chrome, monkeypatch):
    processar = ProcessarFalso({("Instagram", 1): fechou(2)})
    assert rodar_em_paralelo(conn, chrome, processar, monkeypatch) == {"Instagram": 3, "TikTok": 2}
    instagram_ = [c for c in processar.chamadas if c[1] == "Instagram"]
    assert [(c[3], c[4]) for c in instagram_] == [(0, "principal"), (2, "aba1")]


def test_navegador_inteiro_fechado_devolve_onde_cada_rede_parou(conn, chrome, monkeypatch):
    def tudo_fechou(proximo):
        def levantar():
            chrome.contexto.vivo = False
            chrome.navegador.conectado = False
            fechou(proximo)()
        return levantar

    processar = ProcessarFalso({("Instagram", 1): tudo_fechou(1), ("TikTok", 1): tudo_fechou(1)})
    with pytest.raises(NavegadorFechado) as erro:
        rodar_em_paralelo(conn, chrome, processar, monkeypatch)
    assert erro.value.proximos == {"Instagram": 1, "TikTok": 1}


def test_sem_reabrir_nao_reabre_a_janela(conn, chrome, monkeypatch):
    processar = ProcessarFalso({("TikTok", 1): fechou(1)})
    with pytest.raises(NavegadorFechado) as erro:
        rodar_em_paralelo(conn, chrome, processar, monkeypatch, reabrir=False)
    assert erro.value.proximos["TikTok"] == 1


def test_erro_inesperado_na_segunda_rede_nao_derruba_a_primeira(conn, chrome, monkeypatch, capsys):
    def quebra():
        raise ValueError("pane")

    processar = ProcessarFalso({("TikTok", 1): quebra})
    assert rodar_em_paralelo(conn, chrome, processar, monkeypatch) == {"Instagram": 3, "TikTok": 0}
    assert "[TikTok] Erro inesperado: pane" in capsys.readouterr().out


# ------------------------------------------------------- Parar

def test_parar_na_rede_principal_para_a_outra_tambem(conn, chrome, monkeypatch):
    tiktok_parou = threading.Event()

    def tiktok_espera():
        try:
            for _ in range(200):           # uma espera longa (pausa entre publicações)
                terminal.dormir(0.05)
        except KeyboardInterrupt:
            tiktok_parou.set()
            raise
        return 2

    def instagram_ctrl_c():
        time.sleep(0.2)
        raise KeyboardInterrupt

    processar = ProcessarFalso({("TikTok", 1): tiktok_espera, ("Instagram", 1): instagram_ctrl_c})
    with pytest.raises(KeyboardInterrupt):
        rodar_em_paralelo(conn, chrome, processar, monkeypatch)
    assert terminal.PARADA.is_set()
    assert tiktok_parou.wait(2)


def test_esperas_curtas_acordam_com_o_parar():
    terminal.PARADA.set()
    with pytest.raises(KeyboardInterrupt):
        comum.esperar_ate(lambda: False, 5)


# ------------------------------------------------------- quando vale o paralelo

@pytest.mark.parametrize(("redes", "opcoes", "esperado"), [
    (("Instagram", "TikTok"), {}, True),
    (("Instagram",), {}, False),
    (("TikTok",), {}, False),
    (("Instagram", "TikTok"), {"uma_rede_por_vez": True}, False),
    (("Instagram", "TikTok"), {"trace": True}, False),
])
def test_pode_rodar_em_paralelo(redes, opcoes, esperado):
    args = argparse.Namespace(**dict({"uma_rede_por_vez": False, "trace": False}, **opcoes))
    assert interagir.pode_rodar_em_paralelo(config(redes=redes), args) is esperado


def test_endereco_de_controle_le_a_porta_do_chrome(tmp_path):
    antes = time.time()
    (tmp_path / "DevToolsActivePort").write_text("41183\n/devtools/browser/abc\n")
    assert abrir_redes.endereco_de_controle(tmp_path, antes) == "http://127.0.0.1:41183"


def test_endereco_de_controle_ignora_arquivo_de_outra_execucao(tmp_path):
    arquivo = tmp_path / "DevToolsActivePort"
    arquivo.write_text("41183\n/devtools/browser/abc\n")
    os.utime(arquivo, (time.time() - 3600, time.time() - 3600))
    assert abrir_redes.endereco_de_controle(tmp_path, time.time(), espera_s=0.3) is None


@pytest.mark.parametrize("conteudo", ["", "abc\n/x", "0\n/x"])
def test_endereco_de_controle_recusa_conteudo_estranho(tmp_path, conteudo):
    (tmp_path / "DevToolsActivePort").write_text(conteudo)
    assert abrir_redes.endereco_de_controle(tmp_path, time.time() - 5, espera_s=0.3) is None


# ------------------------------------------------------- saída e estado por linha

def test_saida_com_rotulos_nao_mistura_as_linhas():
    destino = io.StringIO()
    saida = SaidaComRotulos(destino)

    def escrever(rotulo):
        terminal.definir_rotulo(rotulo)
        for i in range(200):
            saida.write(f"    ação {i}")     # o print escreve o texto e o fim de linha separados
            saida.write(" feita")
            saida.write("\n")
        saida.write("[TikTok] Conta: @x\n")   # já tem rótulo: fica como está
        terminal.definir_rotulo(None)

    linhas = [threading.Thread(target=escrever, args=(rotulo,)) for rotulo in ("Instagram", "TikTok")]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join()
    texto = destino.getvalue().splitlines()
    assert len(texto) == 402
    for rotulo in ("Instagram", "TikTok"):
        assert [t for t in texto if t.startswith(f"[{rotulo}]     ação")] == [
            f"[{rotulo}]     ação {i} feita" for i in range(200)]
    assert texto.count("[TikTok] Conta: @x") == 2


def test_marcador_antes_do_clique_e_de_cada_linha_de_execucao():
    """O clique do TikTok nunca pode gravar o marcador da publicação do Instagram."""
    comum._linha.ao_clicar = lambda: None
    visto = {}
    linha = threading.Thread(target=lambda: visto.update(ao_clicar=comum._linha.ao_clicar,
                                                         ultimo=comum._linha.ultimo_comentario))
    linha.start()
    linha.join()
    comum._linha.ao_clicar = None
    assert visto == {"ao_clicar": None, "ultimo": None}


# ------------------------------------------------------- boost

def test_boost_vem_da_configuracao_e_o_anti_bloqueio_vale_mais():
    assert protecao.protecao_da_config({"boost": "sim"}) is PROTECAO_BOOST
    assert protecao.protecao_da_config({}, forcar_boost=True) is PROTECAO_BOOST
    assert protecao.protecao_da_config({"boost": "sim", "anti_bloqueio": "sim"}) is PROTECAO_ANTI_BLOQUEIO
    assert protecao.protecao_da_config({"boost": "nao"}) is PROTECAO_NORMAL
    assert "boost" in PROTECAO_BOOST.descricao


def test_boost_mantem_as_pausas_curtas_do_normal():
    for campo in ("pausa_leitura", "pausa_foto", "pausa_item_carrossel", "pausa_entre_acoes",
                  "pausa_entre_publicacoes", "pausa_entre_perfis"):
        assert getattr(PROTECAO_BOOST, campo) == getattr(PROTECAO_NORMAL, campo)
    assert PROTECAO_BOOST.pausa_apos_aviso_h == PROTECAO_NORMAL.pausa_apos_aviso_h  # aviso da rede continua


def test_boost_nao_espera_nem_pula_com_os_tetos_estourados(conn):
    from automacao import banco
    agora = time.strftime("%Y-%m-%dT%H:%M:%S")
    for i in range(250):                         # muito acima dos tetos do dia e da hora
        pub_id = banco.registrar_publicacao(conn, "Instagram", f"P{i}", "alvo", "posts", 1, "u")
        conn.execute("INSERT INTO acoes (publicacao_id, acao, status, registrado_em, conta) "
                     "VALUES (?, 'comentar', 'feito', ?, '@ana')", (pub_id, agora))
    conn.commit()
    esperas = []
    for acao in ("comentar", "curtir", "repostar"):
        controlar_ritmo(conn, PROTECAO_BOOST, "Instagram", "@ana", acao, dormir=esperas.append)
    assert esperas == []


# ------------------------------------------------------- Reels

def test_instagram_trabalha_posts_reels_e_reposts(conn, monkeypatch):
    visitadas = []
    monkeypatch.setattr(instagram, "listar_publicacoes",
                        lambda pg, cfg, perfil, aba, sufixo: visitadas.append((aba, sufixo)) or [])
    instagram.processar_perfil(conn, PaginaFalsa("p"), None, config(), "fulano", [], conta="@ig")
    assert visitadas == [("posts", ""), ("reels", "reels/"), ("reposts", "reposts/")]


# ------------------------------------------------------- emojis nos comentários

@pytest.mark.parametrize(("texto", "partes"), [
    ("Boa!", ["B", "o", "a", "!"]),
    ("Top 🔥", ["T", "o", "p", " ", "🔥"]),
    ("👍🏽", ["👍🏽"]),                                   # tom de pele
    ("👨‍👩‍👧!", ["👨‍👩‍👧", "!"]),                          # família (ligações ZWJ)
    ("🇧🇷🇵🇹", ["🇧🇷", "🇵🇹"]),                             # duas bandeiras seguidas
    ("❤️ão", ["❤️", "ã", "o"]),                          # coração com variação de estilo
    ("1️⃣", ["1️⃣"]),                                     # tecla
])
def test_partes_para_digitar_mantem_o_emoji_inteiro(texto, partes):
    assert comum.partes_para_digitar(texto) == partes
    assert "".join(comum.partes_para_digitar(texto)) == texto


def test_digitar_manda_o_emoji_de_uma_vez(monkeypatch):
    monkeypatch.setattr(comum.time, "sleep", lambda s: None)
    eventos = []
    teclado = argparse.Namespace(type=lambda t: eventos.append(("tecla", t)),
                                 insert_text=lambda t: eventos.append(("inteiro", t)))
    comum.digitar(argparse.Namespace(keyboard=teclado), "Oi 👏🏽🇧🇷")
    assert eventos == [("tecla", "O"), ("tecla", "i"), ("tecla", " "), ("inteiro", "👏🏽"), ("inteiro", "🇧🇷")]


# ------------------------------------------------------- tetos escolhidos na interface

import json  # noqa: E402

from automacao import configuracao  # noqa: E402


def test_sem_tetos_salvos_valem_as_sugestoes():
    assert protecao.ler_tetos("") == protecao.tetos_sugeridos(PROTECAO_NORMAL)
    assert protecao.protecao_da_config({"tetos": ""}) is PROTECAO_NORMAL
    assert protecao.protecao_da_config({"tetos": "{estragado"}) is PROTECAO_NORMAL


def test_tetos_escolhidos_valem_no_modo_normal():
    tetos = json.dumps({"por_hora": {"curtir": 40, "total": 80}, "por_dia": {"curtir": 400, "total": 600}})
    escolhida = protecao.protecao_da_config({"tetos": tetos})
    assert escolhida.nome == "normal" and escolhida is not PROTECAO_NORMAL
    assert escolhida.por_hora["curtir"] == 40 and escolhida.por_dia["total"] == 600
    assert escolhida.por_hora["comentar"] == PROTECAO_NORMAL.por_hora["comentar"]   # o resto: sugestão
    assert escolhida.pausa_entre_acoes == PROTECAO_NORMAL.pausa_entre_acoes
    assert "80 ações por hora" in escolhida.descricao


def test_anti_bloqueio_nunca_fica_mais_solto_que_o_seu_modo():
    tetos = json.dumps({"por_hora": {"curtir": 99, "comentar": 1}, "por_dia": {"curtir": 999, "comentar": 2}})
    escolhida = protecao.protecao_da_config({"tetos": tetos, "anti_bloqueio": "sim"})
    assert escolhida.nome == "anti_bloqueio"
    assert escolhida.por_hora["curtir"] == PROTECAO_ANTI_BLOQUEIO.por_hora["curtir"]   # 99 > 8: vale o 8
    assert (escolhida.por_hora["comentar"], escolhida.por_dia["comentar"]) == (1, 2)   # menores: valem
    assert escolhida.janela_horario == PROTECAO_ANTI_BLOQUEIO.janela_horario


def test_boost_ignora_os_tetos_escolhidos():
    tetos = json.dumps({"por_hora": {"curtir": 1}, "por_dia": {"curtir": 1}})
    assert protecao.protecao_da_config({"tetos": tetos, "boost": "sim"}) is PROTECAO_BOOST


@pytest.mark.parametrize(("tetos", "trecho"), [
    ({"por_hora": {"curtir": 0}}, "de 1 a 10000"),
    ({"por_dia": {"total": 10001}}, "de 1 a 10000"),
    ({"por_hora": {"curtir": "dez"}}, "de 1 a 10000"),
    ({"por_hora": {"curtir": True}}, "de 1 a 10000"),
    ({"por_hora": {"comentar": 50}, "por_dia": {"comentar": 10}}, "não pode ser maior que o do dia"),
    ([1, 2], "formato inválido"),
])
def test_validar_tetos_recusa_valores_errados(tetos, trecho):
    _, erros = protecao.validar_tetos(json.dumps(tetos))
    assert erros and trecho in erros[0]


def test_validar_aceita_numeros_em_texto():
    tetos, erros = protecao.validar_tetos(json.dumps({"por_hora": {"curtir": " 20 "}}))
    assert erros == [] and tetos["por_hora"]["curtir"] == 20


def test_configuracao_grava_vazio_quando_sao_as_sugestoes():
    base = {"modo": "quantidade", "quantidade": "3", "semanas": "2", "comentario": "Boa!", "limite_diario": "40"}
    valores, erros = configuracao.validar(dict(base, tetos=json.dumps(protecao.tetos_sugeridos())))
    assert erros == [] and valores["tetos"] == ""
    valores, erros = configuracao.validar(dict(base, tetos=json.dumps({"por_hora": {"curtir": 20}})))
    assert erros == [] and json.loads(valores["tetos"])["por_hora"]["curtir"] == 20
    _, erros = configuracao.validar(dict(base, tetos=json.dumps({"por_hora": {"curtir": 0}})))
    assert any("curtir" in erro for erro in erros)


# ------------------------------------------------------- zerar a contagem dos tetos

def test_zerar_contagem_libera_os_tetos_sem_esquecer_o_que_foi_feito(conn):
    from datetime import datetime, timedelta
    from automacao import banco
    from automacao.redes.protecao import PularAcao
    agora = datetime.now().astimezone()
    for i in range(15):                            # 15 reposts agora há pouco: tetos da hora e do dia
        pub = banco.registrar_publicacao(conn, "Instagram", f"R{i}", "alvo", "posts", 1, "u")
        conn.execute("INSERT INTO acoes (publicacao_id, acao, status, registrado_em, conta) "
                     "VALUES (?, 'repostar', 'feito', ?, '@ana')",
                     (pub, (agora - timedelta(seconds=i + 2)).isoformat(timespec="seconds")))
    conn.commit()
    esperas = []
    with pytest.raises(PularAcao):
        controlar_ritmo(conn, PROTECAO_NORMAL, "Instagram", "@ana", "repostar", dormir=esperas.append)
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram", "@ana") == 15
    assert banco.publicacoes_do_alvo_hoje(conn, "Instagram", "alvo", "@ana") == 15

    banco.zerar_contagem_dos_tetos(conn)
    controlar_ritmo(conn, PROTECAO_NORMAL, "Instagram", "@ana", "repostar", dormir=esperas.append)
    assert esperas == []                           # liberado na hora
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram", "@ana") == 0
    assert banco.publicacoes_do_alvo_hoje(conn, "Instagram", "alvo", "@ana") == 0
    assert banco.acoes_concluidas(conn, "Instagram", "R0", "@ana") == {"repostar"}   # nunca repete


# ------------------------------------------------------- aviso de bloqueio para tudo

class ContextoLogado(ContextoFalso):
    def cookies(self):
        return [{"name": "sessionid", "domain": ".instagram.com", "value": "x"},
                {"name": "sessionid", "domain": ".tiktok.com", "value": "x"}]


class RedeFalsa:
    """No lugar de instagram/tiktok: anota os perfis e levanta um aviso no perfil pedido."""

    def __init__(self, avisar_em=None, erro=None):
        self.visitados = []
        self.avisar_em = avisar_em
        self.erro = erro

    def processar_perfil(self, conn, pagina, rodada_id, cfg, perfil, resumo, conta=None):
        self.visitados.append(perfil)
        if perfil == self.avisar_em:
            raise self.erro


def test_aviso_de_bloqueio_para_a_lista_inteira_e_poe_a_conta_em_pausa(conn, monkeypatch):
    from automacao import banco
    from automacao.redes.protecao import Bloqueio
    instagram_falso = RedeFalsa(avisar_em="b", erro=Bloqueio("Tente novamente mais tarde"))
    tiktok_falso = RedeFalsa()
    monkeypatch.setattr(interagir, "MODULOS", {"Instagram": instagram_falso, "TikTok": tiktok_falso})
    monkeypatch.setattr(interagir, "pausa", lambda intervalo=None: None)
    with pytest.raises(interagir.ParadaPorAviso, match="Tente novamente mais tarde"):
        interagir.processar_perfis(conn, ContextoLogado(), None, config(), ALVOS, 0, set(), [], CONTAS)
    assert instagram_falso.visitados == ["a", "b"]     # parou no aviso...
    assert tiktok_falso.visitados == []                # ...e nada depois dele, nem o TikTok "c"
    assert banco.pausa_ativa(conn, "Instagram", "@ig") is not None


def test_teto_do_dia_da_conta_nao_para_as_outras_redes(conn, monkeypatch):
    from automacao.redes.protecao import LimiteDiario
    instagram_falso = RedeFalsa(avisar_em="a", erro=LimiteDiario("teto do dia"))
    tiktok_falso = RedeFalsa()
    monkeypatch.setattr(interagir, "MODULOS", {"Instagram": instagram_falso, "TikTok": tiktok_falso})
    monkeypatch.setattr(interagir, "pausa", lambda intervalo=None: None)
    assert interagir.processar_perfis(conn, ContextoLogado(), None, config(), ALVOS, 0, set(), [], CONTAS) == 5
    assert instagram_falso.visitados == ["a"] and tiktok_falso.visitados == ["c", "e"]


def test_aviso_na_segunda_rede_para_a_primeira_tambem(conn, chrome, monkeypatch):
    instagram_parou = threading.Event()

    def instagram_trabalhando():
        try:
            for _ in range(200):
                terminal.dormir(0.05)
        except KeyboardInterrupt:
            instagram_parou.set()
            raise
        return 3

    def tiktok_avisou():
        time.sleep(0.2)
        raise interagir.ParadaPorAviso("TikTok (@tt): Você está indo rápido demais")

    processar = ProcessarFalso({("Instagram", 1): instagram_trabalhando, ("TikTok", 1): tiktok_avisou})
    with pytest.raises(interagir.ParadaPorAviso, match="rápido demais"):
        rodar_em_paralelo(conn, chrome, processar, monkeypatch)
    assert instagram_parou.is_set()


def test_aviso_na_primeira_rede_para_a_segunda_tambem(conn, chrome, monkeypatch):
    tiktok_parou = threading.Event()

    def tiktok_trabalhando():
        try:
            for _ in range(200):
                terminal.dormir(0.05)
        except KeyboardInterrupt:
            tiktok_parou.set()
            raise
        return 2

    def instagram_avisou():
        time.sleep(0.2)
        raise interagir.ParadaPorAviso("Instagram (@ig): Ação bloqueada")

    processar = ProcessarFalso({("TikTok", 1): tiktok_trabalhando, ("Instagram", 1): instagram_avisou})
    with pytest.raises(interagir.ParadaPorAviso, match="Ação bloqueada"):
        rodar_em_paralelo(conn, chrome, processar, monkeypatch)
    assert tiktok_parou.wait(2)


def test_aviso_encerra_todas_as_contas_e_nao_recomeca(conn, monkeypatch, capsys):
    from automacao import banco
    contas_rodadas = []

    def rodar(p, conn_, rodada_id, cfg, perfil_nav, alvos, resumo, args):
        contas_rodadas.append(perfil_nav["nome"])
        raise interagir.ParadaPorAviso("Instagram (@ig): Ação bloqueada")

    monkeypatch.setattr(interagir, "rodar_perfil_navegador", rodar)
    banco.criar_perfil(conn, "Loja")
    perfis_nav = banco.listar_perfis(conn, somente_ativos=True)
    args = argparse.Namespace(perfis_navegador=None, simular=False, continuar_sem_login=True, trace=False)
    status = interagir.executar_volta(None, conn, config(), ALVOS, perfis_nav, args, 1, 20)
    assert status == "aviso"
    assert contas_rodadas == [perfis_nav[0]["nome"]]          # a segunda conta nem começa
    assert "A automação parou inteira" in capsys.readouterr().out
    assert conn.execute("SELECT status FROM rodadas ORDER BY id DESC").fetchone()[0] == "interrompida"


def test_captcha_do_tiktok_conta_como_aviso_e_nao_espera(monkeypatch):
    from automacao.redes import tiktok
    from automacao.redes.protecao import Bloqueio
    visivel = SimpleNamespace(first=SimpleNamespace(is_visible=lambda: True))
    pagina = SimpleNamespace(locator=lambda seletor: visivel)
    inicio = time.monotonic()
    with pytest.raises(Bloqueio, match="captcha"):
        tiktok.esperar_captcha(pagina)
    assert time.monotonic() - inicio < 1
    escondido = SimpleNamespace(first=SimpleNamespace(is_visible=lambda: False))
    tiktok.esperar_captcha(SimpleNamespace(locator=lambda seletor: escondido))   # sem captcha: segue


# ------------------------------------------------------- faixa de publicações por aba

def test_sem_faixa_todas_as_contas_usam_o_maximo():
    cfg = config(quantidade=5)
    assert comum.sortear_quantidades(cfg, CONTAS) is cfg
    assert cfg.quantidade_de("Instagram") == 5


def test_faixa_sorteia_um_numero_por_conta_e_varia(capsys):
    cfg = config(quantidade=8, quantidade_min=2)
    assert cfg.tem_faixa and "de 2 a 8" in cfg.descricao()
    vistos = {rede: set() for rede in CONTAS}
    for _ in range(300):
        sorteada = comum.sortear_quantidades(cfg, CONTAS)
        for rede in CONTAS:
            n = sorteada.quantidade_de(rede)
            assert 2 <= n <= 8
            vistos[rede].add(n)
    assert all(len(numeros) >= 6 for numeros in vistos.values())   # usa a faixa toda, sem padrão fixo
    assert "publicações por aba nesta volta (sorteio de 2 a 8)" in capsys.readouterr().out


def test_coletar_da_grade_usa_a_quantidade_da_conta():
    cfg = comum.sortear_quantidades(config(quantidade=6, quantidade_min=6), CONTAS)   # faixa 6-6: fixo
    assert cfg.quantidade_de("TikTok") == 6
    cfg = config(quantidade=10, quantidade_min=1, quantidades={"Instagram": 2, "TikTok": 7})
    hrefs = [f"/p/C{i}/" for i in range(20)]
    lidos = comum.coletar_da_grade(SimpleNamespace(), lambda: hrefs,
                                   lambda h: (h.split("/")[2], "https://x" + h), lambda codigo: None,
                                   cfg, "Instagram", "fulano", "posts")
    assert len(lidos) == 2


@pytest.mark.parametrize(("salvas", "esperado"), [
    ({"quantidade": "6", "quantidade_min": "2"}, 2),
    ({"quantidade": "6", "quantidade_min": ""}, None),
    ({"quantidade": "6", "quantidade_min": "9"}, None),      # maior que o máximo: sem faixa
    ({"quantidade": "6", "quantidade_min": "x"}, None),
    ({"quantidade": "6"}, None),
])
def test_quantidade_minima_salva(salvas, esperado):
    assert interagir.quantidade_minima(salvas) == esperado


def test_validar_faixa_de_quantidade():
    from automacao import configuracao
    base = {"modo": "quantidade", "quantidade": "6", "semanas": "2", "comentario": "Boa!", "limite_diario": "40"}
    valores, erros = configuracao.validar(dict(base, quantidade_min="2"))
    assert erros == [] and valores["quantidade_min"] == "2"
    _, erros = configuracao.validar(dict(base, quantidade_min="7"))
    assert erros and "não pode ser maior que o máximo" in erros[0]
    _, erros = configuracao.validar(dict(base, quantidade_min="0"))
    assert erros and "de 1 a 50" in erros[0]
    valores, erros = configuracao.validar(dict(base, quantidade_min=""))
    assert erros == [] and valores["quantidade_min"] == ""
    valores, erros = configuracao.validar(dict(base, modo="tempo", quantidade_min="99"))
    assert erros == [] and "quantidade_min" not in valores        # fora de uso: não cobra, não grava



# ------------------------------------------------------- Instagram: limite da API na janela de compartilhar

class PaginaComRespostas:
    def __init__(self):
        self.ouvintes = []

    def on(self, evento, funcao):
        assert evento == "response"
        self.ouvintes.append(funcao)

    def remove_listener(self, evento, funcao):
        self.ouvintes.remove(funcao)

    def responder(self, url, corpo):
        for funcao in list(self.ouvintes):
            funcao(SimpleNamespace(url=url, text=lambda: corpo))


LIMITE = '{"errors":[{"message":"Rate limit exceeded","severity":"CRITICAL","code":1675004}]}'


def test_vigiar_limite_da_api_reconhece_o_rate_limit_e_para_de_ouvir():
    pg = PaginaComRespostas()
    estado, parar = instagram.vigiar_limite_da_api(pg)
    pg.responder("https://www.instagram.com/api/graphql", '{"data":{"results":[]}}')
    assert estado["limitada"] is False
    pg.responder("https://static.cdninstagram.com/x.js", LIMITE)      # fora da API: não conta
    assert estado["limitada"] is False
    pg.responder("https://www.instagram.com/api/graphql", LIMITE)
    assert estado["limitada"] is True
    parar()
    assert pg.ouvintes == []


@pytest.mark.parametrize(("corpo", "limitada"), [
    (LIMITE, True),
    ('{"errors":[{"code": 1675004}]}', True),
    ('{"message":"rate limit exceeded"}', True),
    ('{"data":{"user":"ok"}}', False),
])
def test_resposta_de_limite(corpo, limitada):
    assert bool(instagram.RE_LIMITE_API.search(corpo)) is limitada
