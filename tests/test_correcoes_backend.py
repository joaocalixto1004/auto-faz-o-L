"""Testes das correções da varredura de bugs do backend (relatório do Codex)."""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from automacao import abrir_redes, banco, interagir, terminal
from automacao.redes import comum, protecao, tiktok
from automacao.redes.protecao import PROTECAO_ANTI_BLOQUEIO


# ------------------------------------- [CRÍTICO] clique sem posição ainda reconfere

class LocalizadorSemCaixa:
    def __init__(self):
        self.cliques = 0

    def wait_for(self, **_):
        pass

    def scroll_into_view_if_needed(self, **_):
        pass

    def bounding_box(self):
        return None  # botão sendo redesenhado

    def click(self, **_):
        self.cliques += 1


def test_clique_sem_posicao_ainda_reconfere_o_estado():
    alvo = LocalizadorSemCaixa()

    def ja_curtido():
        raise RuntimeError("o estado mudou")

    with pytest.raises(RuntimeError):
        comum.clicar(object(), alvo, conferir_antes=ja_curtido)
    assert alvo.cliques == 0


# ------------------------------------------------ avisos do TikTok durante a espera

class Toast:
    def __init__(self, visivel):
        self.visivel = visivel

    def count(self):
        return 1 if self.visivel() else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.visivel()

    def inner_text(self):
        return "Você está tocando rápido demais"

    def filter(self, **_):
        return self


class PaginaComToast:
    def __init__(self, aparece_na_checagem):
        self.checagens = 0
        self.aparece = aparece_na_checagem

    def locator(self, _seletor):
        return Toast(lambda: self.aparece <= self.checagens)


def test_aviso_rapido_do_tiktok_durante_a_confirmacao_vira_bloqueio(monkeypatch):
    pagina = PaginaComToast(aparece_na_checagem=2)

    def condicao():
        pagina.checagens += 1
        return False  # o clique ainda não confirmou

    monkeypatch.setattr(comum.time, "sleep", lambda s: None)
    with pytest.raises(protecao.Bloqueio, match="rápido demais"):
        tiktok.confirmar(pagina, condicao, 5)


# ------------------------------------------------------------ contas em minúsculas

def test_contas_ficam_em_minusculas(conn):
    assert abrir_redes.extrair_conta("Instagram", "/Ana.Silva/") == "@ana.silva"
    pub_id = banco.registrar_publicacao(conn, "Instagram", "P1", "alvo", "posts", 1, "u")
    banco.registrar_acao(conn, pub_id, None, "curtir", "feito", conta="@Ana")
    assert banco.acoes_concluidas(conn, "Instagram", "P1", "@ana") == {"curtir"}
    assert banco.acoes_concluidas(conn, "Instagram", "P1", "@ANA") == {"curtir"}
    assert banco.publicacoes_trabalhadas_hoje(conn, "Instagram", "@aNa") == 1


def test_limite_por_alvo_usa_o_perfil_da_visita(conn):
    # A publicação foi vista primeiro no perfil X; a ação foi feita visitando Y.
    pub_id = banco.registrar_publicacao(conn, "Instagram", "P1", "x", "reposts", 1, "u")
    banco.registrar_acao(conn, pub_id, None, "comentar", "feito", conta="@ana", alvo="y")
    assert banco.publicacoes_do_alvo_hoje(conn, "Instagram", "y", "@ana") == 1
    assert banco.publicacoes_do_alvo_hoje(conn, "Instagram", "x", "@ana") == 0
    assert banco.comentou_no_alvo(conn, "Instagram", "y", "@ana", 7)


def test_textos_comentados_separados_por_rede(conn):
    for rede in ("Instagram", "TikTok"):
        pub_id = banco.registrar_publicacao(conn, rede, f"C-{rede}", "alvo", "posts", 1, "u")
        banco.registrar_acao(conn, pub_id, None, "comentar", "feito", detalhe=f"texto {rede}", conta="@ana")
    assert banco.textos_comentados(conn, "@ana", 7, "TikTok") == {"texto TikTok"}
    assert banco.textos_comentados(conn, "@ana", 7) == {"texto Instagram", "texto TikTok"}


def test_pausa_com_outro_fuso_e_comparada_como_horario(conn):
    # Fim daqui a 2 h, gravado em UTC: como texto ele pode parecer "antes" do horário local.
    fim = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO pausas (rede, conta, motivo, inicio, fim) VALUES ('Instagram', '@ana', 'x', ?, ?)",
                 (banco.agora(), fim))
    conn.commit()
    assert banco.pausa_ativa(conn, "Instagram", "@ANA") is not None


# ------------------------------------------- horário rechecado depois das esperas

def test_horario_e_rechecado_depois_de_uma_espera(conn, monkeypatch):
    protecao.reiniciar_estado()
    monkeypatch.setattr(protecao.random, "uniform", lambda a, b: a)
    monkeypatch.setattr(protecao.random, "randint", lambda a, b: a)
    agora = datetime(2026, 10, 23, 21, 55).astimezone()
    for minutos in range(1, PROTECAO_ANTI_BLOQUEIO.por_hora["curtir"] + 1):
        pub_id = banco.registrar_publicacao(conn, "Instagram", f"P{minutos}", "a", "posts", 1, "u")
        conn.execute("INSERT INTO acoes (publicacao_id, acao, status, registrado_em, conta) "
                     "VALUES (?, 'curtir', 'feito', ?, '@ana')",
                     (pub_id, (agora - timedelta(minutes=minutos)).isoformat(timespec="seconds")))
    conn.commit()
    relogio = {"agora": agora}
    esperas = []

    def dormir(segundos):
        esperas.append(segundos)
        relogio["agora"] += timedelta(seconds=segundos)

    protecao.controlar_ritmo(conn, PROTECAO_ANTI_BLOQUEIO, "Instagram", "@ana", "curtir",
                             dormir=dormir, relogio=lambda: relogio["agora"])
    assert len(esperas) == 2                     # teto por hora e, depois, o fim do horário
    assert relogio["agora"].hour == 9            # só volta a agir às 9h do dia seguinte
    protecao.reiniciar_estado()


# ------------------------------------------------- parada acorda as esperas longas

def test_parada_acorda_espera_longa(monkeypatch):
    evento = threading.Event()
    monkeypatch.setattr(terminal, "PARADA", evento)
    monkeypatch.setattr(terminal.time, "sleep", lambda s: None)
    threading.Timer(0.2, evento.set).start()
    inicio = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        terminal.dormir(60)
    assert time.monotonic() - inicio < 5


def test_trava_impede_duas_automacoes(tmp_path):
    primeira = terminal.obter_trava(tmp_path / "t.trava")
    assert primeira is not None
    assert terminal.obter_trava(tmp_path / "t.trava") is None
    primeira.close()
    segunda = terminal.obter_trava(tmp_path / "t.trava")
    assert segunda is not None
    segunda.close()


# ---------------------------------------------------------------- repetição

class _Conexao:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nome):
        return getattr(self._conn, nome)

    def close(self):
        pass


class _Playwright:
    def __enter__(self):
        return object()

    def __exit__(self, *erro):
        return False


def _main(monkeypatch, conn, executar_volta, dormir):
    monkeypatch.setattr(interagir, "executar_volta", executar_volta)
    monkeypatch.setattr(interagir, "sync_playwright", lambda: _Playwright())
    monkeypatch.setattr(interagir, "dormir", dormir)
    monkeypatch.setattr(interagir.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(interagir.banco, "conectar", lambda: _Conexao(conn))
    monkeypatch.setattr("sys.argv", ["interagir", "https://www.instagram.com/alvo/"])
    return interagir.main()


def test_desligar_a_repeticao_durante_a_espera_encerra_sem_outra_volta(monkeypatch, conn, capsys):
    banco.salvar_configuracoes(conn, {"repetir": "sim", "repetir_minutos": "20"})
    voltas = []

    def dormir(segundos):
        banco.salvar_configuracoes(conn, {"repetir": "nao"})  # o usuário desligou na interface

    assert _main(monkeypatch, conn, lambda *a: voltas.append(a[6]) or "concluida", dormir) == 0
    assert voltas == [1]
    assert "A repetição foi desligada" in capsys.readouterr().out


def test_volta_interrompida_nao_repete(monkeypatch, conn):
    banco.salvar_configuracoes(conn, {"repetir": "sim", "repetir_minutos": "20"})
    voltas, esperas = [], []
    assert _main(monkeypatch, conn, lambda *a: voltas.append(a[6]) or "interrompida", esperas.append) == 0
    assert voltas == [1] and esperas == []


def test_ctrl_c_durante_a_volta_encerra_sem_erro(monkeypatch, conn):
    def executar_volta(*_):
        raise KeyboardInterrupt

    assert _main(monkeypatch, conn, executar_volta, lambda s: None) == 0


def test_segunda_automacao_nao_inicia(monkeypatch, conn, tmp_path, capsys):
    trava = terminal.obter_trava(interagir.TRAVA_AUTOMACAO)
    try:
        assert _main(monkeypatch, conn, lambda *a: "concluida", lambda s: None) == 1
        assert "Já existe uma automação rodando" in capsys.readouterr().out
    finally:
        trava.close()



# ----------------------------------------------------- nomes e números de perfil

def test_nomes_de_perfil_sem_diferenciar_maiusculas_com_acento(conn):
    banco.criar_perfil(conn, "Érica")
    with pytest.raises(ValueError, match="Já existe"):
        banco.criar_perfil(conn, "érica")
    assert banco.obter_perfil(conn, "ÉRICA")["nome"] == "Érica"


@pytest.mark.parametrize("nome", ["7", "123"])
def test_nome_so_com_numeros_e_recusado(conn, nome):
    with pytest.raises(ValueError, match="letra"):
        banco.criar_perfil(conn, nome)


def test_numero_de_perfil_apagado_nao_e_reaproveitado(conn):
    loja = banco.criar_perfil(conn, "Loja")
    banco.remover_perfil(conn, loja)
    nova = banco.criar_perfil(conn, "Casa")
    assert nova != loja


def test_compartilhar_com_recusa_link_ou_texto_invalido():
    from automacao import configuracao
    base = {"modo": "quantidade", "quantidade": "3", "semanas": "2", "comentario": "Boa!!!",
            "limite_diario": "40"}
    for invalido in ("https://www.instagram.com/Fulano/", "fulano silva", "fulano/"):
        _, erros = configuracao.validar(dict(base, compartilhar_com=invalido))
        assert any("Compartilhar" in e or "compartilhamento" in e for e in erros), invalido
    valores, erros = configuracao.validar(dict(base, compartilhar_com="@fulano.da_silva"))
    assert erros == [] and valores["compartilhar_com"] == "fulano.da_silva"


def test_conta_desativada_durante_a_volta_e_pulada(conn, monkeypatch, capsys):
    import argparse
    loja = banco.criar_perfil(conn, "Loja")
    usadas = []

    def rodar(p, conn_, rodada_id, cfg, perfil_nav, alvos, resumo, args):
        usadas.append(perfil_nav["nome"])
        banco.definir_perfil_ativo(conn, loja, False)  # o usuário desativa a Loja enquanto a Principal roda
        return True

    monkeypatch.setattr(interagir, "rodar_perfil_navegador", rodar)
    monkeypatch.setattr(interagir, "pausa", lambda intervalo=None: None)
    args = argparse.Namespace(quantidade=None, semanas=None, comentario=None, compartilhar_com=None,
                              limite_diario=None, simular=False, planilha_compartilhar=None, redes=None,
                              anti_bloqueio=False, seguir=False, ver_stories=False, perfis_navegador=None,
                              verificar_comentarios=False)
    cfg = interagir.montar_config(banco.ler_configuracoes(conn), args)
    interagir.executar_volta(None, conn, cfg, [("Instagram", "alvo")], banco.listar_perfis(conn), args, 1, None)
    assert usadas == ["Principal"]
    assert "Conta removida ou desativada durante a execução" in capsys.readouterr().out
