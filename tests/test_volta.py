"""Testes da volta pela lista: várias contas, contas sem login e repetição automática."""

import argparse

import pytest
from playwright.sync_api import Error as PlaywrightError

from automacao import banco, interagir


def args_volta(**opcoes):
    base = dict(quantidade=None, semanas=None, comentario=None, compartilhar_com=None, limite_diario=None,
                simular=False, planilha_compartilhar=None, redes=None, anti_bloqueio=False, seguir=False,
                ver_stories=False, repetir=None, sem_repetir=False, trace=False, perfis_navegador=None,
                verificar_comentarios=False, sem_reabrir=False, perfis=["https://www.instagram.com/alvo/"],
                arquivo=None, planilha=None)
    base.update(opcoes)
    return argparse.Namespace(**base)


def test_minutos_para_repetir():
    assert interagir.minutos_para_repetir({"repetir": "nao", "repetir_minutos": "20"}, args_volta()) is None
    assert interagir.minutos_para_repetir({"repetir": "sim", "repetir_minutos": "20"}, args_volta()) == 20
    assert interagir.minutos_para_repetir({"repetir": "sim", "repetir_minutos": "x"}, args_volta()) == 20
    assert interagir.minutos_para_repetir({"repetir": "nao"}, args_volta(repetir=5)) == 5
    assert interagir.minutos_para_repetir({"repetir": "sim", "repetir_minutos": "20"},
                                          args_volta(sem_repetir=True)) is None


def test_seguir_e_stories_vem_da_configuracao_ou_da_opcao():
    salvas = dict(banco.CONFIG_PADRAO)
    cfg = interagir.montar_config(salvas, args_volta())
    assert (cfg.seguir, cfg.ver_stories) == (False, False)
    cfg = interagir.montar_config(dict(salvas, seguir="sim", ver_stories="sim"), args_volta())
    assert (cfg.seguir, cfg.ver_stories) == (True, True)
    cfg = interagir.montar_config(salvas, args_volta(seguir=True))
    assert cfg.seguir and not cfg.ver_stories


@pytest.fixture
def volta(monkeypatch, conn):
    """Roda executar_volta com contas falsas: cada perfil devolve um resultado programado."""
    chamadas = []
    resultados = {}

    def rodar(p, conn_, rodada_id, cfg, perfil_nav, alvos, resumo, args):
        chamadas.append(perfil_nav["nome"])
        resultado = resultados.get(perfil_nav["nome"], True)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    monkeypatch.setattr(interagir, "rodar_perfil_navegador", rodar)
    monkeypatch.setattr(interagir, "pausa", lambda intervalo=None: None)

    def executar(args=None, **config):
        banco.salvar_configuracoes(conn, config) if config else None
        args = args or args_volta()
        cfg = interagir.montar_config(banco.ler_configuracoes(conn), args)
        perfis_nav = banco.listar_perfis(conn, somente_ativos=True)
        return interagir.executar_volta(None, conn, cfg, [("Instagram", "alvo")], perfis_nav, args, 1, None)

    return executar, chamadas, resultados


def test_contas_sem_login_ou_que_nao_abrem_sao_puladas(volta, conn, capsys):
    executar, chamadas, resultados = volta
    banco.criar_perfil(conn, "Sem login")
    banco.criar_perfil(conn, "Travada")
    banco.criar_perfil(conn, "Boa")
    resultados.update({"Sem login": False, "Travada": PlaywrightError("perfil em uso")})
    assert executar() == "concluida"
    assert chamadas == ["Principal", "Sem login", "Travada", "Boa"]
    saida = capsys.readouterr().out
    assert "[Sem login] nenhuma rede escolhida está logada" in saida
    assert "[Travada] não consegui abrir o navegador deste perfil" in saida


def test_sem_tolerancia_para_na_primeira_conta_sem_login(volta, conn, capsys):
    executar, chamadas, resultados = volta
    banco.criar_perfil(conn, "Boa")
    resultados["Principal"] = False
    executar(continuar_sem_login="nao")
    assert chamadas == ["Principal"]
    assert "Parando, como pedido na configuração" in capsys.readouterr().out


def test_nenhuma_conta_usavel_avisa(volta, conn, capsys):
    executar, chamadas, resultados = volta
    resultados["Principal"] = False
    assert executar() == "concluida"
    assert "Nenhuma conta pôde ser usada" in capsys.readouterr().out


def test_redes_fora_da_escolha_nao_rodam(volta, conn, capsys):
    executar, chamadas, _ = volta
    executar(redes="tiktok")      # o único alvo é do Instagram
    assert chamadas == []
    assert "Nenhum perfil-alvo nas redes escolhidas" in capsys.readouterr().out


def test_lista_recomeca_depois_do_intervalo(monkeypatch, conn, capsys):
    voltas, esperas = [], []

    def executar_volta(p, conn_, cfg, perfis, perfis_nav, args, volta, repetir):
        voltas.append((volta, repetir))
        return "concluida"

    def dormir(segundos):
        esperas.append(segundos)
        if len(esperas) == 2:
            raise KeyboardInterrupt  # o usuário clicou em Parar durante a espera

    class PlaywrightFalso:
        def __enter__(self):
            return object()

        def __exit__(self, *erro):
            return False

    monkeypatch.setattr(interagir, "executar_volta", executar_volta)
    monkeypatch.setattr(interagir, "sync_playwright", lambda: PlaywrightFalso())
    monkeypatch.setattr(interagir, "dormir", dormir)
    monkeypatch.setattr(interagir.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(interagir.banco, "conectar", lambda: conn_sem_fechar(conn))
    banco.salvar_configuracoes(conn, {"repetir": "sim", "repetir_minutos": "20"})
    monkeypatch.setattr("sys.argv", ["interagir", "https://www.instagram.com/alvo/"])
    assert interagir.main() == 0
    assert voltas == [(1, 20), (2, 20)]
    assert esperas == [20 * 60, 20 * 60]
    saida = capsys.readouterr().out
    assert saida.count("Lista concluída. Recomeçando em 20 min") == 2
    assert "Interrompido." in saida


class conn_sem_fechar:
    """Conexão de teste que ignora o close() do closing() de main()."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nome):
        return getattr(self._conn, nome)

    def close(self):
        pass
