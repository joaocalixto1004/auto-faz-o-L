"""Testes das funções puras do interagir.py: links de perfil, arquivo de perfis e configuração."""

import argparse

import pytest

from automacao import banco, interagir


# ----------------------------------------------------- interpretar_perfil

@pytest.mark.parametrize(("texto", "esperado"), [
    ("https://www.instagram.com/neymarjn.br/", ("Instagram", "neymarjn.br")),
    ("https://instagram.com/fulano", ("Instagram", "fulano")),
    ("http://m.instagram.com/fulano/", ("Instagram", "fulano")),
    ("instagram.com/fulano", ("Instagram", "fulano")),
    ("www.instagram.com/fulano/?igsh=abc123", ("Instagram", "fulano")),
    ("   https://www.instagram.com/fulano/   ", ("Instagram", "fulano")),
    ("HTTPS://WWW.INSTAGRAM.COM/fulano/", ("Instagram", "fulano")),
    ("https://www.tiktok.com/@teachergabrielborges", ("TikTok", "teachergabrielborges")),
    ("tiktok.com/@fulano", ("TikTok", "fulano")),
    ("www.tiktok.com/@fulano/", ("TikTok", "fulano")),
    ("https://m.tiktok.com/@fulano", ("TikTok", "fulano")),
    ("https://www.tiktok.com/@fulano?lang=pt-BR", ("TikTok", "fulano")),
    ("https://www.tiktok.com/@fulano/video/7699507358707305746", ("TikTok", "fulano")),
])
def test_interpretar_perfil_reconhece_links(texto, esperado):
    assert interagir.interpretar_perfil(texto) == esperado


@pytest.mark.parametrize("texto", [
    "",
    "fulano",
    "https://www.facebook.com/fulano",
    "https://x.com/fulano",
    "https://notinstagram.com/fulano",
    "https://instagram.com.evil.example/fulano",
    "https://www.instagram.com/",
    "https://www.tiktok.com/fulano",        # TikTok sem @
    "https://www.tiktok.com/",
    "https://vm.tiktok.com/ZMabc123/",      # link curto, não é perfil
    "https://nottiktok.com/@fulano",
])
def test_interpretar_perfil_rejeita_hosts_e_caminhos_desconhecidos(texto):
    with pytest.raises(ValueError, match="não reconheci o perfil"):
        interagir.interpretar_perfil(texto)


@pytest.mark.parametrize("texto", [
    "https://www.instagram.com/p/DemwcD8lKAk/",
    "https://www.instagram.com/reel/DemwcD8lKAk/",
    "https://www.tiktok.com/@",
])
def test_interpretar_perfil_rejeita_links_que_nao_sao_perfil(texto):
    with pytest.raises(ValueError):
        interagir.interpretar_perfil(texto)


# ------------------------------------------------------------- ler_perfis

def argumentos_perfis(perfis=(), arquivo=None) -> argparse.Namespace:
    # sem --planilha; a planilha padrão de data/ aponta para um arquivo inexistente (conftest)
    return argparse.Namespace(perfis=list(perfis), arquivo=arquivo, planilha=None)


def test_ler_perfis_do_arquivo_ignora_comentarios_e_duplicados(tmp_path):
    arquivo = tmp_path / "perfis.txt"
    arquivo.write_text(
        "# Um link de perfil por linha\n"
        "https://www.instagram.com/fulano/\n"
        "\n"
        "   \n"
        "   https://www.tiktok.com/@ciclano   # comentário no fim da linha\n"
        "instagram.com/fulano\n"
        "# https://www.instagram.com/comentado/\n"
        "https://www.instagram.com/beltrano/\n",
        encoding="utf-8",
    )
    args = argumentos_perfis(["https://www.tiktok.com/@ciclano", "https://www.instagram.com/primeiro/"],
                             str(arquivo))
    assert interagir.ler_perfis(args) == [
        ("TikTok", "ciclano"),
        ("Instagram", "primeiro"),
        ("Instagram", "fulano"),
        ("Instagram", "beltrano"),
    ]


def test_ler_perfis_so_da_linha_de_comando():
    args = argumentos_perfis(["instagram.com/a", "tiktok.com/@b", "https://www.instagram.com/a/"])
    assert interagir.ler_perfis(args) == [("Instagram", "a"), ("TikTok", "b")]
    assert interagir.ler_perfis(argumentos_perfis()) == []


def test_ler_perfis_linha_invalida_no_arquivo_e_pulada_com_aviso(tmp_path, capsys):
    arquivo = tmp_path / "perfis.txt"
    arquivo.write_text("https://www.instagram.com/ok/\nhttps://www.youtube.com/@canal\n", encoding="utf-8")
    assert interagir.ler_perfis(argumentos_perfis(arquivo=str(arquivo))) == [("Instagram", "ok")]
    assert "youtube" in capsys.readouterr().out


def test_ler_perfis_link_invalido_na_linha_de_comando_e_erro():
    with pytest.raises(ValueError, match="youtube"):
        interagir.ler_perfis(argumentos_perfis(perfis=["https://www.youtube.com/@canal"]))


def test_ler_perfis_arquivo_inexistente(tmp_path):
    with pytest.raises(OSError):
        interagir.ler_perfis(argumentos_perfis(arquivo=str(tmp_path / "nao_existe.txt")))


# ---------------------------------------------------------- montar_config

SALVAS = {
    "modo": "quantidade",
    "quantidade": "3",
    "semanas": "2",
    "comentario": "Boa!!!",
    "compartilhar_com": "@joao",
    "limite_diario": "40",
}


def argumentos_cli(**opcoes) -> argparse.Namespace:
    base = dict(quantidade=None, semanas=None, comentario=None, compartilhar_com=None,
                limite_diario=None, simular=False, planilha_compartilhar=None)
    base.update(opcoes)
    return argparse.Namespace(**base)


def test_montar_config_sem_opcoes_usa_o_que_foi_salvo():
    cfg = interagir.montar_config(SALVAS, argumentos_cli())
    assert cfg.modo == "quantidade"
    assert (cfg.quantidade, cfg.semanas, cfg.limite_diario) == (3, 2, 40)
    assert cfg.comentario == "Boa!!!"
    assert cfg.compartilhar_com == "joao"   # o @ salvo também é tirado
    assert cfg.destinatarios["Instagram"] == ["joao"]   # sem planilha: só a conta salva
    assert cfg.simular is False


def test_montar_config_sem_conta_reserva_e_sem_planilha_nao_compartilha_no_instagram():
    cfg = interagir.montar_config(dict(SALVAS, compartilhar_com=""), argumentos_cli())
    assert cfg.destinatarios["Instagram"] == []   # e não [""]: nenhuma ação "compartilhar:"


def test_montar_config_modo_tempo_salvo():
    cfg = interagir.montar_config(dict(SALVAS, modo="tempo"), argumentos_cli())
    assert cfg.modo == "tempo" and cfg.semanas == 2


def test_montar_config_semanas_na_linha_de_comando_troca_para_tempo():
    cfg = interagir.montar_config(SALVAS, argumentos_cli(semanas=4))
    assert cfg.modo == "tempo"
    assert cfg.semanas == 4
    assert cfg.quantidade == 3


def test_montar_config_quantidade_na_linha_de_comando_troca_para_quantidade():
    cfg = interagir.montar_config(dict(SALVAS, modo="tempo"), argumentos_cli(quantidade=7))
    assert cfg.modo == "quantidade"
    assert cfg.quantidade == 7
    assert cfg.semanas == 2


def test_montar_config_linha_de_comando_tem_prioridade():
    cfg = interagir.montar_config(SALVAS, argumentos_cli(comentario=["Top", " Show "], compartilhar_com="@outra",
                                                          limite_diario=5, simular=True))
    assert cfg.comentario == "Top"
    assert cfg.comentarios == ("Top", "Show")
    assert cfg.compartilhar_com == "outra"
    assert cfg.destinatarios["Instagram"] == ["outra"]
    assert cfg.limite_diario == 5
    assert cfg.simular is True


def test_montar_config_com_configuracoes_do_banco(conn):
    banco.salvar_configuracoes(conn, {"modo": "tempo", "semanas": "3", "comentario": "Do banco"})
    cfg = interagir.montar_config(banco.ler_configuracoes(conn), argumentos_cli())
    assert cfg.modo == "tempo" and cfg.semanas == 3 and cfg.comentario == "Do banco"
    assert cfg.quantidade == int(banco.CONFIG_PADRAO["quantidade"])
    assert cfg.limite_diario == int(banco.CONFIG_PADRAO["limite_diario"])
