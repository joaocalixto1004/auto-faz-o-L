"""Testes da conta logada no banco: migração da coluna logins.conta, registrar_conta e ultimo_status_logins."""

import sqlite3

from automacao import banco


def colunas(conn, tabela: str) -> set[str]:
    return {linha[1] for linha in conn.execute(f"PRAGMA table_info({tabela})")}


# Posição (1, 2, ...) da migração que cria logins.conta.
VERSAO_CONTA = next(i for i, m in enumerate(banco.MIGRACOES, 1)
                    if "ALTER TABLE logins" in m and "conta" in m)


def test_migracao_da_conta_adiciona_a_coluna(conn):
    assert "conta" in colunas(conn, "logins")


def test_banco_antigo_ganha_a_coluna_sem_perder_os_logins(tmp_path):
    caminho = tmp_path / "antigo.db"
    antigo = sqlite3.connect(caminho)
    for numero, migracao in enumerate(banco.MIGRACOES[:VERSAO_CONTA - 1], start=1):
        antigo.executescript(migracao)
        antigo.execute(f"PRAGMA user_version = {numero}")
    antigo.execute("INSERT INTO execucoes (iniciada_em) VALUES ('2026-01-01T10:00:00-03:00')")
    antigo.execute("INSERT INTO logins (execucao_id, rede, status, registrado_em) "
                   "VALUES (1, 'TikTok', 'ja_logado', '2026-01-01T10:00:01-03:00')")
    antigo.commit()
    antigo.close()

    conn = banco.conectar(caminho)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(banco.MIGRACOES)
        ultimos = banco.ultimo_status_logins(conn)
        assert ultimos["TikTok"]["status"] == "ja_logado"
        assert ultimos["TikTok"]["conta"] is None
    finally:
        conn.close()


def test_sem_registros_devolve_vazio(conn):
    assert banco.ultimo_status_logins(conn) == {}


def test_registrar_conta_e_ultimo_status(conn):
    execucao = banco.iniciar_execucao(conn)
    banco.registrar_login(conn, execucao, "TikTok", "ja_logado")
    banco.registrar_login(conn, execucao, "Instagram", "nao_logado")
    banco.registrar_conta(conn, execucao, "TikTok", "@fulano")

    ultimos = banco.ultimo_status_logins(conn)
    assert set(ultimos) == {"TikTok", "Instagram"}
    assert ultimos["TikTok"]["status"] == "ja_logado"
    assert ultimos["TikTok"]["conta"] == "@fulano"
    assert ultimos["TikTok"]["ultima_conta"] == "@fulano"
    assert ultimos["TikTok"]["registrado_em"]
    assert ultimos["Instagram"]["conta"] is None and ultimos["Instagram"]["ultima_conta"] is None


def test_atualizar_o_status_mantem_a_conta(conn):
    execucao = banco.iniciar_execucao(conn)
    banco.registrar_login(conn, execucao, "Instagram", "nao_logado")
    banco.registrar_login(conn, execucao, "Instagram", "login_detectado")
    banco.registrar_conta(conn, execucao, "Instagram", "@ciclano")
    banco.registrar_login(conn, execucao, "Instagram", "login_detectado")
    ultimo = banco.ultimo_status_logins(conn)["Instagram"]
    assert (ultimo["status"], ultimo["conta"]) == ("login_detectado", "@ciclano")


def test_registrar_conta_sem_login_registrado_nao_cria_linha(conn):
    execucao = banco.iniciar_execucao(conn)
    banco.registrar_conta(conn, execucao, "Instagram", "@ninguem")
    assert conn.execute("SELECT count(*) FROM logins").fetchone()[0] == 0


def test_ultimo_status_vem_da_execucao_mais_recente(conn):
    primeira = banco.iniciar_execucao(conn)
    banco.registrar_login(conn, primeira, "TikTok", "ja_logado")
    banco.registrar_conta(conn, primeira, "TikTok", "@antigo")
    banco.registrar_login(conn, primeira, "Instagram", "ja_logado")

    segunda = banco.iniciar_execucao(conn)
    banco.registrar_login(conn, segunda, "TikTok", "nao_logado")  # sem conta desta vez

    ultimos = banco.ultimo_status_logins(conn)
    assert ultimos["TikTok"]["status"] == "nao_logado"
    assert ultimos["TikTok"]["conta"] is None
    assert ultimos["TikTok"]["ultima_conta"] == "@antigo"  # pista da execução anterior
    assert ultimos["Instagram"]["status"] == "ja_logado"           # a rede que não apareceu na segunda


def test_ultima_conta_e_a_mais_recente(conn):
    for conta in ("@primeira", "@segunda"):
        execucao = banco.iniciar_execucao(conn)
        banco.registrar_login(conn, execucao, "Instagram", "ja_logado")
        banco.registrar_conta(conn, execucao, "Instagram", conta)
    assert banco.ultimo_status_logins(conn)["Instagram"]["ultima_conta"] == "@segunda"
