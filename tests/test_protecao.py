"""Testes do ritmo e dos limites (modo normal e anti-bloqueio), com relógio e espera falsos."""

from datetime import datetime, timedelta

import pytest

from automacao import banco
from automacao.redes import comum, protecao
from automacao.redes.protecao import (PROTECAO_ANTI_BLOQUEIO, PROTECAO_NORMAL, LimiteDiario, PularAcao,
                                      controlar_ritmo, registrar_aviso, tipo_da_acao)

AGORA = datetime(2026, 10, 23, 15, 0, 0).astimezone()


@pytest.fixture(autouse=True)
def estado_limpo(monkeypatch):
    protecao.reiniciar_estado()
    monkeypatch.setattr(protecao.random, "uniform", lambda a, b: a)    # sem sorteio: o menor valor
    monkeypatch.setattr(protecao.random, "randint", lambda a, b: a)
    yield
    protecao.reiniciar_estado()


class Espera:
    def __init__(self):
        self.vezes: list[float] = []

    def __call__(self, segundos):
        self.vezes.append(segundos)


def gravar(conn, acao, minutos_atras, conta="@ana", rede="Instagram", codigo=None, alvo="alvo", status="feito",
           detalhe=None):
    codigo = codigo or f"C{acao}{minutos_atras}{conta}"
    pub_id = banco.registrar_publicacao(conn, rede, codigo, alvo, "posts", 1, f"https://x/{codigo}")
    momento = (AGORA - timedelta(minutes=minutos_atras)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO acoes (publicacao_id, acao, status, detalhe, registrado_em, conta) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (pub_id, acao, status, detalhe, momento, conta))
    conn.commit()
    return pub_id


def ritmo(conn, prot, acao, espera, conta="@ana", relogio=lambda: AGORA):
    controlar_ritmo(conn, prot, "Instagram", conta, acao, dormir=espera, relogio=relogio)


def test_tipo_da_acao():
    assert tipo_da_acao("compartilhar:fulano") == "compartilhar"
    assert tipo_da_acao("curtir") == "curtir"
    assert tipo_da_acao("copiar_link") is None


def test_protecao_da_config():
    assert protecao.protecao_da_config({"anti_bloqueio": "sim"}) is PROTECAO_ANTI_BLOQUEIO
    assert protecao.protecao_da_config({"anti_bloqueio": "nao"}) is PROTECAO_NORMAL
    assert protecao.protecao_da_config({}, forcar_anti=True) is PROTECAO_ANTI_BLOQUEIO


def test_anti_bloqueio_e_mais_restrito_que_o_normal():
    for chave, teto in PROTECAO_ANTI_BLOQUEIO.por_dia.items():
        assert teto < PROTECAO_NORMAL.por_dia[chave]
    for chave, teto in PROTECAO_ANTI_BLOQUEIO.por_hora.items():
        assert teto <= PROTECAO_NORMAL.por_hora[chave]
    assert PROTECAO_ANTI_BLOQUEIO.pausa_entre_acoes[0] > PROTECAO_NORMAL.pausa_entre_acoes[0]


def test_sem_historico_nao_espera(conn):
    espera = Espera()
    ritmo(conn, PROTECAO_NORMAL, "curtir", espera)
    assert espera.vezes == []


def test_acao_que_nao_clica_nao_e_controlada(conn):
    espera = Espera()
    for minutos in range(40):
        gravar(conn, "curtir", minutos)
    ritmo(conn, PROTECAO_NORMAL, "copiar_link", espera)
    assert espera.vezes == []


def test_teto_por_hora_do_tipo_longe_de_liberar_pula_a_acao(conn):
    """Não para a rede inteira por quase uma hora: só aquela ação fica para a próxima volta."""
    espera = Espera()
    for minutos in range(1, 16):              # 15 curtidas na última hora (teto normal: 15)
        gravar(conn, "curtir", minutos)
    with pytest.raises(PularAcao, match="teto por hora de curtir"):
        ritmo(conn, PROTECAO_NORMAL, "curtir", espera)   # liberaria só daqui a ~45 min
    assert espera.vezes == []
    ritmo(conn, PROTECAO_NORMAL, "comentar", espera)     # os outros tipos seguem normalmente
    assert espera.vezes == []


def test_teto_por_hora_do_tipo_perto_de_liberar_espera(conn):
    espera = Espera()
    gravar(conn, "curtir", 58)                # a mais antiga sai da janela em 2 min
    for minutos in range(1, 15):
        gravar(conn, "curtir", minutos)
    ritmo(conn, PROTECAO_NORMAL, "curtir", espera)
    assert espera.vezes == [pytest.approx(2 * 60 + 30)]  # +30 s de folga sorteada


def test_teto_total_por_hora_espera_a_mais_antiga_sair_da_janela(conn):
    espera = Espera()
    for minutos in range(1, 31):              # 30 ações na última hora (teto total normal: 30)
        gravar(conn, ["curtir", "favoritar"][minutos % 2], minutos)
    ritmo(conn, PROTECAO_NORMAL, "comentar", espera)
    # a 30ª mais recente foi há 30 min: espera ~30 min (+30 s de folga sorteada)
    assert espera.vezes == [pytest.approx(30 * 60 + 30)]


def test_marca_antes_do_clique_e_confirmacao_contam_uma_vez(conn):
    espera = Espera()
    for minutos in range(1, 15):              # 14 curtidas, cada uma com marca + confirmação
        pub_id = gravar(conn, "curtir", minutos, status="nao_confirmado")
        conn.execute("INSERT INTO acoes (publicacao_id, acao, status, registrado_em, conta) VALUES (?, 'curtir', "
                     "'feito', ?, '@ana')", (pub_id, (AGORA - timedelta(minutes=minutos - 0.5)).isoformat()))
    conn.commit()
    ritmo(conn, PROTECAO_NORMAL, "curtir", espera)
    assert espera.vezes == []                 # 14 < 15


def test_teto_do_dia_por_tipo_pula_a_acao(conn):
    for minutos in range(70, 70 + PROTECAO_ANTI_BLOQUEIO.por_dia["repostar"]):
        gravar(conn, "repostar", minutos)
    with pytest.raises(PularAcao, match="repostar"):
        ritmo(conn, PROTECAO_ANTI_BLOQUEIO, "repostar", Espera())


def test_teto_total_do_dia_para_a_conta(conn):
    for minutos in range(70, 70 + PROTECAO_ANTI_BLOQUEIO.por_dia["total"]):
        gravar(conn, ["curtir", "favoritar"][minutos % 2], minutos)
    with pytest.raises(LimiteDiario):
        ritmo(conn, PROTECAO_ANTI_BLOQUEIO, "curtir", Espera())


def test_tetos_sao_por_conta(conn):
    for minutos in range(1, 16):
        gravar(conn, "curtir", minutos, conta="@ana")
    espera = Espera()
    ritmo(conn, PROTECAO_NORMAL, "curtir", espera, conta="@bia")
    assert espera.vezes == []


def test_intervalo_minimo_entre_comentarios(conn):
    gravar(conn, "comentar", 1)
    espera = Espera()
    ritmo(conn, PROTECAO_ANTI_BLOQUEIO, "comentar", espera, relogio=lambda: AGORA.replace(hour=15))
    assert espera.vezes == [pytest.approx(900 - 60 + 10)]


def test_fora_do_horario_espera_o_inicio(conn):
    espera = Espera()
    madrugada = AGORA.replace(hour=3, minute=0)
    ritmo(conn, PROTECAO_ANTI_BLOQUEIO, "curtir", espera, relogio=lambda: madrugada)
    assert espera.vezes[0] == pytest.approx(6 * 3600)   # até 9h (minuto sorteado = 0)


def test_depois_do_horario_espera_o_dia_seguinte(conn):
    espera = Espera()
    noite = AGORA.replace(hour=23, minute=0)
    ritmo(conn, PROTECAO_ANTI_BLOQUEIO, "curtir", espera, relogio=lambda: noite)
    assert espera.vezes[0] == pytest.approx(10 * 3600)  # 23h -> 9h


def test_pausa_longa_depois_de_n_acoes(conn):
    espera = Espera()
    n = PROTECAO_NORMAL.pausa_longa_a_cada[0]
    for _ in range(n):
        ritmo(conn, PROTECAO_NORMAL, "copiar_link", espera)   # não conta
    for _ in range(n):
        ritmo(conn, PROTECAO_NORMAL, "favoritar", espera)
    assert espera.vezes == []
    ritmo(conn, PROTECAO_NORMAL, "favoritar", espera)
    assert espera.vezes == [PROTECAO_NORMAL.pausa_longa[0]]


def test_aviso_poe_a_conta_em_pausa(conn):
    mensagem = registrar_aviso(conn, PROTECAO_NORMAL, "Instagram", "@ana", "Tente novamente mais tarde")
    assert "@ana em pausa" in mensagem
    assert banco.pausa_ativa(conn, "Instagram", "@ana")["motivo"] == "Tente novamente mais tarde"
    assert banco.pausa_ativa(conn, "Instagram", "@bia") is None
    assert banco.pausa_ativa(conn, "TikTok", "@ana") is None


def test_avisos_em_duas_contas_pausam_todas_no_anti_bloqueio(conn):
    registrar_aviso(conn, PROTECAO_ANTI_BLOQUEIO, "Instagram", "@ana", "aviso")
    assert banco.pausa_ativa(conn, "TikTok", "@carla") is None
    mensagem = registrar_aviso(conn, PROTECAO_ANTI_BLOQUEIO, "TikTok", "@bia", "aviso")
    assert "todas em pausa" in mensagem
    assert banco.pausa_ativa(conn, "TikTok", "@carla")["conta"] == "*"


def test_no_modo_normal_nao_ha_pausa_geral(conn):
    registrar_aviso(conn, PROTECAO_NORMAL, "Instagram", "@ana", "aviso")
    registrar_aviso(conn, PROTECAO_NORMAL, "TikTok", "@bia", "aviso")
    assert banco.pausa_ativa(conn, "TikTok", "@carla") is None


def test_pausa_vencida_nao_vale(conn):
    conn.execute("INSERT INTO pausas (rede, conta, motivo, inicio, fim) VALUES ('Instagram', '@ana', 'x', "
                 "'2020-01-01T00:00:00-03:00', '2020-01-02T00:00:00-03:00')")
    conn.commit()
    assert banco.pausa_ativa(conn, "Instagram", "@ana") is None


# ------------------------------------------- regras do anti-bloqueio no motor

@pytest.fixture
def sem_espera(monkeypatch):
    monkeypatch.setattr(comum, "pausa", lambda intervalo=None: None)
    monkeypatch.setattr(comum, "monitorar_video", lambda pg, token: None)
    monkeypatch.setattr(comum, "esperar_fim_do_video", lambda pg, token: None)
    monkeypatch.setattr(comum, "capturar_tela", lambda pg, pub, acao: None)
    monkeypatch.setattr(comum, "controlar_ritmo", lambda *a, **k: None)


class PaginaFalsa:
    def is_closed(self):
        return False


def cfg(**opcoes):
    base = dict(modo="quantidade", quantidade=3, semanas=2, comentario="Boa!!!", compartilhar_com="x",
                limite_diario=40, simular=False, protecao=PROTECAO_ANTI_BLOQUEIO)
    base.update(opcoes)
    return comum.Config(**base)


def processar(conn, codigo, acoes, configuracao, alvo="alvo", conta="@ana"):
    pub = comum.Publicacao("Instagram", alvo, "posts", 1, codigo, f"https://x/{codigo}")
    resumo = []
    comum.processar_publicacao(conn, PaginaFalsa(), None, configuracao, pub, abrir=lambda: None,
                               acoes=acoes, resumo=resumo, conta=conta)
    return resumo[0].acoes


def test_limite_de_publicacoes_por_perfil_alvo_no_dia(conn, sem_espera):
    chamadas = []
    acao = [("curtir", lambda pg, c, pub: chamadas.append(pub.codigo) or comum.Resultado("feito"))]
    for codigo in ("P1", "P2", "P3"):
        processar(conn, codigo, acao, cfg())
    assert chamadas == ["P1", "P2"]                    # máximo 2 por perfil-alvo por dia
    processar(conn, "Q1", acao, cfg(), alvo="outro")
    assert chamadas == ["P1", "P2", "Q1"]


def test_um_comentario_por_perfil_alvo_na_semana(conn, sem_espera, monkeypatch):
    monkeypatch.setattr(comum._linha, "ultimo_comentario", None)
    comentarios = []

    def comentar(pg, c, pub):
        texto = comum.sortear_comentario(c)
        comentarios.append(texto)
        return comum.Resultado("feito", texto)

    configuracao = cfg(comentarios=("Boa!!!", "Top", "Show"))
    resultado1 = processar(conn, "P1", [("comentar", comentar)], configuracao)
    resultado2 = processar(conn, "P2", [("comentar", comentar)], configuracao)
    assert resultado1 == {"comentar": "feito"}
    assert resultado2 == {"comentar": "pulado"}        # mesmo perfil-alvo na mesma semana
    assert len(comentarios) == 1
    assert banco.acoes_concluidas(conn, "Instagram", "P2", "@ana") == set()  # pulado não é gravado


def test_comentario_nao_se_repete_na_semana(conn, sem_espera, monkeypatch):
    monkeypatch.setattr(comum._linha, "ultimo_comentario", None)
    gravar(conn, "comentar", 600, alvo="antigo1", detalhe="Boa!!!")
    gravar(conn, "comentar", 500, alvo="antigo2", detalhe="Top (nenhum comentário seu entre os 3 carregados)")
    textos = []

    def comentar(pg, c, pub):
        textos.append(comum.sortear_comentario(c))
        return comum.Resultado("feito", textos[-1])

    configuracao = cfg(comentarios=("Boa!!!", "Top", "Show"))
    processar(conn, "N1", [("comentar", comentar)], configuracao, alvo="novo1")
    assert textos == ["Show"]
    assert processar(conn, "N2", [("comentar", comentar)], configuracao, alvo="novo2") == {"comentar": "pulado"}
