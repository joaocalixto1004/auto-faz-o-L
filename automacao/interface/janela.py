#!/usr/bin/env python3
"""Interface gráfica (programa de janela, não página web) da automação, feita com Tkinter.

Junta numa janela o que antes era feito no terminal:
- Início: situação dos logins de cada conta e da configuração, botões para
  fazer login / verificar uma conta (automacao.abrir_redes --perfil) e para
  iniciar a automação com todas as contas ativas (automacao.interagir), e a
  saída do processo em tempo real;
- Contas: as contas (perfis do navegador, cada um com um login do Instagram e
  um do TikTok): adicionar, fazer login, ativar/desativar, renomear e remover;
- Configurações: os mesmos valores da página do configuracao.py, mais as
  redes em que agir e as opções extras;
- Planilhas: os perfis a acessar e com quem compartilhar (pasta data/).

Uso (na raiz do projeto; ou com dois cliques no Iniciar.bat / iniciar.sh):
    .venv/bin/python -m automacao.interface          (Linux)
    .venv\\Scripts\\python -m automacao.interface      (Windows)
"""

import atexit
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from automacao import banco, planilhas
from automacao.caminhos import RAIZ
from automacao.interface import dados
from automacao.interface.aba_configuracoes import AbaConfiguracoes
from automacao.interface.aba_contas import AbaContas
from automacao.interface.aba_planilhas import AbaPlanilhas
from automacao.interface.componentes import largura_do_texto, quebrar_na_largura
from automacao.interface.processo import (MODULO_AUTOMACAO, MODULO_LOGIN, Processo, argumentos_automacao,
                                          argumentos_login, montar_comando, nome_do_alvo, travar_interface)
from automacao.terminal import preparar_console

TITULO = "Automação das redes sociais"
NOMES_PROCESSOS = {"login": "Verificação de login", "automacao": "Automação"}
ROTULOS_RESUMO = ("Redes", "Contas ativas", "Publicações", "Comentários", "Ações extras", "Limite diário",
                  "Proteção anti-bloqueio", "Perfis a acessar", "Compartilhar com")
INTERVALO_LEITURA_MS = 100   # de quanto em quanto tempo a saída do processo é lida
LINHAS_POR_LEITURA = 500
MAXIMO_LINHAS_SAIDA = 5000   # linhas mais antigas saem do registro
LINHAS_MINIMAS_SAIDA = 4     # a saída do processo nunca fica menor que isso (entra no tamanho mínimo)
ESPERA_LIMPEZA = 15.0        # segundos, no máximo, esperando os restos (Chrome, driver) ao sair
JA_ABERTA = "A janela da automação já está aberta."

CORES = {"ok": "#1d7a3f", "erro": "#b3261e", "aviso": "#9a6700", "suave": "#5b6472",
         "sistema": "#2f5bea"}


def configurar_estilos(raiz: tk.Tk) -> dict[str, tkfont.Font]:
    """Tema, cores e fontes. Devolve as fontes criadas (é preciso guardar a referência)."""
    estilo = ttk.Style(raiz)
    temas = estilo.theme_names()
    if "vista" not in temas and "aqua" not in temas and "clam" in temas:
        estilo.theme_use("clam")  # o tema padrão do Linux é bem antigo

    base = tkfont.nametofont("TkDefaultFont", root=raiz)
    tamanho = base.cget("size")  # negativo quando é em pixels
    fontes = {"titulo": base.copy(), "forte": base.copy(),
              "fixa_forte": tkfont.nametofont("TkFixedFont", root=raiz).copy()}
    fontes["titulo"].configure(weight="bold", size=tamanho + 3 if tamanho > 0 else tamanho - 4)
    fontes["forte"].configure(weight="bold")
    fontes["fixa_forte"].configure(weight="bold")

    estilo.configure("Titulo.TLabel", font=fontes["titulo"])
    estilo.configure("Forte.TLabel", font=fontes["forte"])
    estilo.configure("Destaque.TButton", font=fontes["forte"])
    for nome, cor in CORES.items():
        estilo.configure(f"{nome.capitalize()}.TLabel", foreground=cor)
    # Linhas das tabelas na altura da fonte (com zoom de tela, a altura fixa cortaria o texto).
    try:
        altura_linha = int(estilo.lookup("Treeview", "rowheight") or 0)
    except (ValueError, tk.TclError):
        altura_linha = 0
    estilo.configure("Treeview", rowheight=max(altura_linha, base.metrics("linespace") + 4))
    return fontes


class AbaInicio(ttk.Frame):
    """Resumo das contas e da configuração, botões dos scripts e saída do processo.

    Também é a dona da situação das contas (`contas`, relida do banco e
    atualizada ao vivo pela saída do processo): a aba Contas se inscreve em
    `observadores` para ser redesenhada quando ela muda.
    """

    def __init__(self, master: tk.Misc, scripts: dict[str, str | Path],
                 antes_da_automacao: Callable[[], bool], ir_para: Callable[[str], None]) -> None:
        super().__init__(master, padding=12)
        self.scripts = scripts
        self.antes_da_automacao = antes_da_automacao
        self.ir_para = ir_para
        self.ao_terminar: Callable[[], None] | None = None  # chamada quando o processo acabar
        self.processo: Processo | None = None
        self.tipo: str | None = None
        self.em_execucao = False
        self.contas: list[dados.SituacaoConta] = []
        self.configuracoes: dict[str, str] = dict(banco.CONFIG_PADRAO)
        self.resumo_alvos: dados.ResumoPerfis | None = None
        self.perfil_login_id = banco.PERFIL_PRINCIPAL  # conta do botão de login (e a selecionada na aba Contas)
        self.perfil_em_uso: int | None = None          # conta que o processo está usando agora
        self.aguardando: str | None = None             # a automação espera para recomeçar a lista
        self.observadores: list[Callable[[], None]] = []
        self._ids_por_nome: dict[str, int] = {}
        self._contas_ao_vivo: set[int] = set()        # contas que a saída do processo atual já atualizou
        self._pausas: list[dict] = []                  # pausas por aviso da rede (banco.pausas_ativas)
        self._texto_pausas = ""
        self._tarefas: dict[str, str] = {}
        self._destruida = False
        self._montar()
        self.atualizar_resumo()
        self._agendar("leitura", INTERVALO_LEITURA_MS, self._ler_saida)
        self._agendar("relogio", 1000, self._relogio)

    # ------------------------------------------------------------ montagem

    def _montar(self) -> None:
        # Colunas proporcionais (uniform): a tabela de contas fica com 4/9 da largura.
        self.columnconfigure(0, weight=4, uniform="topo")
        self.columnconfigure(1, weight=5, uniform="topo")
        self.rowconfigure(2, weight=1)

        # Coluna da esquerda: a tabela de contas e os atalhos (a da direita é a mais alta).
        esquerda = ttk.Frame(self)
        esquerda.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        esquerda.columnconfigure(0, weight=1)
        esquerda.rowconfigure(0, weight=1)
        contas = ttk.LabelFrame(esquerda, text="Contas", padding=10)
        contas.grid(row=0, column=0, sticky="nsew")
        contas.columnconfigure(0, weight=1)
        contas.rowconfigure(0, weight=1)
        colunas = ("conta", *dados.REDES_AUTOMACAO)
        self.arvore_contas = ttk.Treeview(contas, columns=colunas, show="headings", height=4,
                                          selectmode="browse")
        for coluna, titulo, exemplo in (("conta", "Conta", "Principal (inativa)"),
                                        *((rede, rede, "nunca verificado") for rede in dados.REDES_AUTOMACAO)):
            self.arvore_contas.heading(coluna, text=titulo, anchor="w")
            self.arvore_contas.column(coluna, width=largura_do_texto(self, exemplo, folga=6), minwidth=50,
                                      stretch=True, anchor="w")
        self.arvore_contas.tag_configure("inativa", foreground=CORES["suave"])
        self.arvore_contas.tag_configure("em_uso", foreground=CORES["sistema"])
        self.arvore_contas.grid(row=0, column=0, sticky="nsew")
        barra_contas = ttk.Scrollbar(contas, orient="vertical", command=self.arvore_contas.yview)
        barra_contas.grid(row=0, column=1, sticky="ns")
        self.arvore_contas.configure(yscrollcommand=barra_contas.set)
        self.arvore_contas.bind("<<TreeviewSelect>>", self._ao_selecionar_na_tabela)
        botoes_contas = ttk.Frame(contas)
        botoes_contas.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(botoes_contas, text="Gerenciar contas", command=lambda: self.ir_para("contas")).pack(side="left")
        ttk.Button(botoes_contas, text="Atualizar", command=self.atualizar_resumo).pack(side="left", padx=(6, 0))
        atalhos = ttk.Frame(esquerda)
        atalhos.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(atalhos, text="Editar configurações",
                   command=lambda: self.ir_para("configuracoes")).pack(side="left")
        ttk.Button(atalhos, text="Editar planilhas",
                   command=lambda: self.ir_para("planilhas")).pack(side="left", padx=(6, 0))
        # Pausas por aviso da rede (valem também para as próximas execuções); só aparece se houver.
        self.rotulo_pausas = quebrar_na_largura(ttk.Label(esquerda, style="Aviso.TLabel", wraplength=300,
                                                          justify="left"))
        self.rotulo_pausas.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.rotulo_pausas.grid_remove()

        resumo = ttk.LabelFrame(self, text="Configuração atual", padding=10)
        resumo.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        resumo.columnconfigure(1, weight=1)
        self._rotulos_resumo = {}
        for linha, rotulo in enumerate(ROTULOS_RESUMO):
            ttk.Label(resumo, text=f"{rotulo}:", style="Forte.TLabel").grid(row=linha, column=0, sticky="nw",
                                                                             padx=(0, 8), pady=1)
            valor = quebrar_na_largura(ttk.Label(resumo, wraplength=220, justify="left"))
            valor.grid(row=linha, column=1, sticky="ew", pady=1)
            self._rotulos_resumo[rotulo] = valor

        acoes = ttk.LabelFrame(self, text="Ações", padding=(10, 6, 10, 8))
        acoes.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8)
        acoes.columnconfigure(5, weight=1)
        # O login é de uma conta só (a escolhida aqui ou na aba Contas); a automação usa todas as ativas.
        ttk.Label(acoes, text="Conta:").grid(row=0, column=0, padx=(0, 4))
        self.combo_conta = ttk.Combobox(acoes, state="readonly", width=14)
        self.combo_conta.grid(row=0, column=1)
        self.combo_conta.bind("<<ComboboxSelected>>", self._ao_escolher_no_combo)
        self.botao_login = ttk.Button(acoes, text="Fazer login / verificar contas", command=self.iniciar_login)
        self.botao_login.grid(row=0, column=2, padx=(6, 0))
        ttk.Separator(acoes, orient="vertical").grid(row=0, column=3, sticky="ns", padx=12)
        self.botao_automacao = ttk.Button(acoes, text="Iniciar automação", style="Destaque.TButton",
                                          command=self.iniciar_automacao)
        self.botao_automacao.grid(row=0, column=4)
        self.var_simular = tk.BooleanVar(self, value=False)
        self.var_trace = tk.BooleanVar(self, value=False)
        self.botao_parar = ttk.Button(acoes, text="Parar", command=self.parar, state="disabled")
        self.botao_parar.grid(row=0, column=6, sticky="e")

        # Opções da automação numa linha própria, para os botões nunca serem cortados.
        opcoes = ttk.Frame(acoes)
        opcoes.grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))
        self.check_simular = ttk.Checkbutton(opcoes, text="Simular (não clica em nada)", variable=self.var_simular)
        self.check_simular.pack(side="left")
        self.check_trace = ttk.Checkbutton(opcoes, text="Gravar trace", variable=self.var_trace)
        self.check_trace.pack(side="left", padx=(14, 0))
        self.var_verificar = tk.BooleanVar(self, value=False)
        self.check_verificar = ttk.Checkbutton(
            opcoes, text="Modo teste: não comentar onde já comentei", variable=self.var_verificar)
        self.check_verificar.pack(side="left", padx=(14, 0))

        saida = ttk.LabelFrame(self, text="Saída do processo", padding=(10, 6))
        saida.grid(row=2, column=0, columnspan=2, sticky="nsew")
        saida.columnconfigure(0, weight=1)
        saida.rowconfigure(1, weight=1)
        # O estado do processo fica na linha do "Limpar", para sobrar mais altura para a saída.
        self.rotulo_estado = quebrar_na_largura(ttk.Label(saida, text="Nada em execução.", style="Suave.TLabel",
                                                          wraplength=500, justify="left"))
        self.rotulo_estado.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 4))
        ttk.Button(saida, text="Limpar", command=self.limpar_saida).grid(row=0, column=1, sticky="e", pady=(0, 4))
        # A altura pedida é a mínima; a saída cresce com a janela (e é ela que encolhe primeiro).
        self.texto = tk.Text(saida, height=LINHAS_MINIMAS_SAIDA, wrap="word", font="TkFixedFont", undo=False,
                             background="#fbfbfc", foreground="#1d2129", relief="flat",
                             borderwidth=1, highlightthickness=1, highlightcolor="#c5cad3",
                             highlightbackground="#d9dde3", padx=6, pady=4)
        self.texto.grid(row=1, column=0, sticky="nsew")
        barra = ttk.Scrollbar(saida, orient="vertical", command=self.texto.yview)
        barra.grid(row=1, column=1, sticky="ns")
        self.texto.configure(yscrollcommand=barra.set)
        for nome, cor in CORES.items():
            self.texto.tag_configure(nome, foreground=cor)
        self.texto.tag_configure("sistema", foreground=CORES["sistema"],
                                 font=getattr(self.winfo_toplevel(), "fontes", {}).get("fixa_forte",
                                                                                       "TkFixedFont"))
        _somente_leitura(self.texto)

    # ------------------------------------------------------------ resumo

    def atualizar_resumo(self) -> None:
        """Relê o banco (contas, configuração e pausas) e as planilhas e mostra a situação atual.

        Enquanto o login ou a automação rodam, a situação das contas que eles já
        mostraram vem da saída deles (ao vivo) e não é relida do banco.
        """
        try:
            with closing(banco.conectar()) as conn:
                contas = dados.situacao_contas(conn)
                configuracoes = banco.ler_configuracoes(conn)
                self._pausas = [dict(pausa) for pausa in banco.pausas_ativas(conn)]
        except sqlite3.Error as erro:
            contas, configuracoes = None, dict(banco.CONFIG_PADRAO)
            self.escrever(f"Erro ao ler o banco de dados: {erro}", "erro")
        self.configuracoes = configuracoes
        self._mostrar_pausas()
        for rotulo, texto in dados.resumo_configuracao(configuracoes):
            self._rotulos_resumo[rotulo].configure(text=texto)
        self._mostrar_perfis()
        self._mostrar_destinatarios(configuracoes.get("compartilhar_com", ""))
        if contas is not None:
            self._definir_contas(contas)
        else:
            self._mostrar_contas()

    def atualizar_contas(self) -> None:
        """Relê só as contas e os logins (ao abrir a aba Contas ou depois de mudar uma conta)."""
        try:
            with closing(banco.conectar()) as conn:
                contas = dados.situacao_contas(conn)
        except sqlite3.Error as erro:
            self.escrever(f"Erro ao ler o banco de dados: {erro}", "erro")
            return
        self._definir_contas(contas)

    def _definir_contas(self, contas: list[dados.SituacaoConta]) -> None:
        if self.em_execucao:
            # A saída do processo (login ou automação) vale mais que o banco para as
            # contas que ela já atualizou: a automação não grava os logins no banco.
            for perfil_id in self._contas_ao_vivo:
                ao_vivo = dados.achar_conta(self.contas, perfil_id)
                nova = dados.achar_conta(contas, perfil_id)
                if ao_vivo is not None and nova is not None:
                    nova.logins = ao_vivo.logins
        self.contas = contas
        if contas and dados.achar_conta(contas, self.perfil_login_id) is None:
            self.perfil_login_id = banco.PERFIL_PRINCIPAL  # a conta escolhida foi removida
        self._mostrar_contas()

    def _mostrar_contas(self) -> None:
        """Redesenha a tabela de contas, a conta do login e o resumo das ativas; avisa a aba Contas."""
        arvore = self.arvore_contas
        arvore.delete(*arvore.get_children())
        for conta in self.contas:
            nome, tags = conta.nome, ()
            if self.em_execucao and conta.id == self.perfil_em_uso:
                nome, tags = f"▶ {nome}", ("em_uso",)
            elif not conta.ativo:
                nome, tags = f"{nome} (inativa)", ("inativa",)
            arvore.insert("", "end", iid=str(conta.id), tags=tags, values=(
                nome, *(dados.texto_login_curto(conta.login(rede)) for rede in dados.REDES_AUTOMACAO)))
        self._mostrar_conta_do_login()
        texto, estilo = dados.resumo_contas_ativas(self.contas)
        self._rotulos_resumo["Contas ativas"].configure(text=texto,
                                                        style="Erro.TLabel" if estilo == "erro" else "TLabel")
        self._avisar()

    def _mostrar_conta_do_login(self) -> None:
        self.combo_conta.configure(values=[conta.nome for conta in self.contas])
        conta = dados.achar_conta(self.contas, self.perfil_login_id)
        self.combo_conta.set(conta.nome if conta else "")
        iid = str(self.perfil_login_id)
        if self.arvore_contas.exists(iid) and self.arvore_contas.selection() != (iid,):
            self.arvore_contas.selection_set(iid)

    def escolher_conta_login(self, perfil_id: int) -> None:
        """Escolhe a conta do botão "Fazer login / verificar contas" (é a mesma selecionada na aba Contas)."""
        if perfil_id == self.perfil_login_id or dados.achar_conta(self.contas, perfil_id) is None:
            return
        self.perfil_login_id = perfil_id
        self._mostrar_conta_do_login()
        self._avisar()

    def _ao_escolher_no_combo(self, _evento=None) -> None:
        indice = self.combo_conta.current()
        if 0 <= indice < len(self.contas):
            self.escolher_conta_login(self.contas[indice].id)

    def _ao_selecionar_na_tabela(self, _evento=None) -> None:
        selecao = self.arvore_contas.selection()
        if selecao:
            self.escolher_conta_login(int(selecao[0]))

    def _avisar(self) -> None:
        for observador in list(self.observadores):
            observador()

    def linha_da_conta(self, perfil_id: int) -> tuple[str, ...]:
        """O que a tabela de contas mostra para a conta (conta, Instagram, TikTok)."""
        return tuple(self.arvore_contas.item(str(perfil_id), "values"))

    def _mostrar_perfis(self) -> None:
        rotulo = self._rotulos_resumo["Perfis a acessar"]
        self.resumo_alvos = None
        try:
            resumo = dados.resumo_perfis()
        except Exception as erro:  # noqa: BLE001 - planilha corrompida etc.
            rotulo.configure(text=f"erro ao ler a planilha: {erro}", style="Erro.TLabel")
            return
        self.resumo_alvos = resumo
        if not resumo.existe:
            rotulo.configure(text=f"{planilhas.PLANILHA_PERFIS.name} não existe (crie na aba Planilhas)",
                             style="Aviso.TLabel")
            return
        redes = banco.redes_escolhidas(self.configuracoes)
        texto = resumo.texto(redes)
        estilo = "TLabel" if resumo.nas_redes(redes) else "Aviso.TLabel"
        if resumo.invalidos:
            # Como a automação faz: ela pula essas linhas (com um aviso) e segue com as outras.
            texto += "\n" + dados.texto_links_invalidos(resumo.invalidos, mostrar=2)
            estilo = "Aviso.TLabel"
        rotulo.configure(text=texto, style=estilo)

    def _mostrar_pausas(self) -> None:
        """Mostra as pausas por aviso da rede que ainda valem (some quando não há nenhuma)."""
        texto = dados.texto_pausas(self._pausas)
        if texto == self._texto_pausas:
            return
        self._texto_pausas = texto
        self.rotulo_pausas.configure(text=texto)
        if texto:
            self.rotulo_pausas.grid()
        else:
            self.rotulo_pausas.grid_remove()

    def _mostrar_destinatarios(self, compartilhar_com: str) -> None:
        rotulo = self._rotulos_resumo["Compartilhar com"]
        try:
            resumo = dados.resumo_destinatarios(compartilhar_com)
        except Exception as erro:  # noqa: BLE001
            rotulo.configure(text=f"erro ao ler a planilha: {erro}", style="Erro.TLabel")
            return
        partes = []
        redes = banco.redes_escolhidas(self.configuracoes)
        for rede, (usuarios, da_configuracao) in resumo.items():
            if rede not in redes:
                continue  # a automação não age nessa rede
            nomes = ", ".join(f"@{u}" for u in usuarios) or "ninguém"
            partes.append(f"{rede}: {nomes}" + (" (da configuração)" if da_configuracao else ""))
        rotulo.configure(text="\n".join(partes), style="TLabel")

    # ------------------------------------------------------------ processos

    def iniciar_login(self, perfil_id: int | None = None) -> bool:
        """Abre o navegador numa conta para logar ou verificar: `perfil_id` ou a escolhida. True se iniciou."""
        if self.em_execucao:
            return False
        if perfil_id is None:
            perfil_id = self.perfil_login_id
        else:
            self.escolher_conta_login(perfil_id)
        return self._iniciar("login", argumentos_login(perfil_id), perfil_id=perfil_id)

    def iniciar_automacao(self) -> None:
        if self.em_execucao or not self.antes_da_automacao():
            return
        self.atualizar_resumo()  # vale o que está salvo agora (contas, redes, planilhas)
        resumo = self.resumo_alvos  # None: planilha com erro; a própria automação vai mostrar
        redes = banco.redes_escolhidas(self.configuracoes)
        nome = planilhas.PLANILHA_PERFIS.name
        if resumo is not None and not resumo.nas_redes(redes):
            if resumo.total:
                partes = [f"Redes escolhidas nas Configurações: {dados.texto_redes(self.configuracoes)}.\n"
                          f"Não há perfis dessas redes para visitar em {nome}."]
            elif resumo.invalidos:
                partes = [f"Nenhum link de perfil válido em {nome}: a automação não teria o que visitar."]
            else:
                partes = [f"Não há perfis para visitar em {nome}."]
            if resumo.invalidos:
                partes.append(dados.texto_links_invalidos(resumo.invalidos)
                              + ". Use o link do perfil (não o de uma publicação).")
            partes.append("Adicione ou corrija os perfis na aba Planilhas"
                          + (" ou troque as redes na aba Configurações." if resumo.total else "."))
            messagebox.showwarning("Iniciar automação", "\n".join(partes), parent=self)
            return
        if self.contas and not any(conta.ativo for conta in self.contas):
            messagebox.showwarning("Iniciar automação", "Nenhuma conta está ativa.\n"
                                   "Ative pelo menos uma conta na aba Contas.", parent=self)
            return
        if resumo is not None and resumo.invalidos and not messagebox.askyesno(
                "Iniciar automação",
                f"{dados.texto_links_invalidos(resumo.invalidos)} (a automação pula essas linhas de {nome} "
                "e visita os outros perfis).\n\nIniciar mesmo assim?", parent=self):
            return
        self._iniciar("automacao", argumentos_automacao(self.var_simular.get(), self.var_trace.get(),
                                                        self.var_verificar.get()))

    def _iniciar(self, tipo: str, argumentos: list[str], perfil_id: int | None = None) -> bool:
        """Inicia o processo. `perfil_id`: a conta que ele usa desde o começo (o login). True se iniciou."""
        if self.em_execucao:
            return False
        script = self.scripts[tipo]
        nome = nome_do_alvo(script)
        processo = Processo(montar_comando(script, argumentos), cwd=RAIZ)
        try:
            processo.iniciar()
        except OSError as erro:
            messagebox.showerror(NOMES_PROCESSOS[tipo], f"Não consegui iniciar {nome}:\n{erro}",
                                 parent=self)
            return False
        self.processo, self.tipo, self.em_execucao = processo, tipo, True
        self.perfil_em_uso, self.aguardando = perfil_id, None
        self._contas_ao_vivo = set()
        # Os nomes de agora: se uma conta for renomeada no meio, o que o processo imprime continua valendo.
        self._ids_por_nome = {dados.chave_nome(conta.nome): conta.id for conta in self.contas}
        conta = dados.achar_conta(self.contas, perfil_id)
        titulo = NOMES_PROCESSOS[tipo] + (f" (conta {conta.nome})" if conta else "")
        self.escrever(f"▶ {titulo}: {' '.join([nome, *argumentos])}", "sistema")
        if tipo == "login" and conta is not None:
            dados.marcar_verificando(conta)
            self._contas_ao_vivo.add(conta.id)
        self._mostrar_contas()
        self._atualizar_controles()
        return True

    def parar(self) -> None:
        """Primeiro clique: para com calma (como Ctrl+C). Segundo clique: força."""
        if not self.em_execucao or self.processo is None:
            return
        if self.processo.parada_pedida:
            self.escrever("■ Forçando a parada…", "sistema")
            self.processo.parar(forcar=True)
        else:
            self.escrever("■ Parando… (o navegador será fechado)", "sistema")
            self.processo.parar()
        self._atualizar_controles()

    def matar_processo(self) -> None:
        """Mata o processo na hora e espera os restos fecharem (bloqueia; usado ao sair do programa)."""
        if self.processo is not None:
            _matar_e_esperar(self.processo)

    def _ler_saida(self) -> None:
        """Lê o que chegou da saída do processo: escreve tudo de uma vez e redesenha uma vez só."""
        try:
            processo = self.processo
            if processo is None:
                return
            linhas: list[tuple[str, str | None]] = []
            redesenhar, fim = False, None
            for _ in range(LINHAS_POR_LEITURA):
                try:
                    tipo, valor = processo.saida.get_nowait()
                except queue.Empty:
                    break
                if tipo == "linha":
                    linhas.append((valor, dados.estilo_da_linha(valor)))
                    redesenhar |= self._tratar_linha(valor)
                elif tipo == "fim":
                    fim = valor
                    break
            if linhas:
                self.escrever_varias(linhas)
            if redesenhar:
                self._mostrar_contas()
                self._relogio(reagendar=False)
            if fim is not None and self.em_execucao and processo is self.processo:
                self._processo_terminou(fim)
        finally:
            if not self._destruida:
                self._agendar("leitura", INTERVALO_LEITURA_MS, self._ler_saida)

    def _tratar_linha(self, linha: str) -> bool:
        """Aplica a linha ao estado (conta em uso, logins, espera). True se é preciso redesenhar."""
        espera = dados.espera_recomeco(linha)
        if espera is not None:
            self.aguardando = espera
            return True
        trocou_de_conta = dados.perfil_da_linha(linha) is not None
        redesenhar = False
        if trocou_de_conta and self.aguardando:
            self.aguardando = None  # começou a próxima volta da lista
            redesenhar = True
        em_uso, mudou = dados.tratar_linha_de_contas(self.contas, self.perfil_em_uso, linha, self._ids_por_nome)
        if mudou:
            if not trocou_de_conta and em_uso is not None:
                self._contas_ao_vivo.add(em_uso)  # a saída atualizou os logins desta conta
            self.perfil_em_uso = em_uso
            redesenhar = True
        return redesenhar

    def _processo_terminou(self, codigo: int) -> None:
        processo = self.processo
        duracao = _duracao(time.monotonic() - (processo.iniciado_em or time.monotonic()))
        nome = NOMES_PROCESSOS.get(self.tipo, "Processo")
        if processo.parada_pedida:
            self.escrever(f"■ {nome} parada pelo usuário ({duracao}).", "sistema")
        elif codigo == 0:
            self.escrever(f"■ {nome} terminou ({duracao}).", "sistema")
        else:
            self.escrever(f"■ {nome} terminou com erro (código {codigo}, {duracao}).", "erro")
        # O "fim" só chega depois que os restos (Chrome, driver) foram encerrados.
        self.processo = None
        self.em_execucao = False
        self.perfil_em_uso, self.aguardando = None, None
        self._contas_ao_vivo = set()
        self.atualizar_resumo()
        self._atualizar_controles()
        if self.ao_terminar:
            self.ao_terminar()  # pode destruir a janela: nada de widgets depois daqui

    def _atualizar_controles(self) -> None:
        livre = "disabled" if self.em_execucao else "normal"
        for widget in (self.botao_login, self.botao_automacao, self.check_simular, self.check_trace,
                       self.check_verificar):
            widget.configure(state=livre)
        self.combo_conta.configure(state="disabled" if self.em_execucao else "readonly")
        forcar = self.em_execucao and self.processo is not None and self.processo.parada_pedida
        self.botao_parar.configure(state="normal" if self.em_execucao else "disabled",
                                   text="Forçar parada" if forcar else "Parar")
        self._relogio(reagendar=False)
        self._avisar()

    def _relogio(self, reagendar: bool = True) -> None:
        if self.em_execucao and self.processo is not None:
            partes = [f"{NOMES_PROCESSOS[self.tipo]} em execução",
                      _duracao(time.monotonic() - self.processo.iniciado_em)]
            conta = dados.achar_conta(self.contas, self.perfil_em_uso)
            if conta is not None and not self.aguardando:
                partes.append(f"conta {conta.nome}")
            if self.processo.parada_pedida:
                partes.append("parando…")
            elif self.aguardando:
                partes.append(self.aguardando if "Parar" in self.aguardando
                              else f"{self.aguardando} (para encerrar, clique em Parar)")
            elif self.tipo == "login":
                partes.append("quando terminar de logar, clique em Parar (fechar o Chrome faz ele reabrir)")
            self.rotulo_estado.configure(text=" · ".join(partes), style="Sistema.TLabel")
        else:
            self.rotulo_estado.configure(text="Nada em execução.", style="Suave.TLabel")
        self._mostrar_pausas()  # tira as que acabaram
        if reagendar and not self._destruida:
            self._agendar("relogio", 1000, self._relogio)

    # ------------------------------------------------------------ registro da saída

    def escrever(self, linha: str, estilo: str | None = None) -> None:
        """Acrescenta uma linha ao registro, rolando junto só se ele já estava no fim."""
        self.escrever_varias([(linha, estilo)])

    def escrever_varias(self, linhas: list[tuple[str, str | None]]) -> None:
        """Acrescenta várias linhas (texto, estilo) de uma vez: uma inserção e um corte só."""
        if not linhas:
            return
        no_fim = self.texto.yview()[1] >= 0.999
        partes: list[object] = []
        for linha, estilo in linhas[-MAXIMO_LINHAS_SAIDA:]:
            partes += [linha + "\n", (estilo,) if estilo else ()]
        self.texto.insert("end", *partes)
        # Cada linha termina em "\n": "end-1c" fica no começo da linha vazia depois da última.
        excesso = int(self.texto.index("end-1c").split(".")[0]) - 1 - MAXIMO_LINHAS_SAIDA
        if excesso > 0:
            self.texto.delete("1.0", f"{excesso + 1}.0")
        if no_fim:
            self.texto.see("end")

    def limpar_saida(self) -> None:
        self.texto.delete("1.0", "end")

    def texto_da_saida(self) -> str:
        return self.texto.get("1.0", "end-1c")

    # ------------------------------------------------------------ ciclo de vida

    def _agendar(self, nome: str, ms: int, funcao: Callable[[], None]) -> None:
        self._tarefas[nome] = self.after(ms, funcao)

    def destroy(self) -> None:
        self._destruida = True
        for tarefa in self._tarefas.values():
            try:
                self.after_cancel(tarefa)
            except tk.TclError:
                pass
        self._tarefas.clear()
        processo = self.processo
        if processo is not None and not processo.esperar_limpeza(0):
            # Sem travar a janela: uma thread comum (não daemon) mata e espera os restos,
            # e o Python só sai depois que ela terminar (nada de Chrome órfão).
            threading.Thread(target=_matar_e_esperar, args=(processo,), name="encerrar-processo",
                             daemon=False).start()
        super().destroy()


class Aplicacao(tk.Tk):
    """Janela principal.

    O login e a automação são módulos do projeto (nome do módulo, rodado com
    "python -m") e podem ser trocados por scripts .py (os testes usam scripts falsos).
    """

    def __init__(self, script_login: str | Path = MODULO_LOGIN,
                 script_automacao: str | Path = MODULO_AUTOMACAO) -> None:
        super().__init__(className="automacao")
        self.title(TITULO)
        self.fontes = configurar_estilos(self)
        self._fechando = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.cadernos = ttk.Notebook(self)
        self.cadernos.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.inicio = AbaInicio(self.cadernos, {"login": script_login, "automacao": script_automacao},
                                antes_da_automacao=self._confirmar_antes_da_automacao, ir_para=self.ir_para)
        self.contas = AbaContas(self.cadernos, inicio=self.inicio, ir_para=self.ir_para)
        self.configuracoes = AbaConfiguracoes(self.cadernos, ao_salvar=self.inicio.atualizar_resumo)
        self.planilhas = AbaPlanilhas(self.cadernos, ao_salvar=self.inicio.atualizar_resumo)
        self.cadernos.add(self.inicio, text="Início")
        self.cadernos.add(self.contas, text="Contas")
        self.cadernos.add(self.configuracoes, text="Configurações")
        self.cadernos.add(self.planilhas, text="Planilhas")
        self.cadernos.bind("<<NotebookTabChanged>>", self._ao_trocar_de_aba)
        self._tamanho_inicial()
        # As medidas do conteúdo só valem depois que o Tk arruma os widgets (ao ficar ocioso).
        self._tarefa_tamanho: str | None = self.after_idle(self._ajustar_tamanho_minimo)

        self.protocol("WM_DELETE_WINDOW", self.ao_fechar)
        atexit.register(self.inicio.matar_processo)  # rede de segurança se o Python sair de outro jeito

    def _fator_zoom(self) -> float:
        return max(1.0, self.winfo_fpixels("1i") / 96.0)

    def _tela(self) -> tuple[int, int]:
        """O espaço que a janela pode ocupar (a tela menos a barra de tarefas)."""
        return self.winfo_screenwidth() - 40, self.winfo_screenheight() - 80

    def _tamanho_inicial(self) -> None:
        """Tamanho de partida: em telas com zoom (125%, 150%...), as fontes crescem e a janela acompanha."""
        fator = self._fator_zoom()
        tela_largura, tela_altura = self._tela()
        self._geometria = (min(int(1040 * fator), tela_largura), min(int(760 * fator), tela_altura))
        self.geometry("{}x{}".format(*self._geometria))
        self.minsize(min(int(900 * fator), self._geometria[0]), min(int(640 * fator), self._geometria[1]))

    def _ajustar_tamanho_minimo(self) -> None:
        """Mínimo pelo que o conteúdo pede (roda uma vez, quando o Tk fica ocioso pela primeira vez).

        Com zoom, as fontes crescem mais que as margens (em pixels) e o texto
        quebra em mais linhas: o mínimo é o maior entre o de sempre (900 x 640,
        com o zoom) e o que a aba Início pede com a saída do processo em
        LINHAS_MINIMAS_SAIDA linhas. As outras abas rolam ou encolhem as listas;
        na largura, valem todas. Nunca maior que a tela.
        """
        self._tarefa_tamanho = None
        try:
            self.update_idletasks()
            fator = self._fator_zoom()
            abas = [self.nametowidget(aba) for aba in self.cadernos.tabs()]
            sobra = self.winfo_reqheight() - max(aba.winfo_reqheight() for aba in abas)  # abas, margens
            tela_largura, tela_altura = self._tela()
            largura_minima = min(max(int(900 * fator), self.winfo_reqwidth()), tela_largura)
            altura_minima = min(max(int(640 * fator), sobra + self.inicio.winfo_reqheight()), tela_altura)
            self.minsize(largura_minima, altura_minima)
            largura, altura = self._geometria
            if largura < largura_minima or altura < altura_minima:
                self._geometria = (max(largura, largura_minima), max(altura, altura_minima))
                self.geometry("{}x{}".format(*self._geometria))
        except tk.TclError:
            pass  # a janela fechou antes

    def destroy(self) -> None:
        atexit.unregister(self.inicio.matar_processo)
        if self._tarefa_tamanho is not None:
            try:
                self.after_cancel(self._tarefa_tamanho)
            except tk.TclError:
                pass
            self._tarefa_tamanho = None
        super().destroy()  # a AbaInicio mata o processo, se ainda houver um

    def ir_para(self, aba: str) -> None:
        self.cadernos.select({"inicio": self.inicio, "contas": self.contas, "configuracoes": self.configuracoes,
                              "planilhas": self.planilhas}[aba])

    def _ao_trocar_de_aba(self, _evento=None) -> None:
        atual = self.cadernos.select()
        if atual == str(self.planilhas):
            self.planilhas.recarregar_se_mudou_fora()
        elif atual == str(self.inicio):
            self.inicio.atualizar_resumo()
        elif atual == str(self.contas):
            self.inicio.atualizar_contas()

    # ------------------------------------------------------------ alterações pendentes

    def _pendencias(self) -> list[str]:
        nomes = []
        if self.configuracoes.tem_alteracoes():
            nomes.append("Configurações")
        if self.planilhas.tem_alteracoes():
            nomes.append("Planilhas")
        return nomes

    def _salvar_pendencias(self) -> bool:
        if self.configuracoes.tem_alteracoes() and not self.configuracoes.salvar():
            self.ir_para("configuracoes")
            return False
        if self.planilhas.tem_alteracoes() and not self.planilhas.salvar():
            self.ir_para("planilhas")
            return False
        return True

    def _confirmar_pendencias(self, titulo: str, pergunta: str, se_nao: str) -> bool:
        """Pergunta o que fazer com o que não foi salvo. False se o usuário cancelou."""
        pendencias = self._pendencias()
        if not pendencias:
            return True
        resposta = messagebox.askyesnocancel(
            titulo, f"Há alterações não salvas em {' e '.join(pendencias)}.\n\n{pergunta}\n"
                    f"(Não: {se_nao})", parent=self)
        if resposta is None:
            return False
        return self._salvar_pendencias() if resposta else True

    def _confirmar_antes_da_automacao(self) -> bool:
        return self._confirmar_pendencias("Iniciar automação", "Salvar antes de iniciar?",
                                          "a automação usa o que já está salvo.")

    # ------------------------------------------------------------ saída

    def ao_fechar(self) -> None:
        if self._fechando:  # segundo pedido enquanto o processo encerra: força
            self.inicio.parar()
            return
        if self.inicio.em_execucao:
            nome = NOMES_PROCESSOS.get(self.inicio.tipo, "Um processo")
            if not messagebox.askyesno(TITULO, f"{nome} ainda está em execução.\n"
                                               "Parar o processo (fechando o navegador) e sair?", parent=self):
                return
        if not self._confirmar_pendencias(TITULO, "Salvar antes de sair?", "as alterações são descartadas."):
            return
        if self.inicio.em_execucao:
            # A janela some na hora, mas o programa continua (escondido, sem travar)
            # até o processo parar e os restos (Chrome, driver) serem encerrados.
            self._fechando = True
            self.title(f"{TITULO} — encerrando…")
            self.withdraw()
            self.inicio.ao_terminar = self.destroy
            self.inicio.parar()
            return
        self.destroy()


def _matar_e_esperar(processo: Processo) -> None:
    """Mata o processo, se ainda roda, e espera os restos (Chrome, driver) fecharem."""
    if processo.rodando:
        processo.matar()
    processo.esperar_limpeza(ESPERA_LIMPEZA)  # não sai deixando Chrome órfão


def _somente_leitura(texto: tk.Text) -> None:
    """Deixa o Text sem edição, mas com seleção e cópia (Ctrl+C) funcionando."""
    navegacao = {"Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"}

    def tecla(evento: tk.Event) -> str | None:
        if evento.keysym in navegacao:
            return None
        if evento.state & 0x4 and evento.keysym.lower() in ("c", "a", "insert"):
            return None  # Ctrl+C, Ctrl+A
        return "break"

    texto.bind("<Key>", tecla)
    for sequencia in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<<PasteSelection>>", "<<Undo>>", "<<Redo>>"):
        texto.bind(sequencia, lambda _e: "break")


def _duracao(segundos: float) -> str:
    segundos = int(max(segundos, 0))
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{segundos:02d}" if horas else f"{minutos:02d}:{segundos:02d}"


def _nitidez_no_windows() -> None:
    """No Windows com escala de tela (125%, 150%), evita a janela borrada."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


_TRAVA_DA_JANELA = None  # fica aberta até o processo acabar (o sistema solta a trava)


def _avisar_que_ja_esta_aberta() -> None:
    print(JA_ABERTA, flush=True)  # vai para o logs/interface.log dos lançadores
    try:
        raiz = tk.Tk()
    except tk.TclError:
        return  # sem tela: fica só a mensagem acima
    try:
        raiz.withdraw()
        messagebox.showinfo(TITULO, f"{JA_ABERTA}\n\nProcure-a na barra de tarefas. Se você acabou de "
                                    "fechá-la, espere alguns segundos (ela termina de fechar o navegador) e "
                                    "abra de novo.", parent=raiz)
    finally:
        raiz.destroy()


def main() -> int:
    global _TRAVA_DA_JANELA
    preparar_console()
    _nitidez_no_windows()
    # Uma janela por vez: duas abririam o login ou a automação no mesmo perfil do Chrome.
    try:
        _TRAVA_DA_JANELA = travar_interface()
        ja_aberta = _TRAVA_DA_JANELA is None
    except OSError:
        ja_aberta = False  # sem como criar a trava (pasta logs/ sem permissão): abre sem ela
    if ja_aberta:
        _avisar_que_ja_esta_aberta()
        return 0
    app = Aplicacao()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.inicio.matar_processo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
