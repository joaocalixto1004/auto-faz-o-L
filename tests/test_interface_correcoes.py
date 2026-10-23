"""Testes das correções da janela: contas ao vivo, saída em lotes, links inválidos,
planilhas, pausas por aviso, uma janela por vez, fechar sem travar e tamanho com zoom.

Como em test_interface_gui.py: janela escondida, scripts falsos no lugar do login e
da automação, caixas de diálogo trocadas e banco/planilhas temporários (conftest).
"""

import dataclasses
import json
import queue
import time
import tkinter as tk
from contextlib import closing
from datetime import datetime, timedelta
from tkinter import ttk

import pytest

from automacao import banco, planilhas, terminal
from automacao.redes import comum, protecao
from automacao.interface import aba_configuracoes, componentes, dados
from automacao.interface import janela as interface
from automacao.interface import processo as processo_interface
from test_interface_gui import (bombear, cortados, criar_conta, criar_script, dialogos,  # noqa: F401 - fixtures
                                existe, janela, resumo)


# ------------------------------------------------------------ contas ao vivo

def test_conta_lida_na_automacao_continua_ao_trocar_de_aba(janela, tmp_path):
    """"[Rede] Conta: @x" na automação vale como logado e não some ao reler o banco."""
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/"]}, planilhas.PLANILHA_PERFIS)
    with closing(banco.conectar()) as conn:  # o banco diz "não logado" (verificação antiga)
        execucao = banco.iniciar_execucao(conn, banco.PERFIL_PRINCIPAL)
        banco.registrar_login(conn, execucao, "Instagram", "nao_logado")
    linhas = ["===== Perfil Principal =====", "[Instagram] Conta: @principal_ig"]
    inicio = janela.inicio
    inicio.scripts["automacao"] = criar_script(tmp_path / "automacao_viva.py", linhas, espera=30)
    janela.inicio.atualizar_resumo()
    assert inicio.linha_da_conta(banco.PERFIL_PRINCIPAL)[1] == "não logado"

    inicio.iniciar_automacao()
    assert bombear(janela, lambda: "args:" in inicio.texto_da_saida())
    assert inicio.linha_da_conta(banco.PERFIL_PRINCIPAL)[:2] == ("▶ Principal", "@principal_ig")
    for aba in ("contas", "configuracoes", "inicio"):  # cada troca relê o banco
        janela.ir_para(aba)
        janela.update()
    assert inicio.linha_da_conta(banco.PERFIL_PRINCIPAL)[:2] == ("▶ Principal", "@principal_ig")
    assert janela.contas.arvore.item("1", "values")[1] == "@principal_ig"

    processo = inicio.processo
    inicio.parar()
    assert bombear(janela, lambda: not inicio.em_execucao)
    assert inicio.processo is None and not processo.rodando  # o fim zera o processo
    inicio.parar()  # sem processo: não faz nada
    assert inicio.linha_da_conta(banco.PERFIL_PRINCIPAL)[1] == "não logado"  # de novo, o banco


# ------------------------------------------------------------ saída do processo

class ProcessoFalso:
    """Só a fila de saída e o que a aba Início consulta."""

    def __init__(self):
        self.saida = queue.Queue()
        self.parada_pedida = False
        self.iniciado_em = time.monotonic()
        self.rodando = True

    def esperar_limpeza(self, timeout=None):
        return True


def test_saida_em_rajada_entra_de_uma_vez_e_corta_uma_vez(janela, monkeypatch):
    inicio = janela.inicio
    monkeypatch.setattr(interface, "MAXIMO_LINHAS_SAIDA", 50)
    for numero in range(10):
        inicio.escrever(f"antiga {numero}")
    falso = ProcessoFalso()
    for numero in range(120):
        falso.saida.put(("linha", f"linha {numero} ✔"))
    inicio.processo, inicio.tipo, inicio.em_execucao = falso, "automacao", True
    chamadas = {"insert": 0, "delete": 0}
    texto = inicio.texto
    for nome in chamadas:
        original = getattr(texto, nome)

        def contar(*args, _nome=nome, _original=original, **kwargs):
            chamadas[_nome] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(texto, nome, contar)
    try:
        inicio._ler_saida()
        assert chamadas == {"insert": 1, "delete": 1}
        linhas = inicio.texto_da_saida().splitlines()
        assert len(linhas) == 50 and linhas[0] == "linha 70 ✔" and linhas[-1] == "linha 119 ✔"
        assert texto.tag_ranges("ok")  # cada linha com a sua cor
    finally:
        inicio.processo, inicio.em_execucao = None, False


# ------------------------------------------------------------ links inválidos

def test_links_invalidos_sao_avisados_e_a_automacao_pergunta(janela, dialogos):
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/",
                                           "https://www.instagram.com/p/ABC123/"]}, planilhas.PLANILHA_PERFIS)
    inicio = janela.inicio
    inicio.atualizar_resumo()
    rotulo = inicio._rotulos_resumo["Perfis a acessar"]
    assert rotulo.cget("text") == ("Instagram: 1 · TikTok: 0\n"
                                   "1 link inválido será ignorado: https://www.instagram.com/p/ABC123/")
    assert str(rotulo.cget("style")) == "Aviso.TLabel"
    assert "não inicia" not in rotulo.cget("text")

    dialogos.respostas["askyesno"] = False
    inicio.iniciar_automacao()
    assert not inicio.em_execucao and dialogos.nomes() == ["askyesno"]
    assert "será ignorado" in dialogos.chamadas[-1][1][1] and "Iniciar mesmo assim?" in dialogos.chamadas[-1][1][1]

    dialogos.respostas["askyesno"] = True
    inicio.iniciar_automacao()
    assert inicio.em_execucao
    assert bombear(janela, lambda: not inicio.em_execucao)


def test_so_links_invalidos_nas_redes_escolhidas_nao_inicia(janela, dialogos):
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/p/ABC123/", "https://www.instagram.com/reel/X/"],
                             "tiktok": []}, planilhas.PLANILHA_PERFIS)
    janela.inicio.iniciar_automacao()
    assert not janela.inicio.em_execucao and dialogos.nomes() == ["showwarning"]
    texto = dialogos.chamadas[-1][1][1]
    assert "Nenhum link de perfil válido" in texto and "2 links inválidos serão ignorados" in texto


# ------------------------------------------------------------ planilhas

def test_salvar_planilhas_salva_a_outra_mesmo_se_uma_falhar(janela, dialogos):
    aba = janela.planilhas
    aba.perfis.listas["instagram"].var_novo.set("https://www.instagram.com/fulano/")
    aba.perfis.listas["instagram"].adicionar()
    aba.destinatarios.listas["tiktok"].var_novo.set("amigo")
    aba.destinatarios.listas["tiktok"].adicionar()

    def aberta_no_excel(_dados, caminho):
        raise PermissionError(13, "Permission denied", str(caminho))
    aba.perfis.tipo = dataclasses.replace(aba.perfis.tipo, salvar=aberta_no_excel)

    assert not aba.salvar()
    assert dialogos.nomes() == ["showerror"]  # uma mensagem só, no fim
    mensagem = dialogos.chamadas[-1][1][1]
    assert "Perfis a acessar" in mensagem and "feche o arquivo no Excel e tente de novo" in mensagem
    assert "Compartilhar com: salva." in mensagem
    assert planilhas.ler_destinatarios(planilhas.PLANILHA_DESTINATARIOS)["TikTok"] == ["amigo"]
    assert aba.perfis.tem_alteracoes() and not aba.destinatarios.tem_alteracoes()
    assert aba.cadernos.select() == str(aba.perfis)  # mostra a que falhou


def test_link_com_virgula_no_endereco_nao_vira_dois(janela):
    lista = janela.planilhas.perfis.listas["instagram"]
    lista.var_novo.set("https://www.instagram.com/fulano/?utm_source=a,b;c\nhttps://www.instagram.com/ciclano/,"
                       "https://www.instagram.com/beltrano/")
    lista.adicionar()
    assert lista.valores() == ["https://www.instagram.com/fulano/", "https://www.instagram.com/ciclano/",
                               "https://www.instagram.com/beltrano/"]
    assert lista.rotulo_erro.cget("text") == "" and lista.var_novo.get() == ""
    usuarios = janela.planilhas.destinatarios.listas["instagram"]
    usuarios.var_novo.set("amigo,colega; vizinho")
    usuarios.adicionar()
    assert usuarios.valores() == ["amigo", "colega", "vizinho"]


# ------------------------------------------------------------ pausas por aviso da rede

def test_pausas_por_aviso_aparecem_no_inicio(janela):
    inicio = janela.inicio
    assert not inicio.rotulo_pausas.winfo_manager()  # sem pausa, nem aparece
    with closing(banco.conectar()) as conn:
        fim = banco.registrar_pausa(conn, "Instagram", "@Fulano", "Tente novamente mais tarde", 24)
    inicio.atualizar_resumo()
    esperado = f"Em pausa por aviso da rede: @fulano no Instagram até {datetime.fromisoformat(fim):%d/%m %H:%M}"
    assert inicio.rotulo_pausas.cget("text") == esperado
    assert inicio.rotulo_pausas.winfo_manager() == "grid"

    inicio._pausas = [dict(p, fim=(datetime.now().astimezone() - timedelta(minutes=1)).isoformat())
                      for p in inicio._pausas]  # acabou enquanto a janela estava aberta
    inicio._relogio(reagendar=False)  # o relógio de cada segundo tira as que acabaram
    assert not inicio.rotulo_pausas.winfo_manager()


# ------------------------------------------------------------ fechar com processo rodando

def test_fechar_esconde_a_janela_na_hora_e_so_sai_depois_da_limpeza(janela, dialogos, monkeypatch):
    escondida = []
    monkeypatch.setattr(janela, "withdraw", lambda: escondida.append(True))
    inicio = janela.inicio
    inicio.iniciar_login()
    processo = inicio.processo
    assert bombear(janela, lambda: "args:" in inicio.texto_da_saida())
    limpo_ao_destruir = []
    destruir = janela.destroy
    monkeypatch.setattr(janela, "destroy", lambda: (limpo_ao_destruir.append(processo.esperar_limpeza(0)),
                                                    destruir()))

    comeco = time.monotonic()
    janela.ao_fechar()
    assert time.monotonic() - comeco < 0.5  # não espera o processo parar
    assert escondida == [True] and existe(janela)
    assert bombear(janela, lambda: not existe(janela))
    assert limpo_ao_destruir == [True] and not processo.rodando


def test_destruir_com_processo_rodando_nao_trava_a_janela(janela):
    inicio = janela.inicio
    inicio.iniciar_login()
    processo = inicio.processo
    assert bombear(janela, lambda: "args:" in inicio.texto_da_saida())
    comeco = time.monotonic()
    janela.destroy()
    assert time.monotonic() - comeco < 1.0
    assert processo.esperar_limpeza(15) and not processo.rodando  # a thread de fundo matou e limpou


# ------------------------------------------------------------ uma janela por vez

class AplicacaoFalsa:
    criadas = 0

    def __init__(self):
        AplicacaoFalsa.criadas += 1
        self.inicio = self
        self.trava_livre_durante = None

    def mainloop(self):
        outra = terminal.obter_trava(processo_interface.TRAVA_INTERFACE)
        self.trava_livre_durante = outra is not None
        if outra:
            outra.close()

    def matar_processo(self):
        pass


def test_segunda_janela_avisa_e_sai(monkeypatch, dialogos, capsys):
    monkeypatch.setattr(interface, "_TRAVA_DA_JANELA", None)
    monkeypatch.setattr(interface, "Aplicacao", AplicacaoFalsa)
    AplicacaoFalsa.criadas = 0
    assert processo_interface.TRAVA_INTERFACE.name == "interface.trava"
    primeira = terminal.obter_trava(processo_interface.TRAVA_INTERFACE)
    assert primeira is not None
    try:
        assert interface.main() == 0
    finally:
        primeira.close()
    assert AplicacaoFalsa.criadas == 0
    assert interface.JA_ABERTA in capsys.readouterr().out
    if dialogos.nomes():  # sem tela, só a mensagem no terminal
        assert dialogos.nomes() == ["showinfo"] and interface.JA_ABERTA in dialogos.chamadas[0][1][1]


def test_primeira_janela_segura_a_trava(monkeypatch, dialogos):
    monkeypatch.setattr(interface, "_TRAVA_DA_JANELA", None)
    criadas = []
    monkeypatch.setattr(interface, "Aplicacao", lambda: criadas.append(AplicacaoFalsa()) or criadas[-1])
    assert interface.main() == 0
    assert len(criadas) == 1 and criadas[0].trava_livre_durante is False  # outra janela não pegaria
    assert dialogos.nomes() == []


# ------------------------------------------------------------ quebra de texto

def test_quebra_de_texto_so_alarga_com_folga(janela):
    quadro = ttk.Frame(janela)
    rotulo = componentes.quebrar_na_largura(ttk.Label(quadro, text="texto " * 40, wraplength=220))

    def largura(pixels):
        rotulo.event_generate("<Configure>", width=pixels, height=20)
        return int(str(rotulo.cget("wraplength")))
    assert largura(300) == 294
    assert largura(297) == 291          # estreitou: requebra na hora (nada cortado)
    assert largura(299) == 291          # alargou pouco: fica
    assert largura(291 + 6 + componentes.FOLGA_QUEBRA) == 291
    assert largura(310) == 304          # alargou de verdade: requebra
    quadro.destroy()


# ------------------------------------------------------------ tamanho com zoom

@pytest.mark.parametrize("protecao", ["anti_bloqueio", "boost"])
@pytest.mark.parametrize(("zoom", "tela"), [(1.0, (1920, 1080)), (1.25, (1920, 1080)), (1.5, (1920, 1080)),
                                            (1.0, (1366, 768))])
def test_nada_cortado_no_tamanho_minimo_com_zoom(monkeypatch, dialogos, zoom, tela, protecao):
    """Com zoom de 125% e 150% (fontes maiores, margens iguais), nada fica cortado e a saída tem 4 linhas.

    Vale com os textos mais longos do resumo: anti-bloqueio ou boost (que muda também o limite diário)."""
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("sem tela para abrir janelas")
    iniciar = tk.Tk.__init__

    def com_zoom(self, *args, **kwargs):
        iniciar(self, *args, **kwargs)
        self.tk.call("tk", "scaling", zoom * 96 / 72)  # o que o Tk usa com a escala da tela
    monkeypatch.setattr(tk.Tk, "__init__", com_zoom)
    monkeypatch.setattr(tk.Misc, "winfo_screenwidth", lambda self: tela[0])
    monkeypatch.setattr(tk.Misc, "winfo_screenheight", lambda self: tela[1])
    for nome in ("Loja do Centro", "Maria Aparecida de Souza"):
        criar_conta(nome)
    with closing(banco.conectar()) as conn:
        banco.salvar_configuracoes(conn, {"comentarios": '["Que publicação incrível, parabéns!", "Top", "Show"]',
                                          "repetir": "sim", "seguir": "sim", protecao: "sim"})
        banco.registrar_pausa(conn, "Instagram", "@maria.aparecida_souza", "aviso", 24)
        banco.registrar_pausa(conn, "*", "*", "avisos em duas contas no mesmo dia", 48)
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/", "https://www.instagram.com/p/AB1/"],
                             "tiktok": ["https://www.tiktok.com/@ciclano"]}, planilhas.PLANILHA_PERFIS)
    app = interface.Aplicacao()
    try:
        app.withdraw()
        app.update()  # o mínimo pelo conteúdo é calculado quando o Tk fica ocioso
        largura, altura = app.minsize()
        # (o Tk arredonda a escala: 150% vira 143,98 pixels por polegada)
        assert largura >= min(int(900 * zoom * 0.99), tela[0] - 40)
        assert altura >= min(int(640 * zoom * 0.99), tela[1] - 80)
        assert largura <= tela[0] - 40 and altura <= tela[1] - 80
        app.inicio.botao_parar.configure(text="Forçar parada")
        app.geometry(f"{largura}x{altura}")
        for aba in app.cadernos.tabs():
            app.cadernos.select(aba)
            for _ in range(3):
                app.update()
            assert cortados(app.nametowidget(aba)) == [], (zoom, app.cadernos.tab(aba, "text"))
        app.cadernos.select(app.inicio)
        for _ in range(3):
            app.update()
        assert app.inicio.rotulo_pausas.winfo_manager() == "grid"
        assert resumo(app)["Proteção"].startswith("boost" if protecao == "boost" else "anti-bloqueio")
        assert "todas as contas em todas as redes" in app.inicio.rotulo_pausas.cget("text")
        inicio = app.inicio
        assert inicio.winfo_y() + inicio.winfo_height() <= app.cadernos.winfo_height()  # nada passa da janela
        linha = interface.tkfont.nametofont("TkFixedFont", root=app).metrics("linespace")
        assert inicio.texto.winfo_height() >= interface.LINHAS_MINIMAS_SAIDA * linha
    finally:
        app.destroy()


def test_conftest_aponta_a_trava_da_janela_para_o_temporario(tmp_path):
    assert processo_interface.TRAVA_INTERFACE == tmp_path / "interface.trava"
    assert dados.OBSERVACAO_INVALIDO == "inválido: será ignorado"


# ------------------------------------------------------------ modo boost

def salvas():
    with closing(banco.conectar()) as conn:
        return banco.ler_configuracoes(conn)


def test_modo_boost_salva_e_trava_o_limite_diario(janela, dialogos):
    aba = janela.configuracoes
    assert aba.vars["boost"].get() == "nao" and str(aba.spin_limite.cget("state")) == "normal"
    assert aba.check_boost.cget("text") == "Modo boost (ignorar os tetos)"
    aba.vars["limite_diario"].set("25")

    aba.check_boost.invoke()
    assert aba.vars["boost"].get() == "sim" and aba.vars["anti_bloqueio"].get() == "nao"
    assert str(aba.spin_limite.cget("state")) == "disabled"
    assert aba.vars["limite_diario"].get() == "25"  # travado, mas o valor fica
    assert "ignorado no modo boost" in aba.dica_limite.cget("text")
    assert aba.tem_alteracoes() and aba.salvar()
    assert (salvas()["boost"], salvas()["anti_bloqueio"], salvas()["limite_diario"]) == ("sim", "nao", "25")

    textos = resumo(janela)
    assert textos["Proteção"].startswith("boost: sem tetos") and "risco" in textos["Proteção"]
    assert textos["Limite diário"] == "ignorado no modo boost (salvo: 25 por conta, em cada rede)"
    assert str(janela.inicio._rotulos_resumo["Proteção"].cget("style")) == "Aviso.TLabel"

    aba.check_boost.invoke()
    assert aba.vars["boost"].get() == "nao" and str(aba.spin_limite.cget("state")) == "normal"
    assert aba.salvar() and salvas()["boost"] == "nao"
    textos = resumo(janela)
    assert (textos["Proteção"], textos["Limite diário"]) == ("normal", "25 publicações por conta, em cada rede")
    assert str(janela.inicio._rotulos_resumo["Proteção"].cget("style")) == "TLabel"


def test_boost_e_anti_bloqueio_se_desmarcam(janela):
    aba = janela.configuracoes
    marcados = lambda: (aba.vars["anti_bloqueio"].get(), aba.vars["boost"].get())  # noqa: E731
    aba.check_anti_bloqueio.invoke()
    assert marcados() == ("sim", "nao")
    aba.check_boost.invoke()
    assert marcados() == ("nao", "sim") and str(aba.spin_limite.cget("state")) == "disabled"
    aba.check_anti_bloqueio.invoke()
    assert marcados() == ("sim", "nao") and str(aba.spin_limite.cget("state")) == "normal"


def test_configuracao_antiga_com_os_dois_ligados(janela):
    """Os dois aparecem marcados como foram salvos; vale o anti-bloqueio (como na automação) até mexer num deles."""
    with closing(banco.conectar()) as conn:
        banco.salvar_configuracoes(conn, {"anti_bloqueio": "sim", "boost": "sim"})
    aba = janela.configuracoes
    aba.carregar()
    janela.inicio.atualizar_resumo()
    assert (aba.vars["anti_bloqueio"].get(), aba.vars["boost"].get()) == ("sim", "sim")
    assert not aba.tem_alteracoes()
    assert str(aba.spin_limite.cget("state")) == "normal"  # o limite vale no anti-bloqueio
    assert resumo(janela)["Proteção"] == "anti-bloqueio (lento e cauteloso); o boost marcado fica sem efeito"

    aba.check_anti_bloqueio.invoke()  # desmarcou o anti-bloqueio: sobra o boost
    assert (aba.vars["anti_bloqueio"].get(), aba.vars["boost"].get()) == ("nao", "sim")
    assert str(aba.spin_limite.cget("state")) == "disabled"
    aba.check_anti_bloqueio.invoke()  # marcou de novo: desmarca o boost
    assert (aba.vars["anti_bloqueio"].get(), aba.vars["boost"].get()) == ("sim", "nao")


def test_boost_com_limite_invalido_volta_ao_salvo(janela):
    """O campo trava no boost: um valor inválido nele impediria salvar, então volta ao último válido."""
    aba = janela.configuracoes
    aba.vars["limite_diario"].set("")
    aba.check_boost.invoke()
    assert aba.vars["limite_diario"].get() == banco.CONFIG_PADRAO["limite_diario"]
    assert aba.salvar()


# ------------------------------------------------------------ tetos por ação

def tetos_na_tela(aba):
    return {periodo: {chave: int(aba.vars_tetos[(periodo, chave)].get()) for chave, _ in aba_configuracoes.NOMES_TETOS}
            for periodo in protecao.PERIODOS_TETO}


def test_tetos_comecam_nas_sugestoes_e_salvam_so_o_que_mudou(janela, dialogos):
    aba = janela.configuracoes
    assert tuple(chave for chave, _ in aba_configuracoes.NOMES_TETOS) == protecao.CHAVES_TETO
    assert tetos_na_tela(aba) == protecao.tetos_sugeridos() and not aba.tem_alteracoes()
    assert resumo(janela)["Tetos"] == "sugeridos: até 30 ações/h e 200/dia no total, por conta e rede"

    aba.vars_tetos[("por_hora", "curtir")].set("20")
    aba.vars_tetos[("por_dia", "total")].set("150")
    assert aba.tem_alteracoes() and aba.salvar()
    salvos = json.loads(salvas()["tetos"])
    esperado = protecao.tetos_sugeridos()
    esperado["por_hora"]["curtir"], esperado["por_dia"]["total"] = 20, 150
    assert salvos == esperado
    assert resumo(janela)["Tetos"] == "personalizados: até 30 ações/h e 150/dia no total, por conta e rede"
    aba.carregar()
    assert aba.vars_tetos[("por_hora", "curtir")].get() == "20" and not aba.tem_alteracoes()

    # "Restaurar sugestões" só preenche: vale depois de Salvar.
    aba.botao_sugestoes.invoke()
    assert tetos_na_tela(aba) == protecao.tetos_sugeridos() and aba.tem_alteracoes()
    assert json.loads(salvas()["tetos"]) == esperado
    assert aba.salvar() and salvas()["tetos"] == ""  # as sugestões ficam como vazio
    assert not aba.tem_alteracoes()


def test_teto_por_hora_maior_que_o_do_dia_nao_salva(janela, dialogos):
    aba = janela.configuracoes
    aba.vars_tetos[("por_hora", "comentar")].set("50")
    aba.vars_tetos[("por_dia", "comentar")].set("10")
    aba.vars_tetos[("por_dia", "seguir")].set("")
    assert not aba.salvar()
    assert dialogos.nomes() == ["showwarning"]
    erros = aba.rotulo_erros.cget("text")
    assert "O teto por hora de comentar (50) não pode ser maior que o do dia (10)." in erros
    assert "seguir precisa ser um número" in erros
    assert salvas()["tetos"] == ""


def test_tetos_travados_no_boost(janela, dialogos):
    aba = janela.configuracoes
    aba.vars_tetos[("por_hora", "total")].set("25")
    aba.vars_tetos[("por_dia", "favoritar")].set("")  # inválido: volta ao salvo, senão travaria o salvar
    aba.check_boost.invoke()
    assert all(str(spin.cget("state")) == "disabled" for spin in aba.spins_tetos)
    assert str(aba.botao_sugestoes.cget("state")) == "disabled"
    assert "Ignorados no modo boost" in aba.aviso_tetos.cget("text")
    assert aba.vars_tetos[("por_hora", "total")].get() == str(protecao.tetos_sugeridos()["por_hora"]["total"])
    assert aba.vars_tetos[("por_dia", "favoritar")].get() == str(protecao.tetos_sugeridos()["por_dia"]["favoritar"])
    aba.vars_tetos[("por_hora", "total")].set("25")  # os valores ficam guardados no boost
    assert aba.salvar()
    assert resumo(janela)["Tetos"] == "ignorados (boost)"
    assert str(janela.inicio._rotulos_resumo["Tetos"].cget("style")) == "Aviso.TLabel"
    aba.check_boost.invoke()
    assert all(str(spin.cget("state")) == "normal" for spin in aba.spins_tetos)
    assert aba.vars_tetos[("por_hora", "total")].get() == "25"


# ------------------------------------------------------------ emojis nos comentários

def test_paleta_poe_o_emoji_onde_esta_o_cursor(janela):
    aba = janela.configuracoes
    entrada, paleta = aba.entrada_comentario, aba.paleta_emojis
    assert len(paleta.botoes) == len(componentes.emojis_da_paleta()) >= 24
    botao = {emoji: b for (emoji, _), b in zip(paleta.emojis, paleta.botoes, strict=True)}
    aba.var_novo_comentario.set("Top demais")
    entrada.icursor(3)
    botao["🔥"].invoke()
    assert aba.var_novo_comentario.get() == "Top🔥 demais"
    entrada.icursor("end")
    botao["🇧🇷"].invoke()
    assert aba.var_novo_comentario.get() == "Top🔥 demais🇧🇷"
    entrada.selection_range(0, 3)  # com texto selecionado, o emoji entra no lugar dele
    botao["👏"].invoke()
    assert aba.var_novo_comentario.get() == "👏🔥 demais🇧🇷"
    botao["🔥"].event_generate("<Enter>")
    assert paleta.dica.cget("text") == "fogo"
    botao["🔥"].event_generate("<Leave>")
    assert paleta.dica.cget("text") == componentes.DICA_PALETA


def test_comentario_com_emojis_volta_igual_do_banco(janela, dialogos):
    aba = janela.configuracoes
    comentario = "Show 👏🏽🇧🇷 👨‍👩‍👧 ❤️"
    aba.var_novo_comentario.set(comentario)
    aba.adicionar_comentario()
    assert aba.comentarios() == ["Boa!!!", comentario]
    assert aba.salvar()
    assert comum.comentarios_da_config(salvas()) == ["Boa!!!", comentario]
    aba.carregar()
    assert aba.lista_comentarios.get(1) == comentario and aba.comentarios()[1] == comentario
    assert comentario in resumo(janela)["Comentários"]


def test_editar_comentario_tem_a_paleta(janela, monkeypatch):
    for nome in ("wait_visibility", "grab_set", "wait_window"):  # sem esperar ninguém clicar
        monkeypatch.setattr(aba_configuracoes.simpledialog.Dialog, nome, lambda self, *a: None)
    dialogo = aba_configuracoes.DialogoComentario("Editar comentário", "Comentário:", initialvalue="Top",
                                                  parent=janela.configuracoes)
    assert not dialogo.entry.selection_present()  # o texto não fica selecionado (o emoji não o apaga)
    dialogo.paleta.botoes[0].invoke()
    dialogo.ok()
    assert dialogo.result == "Top" + componentes.emojis_da_paleta()[0][0]


def test_paleta_no_tk_antigo_so_tem_simbolos_do_plano_basico(janela):
    """No Tk 8.6, os emojis fora do plano básico do Unicode saem errados: a paleta não os oferece."""
    assert componentes.emojis_da_paleta(8.6) == componentes.EMOJIS_BASICOS
    assert componentes.emojis_da_paleta(9.0) == componentes.EMOJIS
    assert all(ord(letra) <= 0xFFFF for emoji, _ in componentes.EMOJIS_BASICOS for letra in emoji)
    assert len(componentes.EMOJIS_BASICOS) >= 24
    quadro = ttk.Frame(janela)
    entrada = ttk.Entry(quadro)
    paleta = componentes.PaletaEmojis(quadro, alvo=entrada, versao_tk=8.6)
    assert [botao.cget("text") for botao in paleta.botoes] == [emoji for emoji, _ in componentes.EMOJIS_BASICOS]
    paleta.botoes[0].invoke()
    assert entrada.get() == "❤"
    quadro.destroy()


def test_todo_emoji_da_paleta_tem_imagem():
    """Os botões usam imagens: o Tk de alguns Pythons não desenha emojis com as fontes do sistema."""
    for emoji, _nome in componentes.EMOJIS + componentes.EMOJIS_BASICOS:
        assert (componentes.PASTA_EMOJIS / componentes.arquivo_do_emoji(emoji)).is_file(), emoji
    assert componentes.arquivo_do_emoji("🇧🇷") == "1f1e7-1f1f7.png"
    assert componentes.arquivo_do_emoji("❤️") == "2764-fe0f.png"


# ------------------------------------------------------------ faixa de publicações por aba

def test_faixa_de_quantidade_na_aba_configuracoes(janela, dialogos):
    aba = janela.configuracoes
    assert aba.vars["quantidade_min"].get() == aba.vars["quantidade"].get()   # sem faixa: mínimo = máximo
    assert not aba.tem_alteracoes()
    aba.vars["modo"].set("quantidade")
    aba.vars["quantidade_min"].set("2")
    aba.vars["quantidade"].set("6")
    assert aba.salvar()
    with closing(banco.conectar()) as conn:
        salvas = banco.ler_configuracoes(conn)
    assert (salvas["quantidade_min"], salvas["quantidade"]) == ("2", "6")
    assert "de 2 a 6 de cada aba, sorteado para cada conta" in resumo(janela)["Publicações"]
    aba.carregar()
    assert aba.vars["quantidade_min"].get() == "2" and not aba.tem_alteracoes()

    aba.vars["quantidade_min"].set("9")                          # mínimo maior que o máximo
    assert not aba.salvar()
    assert "não pode ser maior que o máximo" in dialogos.chamadas[-1][1][1]

    aba.vars["modo"].set("tempo")
    aba._atualizar_modo()
    assert str(aba.spin_quantidade_min.cget("state")) == "disabled"
    assert str(aba.spin_quantidade.cget("state")) == "disabled"
