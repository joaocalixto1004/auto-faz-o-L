"""Testes da janela (interface/janela.py) com scripts falsos no lugar do login e da automação.

A janela fica escondida (withdraw) e é sempre destruída no fim. As caixas de
diálogo são trocadas por funções que só anotam a chamada. O banco e as
planilhas são os temporários do conftest.
"""

import textwrap
import time
import tkinter as tk
from contextlib import closing
from tkinter import messagebox, simpledialog

import pytest

from automacao import banco, planilhas
from automacao.interface import janela as interface
from automacao.redes import comum

LINHAS_LOGIN = [
    "Perfil: Principal",
    "[TikTok] Já logado ✔",
    "[Instagram] Não logado — faça login manualmente na aba.",
    "Aguardando login (até 15 min)...",
    "[TikTok] Conta: @fulano",
    "[Instagram] Login detectado ✔",
    "[Instagram] Conta: @ciclano",
    "✔ TikTok  ✔ Instagram",
    "O navegador fica aberto até você clicar em Parar na interface (se a janela for fechada, ela reabre).",
]


def criar_script(caminho, linhas, espera=0.0):
    caminho.write_text(textwrap.dedent(f"""
        import sys, time
        for linha in {linhas!r}:
            print(linha, flush=True)
        print("args:", sys.argv[1:], flush=True)
        try:
            time.sleep({espera})
        except KeyboardInterrupt:
            print("Navegador fechado.")
    """), encoding="utf-8")
    return caminho


def bombear(app, condicao, timeout=15.0) -> bool:
    """Processa os eventos do Tk até a condição valer (ou a janela ser destruída)."""
    prazo = time.monotonic() + timeout
    while time.monotonic() < prazo:
        try:
            app.update()
        except tk.TclError:
            return condicao()
        if condicao():
            return True
        time.sleep(0.02)
    return False


class Dialogos:
    """Substitui as caixas de diálogo: anota e responde o que o teste mandar."""

    def __init__(self, monkeypatch):
        self.chamadas = []
        self.respostas = {"askyesno": True, "askyesnocancel": False, "askstring": None}
        for nome in ("showwarning", "showerror", "showinfo"):
            monkeypatch.setattr(messagebox, nome, self._anotar(nome))
        for nome in ("askyesno", "askyesnocancel"):
            monkeypatch.setattr(messagebox, nome, self._anotar(nome))
        monkeypatch.setattr(simpledialog, "askstring", self._anotar("askstring"))

    def _anotar(self, nome):
        def funcao(*args, **kwargs):
            self.chamadas.append((nome, args))
            resposta = self.respostas.get(nome)
            if isinstance(resposta, list):  # uma resposta para cada pergunta, em ordem
                return resposta.pop(0) if resposta else None
            return resposta
        return funcao

    def nomes(self):
        return [nome for nome, _ in self.chamadas]


@pytest.fixture
def dialogos(monkeypatch):
    return Dialogos(monkeypatch)


@pytest.fixture
def janela(tmp_path, dialogos):
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("sem tela para abrir janelas")
    login = criar_script(tmp_path / "login_falso.py", LINHAS_LOGIN, espera=30)
    automacao = criar_script(tmp_path / "automacao_falsa.py", ["Perfis: 1.", "Resumo:"])
    app = interface.Aplicacao(script_login=login, script_automacao=automacao)
    app.withdraw()
    app.update()
    try:
        yield app
    finally:
        try:
            app.inicio.matar_processo()
            app.destroy()
        except tk.TclError:
            pass  # o próprio teste já fechou a janela


def existe(app) -> bool:
    try:
        return bool(app.winfo_exists())
    except tk.TclError:
        return False


def linha_principal(app):
    """(conta, Instagram, TikTok) da conta Principal na tabela da aba Início."""
    return app.inicio.linha_da_conta(banco.PERFIL_PRINCIPAL)


def resumo(app):
    return {nome: rotulo.cget("text") for nome, rotulo in app.inicio._rotulos_resumo.items()}


# ------------------------------------------------------------ Início

def test_resumo_inicial_com_banco_e_planilhas_vazios(janela):
    assert linha_principal(janela) == ("Principal", "nunca verificado", "nunca verificado")
    textos = resumo(janela)
    assert textos["Redes"] == "as duas (Instagram e TikTok)"
    assert textos["Contas ativas"] == "Principal"
    assert textos["Comentários"] == "1 comentário: “Boa!!!”"
    assert textos["Ações extras"] == "Seguir: não · Stories: não · Recomeçar: não · Conta sem login: pula"
    assert textos["Proteção anti-bloqueio"] == "desligada"
    assert "não existe" in textos["Perfis a acessar"]
    assert textos["Compartilhar com"] == "Instagram: ninguém\nTikTok: ninguém"
    assert janela.inicio.combo_conta.get() == "Principal"  # o login vai para a Principal
    assert str(janela.inicio.botao_parar.cget("state")) == "disabled"


def test_login_ao_vivo_e_parar(janela):
    inicio = janela.inicio
    inicio.iniciar_login()
    assert inicio.em_execucao
    assert all(str(b.cget("state")) == "disabled" for b in (inicio.botao_login, inicio.botao_automacao))
    assert str(inicio.botao_parar.cget("state")) == "normal"

    # As linhas atualizam a situação enquanto o processo ainda roda.
    assert bombear(janela, lambda: "args: ['--perfil', '1']" in inicio.texto_da_saida())
    assert inicio.em_execucao and inicio.perfil_em_uso == banco.PERFIL_PRINCIPAL
    assert "▶ Verificação de login (conta Principal)" in inicio.texto_da_saida()
    assert linha_principal(janela) == ("▶ Principal", "@ciclano", "@fulano")
    assert janela.contas.arvore.item("1", "values")[3].startswith("hoje às")  # último login
    assert str(inicio.combo_conta.cget("state")) == "disabled"
    inicio.atualizar_resumo()  # ex.: voltar para a aba Início no meio da verificação
    assert linha_principal(janela) == ("▶ Principal", "@ciclano", "@fulano")

    inicio.parar()
    assert inicio.botao_parar.cget("text") == "Forçar parada"
    assert bombear(janela, lambda: not inicio.em_execucao)
    saida = inicio.texto_da_saida()
    assert "Navegador fechado." in saida and "parada pelo usuário" in saida
    assert str(inicio.botao_login.cget("state")) == "normal"
    assert str(inicio.botao_parar.cget("state")) == "disabled"
    assert inicio.botao_parar.cget("text") == "Parar"
    # No fim, a situação volta a ser a do banco (o script falso não gravou nada).
    assert linha_principal(janela) == ("Principal", "nunca verificado", "nunca verificado")
    assert inicio.perfil_em_uso is None

    inicio.limpar_saida()
    assert inicio.texto_da_saida() == ""


def test_automacao_sem_perfis_nao_inicia(janela, dialogos):
    janela.inicio.iniciar_automacao()
    assert not janela.inicio.em_execucao
    assert dialogos.nomes() == ["showwarning"]


def test_automacao_com_simular_e_trace(janela):
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/"]}, planilhas.PLANILHA_PERFIS)
    janela.inicio.atualizar_resumo()
    assert janela.inicio._rotulos_resumo["Perfis a acessar"].cget("text") == "Instagram: 1 · TikTok: 0"

    janela.inicio.var_simular.set(True)
    janela.inicio.var_trace.set(True)
    janela.inicio.iniciar_automacao()
    assert bombear(janela, lambda: not janela.inicio.em_execucao)
    saida = janela.inicio.texto_da_saida()
    assert "▶ Automação: automacao_falsa.py --simular --trace" in saida
    assert "args: ['--simular', '--trace']" in saida
    assert "Automação terminou" in saida


def test_iniciar_automacao_pergunta_sobre_alteracoes_nao_salvas(janela, dialogos):
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/"]}, planilhas.PLANILHA_PERFIS)
    janela.configuracoes.vars["limite_diario"].set("12")
    dialogos.respostas["askyesnocancel"] = None  # Cancelar
    janela.inicio.iniciar_automacao()
    assert not janela.inicio.em_execucao
    assert dialogos.nomes() == ["askyesnocancel"]

    dialogos.respostas["askyesnocancel"] = True  # Sim: salva e inicia
    janela.inicio.iniciar_automacao()
    assert janela.inicio.em_execucao
    assert bombear(janela, lambda: not janela.inicio.em_execucao)
    with closing(banco.conectar()) as conn:
        assert banco.ler_configuracoes(conn)["limite_diario"] == "12"


def test_fechar_com_processo_rodando_pergunta_e_para_antes(janela, dialogos):
    janela.inicio.iniciar_login()
    processo = janela.inicio.processo
    assert bombear(janela, lambda: "args: ['--perfil', '1']" in janela.inicio.texto_da_saida())

    dialogos.respostas["askyesno"] = False
    janela.ao_fechar()
    assert janela.winfo_exists() and processo.rodando

    dialogos.respostas["askyesno"] = True
    janela.ao_fechar()
    assert janela.title().endswith("encerrando…")
    assert bombear(janela, lambda: not existe(janela))  # fecha sozinha quando o processo termina
    assert processo.parada_pedida and not processo.rodando


# ------------------------------------------------------------ Configurações

def test_configuracoes_com_lista_de_comentarios(janela, dialogos):
    aba = janela.configuracoes
    assert aba.comentarios() == ["Boa!!!"] and not aba.tem_alteracoes()

    aba.var_novo_comentario.set("Top demais\nShow\nboa!!!")
    aba.adicionar_comentario()
    assert aba.comentarios() == ["Boa!!!", "Top demais", "Show"]
    assert "já está na lista" in aba.rotulo_erro_comentario.cget("text")
    assert aba.var_novo_comentario.get() == "boa!!!"  # só o recusado fica no campo
    assert aba.tem_alteracoes()

    aba.lista_comentarios.selection_set(2)
    dialogos.respostas["askstring"] = "Show de bola"
    aba.editar_comentario()
    aba.lista_comentarios.selection_clear(0, "end")
    aba.lista_comentarios.selection_set(0)
    aba.remover_comentarios()
    assert aba.comentarios() == ["Top demais", "Show de bola"]

    aba.vars["modo"].set("tempo")
    aba._atualizar_modo()
    assert str(aba.spin_quantidade.cget("state")) == "disabled"
    assert str(aba.spin_semanas.cget("state")) == "normal"
    aba.vars["semanas"].set("4")
    assert aba.salvar()
    assert not aba.tem_alteracoes()

    with closing(banco.conectar()) as conn:
        salvas = banco.ler_configuracoes(conn)
    assert comum.comentarios_da_config(salvas) == ["Top demais", "Show de bola"]
    assert (salvas["modo"], salvas["semanas"], salvas["comentario"]) == ("tempo", "4", "Top demais")
    resumo = janela.inicio._rotulos_resumo
    assert resumo["Comentários"].cget("text").startswith("2 comentários")
    assert resumo["Publicações"].cget("text").startswith("as das últimas 4 semana(s)")


def test_configuracoes_invalidas_nao_salvam(janela, dialogos):
    aba = janela.configuracoes
    aba.vars["limite_diario"].set("")
    aba.vars["compartilhar_com"].set("nome com espaço")
    aba.lista_comentarios.selection_set(0)
    aba.remover_comentarios()
    assert not aba.salvar()
    assert dialogos.nomes() == ["showwarning"]
    erros = aba.rotulo_erros.cget("text")
    assert "limite diário" in erros and "comentário" in erros and "sem espaços" in erros
    with closing(banco.conectar()) as conn:
        assert banco.ler_configuracoes(conn) == banco.CONFIG_PADRAO  # nada foi gravado

    aba.carregar()  # "Desfazer alterações"
    assert aba.comentarios() == ["Boa!!!"] and not aba.tem_alteracoes()


def test_campos_numericos_so_aceitam_algarismos(janela):
    spin = janela.configuracoes.spin_quantidade
    spin.delete(0, "end")
    for texto in ("1", "²", "a", "5"):
        spin.insert("end", texto)
    assert spin.get() == "15"


# ------------------------------------------------------------ Planilhas

def test_planilhas_adicionar_remover_e_salvar(janela):
    perfis = janela.planilhas.perfis
    instagram = perfis.listas["instagram"]
    instagram.var_novo.set("https://www.instagram.com/fulano/?igsh=1, https://www.tiktok.com/@x lixo")
    instagram.adicionar()
    assert instagram.valores() == ["https://www.instagram.com/fulano/"]
    assert instagram.var_novo.get() == "https://www.tiktok.com/@x lixo"
    assert "esta aba é do Instagram" in instagram.rotulo_erro.cget("text")

    instagram.var_novo.set("instagram.com/ciclano")
    instagram.adicionar()
    instagram.arvore.selection_set("0")
    instagram.remover()
    assert instagram.valores() == ["https://www.instagram.com/ciclano/"]
    assert perfis.tem_alteracoes() and janela.planilhas.tem_alteracoes()

    assert perfis.salvar()
    assert not perfis.tem_alteracoes()
    assert planilhas.ler_perfis(planilhas.PLANILHA_PERFIS) == ["https://www.instagram.com/ciclano/"]
    assert janela.inicio._rotulos_resumo["Perfis a acessar"].cget("text") == "Instagram: 1 · TikTok: 0"

    tiktok = janela.planilhas.destinatarios.listas["tiktok"]
    tiktok.var_novo.set("@amigo @Amigo")
    tiktok.adicionar()
    assert tiktok.valores() == ["amigo"] and "já está na lista" in tiktok.rotulo_erro.cget("text")
    assert janela.planilhas.salvar()
    assert planilhas.ler_destinatarios(planilhas.PLANILHA_DESTINATARIOS) == {"Instagram": [], "TikTok": ["amigo"]}
    assert janela.inicio._rotulos_resumo["Compartilhar com"].cget("text") == (
        "Instagram: ninguém\nTikTok: @amigo")


def test_planilha_alterada_fora_pede_confirmacao(janela, dialogos):
    painel = janela.planilhas.perfis
    painel.listas["tiktok"].var_novo.set("https://www.tiktok.com/@novo")
    painel.listas["tiktok"].adicionar()
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/de_fora/"]}, planilhas.PLANILHA_PERFIS)

    dialogos.respostas["askyesno"] = False
    assert not painel.salvar()
    assert planilhas.linhas_perfis()["instagram"] == ["https://www.instagram.com/de_fora/"]

    dialogos.respostas["askyesno"] = True
    assert painel.salvar()
    assert planilhas.linhas_perfis() == {"instagram": [], "tiktok": ["https://www.tiktok.com/@novo"]}


# ------------------------------------------------------------ janela de verdade

def test_janela_padrao_abre_escondida_e_usa_os_modulos_do_projeto(dialogos):
    """Teste de fumaça: a janela com o login e a automação de verdade (sem rodar nenhum dos dois)."""
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("sem tela para abrir janelas")
    app = interface.Aplicacao()
    try:
        app.withdraw()
        app.update()
        assert app.inicio.scripts == {"login": "automacao.abrir_redes", "automacao": "automacao.interagir"}
        assert [app.cadernos.tab(aba, "text") for aba in app.cadernos.tabs()] == [
            "Início", "Contas", "Configurações", "Planilhas"]
        assert not app.inicio.em_execucao and app.inicio.processo is None
        assert dialogos.nomes() == []
    finally:
        app.destroy()


# ------------------------------------------------------------ Contas

def criar_conta(nome, ativo=True):
    with closing(banco.conectar()) as conn:
        perfil_id = banco.criar_perfil(conn, nome)
        if not ativo:
            banco.definir_perfil_ativo(conn, perfil_id, False)
    return perfil_id


def conta_no_banco(perfil_id):
    with closing(banco.conectar()) as conn:
        return banco.obter_perfil(conn, perfil_id)


def test_aba_contas_adicionar_renomear_ativar_e_remover(janela, dialogos):
    aba = janela.contas
    assert [janela.cadernos.tab(t, "text") for t in janela.cadernos.tabs()] == [
        "Início", "Contas", "Configurações", "Planilhas"]
    janela.ir_para("contas")
    janela.update()
    assert aba.arvore.get_children() == ("1",)
    assert aba.arvore.item("1", "values") == ("Principal", "nunca verificado", "nunca verificado", "—", "sim")
    assert str(aba.botao_remover.cget("state")) == "disabled"  # a Principal não sai

    # Adicionar (sem fazer o login agora).
    dialogos.respostas.update(askstring="  Loja  ", askyesno=False)
    aba.adicionar()
    assert dialogos.nomes() == ["askstring", "askyesno"]
    with closing(banco.conectar()) as conn:
        loja = banco.obter_perfil(conn, "Loja")
    iid = str(loja["id"])
    assert loja["pasta"] == "perfis/loja" and not janela.inicio.em_execucao
    assert aba.arvore.get_children() == ("1", iid)
    assert aba.selecionada().nome == "Loja"
    assert janela.inicio.combo_conta.get() == "Loja"  # a mesma conta no botão de login da aba Início
    assert str(aba.botao_remover.cget("state")) == "normal"
    assert resumo(janela)["Contas ativas"] == "2 de 2: Principal, Loja"
    assert "Conta Loja adicionada." in aba.rotulo_estado.cget("text")

    dialogos.respostas["askstring"] = "LOJA"  # repetida
    aba.adicionar()
    assert dialogos.nomes()[-1] == "showwarning" and len(aba.arvore.get_children()) == 2

    # Renomear.
    dialogos.respostas["askstring"] = "Loja Centro"
    aba.renomear()
    assert conta_no_banco(loja["id"])["nome"] == "Loja Centro"
    assert aba.arvore.item(iid, "values")[0] == "Loja Centro"
    assert "Loja Centro" in janela.inicio.combo_conta.cget("values")

    # Desativar e ativar.
    assert aba.botao_ativar.cget("text") == "Desativar"
    aba.alternar_ativo()
    assert conta_no_banco(loja["id"])["ativo"] == 0
    assert aba.arvore.item(iid, "values")[4] == "não" and aba.botao_ativar.cget("text") == "Ativar"
    assert janela.inicio.linha_da_conta(loja["id"])[0] == "Loja Centro (inativa)"
    assert resumo(janela)["Contas ativas"] == "1 de 2: Principal"
    aba.alternar_ativo()
    assert conta_no_banco(loja["id"])["ativo"] == 1

    # Remover e apagar a pasta com o login salvo (temporária: o conftest troca banco.RAIZ).
    pasta = banco.pasta_do_perfil(loja)
    assert pasta.is_relative_to(banco.PASTA_PERFIS) and str(pasta).startswith(str(banco.RAIZ))
    pasta.mkdir(parents=True)
    (pasta / "Cookies").write_text("sessão", encoding="utf-8")
    dialogos.respostas["askyesno"] = [True, True]  # remover? sim. apagar a pasta? sim
    aba.remover()
    assert dialogos.nomes()[-2:] == ["askyesno", "askyesno"]
    assert conta_no_banco(loja["id"]) is None and not pasta.exists()
    assert aba.arvore.get_children() == ("1",)
    assert janela.inicio.combo_conta.get() == "Principal"
    assert "apagada" in aba.rotulo_estado.cget("text")


def test_remover_sem_apagar_a_pasta_e_principal_protegida(janela, dialogos):
    aba = janela.contas
    casa = criar_conta("Casa")
    janela.inicio.atualizar_contas()
    aba.selecionar(casa)
    pasta = banco.pasta_do_perfil(conta_no_banco(casa))
    pasta.mkdir(parents=True)
    dialogos.respostas["askyesno"] = [True, False]  # remove, mas guarda a pasta
    aba.remover()
    assert conta_no_banco(casa) is None and pasta.exists()
    assert "mantida" in aba.rotulo_estado.cget("text")

    principal = banco.PASTA_PERFIL
    principal.mkdir(parents=True)
    aba.selecionar(banco.PERFIL_PRINCIPAL)
    assert str(aba.botao_remover.cget("state")) == "disabled"
    aba.remover()  # mesmo chamado direto, não remove
    assert dialogos.nomes()[-1] == "showinfo"
    assert conta_no_banco(banco.PERFIL_PRINCIPAL) is not None and principal.exists()


def test_fazer_login_pela_aba_contas_atualiza_so_aquela_conta(janela, tmp_path):
    loja = criar_conta("Loja")
    linhas = ["Perfil: Loja", "[TikTok] Já logado ✔", "[Instagram] Não logado — faça login manualmente na aba.",
              "[TikTok] Conta: @loja_tt"]
    janela.inicio.scripts["login"] = criar_script(tmp_path / "login_loja.py", linhas, espera=30)
    inicio, aba = janela.inicio, janela.contas
    janela.ir_para("contas")
    janela.update()  # ao abrir a aba, as contas são relidas do banco
    aba.selecionar(loja)
    aba.fazer_login()
    assert janela.cadernos.select() == str(inicio)  # a saída e o Parar ficam na aba Início
    assert bombear(janela, lambda: f"args: ['--perfil', '{loja}']" in inicio.texto_da_saida())
    assert "▶ Verificação de login (conta Loja)" in inicio.texto_da_saida()
    assert inicio.linha_da_conta(loja) == ("▶ Loja", "não logado", "@loja_tt")
    assert linha_principal(janela) == ("Principal", "nunca verificado", "nunca verificado")
    assert aba.arvore.item(str(loja), "values")[:3] == ("▶ Loja", "não logado", "@loja_tt")
    assert "Login em execução na conta Loja" in aba.rotulo_estado.cget("text")
    assert "conta Loja" in inicio.rotulo_estado.cget("text")
    assert str(aba.botao_login.cget("state")) == "disabled"  # um navegador por vez
    assert not inicio.iniciar_login(banco.PERFIL_PRINCIPAL)

    inicio.parar()
    assert bombear(janela, lambda: not inicio.em_execucao)
    assert inicio.linha_da_conta(loja)[0] == "Loja"
    assert str(aba.botao_login.cget("state")) == "normal"


def test_remover_com_processo_rodando_nao_apaga_a_pasta(janela, dialogos, tmp_path):
    loja, casa = criar_conta("Loja"), criar_conta("Casa")
    inicio, aba = janela.inicio, janela.contas
    inicio.scripts["login"] = criar_script(tmp_path / "login_loja.py", ["Perfil: Loja"], espera=30)
    inicio.atualizar_contas()
    assert inicio.iniciar_login(loja)
    assert bombear(janela, lambda: "args:" in inicio.texto_da_saida())

    aba.selecionar(loja)
    aba.remover()  # em uso: nem pergunta
    assert dialogos.nomes() == ["showwarning"] and conta_no_banco(loja) is not None

    pasta = banco.pasta_do_perfil(conta_no_banco(casa))
    pasta.mkdir(parents=True)
    aba.selecionar(casa)
    dialogos.respostas["askyesno"] = True
    aba.remover()
    assert conta_no_banco(casa) is None
    assert pasta.exists() and dialogos.nomes()[-2:] == ["askyesno", "showinfo"]  # não oferece apagar

    inicio.parar()
    assert bombear(janela, lambda: not inicio.em_execucao)


def test_automacao_com_varias_contas_e_espera_para_recomecar(janela, tmp_path):
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/"]}, planilhas.PLANILHA_PERFIS)
    loja = criar_conta("Loja")
    linhas = ["Redes: Instagram, TikTok. Perfis-alvo: 1.", "", "===== Perfil Principal =====",
              "[Instagram] Conta: @principal_ig", "", "Pausa antes da próxima conta...", "",
              "===== Perfil Loja =====", "[Instagram] Conta: @loja_ig",
              "[TikTok] Não está logado neste perfil. Faça o login pela aba Contas da interface.",
              "Lista concluída. Recomeçando em 20 min (às 15:00)."]
    janela.inicio.scripts["automacao"] = criar_script(tmp_path / "automacao_voltas.py", linhas, espera=30)
    inicio = janela.inicio
    inicio.iniciar_automacao()
    assert inicio.em_execucao
    assert bombear(janela, lambda: inicio.aguardando is not None)
    assert inicio.perfil_em_uso == loja
    assert inicio.linha_da_conta(loja) == ("▶ Loja", "@loja_ig", "nunca verificado")
    assert linha_principal(janela)[:2] == ("Principal", "@principal_ig")
    estado = inicio.rotulo_estado.cget("text")
    assert "Lista concluída. Recomeçando em 20 min" in estado and "Parar" in estado
    assert str(inicio.botao_parar.cget("state")) == "normal"  # o Parar vale durante a espera

    inicio.parar()
    assert bombear(janela, lambda: not inicio.em_execucao)
    assert "Automação parada pelo usuário" in inicio.texto_da_saida()
    assert inicio.aguardando is None and inicio.perfil_em_uso is None
    assert inicio.rotulo_estado.cget("text") == "Nada em execução."


def test_automacao_nao_inicia_sem_conta_ativa_ou_sem_perfis_nas_redes(janela, dialogos):
    planilhas.salvar_perfis({"tiktok": ["https://www.tiktok.com/@ciclano"]}, planilhas.PLANILHA_PERFIS)
    with closing(banco.conectar()) as conn:
        banco.salvar_configuracoes(conn, {"redes": "instagram"})
    janela.inicio.iniciar_automacao()
    assert not janela.inicio.em_execucao
    assert dialogos.nomes() == ["showwarning"] and "Redes escolhidas" in dialogos.chamadas[-1][1][1]
    assert resumo(janela)["Perfis a acessar"] == "Instagram: 0 · TikTok: 1 (rede não escolhida)"
    assert resumo(janela)["Compartilhar com"] == "Instagram: ninguém"

    with closing(banco.conectar()) as conn:
        banco.salvar_configuracoes(conn, {"redes": "ambas"})
        banco.definir_perfil_ativo(conn, banco.PERFIL_PRINCIPAL, False)
    janela.inicio.iniciar_automacao()
    assert not janela.inicio.em_execucao
    assert "Nenhuma conta está ativa" in dialogos.chamadas[-1][1][1]
    assert resumo(janela)["Contas ativas"].startswith("nenhuma")


# ------------------------------------------------------------ Configurações novas

def test_configuracoes_redes_extras_e_anti_bloqueio(janela, dialogos):
    aba = janela.configuracoes
    valores = {campo: aba.vars[campo].get() for campo in ("redes", "anti_bloqueio", "seguir", "ver_stories",
                                                          "repetir", "repetir_minutos", "continuar_sem_login")}
    assert valores == {"redes": "ambas", "anti_bloqueio": "nao", "seguir": "nao", "ver_stories": "nao",
                       "repetir": "nao", "repetir_minutos": "20", "continuar_sem_login": "sim"}
    assert str(aba.spin_repetir.cget("state")) == "disabled"  # só com "recomeçar" marcado

    aba.radios_redes["tiktok"].invoke()
    aba.check_anti_bloqueio.invoke()
    aba.check_seguir.invoke()
    aba.check_repetir.invoke()
    assert str(aba.spin_repetir.cget("state")) == "normal"
    aba.spin_repetir.delete(0, "end")
    for texto in ("1", "4", "4", "0", "9"):  # até 4 algarismos (1440)
        aba.spin_repetir.insert("end", texto)
    assert aba.spin_repetir.get() == "1440"
    aba.vars["repetir_minutos"].set("45")
    aba.check_sem_login.invoke()
    assert aba.tem_alteracoes()
    assert aba.salvar()

    with closing(banco.conectar()) as conn:
        salvas = banco.ler_configuracoes(conn)
    assert {chave: salvas[chave] for chave in valores} == {
        "redes": "tiktok", "anti_bloqueio": "sim", "seguir": "sim", "ver_stories": "nao", "repetir": "sim",
        "repetir_minutos": "45", "continuar_sem_login": "nao"}
    textos = resumo(janela)
    assert textos["Redes"] == "só TikTok"
    assert textos["Proteção anti-bloqueio"] == "ligada"
    assert textos["Ações extras"] == ("Seguir: sim · Stories: não · Recomeçar: 45 min após terminar · "
                                      "Conta sem login: não inicia")
    assert textos["Compartilhar com"] == "TikTok: ninguém"

    # Intervalo inválido não salva; ao desmarcar "recomeçar", volta ao último válido.
    aba.vars["repetir_minutos"].set("0")
    assert not aba.salvar() and "recomeçar" in aba.rotulo_erros.cget("text")
    aba.check_repetir.invoke()
    assert aba.vars["repetir_minutos"].get() == "45"
    assert str(aba.spin_repetir.cget("state")) == "disabled"
    aba.restaurar_padroes()
    assert aba.vars["redes"].get() == "ambas" and aba.vars["continuar_sem_login"].get() == "sim"


# ------------------------------------------------------------ tamanho mínimo

CONTROLES = {"TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TEntry", "TSpinbox", "TLabel", "Treeview",
             "TLabelframe", "TFrame", "Listbox"}


def cortados(widget):
    """Widgets (dentro de `widget`) que pedem mais espaço do que receberam."""
    problemas = []
    filhos = widget.winfo_children()
    if widget.winfo_class() == "TNotebook":  # as outras páginas não estão na tela
        filhos = [widget.nametowidget(widget.select())]
    for filho in filhos:
        if not filho.winfo_manager():
            continue  # escondido (ex.: o quadro de erros sem erros)
        if filho.winfo_class() not in CONTROLES:
            problemas += cortados(filho)
            continue
        if filho.winfo_reqwidth() > filho.winfo_width() + 1:
            problemas.append((str(filho), "largura", filho.winfo_reqwidth(), filho.winfo_width()))
        # Na altura, só a saída do processo encolhe (e o que está na área com rolagem cresce para baixo).
        if (filho.winfo_class() not in ("TLabelframe", "TFrame", "Listbox")
                and filho.winfo_reqheight() > filho.winfo_height() + 1):
            problemas.append((str(filho), "altura", filho.winfo_reqheight(), filho.winfo_height()))
        problemas += cortados(filho)
    return problemas


def test_nada_cortado_no_tamanho_minimo(janela):
    """Na janela mínima (900 x 640 sem zoom), com dados de verdade, nada fica cortado em nenhuma aba."""
    for nome in ("Loja do Centro", "Maria Aparecida de Souza"):
        criar_conta(nome)
    with closing(banco.conectar()) as conn:
        banco.salvar_configuracoes(conn, {"comentarios": '["Que publicação incrível, parabéns!", "Top", "Show"]',
                                          "repetir": "sim", "seguir": "sim", "anti_bloqueio": "sim"})
    planilhas.salvar_perfis({"instagram": ["https://www.instagram.com/fulano/"],
                             "tiktok": ["https://www.tiktok.com/@ciclano"]}, planilhas.PLANILHA_PERFIS)
    janela.inicio.atualizar_resumo()
    janela.configuracoes.carregar()
    janela.inicio.botao_parar.configure(text="Forçar parada")  # o texto mais largo do botão
    janela.inicio.rotulo_estado.configure(text="Verificação de login em execução · 00:12 · conta Maria "
                                               "Aparecida de Souza · quando terminar de logar, clique em Parar")
    largura, altura = janela.minsize()
    janela.geometry(f"{largura}x{altura}")
    sub_abas = janela.planilhas.cadernos
    paginas = [(aba, None) for aba in janela.cadernos.tabs() if aba != str(janela.planilhas)]
    paginas += [(str(janela.planilhas), sub) for sub in sub_abas.tabs()]
    for aba, sub in paginas:
        janela.cadernos.select(aba)
        if sub:
            sub_abas.select(sub)
        for _ in range(3):  # os rótulos se ajustam à largura em mais de uma passada
            janela.update()
        widget = janela.nametowidget(aba)
        assert widget.winfo_width() <= largura
        assert cortados(widget) == [], (janela.cadernos.tab(aba, "text"), sub)
    # A saída do processo continua com pelo menos 4 linhas.
    linha = interface.tkfont.nametofont("TkFixedFont", root=janela).metrics("linespace")
    assert janela.inicio.texto.winfo_height() >= 4 * linha
