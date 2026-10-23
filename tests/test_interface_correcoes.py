"""Testes das correções da janela: contas ao vivo, saída em lotes, links inválidos,
planilhas, pausas por aviso, uma janela por vez, fechar sem travar e tamanho com zoom.

Como em test_interface_gui.py: janela escondida, scripts falsos no lugar do login e
da automação, caixas de diálogo trocadas e banco/planilhas temporários (conftest).
"""

import dataclasses
import queue
import time
import tkinter as tk
from contextlib import closing
from datetime import datetime, timedelta
from tkinter import ttk

import pytest

from automacao import banco, planilhas, terminal
from automacao.interface import componentes, dados
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

@pytest.mark.parametrize(("zoom", "tela"), [(1.0, (1920, 1080)), (1.25, (1920, 1080)), (1.5, (1920, 1080)),
                                            (1.0, (1366, 768))])
def test_nada_cortado_no_tamanho_minimo_com_zoom(monkeypatch, dialogos, zoom, tela):
    """Com zoom de 125% e 150% (fontes maiores, margens iguais), nada fica cortado e a saída tem 4 linhas."""
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
                                          "repetir": "sim", "seguir": "sim", "anti_bloqueio": "sim"})
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
