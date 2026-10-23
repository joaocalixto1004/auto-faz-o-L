"""Testes do banco SQLite (banco.py), sempre num arquivo temporário."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from automacao import banco

TABELAS = {"execucoes", "logins", "rodadas", "publicacoes", "acoes", "configuracoes"}


def versao(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def tabelas(conn) -> set[str]:
    return {linha[0] for linha in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def colunas(conn, tabela: str) -> set[str]:
    return {linha[1] for linha in conn.execute(f"PRAGMA table_info({tabela})")}


def nova_publicacao(conn, codigo="ABC", rede="Instagram", **extra) -> int:
    dados = dict(perfil="fulano", aba="posts", posicao=1, url=f"https://exemplo/{codigo}/")
    dados.update(extra)
    return banco.registrar_publicacao(conn, rede, codigo, **dados)


def inserir_acao(conn, publicacao_id: int, acao: str, status: str, registrado_em: str) -> None:
    """Insere uma ação com horário fixo (o banco.registrar_acao sempre usa o horário atual)."""
    with conn:
        conn.execute("INSERT INTO acoes (publicacao_id, acao, status, registrado_em) VALUES (?, ?, ?, ?)",
                     (publicacao_id, acao, status, registrado_em))


# ---------------------------------------------------------------- migrações

def test_migracoes_aplicadas_em_banco_novo(conn):
    assert versao(conn) == len(banco.MIGRACOES)
    assert TABELAS <= tabelas(conn)
    assert "publicada_em" in colunas(conn, "publicacoes")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migracoes_idempotentes_ao_reabrir(tmp_path):
    caminho = tmp_path / "t.db"
    conn = banco.conectar(caminho)
    execucao = banco.iniciar_execucao(conn)
    conn.close()

    conn = banco.conectar(caminho)
    assert versao(conn) == len(banco.MIGRACOES)
    banco.migrar(conn)  # rodar de novo não pode quebrar nem duplicar nada
    assert versao(conn) == len(banco.MIGRACOES)
    assert [linha["id"] for linha in banco.historico(conn)] == [execucao]
    conn.close()


def test_banco_na_versao_1_atualiza_para_a_ultima(tmp_path):
    caminho = tmp_path / "antigo.db"
    antigo = sqlite3.connect(caminho)
    antigo.executescript(banco.MIGRACOES[0])
    antigo.execute("PRAGMA user_version = 1")
    antigo.execute("INSERT INTO execucoes (iniciada_em) VALUES ('2026-01-01T10:00:00-03:00')")
    antigo.execute("INSERT INTO logins (execucao_id, rede, status, registrado_em) "
                   "VALUES (1, 'TikTok', 'ja_logado', '2026-01-01T10:00:01-03:00')")
    antigo.commit()
    antigo.close()

    conn = banco.conectar(caminho)
    try:
        assert versao(conn) == len(banco.MIGRACOES)
        assert TABELAS <= tabelas(conn)
        assert "publicada_em" in colunas(conn, "publicacoes")
        # os dados da versão 1 continuam lá
        linhas = banco.historico(conn)
        assert len(linhas) == 1
        assert linhas[0]["logins"] == "TikTok: ja_logado"
        # e as tabelas novas funcionam
        pub = nova_publicacao(conn, publicada_em="2026-10-17T18:30:55+00:00")
        banco.registrar_acao(conn, pub, banco.iniciar_rodada(conn), "curtir", "feito")
        assert banco.acoes_concluidas(conn, "Instagram", "ABC") == {"curtir"}
        banco.salvar_configuracoes(conn, {"comentario": "Oi"})
        assert banco.ler_configuracoes(conn)["comentario"] == "Oi"
    finally:
        conn.close()


def test_chaves_estrangeiras_ativas(conn):
    with pytest.raises(sqlite3.IntegrityError):
        banco.registrar_acao(conn, 999, None, "curtir", "feito")


# ------------------------------------------------------------- publicações

def test_registrar_publicacao_upsert_devolve_mesmo_id_e_mantem_primeira_data(conn):
    primeiro = nova_publicacao(conn, publicada_em="2026-10-01T10:00:00+00:00")
    segundo = nova_publicacao(conn, perfil="outro", aba="reposts", posicao=7,
                              publicada_em="2027-01-01T00:00:00+00:00")
    assert primeiro == segundo
    linhas = conn.execute("SELECT * FROM publicacoes").fetchall()
    assert len(linhas) == 1
    assert linhas[0]["publicada_em"] == "2026-10-01T10:00:00+00:00"
    # perfil, aba e posição continuam os de quando foi encontrada
    assert (linhas[0]["perfil"], linhas[0]["aba"], linhas[0]["posicao"]) == ("fulano", "posts", 1)


def test_registrar_publicacao_preenche_data_que_faltava(conn):
    primeiro = nova_publicacao(conn, publicada_em=None)
    segundo = nova_publicacao(conn, publicada_em="2026-10-01T10:00:00+00:00")
    assert primeiro == segundo
    assert conn.execute("SELECT publicada_em FROM publicacoes").fetchone()[0] == "2026-10-01T10:00:00+00:00"


def test_mesmo_codigo_em_redes_diferentes_sao_publicacoes_diferentes(conn):
    assert nova_publicacao(conn, rede="Instagram") != nova_publicacao(conn, rede="TikTok")


def test_salvar_link(conn):
    pub = nova_publicacao(conn)
    banco.salvar_link(conn, pub, "https://www.instagram.com/p/ABC/")
    assert conn.execute("SELECT link FROM publicacoes WHERE id = ?", (pub,)).fetchone()[0] == \
        "https://www.instagram.com/p/ABC/"


# ------------------------------------------------------------------- ações

def test_acoes_concluidas_so_conta_status_concluidos(conn):
    pub = nova_publicacao(conn)
    banco.registrar_acao(conn, pub, None, "curtir", "feito")
    banco.registrar_acao(conn, pub, None, "repostar", "ja_estava_feito")
    banco.registrar_acao(conn, pub, None, "comentar", "nao_confirmado")
    banco.registrar_acao(conn, pub, None, "compartilhar", "falhou")
    assert banco.acoes_concluidas(conn, "Instagram", "ABC") == {"curtir", "repostar", "comentar"}
    assert set(banco.STATUS_CONCLUIDOS) == {"feito", "ja_estava_feito", "nao_confirmado"}


def test_acao_que_falhou_e_depois_deu_certo_fica_concluida(conn):
    pub = nova_publicacao(conn)
    banco.registrar_acao(conn, pub, None, "curtir", "falhou", "botão sumiu")
    assert banco.acoes_concluidas(conn, "Instagram", "ABC") == set()
    banco.registrar_acao(conn, pub, None, "curtir", "feito")
    assert banco.acoes_concluidas(conn, "Instagram", "ABC") == {"curtir"}


def test_acoes_concluidas_nao_mistura_redes(conn):
    instagram = nova_publicacao(conn, rede="Instagram")
    banco.registrar_acao(conn, instagram, None, "curtir", "feito")
    assert banco.acoes_concluidas(conn, "TikTok", "ABC") == set()
    assert banco.acoes_concluidas(conn, "Instagram", "OUTRO") == set()


@pytest.mark.parametrize("status", ["simulado", "FEITO", "", "concluido"])
def test_check_rejeita_status_de_acao_invalido(conn, status):
    pub = nova_publicacao(conn)
    with pytest.raises(sqlite3.IntegrityError):
        banco.registrar_acao(conn, pub, None, "curtir", status)
    assert conn.execute("SELECT count(*) FROM acoes").fetchone()[0] == 0


def test_check_rejeita_status_invalidos_nas_outras_tabelas(conn):
    execucao = banco.iniciar_execucao(conn)
    with pytest.raises(sqlite3.IntegrityError):
        banco.finalizar_execucao(conn, execucao, "acabou")
    with pytest.raises(sqlite3.IntegrityError):
        banco.registrar_login(conn, execucao, "TikTok", "talvez")
    rodada = banco.iniciar_rodada(conn)
    with pytest.raises(sqlite3.IntegrityError):
        banco.finalizar_rodada(conn, rodada, "mais_ou_menos")


def test_publicacoes_trabalhadas_hoje(conn):
    hoje = datetime.now().astimezone().isoformat(timespec="seconds")
    ontem = (datetime.now().astimezone() - timedelta(days=1)).isoformat(timespec="seconds")

    p1 = nova_publicacao(conn, "P1")
    inserir_acao(conn, p1, "curtir", "feito", hoje)
    inserir_acao(conn, p1, "comentar", "feito", hoje)        # mesma publicação: conta uma vez
    p2 = nova_publicacao(conn, "P2")
    inserir_acao(conn, p2, "curtir", "nao_confirmado", hoje)
    p3 = nova_publicacao(conn, "P3")
    inserir_acao(conn, p3, "curtir", "ja_estava_feito", hoje)  # não foi o script que fez
    inserir_acao(conn, p3, "comentar", "falhou", hoje)
    p4 = nova_publicacao(conn, "P4")
    inserir_acao(conn, p4, "curtir", "feito", ontem)          # outro dia
    p5 = nova_publicacao(conn, "P5", rede="TikTok")
    inserir_acao(conn, p5, "curtir", "feito", hoje)           # outra rede

    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram") == 2
    assert banco.publicacoes_trabalhadas_hoje(conn, "TikTok") == 1
    assert banco.publicacoes_trabalhadas_hoje(conn, "RedeSemRegistros") == 0


def test_publicacoes_trabalhadas_hoje_com_registrar_acao(conn):
    pub = nova_publicacao(conn)
    banco.registrar_acao(conn, pub, None, "curtir", "feito")
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram") == 1


# ----------------------------------------------------------- configurações

def test_ler_configuracoes_sem_nada_salvo_devolve_padrao(conn):
    valores = banco.ler_configuracoes(conn)
    assert valores == banco.CONFIG_PADRAO
    valores["comentario"] = "mudei"
    assert banco.CONFIG_PADRAO["comentario"] != "mudei"  # devolve uma cópia


def test_ler_configuracoes_junta_padrao_com_salvas(conn):
    banco.salvar_configuracoes(conn, {"comentario": "Oi", "quantidade": 7})
    valores = banco.ler_configuracoes(conn)
    esperado = dict(banco.CONFIG_PADRAO, comentario="Oi", quantidade="7")
    assert valores == esperado


def test_salvar_configuracoes_faz_upsert(conn):
    banco.salvar_configuracoes(conn, {"comentario": "Primeiro", "modo": "tempo"})
    banco.salvar_configuracoes(conn, {"comentario": "Segundo"})
    linhas = conn.execute("SELECT chave, valor, atualizada_em FROM configuracoes ORDER BY chave").fetchall()
    assert [(linha["chave"], linha["valor"]) for linha in linhas] == [("comentario", "Segundo"), ("modo", "tempo")]
    assert all(linha["atualizada_em"] for linha in linhas)


# ------------------------------------------------- execuções e rodadas

def test_iniciar_e_finalizar_execucao_com_logins(conn):
    execucao = banco.iniciar_execucao(conn)
    linha = conn.execute("SELECT * FROM execucoes WHERE id = ?", (execucao,)).fetchone()
    assert linha["iniciada_em"] and linha["finalizada_em"] is None and linha["motivo_fim"] is None

    banco.registrar_login(conn, execucao, "TikTok", "nao_logado")
    banco.registrar_login(conn, execucao, "TikTok", "login_detectado")  # atualiza a mesma linha
    banco.registrar_login(conn, execucao, "Instagram", "ja_logado")
    assert conn.execute("SELECT count(*) FROM logins").fetchone()[0] == 2

    banco.finalizar_execucao(conn, execucao, "enter")
    linha = conn.execute("SELECT * FROM execucoes WHERE id = ?", (execucao,)).fetchone()
    assert linha["finalizada_em"] is not None and linha["motivo_fim"] == "enter"


def test_historico(conn):
    primeira = banco.iniciar_execucao(conn)
    banco.registrar_login(conn, primeira, "TikTok", "login_detectado")
    banco.registrar_login(conn, primeira, "Instagram", "ja_logado")
    banco.finalizar_execucao(conn, primeira, "janela_fechada")
    segunda = banco.iniciar_execucao(conn)

    linhas = banco.historico(conn)
    assert [linha["id"] for linha in linhas] == [segunda, primeira]   # mais recente primeiro
    assert linhas[0]["logins"] is None and linhas[0]["finalizada_em"] is None
    assert set(linhas[1]["logins"].split(", ")) == {"TikTok: login_detectado", "Instagram: ja_logado"}
    assert linhas[1]["motivo_fim"] == "janela_fechada"
    assert [linha["id"] for linha in banco.historico(conn, limite=1)] == [segunda]


def test_iniciar_e_finalizar_rodada(conn):
    rodada = banco.iniciar_rodada(conn)
    linha = conn.execute("SELECT * FROM rodadas WHERE id = ?", (rodada,)).fetchone()
    assert linha["iniciada_em"] and linha["status"] is None and linha["finalizada_em"] is None
    banco.finalizar_rodada(conn, rodada, "concluida")
    linha = conn.execute("SELECT * FROM rodadas WHERE id = ?", (rodada,)).fetchone()
    assert linha["status"] == "concluida" and linha["finalizada_em"] is not None
    assert banco.iniciar_rodada(conn) != rodada


def test_publicacoes_recentes(conn):
    antiga = nova_publicacao(conn, "ANTIGA", publicada_em="2026-10-01T10:00:00+00:00")
    nova = nova_publicacao(conn, "NOVA")
    banco.registrar_acao(conn, antiga, None, "curtir", "falhou")
    banco.registrar_acao(conn, antiga, None, "curtir", "feito")
    banco.registrar_acao(conn, antiga, None, "comentar", "nao_confirmado")
    banco.salvar_link(conn, antiga, "https://www.instagram.com/p/ANTIGA/")
    # horários fixos para a ordem não depender do relógio
    with conn:
        conn.execute("UPDATE publicacoes SET atualizada_em = '2026-10-20T10:00:00-03:00' WHERE id = ?", (antiga,))
        conn.execute("UPDATE publicacoes SET atualizada_em = '2026-10-21T10:00:00-03:00' WHERE id = ?", (nova,))

    linhas = banco.publicacoes_recentes(conn)
    assert [linha["codigo"] for linha in linhas] == ["NOVA", "ANTIGA"]
    assert linhas[0]["acoes"] is None
    # só o último status de cada ação aparece
    assert set(linhas[1]["acoes"].split(", ")) == {"curtir: feito", "comentar: nao_confirmado"}
    assert linhas[1]["link"] == "https://www.instagram.com/p/ANTIGA/"
    assert linhas[1]["publicada_em"] == "2026-10-01T10:00:00+00:00"
    assert [linha["codigo"] for linha in banco.publicacoes_recentes(conn, limite=1)] == ["NOVA"]
