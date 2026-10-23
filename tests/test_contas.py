"""Testes do backend das várias contas: perfis do navegador, conta em cada ação,
limite diário por conta, escolha de redes e migração dos registros antigos."""

import argparse
import sqlite3

import pytest

from automacao import banco, interagir
from automacao.redes import comum


# ---------------------------------------------------------------- perfis

def test_banco_novo_tem_o_perfil_principal(conn):
    perfis = banco.listar_perfis(conn)
    assert [(p["id"], p["nome"], p["pasta"], p["ativo"]) for p in perfis] == [
        (1, "Principal", "perfil_navegador", 1)]
    assert banco.pasta_do_perfil(perfis[0]) == banco.PASTA_PERFIL


def test_criar_perfil_gera_pasta_sem_acento_dentro_de_perfis(conn):
    perfil_id = banco.criar_perfil(conn, "  Conta   da Márcia  ")
    perfil = banco.obter_perfil(conn, perfil_id)
    assert perfil["nome"] == "Conta da Márcia"
    assert perfil["pasta"] == "perfis/conta-da-marcia"
    assert banco.pasta_do_perfil(perfil) == banco.RAIZ / "perfis" / "conta-da-marcia"


def test_nomes_que_viram_a_mesma_pasta_ganham_sufixo(conn):
    banco.criar_perfil(conn, "Loja")
    segundo = banco.obter_perfil(conn, banco.criar_perfil(conn, "Loja!"))
    assert segundo["pasta"] == "perfis/loja-2"


@pytest.mark.parametrize("nome", ["", "   ", "x" * 41])
def test_nome_invalido_e_recusado(conn, nome):
    with pytest.raises(ValueError):
        banco.criar_perfil(conn, nome)


def test_nome_repetido_e_recusado_sem_diferenciar_maiusculas(conn):
    banco.criar_perfil(conn, "Loja")
    with pytest.raises(ValueError, match="Já existe"):
        banco.criar_perfil(conn, "LOJA")
    with pytest.raises(ValueError, match="Já existe"):
        banco.criar_perfil(conn, "principal")


def test_obter_perfil_por_id_ou_nome(conn):
    perfil_id = banco.criar_perfil(conn, "Loja")
    assert banco.obter_perfil(conn, perfil_id)["nome"] == "Loja"
    assert banco.obter_perfil(conn, str(perfil_id))["nome"] == "Loja"
    assert banco.obter_perfil(conn, "loja")["id"] == perfil_id
    assert banco.obter_perfil(conn, "não existe") is None


def test_renomear_ativar_e_listar_so_ativos(conn):
    perfil_id = banco.criar_perfil(conn, "Loja")
    banco.renomear_perfil(conn, perfil_id, "Loja Centro")
    banco.definir_perfil_ativo(conn, perfil_id, False)
    assert [p["nome"] for p in banco.listar_perfis(conn)] == ["Principal", "Loja Centro"]
    assert [p["nome"] for p in banco.listar_perfis(conn, somente_ativos=True)] == ["Principal"]
    with pytest.raises(ValueError):
        banco.renomear_perfil(conn, perfil_id, "Principal")


def test_remover_perfil_devolve_a_pasta_e_mantem_o_historico(conn):
    perfil_id = banco.criar_perfil(conn, "Loja")
    execucao = banco.iniciar_execucao(conn, perfil_id)
    pasta = banco.remover_perfil(conn, perfil_id)
    assert pasta == banco.RAIZ / "perfis" / "loja"
    assert banco.obter_perfil(conn, perfil_id) is None
    linha = conn.execute("SELECT perfil_id FROM execucoes WHERE id = ?", (execucao,)).fetchone()
    assert linha[0] is None  # a execução continua no histórico, sem o perfil


def test_principal_nao_pode_ser_removido(conn):
    with pytest.raises(ValueError, match="Principal"):
        banco.remover_perfil(conn, banco.PERFIL_PRINCIPAL)


def test_logins_por_perfil(conn):
    loja = banco.criar_perfil(conn, "Loja")
    e1 = banco.iniciar_execucao(conn)          # Principal
    banco.registrar_login(conn, e1, "Instagram", "ja_logado")
    banco.registrar_conta(conn, e1, "Instagram", "@principal")
    e2 = banco.iniciar_execucao(conn, loja)
    banco.registrar_login(conn, e2, "Instagram", "login_detectado")
    banco.registrar_conta(conn, e2, "Instagram", "@loja")
    assert banco.ultimo_status_logins(conn, banco.PERFIL_PRINCIPAL)["Instagram"]["conta"] == "@principal"
    assert banco.ultimo_status_logins(conn, loja)["Instagram"]["conta"] == "@loja"
    assert banco.ultimo_status_logins(conn)["Instagram"]["conta"] == "@loja"  # sem filtro: o mais recente


# ----------------------------------------------- ações e limite por conta

def _acao(conn, codigo, conta, acao="curtir", status="feito", rede="Instagram"):
    pub_id = banco.registrar_publicacao(conn, rede, codigo, "alvo", "posts", 1, f"https://x/{codigo}")
    banco.registrar_acao(conn, pub_id, None, acao, status, conta=conta)


def test_acoes_concluidas_sao_separadas_por_conta(conn):
    _acao(conn, "P1", "@ana")
    assert banco.acoes_concluidas(conn, "Instagram", "P1", "@ana") == {"curtir"}
    assert banco.acoes_concluidas(conn, "Instagram", "P1", "@bia") == set()
    assert banco.acoes_concluidas(conn, "Instagram", "P1") == {"curtir"}  # sem conta: qualquer uma


def test_acao_antiga_sem_conta_vale_para_todas(conn):
    _acao(conn, "P1", None)
    assert banco.acoes_concluidas(conn, "Instagram", "P1", "@bia") == {"curtir"}


def test_limite_diario_e_por_conta(conn):
    _acao(conn, "P1", "@ana")
    _acao(conn, "P2", "@ana")
    _acao(conn, "P3", "@bia")
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram", "@ana") == 2
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram", "@bia") == 1
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram") == 3


@pytest.fixture
def sem_espera(monkeypatch):
    monkeypatch.setattr(comum, "pausa", lambda intervalo=None: None)
    monkeypatch.setattr(comum, "monitorar_video", lambda pg, token: None)
    monkeypatch.setattr(comum, "esperar_fim_do_video", lambda pg, token: None)
    monkeypatch.setattr(comum, "capturar_tela", lambda pg, pub, acao: None)


class PaginaFalsa:
    def is_closed(self):
        return False


def _cfg(**opcoes):
    base = dict(modo="quantidade", quantidade=3, semanas=2, comentario="Boa!!!",
                compartilhar_com="x", limite_diario=40, simular=False)
    base.update(opcoes)
    return comum.Config(**base)


def _processar(conn, conta, chamadas, cfg=None):
    pub = comum.Publicacao("Instagram", "alvo", "posts", 1, "P1", "https://x/P1")

    def curtir(pg, cfg, pub):
        chamadas.append(conta)
        return comum.Resultado("feito")

    comum.processar_publicacao(conn, PaginaFalsa(), None, cfg or _cfg(), pub, abrir=lambda: None,
                               acoes=[("curtir", curtir)], resumo=[], conta=conta)


def test_segunda_conta_age_na_publicacao_que_a_primeira_ja_fez(conn, sem_espera):
    chamadas = []
    _processar(conn, "@ana", chamadas)
    _processar(conn, "@ana", chamadas)   # a mesma conta não repete
    _processar(conn, "@bia", chamadas)   # outra conta age
    assert chamadas == ["@ana", "@bia"]
    contas = [linha[0] for linha in conn.execute("SELECT conta FROM acoes ORDER BY id")]
    assert contas == ["@ana", "@bia"]


def test_limite_diario_de_uma_conta_nao_bloqueia_a_outra(conn, sem_espera):
    _acao(conn, "OUTRO", "@ana")
    with pytest.raises(comum.LimiteDiario, match="@ana"):
        _processar(conn, "@ana", [], cfg=_cfg(limite_diario=1))
    chamadas = []
    _processar(conn, "@bia", chamadas, cfg=_cfg(limite_diario=1))
    assert chamadas == ["@bia"]


# ------------------------------------------------------------ escolha de redes

@pytest.mark.parametrize(("valor", "redes"), [("instagram", ("Instagram",)), ("tiktok", ("TikTok",)),
                                               ("ambas", ("Instagram", "TikTok")),
                                               ("qualquer", ("Instagram", "TikTok"))])
def test_redes_escolhidas(valor, redes):
    assert banco.redes_escolhidas({"redes": valor}) == redes


def _args(**opcoes):
    base = dict(quantidade=None, semanas=None, comentario=None, compartilhar_com=None,
                limite_diario=None, simular=False, planilha_compartilhar=None, redes=None)
    base.update(opcoes)
    return argparse.Namespace(**base)


def test_montar_config_usa_as_redes_salvas_ou_a_opcao():
    salvas = dict(banco.CONFIG_PADRAO, redes="tiktok")
    assert interagir.montar_config(salvas, _args()).redes == ("TikTok",)
    assert interagir.montar_config(salvas, _args(redes="ambas")).redes == ("Instagram", "TikTok")
    assert interagir.montar_config(dict(banco.CONFIG_PADRAO), _args()).redes == ("Instagram", "TikTok")


def test_escolher_perfis_do_navegador(conn):
    loja = banco.criar_perfil(conn, "Loja")
    casa = banco.criar_perfil(conn, "Casa")
    banco.definir_perfil_ativo(conn, casa, False)
    assert [p["nome"] for p in interagir.escolher_perfis_navegador(conn, None)] == ["Principal", "Loja"]
    escolhidos = interagir.escolher_perfis_navegador(conn, ["casa", str(loja), "Casa"])
    assert [p["nome"] for p in escolhidos] == ["Casa", "Loja"]  # sem repetir
    with pytest.raises(ValueError, match="não encontrado"):
        interagir.escolher_perfis_navegador(conn, ["Fantasma"])
    for perfil in banco.listar_perfis(conn):
        banco.definir_perfil_ativo(conn, perfil["id"], False)
    with pytest.raises(ValueError, match="nenhum perfil ativo"):
        interagir.escolher_perfis_navegador(conn, None)


def test_processar_perfis_pula_rede_sem_conta_identificada(monkeypatch, conn):
    chamadas = []

    class ModuloFalso:
        def __init__(self, rede):
            self.rede = rede

        def processar_perfil(self, conn, pagina, rodada_id, cfg, perfil, resumo, conta=None):
            chamadas.append((self.rede, perfil, conta))

    class ContextoFalso:
        pages = [PaginaFalsa()]

    monkeypatch.setattr(interagir, "MODULOS", {"Instagram": ModuloFalso("Instagram"), "TikTok": ModuloFalso("TikTok")})
    monkeypatch.setattr(interagir, "redes_logadas", lambda contexto: {"Instagram", "TikTok"})
    monkeypatch.setattr(interagir, "pausa", lambda intervalo=None: None)
    PaginaFalsa.bring_to_front = lambda self: None
    alvos = [("Instagram", "a"), ("TikTok", "b"), ("Instagram", "c")]
    fim = interagir.processar_perfis(conn, ContextoFalso(), None, _cfg(), alvos, 0, set(), [],
                                     contas={"Instagram": "@ana"})
    assert fim == 3
    assert chamadas == [("Instagram", "a", "@ana"), ("Instagram", "c", "@ana")]


# ------------------------------------------------------------ migração

def test_migracao_atribui_as_contas_lidas_no_login_as_acoes_antigas(tmp_path):
    caminho = tmp_path / "antigo.db"
    antigo = sqlite3.connect(caminho)
    versao_perfis = next(i for i, m in enumerate(banco.MIGRACOES, 1) if "CREATE TABLE perfis" in m)
    for numero, migracao in enumerate(banco.MIGRACOES[:versao_perfis - 1], start=1):
        antigo.executescript(migracao)
        antigo.execute(f"PRAGMA user_version = {numero}")
    agora = "2026-10-23T12:00:00-03:00"
    antigo.execute("INSERT INTO execucoes (id, iniciada_em) VALUES (1, ?)", (agora,))
    antigo.execute("INSERT INTO logins (execucao_id, rede, status, registrado_em, conta) "
                   "VALUES (1, 'Instagram', 'ja_logado', ?, '@ig_conta'), "
                   "       (1, 'TikTok', 'ja_logado', ?, '@tt_conta')", (agora, agora))
    for pub_id, rede in ((1, "Instagram"), (2, "TikTok")):
        antigo.execute("INSERT INTO publicacoes (id, rede, codigo, perfil, aba, posicao, url, encontrada_em, "
                       "atualizada_em) VALUES (?, ?, ?, 'alvo', 'posts', 1, 'u', ?, ?)",
                       (pub_id, rede, f"C{pub_id}", agora, agora))
        antigo.execute("INSERT INTO acoes (publicacao_id, acao, status, registrado_em) VALUES (?, 'curtir', 'feito', ?)",
                       (pub_id, agora))
    antigo.commit()
    antigo.close()

    conn = banco.conectar(caminho)
    try:
        contas = dict(conn.execute("SELECT p.rede, a.conta FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id"))
        assert contas == {"Instagram": "@ig_conta", "TikTok": "@tt_conta"}
        assert conn.execute("SELECT perfil_id FROM execucoes").fetchone()[0] == banco.PERFIL_PRINCIPAL
        assert [p["nome"] for p in banco.listar_perfis(conn)] == ["Principal"]
    finally:
        conn.close()
