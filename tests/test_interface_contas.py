"""Testes da parte sem Tkinter das várias contas e das opções novas (interface/dados.py e processo.py).

"Conta" na interface é um perfil do navegador (banco.listar_perfis). O banco,
a raiz e a pasta perfis/ são os temporários do conftest.
"""

import json
from pathlib import Path

import pytest

from automacao import banco, caminhos, configuracao
from automacao.interface import dados
from automacao.interface.dados import SituacaoConta, SituacaoLogin
from automacao.interface.processo import argumentos_login


# ------------------------------------------------------------ segurança dos testes

def test_conftest_aponta_as_pastas_das_contas_para_o_temporario(tmp_path):
    for pasta in (banco.RAIZ, banco.PASTA_PERFIS, banco.PASTA_PERFIL):
        assert pasta.is_relative_to(tmp_path)
    assert banco.PASTA_PERFIS.relative_to(banco.RAIZ) == Path("perfis")  # como o criar_perfil espera


# ------------------------------------------------------------ linhas da saída

@pytest.mark.parametrize(("linha", "nome"), [
    ("Perfil: Principal", "Principal"),
    ("Perfil: Conta da Márcia", "Conta da Márcia"),
    ("===== Perfil Loja =====", "Loja"),
    ("  ===== Perfil Loja   do Centro =====  ", "Loja do Centro"),
    ("===== Perfil A = B =====", "A = B"),
    # O nome vai até o " =====" do fim: "=", espaços e acentos não atrapalham.
    ("===== Perfil a=b =====", "a=b"),
    ("===== Perfil Loja = =====", "Loja ="),
    ("===== Perfil == Perfil == =====", "== Perfil =="),
    ("===== Perfil Conta da Márcia (ç) =====", "Conta da Márcia (ç)"),
    ("Perfil: x = y", "x = y"),
])
def test_perfil_da_linha_reconhece(linha, nome):
    assert dados.perfil_da_linha(linha) == nome


@pytest.mark.parametrize("linha", [
    "", "Perfis: 1.", "Perfil:", "Perfil:   ", "Perfis-alvo: 3",
    "Contas (perfis do navegador): Principal, Loja.", "[Instagram] Conta: @fulano",
    "===== Resumo =====", "Pausa antes da próxima conta...",
    "abrir_redes.py: error: perfil 'x' não encontrado (veja a aba Contas da interface)",
])
def test_perfil_da_linha_ignora_o_resto(linha):
    assert dados.perfil_da_linha(linha) is None


def test_espera_para_recomecar():
    linha = "Lista concluída. Recomeçando em 20 min (às 15:00)."
    assert dados.espera_recomeco(f"  {linha} ") == linha
    assert dados.espera_recomeco("Lista de perfis: 3") is None
    assert dados.espera_recomeco("Resumo:") is None


def contas_de_teste():
    return [SituacaoConta(1, "Principal"), SituacaoConta(2, "Loja"), SituacaoConta(3, "Casa", ativo=False)]


def test_saida_da_automacao_atualiza_cada_conta():
    contas, em_uso = contas_de_teste(), None
    saida = ["Redes: Instagram, TikTok. Perfis-alvo: 2.", "[Instagram] Conta: @sem_dono",
             "", "===== Perfil Principal =====", "[Instagram] Conta: @principal_ig", "[TikTok] Conta: @principal_tt",
             "Pausa antes da próxima conta...", "===== Perfil Loja =====", "[Instagram] Conta: @loja_ig",
             "[TikTok] Não está logado neste perfil. Faça o login pela aba Contas da interface."]
    trocas = []
    for linha in saida:
        em_uso, mudou = dados.tratar_linha_de_contas(contas, em_uso, linha, momento="2026-10-23T12:00:00-03:00")
        if dados.perfil_da_linha(linha):
            trocas.append((em_uso, mudou))
    assert trocas == [(1, True), (2, True)]
    assert em_uso == 2
    principal, loja, casa = contas
    assert (principal.login("Instagram").conta, principal.login("TikTok").conta) == ("@principal_ig", "@principal_tt")
    assert loja.login("Instagram").conta == "@loja_ig" and loja.login("TikTok").conta is None
    assert all(not s.conta for s in casa.logins.values())  # "@sem_dono" veio antes de qualquer conta
    assert dados.texto_login_curto(loja.login("Instagram")) == "@loja_ig"  # conta lida: a rede está logada
    assert loja.login("Instagram").logado and not loja.login("TikTok").logado


def test_saida_do_login_vale_para_a_conta_do_login():
    contas = contas_de_teste()
    loja = contas[1]
    dados.marcar_verificando(loja)
    assert [dados.texto_login_curto(loja.login(rede)) for rede in dados.REDES_AUTOMACAO] == ["verificando…"] * 2
    em_uso = 2
    for linha in ("Perfil: Loja", "[TikTok] Já logado ✔", "[Instagram] Não logado — faça login manualmente na aba.",
                  "[TikTok] Conta: @loja_tt", "[Instagram] Login detectado ✔", "[Instagram] Conta: @loja_ig"):
        em_uso, _ = dados.tratar_linha_de_contas(contas, em_uso, linha, momento="2026-10-23T12:00:00-03:00")
    assert em_uso == 2
    assert [dados.texto_login_curto(loja.login(rede)) for rede in dados.REDES_AUTOMACAO] == ["@loja_ig", "@loja_tt"]
    assert loja.ultimo_login == "2026-10-23T12:00:00-03:00"
    assert contas[0].logins == {}  # a Principal não foi tocada


def test_nome_de_quando_o_processo_comecou_continua_valendo():
    contas = contas_de_teste()
    fotos = {dados.chave_nome(conta.nome): conta.id for conta in contas}
    contas[1].nome = "Loja Centro"  # renomeada no meio da automação
    assert dados.tratar_linha_de_contas(contas, 1, "===== Perfil Loja =====", fotos) == (2, True)
    assert dados.tratar_linha_de_contas(contas, 1, "===== Perfil loja centro =====") == (2, True)


def test_conta_desconhecida_ignora_as_linhas():
    contas = contas_de_teste()
    em_uso, mudou = dados.tratar_linha_de_contas(contas, 1, "===== Perfil Fantasma =====")
    assert (em_uso, mudou) == (None, True)
    assert dados.tratar_linha_de_contas(contas, em_uso, "[Instagram] Conta: @x") == (None, False)
    assert all(conta.logins == {} for conta in contas)


def test_linha_sem_evento_nao_muda_nada():
    contas = contas_de_teste()
    assert dados.tratar_linha_de_contas(contas, 1, "Aguardando login (até 15 min)...") == (1, False)


# ------------------------------------------------------------ situação das contas

def test_situacao_contas_do_banco(conn):
    loja = banco.criar_perfil(conn, "Loja")
    casa = banco.criar_perfil(conn, "Casa")
    banco.definir_perfil_ativo(conn, casa, False)
    execucao = banco.iniciar_execucao(conn, loja)
    banco.registrar_login(conn, execucao, "Instagram", "ja_logado")
    banco.registrar_conta(conn, execucao, "Instagram", "@loja_ig")
    banco.registrar_login(conn, execucao, "TikTok", "nao_logado")

    contas = dados.situacao_contas(conn)
    assert [(c.id, c.nome, c.ativo, c.principal) for c in contas] == [
        (1, "Principal", True, True), (loja, "Loja", True, False), (casa, "Casa", False, False)]
    principal, conta_loja, _ = contas
    assert [dados.texto_login_curto(principal.login(r)) for r in dados.REDES_AUTOMACAO] == ["nunca verificado"] * 2
    assert [dados.texto_login_curto(conta_loja.login(r)) for r in dados.REDES_AUTOMACAO] == ["@loja_ig", "não logado"]
    assert conta_loja.ultimo_login and principal.ultimo_login is None
    assert dados.formatar_momento(conta_loja.ultimo_login).startswith("hoje às")
    assert dados.achar_conta(contas, loja) is conta_loja and dados.achar_conta(contas, 99) is None


@pytest.mark.parametrize(("situacao", "texto"), [
    (SituacaoLogin("Instagram"), "nunca verificado"),
    (SituacaoLogin("Instagram", verificando=True), "verificando…"),
    (SituacaoLogin("Instagram", "ja_logado", verificando=True), "verificando…"),
    (SituacaoLogin("Instagram", "ja_logado", conta="@fulano"), "@fulano"),
    (SituacaoLogin("Instagram", "login_detectado", ultima_conta="@antigo"), "@antigo (última vista)"),
    (SituacaoLogin("Instagram", "ja_logado"), "logado"),
    (SituacaoLogin("Instagram", "nao_logado", ultima_conta="@antigo"), "não logado"),
])
def test_texto_login_curto(situacao, texto):
    assert dados.texto_login_curto(situacao) == texto


def test_ultimo_login_compara_as_datas_e_nao_o_texto():
    conta = SituacaoConta(1, "Principal", logins={
        "Instagram": SituacaoLogin("Instagram", "ja_logado", verificado_em="2026-10-23T09:00:00-03:00"),
        "TikTok": SituacaoLogin("TikTok", "ja_logado", verificado_em="2026-10-23T11:30:00-01:00"),  # 09:30 em -03
    })
    assert conta.ultimo_login == "2026-10-23T11:30:00-01:00"


def test_resumo_das_contas_ativas():
    assert dados.resumo_contas_ativas([SituacaoConta(1, "Principal")]) == ("Principal", None)
    assert dados.resumo_contas_ativas(contas_de_teste()) == ("2 de 3: Principal, Loja", None)
    texto, estilo = dados.resumo_contas_ativas([SituacaoConta(1, "Principal", ativo=False)])
    assert estilo == "erro" and "aba Contas" in texto
    muitas = [SituacaoConta(i, f"C{i}") for i in range(1, 8)]
    assert dados.resumo_contas_ativas(muitas) == ("7 de 7: C1, C2, C3, C4 e mais 3", None)


# ------------------------------------------------------------ pasta de uma conta removida

def test_so_apaga_pasta_de_conta_extra_dentro_de_perfis(conn):
    loja = banco.obter_perfil(conn, banco.criar_perfil(conn, "Loja"))
    pasta = banco.pasta_do_perfil(loja)
    assert not dados.pasta_pode_ser_apagada(pasta)  # ainda não existe
    (pasta / "Default").mkdir(parents=True)
    (pasta / "Default" / "Cookies").write_text("x", encoding="utf-8")
    assert dados.pasta_pode_ser_apagada(pasta)
    dados.apagar_pasta_da_conta(pasta)
    assert not pasta.exists() and banco.PASTA_PERFIS.exists()


def test_nunca_apaga_a_principal_nem_pastas_de_fora(tmp_path):
    principal = banco.PASTA_PERFIL
    principal.mkdir(parents=True)
    banco.PASTA_PERFIS.mkdir(parents=True)
    fora = tmp_path / "outra"
    fora.mkdir()
    for pasta in (principal, banco.PASTA_PERFIS, banco.RAIZ, fora, banco.PASTA_PERFIS / ".." / "outra"):
        assert not dados.pasta_pode_ser_apagada(pasta), pasta
        with pytest.raises(ValueError, match="Por segurança"):
            dados.apagar_pasta_da_conta(pasta)
    assert principal.exists() and fora.exists() and banco.PASTA_PERFIS.exists()
    # As pastas de verdade do projeto: só a conferência (que só lê), nunca o apagar.
    for pasta in (caminhos.PASTA_PERFIL, caminhos.RAIZ, caminhos.PASTA_PERFIS):
        assert not dados.pasta_pode_ser_apagada(pasta), pasta


def test_nao_apaga_pasta_que_contem_a_principal(monkeypatch, tmp_path):
    monkeypatch.setattr(banco, "PASTA_PERFIL", banco.PASTA_PERFIS / "x" / "perfil_navegador")
    banco.PASTA_PERFIL.mkdir(parents=True)
    assert not dados.pasta_pode_ser_apagada(banco.PASTA_PERFIS / "x")


def test_nao_segue_atalho_para_fora(tmp_path):
    alvo = tmp_path / "importante"
    alvo.mkdir()
    banco.PASTA_PERFIS.mkdir(parents=True)
    atalho = banco.PASTA_PERFIS / "atalho"
    try:
        atalho.symlink_to(alvo, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar atalhos")
    assert not dados.pasta_pode_ser_apagada(atalho)
    assert alvo.exists()


# ------------------------------------------------------------ configuração: redes e opções

def test_argumentos_do_login():
    assert argumentos_login(3) == ["--perfil", "3"]
    assert argumentos_login(banco.PERFIL_PRINCIPAL) == ["--perfil", "1"]
    assert argumentos_login() == []


@pytest.mark.parametrize(("redes", "texto"), [("instagram", "só Instagram"), ("tiktok", "só TikTok"),
                                              ("ambas", "as duas (Instagram e TikTok)"),
                                              ("qualquer", "as duas (Instagram e TikTok)")])
def test_texto_das_redes(redes, texto):
    assert dados.texto_redes({"redes": redes}) == texto


@pytest.mark.parametrize(("valor", "ligado"), [("sim", True), ("SIM ", True), ("1", True), ("on", True),
                                               ("nao", False), ("", False), (None, False), ("talvez", False)])
def test_ligado_entende_como_o_validar(valor, ligado):
    assert dados.ligado(valor) is ligado
    if valor is not None:
        valores, _ = configuracao.validar({"anti_bloqueio": valor})
        assert (valores["anti_bloqueio"] == "sim") is ligado


def test_resumo_extras():
    valores = dict(banco.CONFIG_PADRAO)
    assert dados.resumo_extras(valores) == "Seguir: não · Stories: não · Recomeçar: não · Conta sem login: pula"
    valores.update(seguir="sim", ver_stories="sim", repetir="sim", repetir_minutos="90", continuar_sem_login="nao")
    assert dados.resumo_extras(valores) == ("Seguir: sim · Stories: sim · Recomeçar: 90 min após terminar · "
                                            "Conta sem login: não inicia")


def test_formulario_da_aba_salva_e_aparece_no_resumo(conn):
    """O mesmo caminho da aba Configurações: validar, salvar e resumir na aba Início."""
    campos = dict(modo="quantidade", quantidade="3", semanas="2", compartilhar_com="fulano", limite_diario="40",
                  comentarios="Boa!!!", redes="TikTok", anti_bloqueio="sim", seguir="nao", ver_stories="sim",
                  repetir="sim", repetir_minutos="30", continuar_sem_login="sim")
    valores, erros = configuracao.validar(campos)
    assert erros == []
    banco.salvar_configuracoes(conn, valores)
    resumo = dict(dados.resumo_configuracao(banco.ler_configuracoes(conn)))
    assert resumo["Redes"] == "só TikTok"
    assert resumo["Proteção"] == "anti-bloqueio (lento e cauteloso)"
    assert resumo["Ações extras"] == ("Seguir: não · Stories: sim · Recomeçar: 30 min após terminar · "
                                      "Conta sem login: pula")


def test_intervalo_invalido_para_recomecar_nao_passa():
    _, erros = configuracao.validar(dict(banco.CONFIG_PADRAO, repetir="sim", repetir_minutos="0",
                                         comentarios=json.dumps(["x"])))
    assert any("recomeçar" in erro for erro in erros)


def test_perfis_a_acessar_nas_redes_escolhidas():
    resumo = dados.ResumoPerfis(existe=True, por_rede={"Instagram": 2, "TikTok": 1})
    assert resumo.nas_redes(("Instagram", "TikTok")) == 3 and resumo.nas_redes(("TikTok",)) == 1
    assert resumo.texto(("Instagram", "TikTok")) == "Instagram: 2 · TikTok: 1"
    assert resumo.texto(("TikTok",)) == "Instagram: 2 (rede não escolhida) · TikTok: 1"
