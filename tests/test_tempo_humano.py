"""Oscilação não determinística dos tempos entre as ações (comum.tempo_humano e quem usa)."""

import statistics
import threading
from types import SimpleNamespace

import pytest

from automacao.redes import comum


@pytest.fixture(autouse=True)
def ritmo_inicial():
    comum._linha.ritmo = 1.0
    yield
    comum._linha.ritmo = 1.0


def amostras(minimo, maximo, n=5000):
    return [comum.tempo_humano(minimo, maximo) for _ in range(n)]


def test_nunca_fica_abaixo_do_minimo_nem_passa_do_teto():
    tempos = amostras(4.0, 12.0)
    assert min(tempos) >= 4.0
    assert max(tempos) <= 12.0 + 8.0                  # máximo + tamanho do intervalo (distração)


def test_quase_sempre_dentro_do_intervalo_e_as_vezes_uma_distracao():
    tempos = amostras(4.0, 12.0)
    dentro = sum(1 for t in tempos if t <= 12.0) / len(tempos)
    assert 0.85 <= dentro < 1.0                       # algumas demoras maiores, mas raras


def test_mais_perto_do_comeco_sem_ser_fixo():
    tempos = amostras(10.0, 30.0)
    media = statistics.mean(tempos)
    assert 15.0 < media < 22.0                        # beta(2, 3): perto do começo, não no meio
    assert statistics.pstdev(tempos) > 2.0            # espalhado, não um valor só
    assert len({round(t, 6) for t in tempos}) > 4900  # praticamente nunca repete


def test_sequencia_nao_se_repete():
    """Usa a aleatoriedade do sistema: duas sequências seguidas nunca saem iguais."""
    assert amostras(1.0, 2.0, 30) != amostras(1.0, 2.0, 30)
    assert isinstance(comum._acaso, comum.random.SystemRandom)


def test_ritmo_anda_aos_poucos_e_fica_nos_limites():
    vistos = []
    for _ in range(3000):
        comum.tempo_humano(1.0, 2.0)
        vistos.append(comum._linha.ritmo)
    assert min(vistos) >= comum.RITMO_LIMITES[0] and max(vistos) <= comum.RITMO_LIMITES[1]
    assert max(vistos) - min(vistos) > 0.2            # ora mais rápido, ora mais lento
    saltos = [abs(b - a) for a, b in zip(vistos, vistos[1:])]
    assert max(saltos) < 0.5                          # muda aos poucos, não aos pulos


def test_cada_linha_de_execucao_tem_o_seu_ritmo():
    comum._linha.ritmo = 1.3
    visto = {}
    linha = threading.Thread(target=lambda: visto.update(ritmo=comum._linha.ritmo))
    linha.start()
    linha.join()
    assert visto["ritmo"] == 1.0 and comum._linha.ritmo == 1.3


def test_intervalo_vazio_devolve_o_minimo():
    assert comum.tempo_humano(3.0, 3.0) == 3.0
    assert comum.tempo_humano(0.0, 0.0) == 0.0


def test_pausa_usa_o_tempo_humano(monkeypatch):
    dormidos = []
    monkeypatch.setattr(comum, "dormir", dormidos.append)
    for _ in range(200):
        comum.pausa((8.0, 25.0))
    assert min(dormidos) >= 8.0 and len(set(dormidos)) == 200


def test_digitacao_oscila_e_as_vezes_para_para_pensar(monkeypatch):
    esperas = []
    monkeypatch.setattr(comum.time, "sleep", esperas.append)
    monkeypatch.setattr(comum, "CHANCE_PENSAR_ENTRE_PALAVRAS", 1.0)   # para o teste: sempre pensa
    teclado = SimpleNamespace(type=lambda t: None, insert_text=lambda t: None)
    comum.digitar(SimpleNamespace(keyboard=teclado), "Muito bom, parabéns!")
    letras = len("Muito bom, parabéns!")
    pensar = 4                                        # " " depois de Muito, "," e " " depois de bom, "!"
    assert len(esperas) == letras + pensar + 1        # uma por letra, as paradas e a do fim
    assert min(esperas) >= comum.DIGITACAO_POR_LETRA[0]
    assert len(set(esperas)) == len(esperas)          # nenhum intervalo igual ao outro


def test_espera_na_pagina_nunca_e_menor_que_o_necessario():
    pedidas = []
    pagina = SimpleNamespace(wait_for_timeout=pedidas.append)
    for _ in range(500):
        comum.esperar_um_pouco(pagina, 1500)
    assert min(pedidas) >= 1500                       # a grade precisa de pelo menos isso para montar
    assert max(pedidas) <= 1500 * 1.6 + 1500 * 0.6 + 1
    assert len(set(pedidas)) > 300
