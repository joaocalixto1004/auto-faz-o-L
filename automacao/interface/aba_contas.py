"""Aba "Contas" da interface: as contas (perfis do navegador) que a automação usa.

Cada conta é uma pasta própria do Chrome com um login do Instagram e um do
TikTok: a Principal é a perfil_navegador/ de sempre e as outras ficam em
perfis/. Aqui dá para adicionar, fazer login, ativar ou desativar, renomear e
remover contas. O login roda pela aba Início (o mesmo processo, a mesma saída
e o mesmo botão Parar), então só um navegador abre por vez.

Enquanto o login ou a automação rodam, a lista de contas fica travada (sem
adicionar, renomear, ativar/desativar nem remover): a automação relê as contas
no meio da execução, e uma mudança ali mudaria o que ela está fazendo.

A situação das contas vem da aba Início (AbaInicio.contas), que relê o banco e
acompanha a saída do processo ao vivo; esta aba só mostra e manda mudar.
"""

import sqlite3
import tkinter as tk
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from automacao import banco
from automacao.interface import dados
from automacao.interface.componentes import largura_do_texto, quebrar_na_largura

AJUDA = ("Cada conta é um perfil separado do Chrome, com um login do Instagram e um do TikTok. "
         "Os logins ficam salvos: só é preciso logar de novo se a rede pedir. A automação usa as "
         "contas ativas, uma depois da outra, e pula as inativas. A Principal é a conta que o "
         "programa já usava e não pode ser removida.")

# Por que os botões ficam desligados enquanto algo roda (mostrado se alguém tentar mesmo assim).
TRAVADA = ("Enquanto o login ou a automação estão rodando, as contas não podem ser adicionadas, "
           "renomeadas, ativadas/desativadas nem removidas: a automação usa essa lista enquanto roda.\n\n"
           "Pare o processo na aba Início e tente de novo.")

# (coluna, título, exemplo de texto que define a largura inicial)
COLUNAS = (("nome", "Nome", "Conta da Maria Silva"), ("Instagram", "Instagram", "@fulano.da_silva_"),
           ("TikTok", "TikTok", "@fulano.da_silva_"), ("ultimo", "Último login", "21/10/2026 às 18:00"),
           ("ativo", "Ativo", "Ativo"))


class AbaContas(ttk.Frame):
    def __init__(self, master: tk.Misc, inicio, ir_para: Callable[[str], None]) -> None:
        super().__init__(master, padding=16)
        self.inicio = inicio          # AbaInicio: dona das contas e do processo
        self.ir_para = ir_para
        self._mensagem = ""           # o que a última operação fez (mostrado no rodapé)
        self._montar()
        inicio.observadores.append(self.redesenhar)
        self.redesenhar()

    # ------------------------------------------------------------ montagem

    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text="Contas", style="Titulo.TLabel").grid(row=0, column=0, sticky="w")
        quebrar_na_largura(ttk.Label(self, text=AJUDA, style="Suave.TLabel", wraplength=480,
                                     justify="left")).grid(row=1, column=0, sticky="ew", pady=(2, 8))

        tabela = ttk.Frame(self)
        tabela.grid(row=2, column=0, sticky="nsew")
        tabela.columnconfigure(0, weight=1)
        tabela.rowconfigure(0, weight=1)
        self.arvore = ttk.Treeview(tabela, columns=[c[0] for c in COLUNAS], show="headings",
                                   height=8, selectmode="browse")
        for coluna, titulo, exemplo in COLUNAS:
            self.arvore.heading(coluna, text=titulo, anchor="w")
            self.arvore.column(coluna, width=largura_do_texto(self, exemplo), minwidth=50,
                               stretch=coluna != "ativo", anchor="w")
        self.arvore.tag_configure("inativa", foreground="#5b6472")
        self.arvore.tag_configure("em_uso", foreground="#2f5bea")
        self.arvore.grid(row=0, column=0, sticky="nsew")
        barra = ttk.Scrollbar(tabela, orient="vertical", command=self.arvore.yview)
        barra.grid(row=0, column=1, sticky="ns")
        self.arvore.configure(yscrollcommand=barra.set)
        self.arvore.bind("<<TreeviewSelect>>", self._ao_selecionar)

        botoes = ttk.Frame(self)
        botoes.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.botao_adicionar = ttk.Button(botoes, text="Adicionar conta…", command=self.adicionar)
        self.botao_login = ttk.Button(botoes, text="Fazer login", command=self.fazer_login)
        self.botao_ativar = ttk.Button(botoes, text="Ativar/Desativar", width=16, command=self.alternar_ativo)
        self.botao_renomear = ttk.Button(botoes, text="Renomear…", command=self.renomear)
        self.botao_remover = ttk.Button(botoes, text="Remover…", command=self.remover)
        for botao in (self.botao_adicionar, self.botao_login, self.botao_ativar, self.botao_renomear,
                      self.botao_remover):
            botao.pack(side="left", padx=(0, 6))

        self.rotulo_estado = quebrar_na_largura(ttk.Label(self, style="Suave.TLabel", wraplength=480,
                                                          justify="left"))
        self.rotulo_estado.grid(row=4, column=0, sticky="ew", pady=(8, 0))

    # ------------------------------------------------------------ exibição

    def redesenhar(self) -> None:
        """Mostra as contas da aba Início (chamada sempre que elas ou o processo mudam)."""
        arvore = self.arvore
        arvore.delete(*arvore.get_children())
        for conta in self.inicio.contas:
            nome, tags = conta.nome, ()
            if self.inicio.em_execucao and conta.id == self.inicio.perfil_em_uso:
                nome, tags = f"▶ {nome}", ("em_uso",)
            elif not conta.ativo:
                tags = ("inativa",)
            valores = (nome, *(dados.texto_login_curto(conta.login(rede)) for rede in dados.REDES_AUTOMACAO),
                       dados.formatar_momento(conta.ultimo_login), "sim" if conta.ativo else "não")
            arvore.insert("", "end", iid=str(conta.id), values=valores, tags=tags)
        iid = str(self.inicio.perfil_login_id)
        if arvore.exists(iid):
            arvore.selection_set(iid)
            arvore.see(iid)
        self._atualizar_botoes()
        self._mostrar_estado()

    def selecionada(self) -> dados.SituacaoConta | None:
        selecao = self.arvore.selection()
        return dados.achar_conta(self.inicio.contas, int(selecao[0])) if selecao else None

    def selecionar(self, perfil_id: int) -> None:
        """Seleciona a conta (é a mesma escolhida para o botão de login da aba Início)."""
        self.inicio.escolher_conta_login(perfil_id)
        if self.arvore.exists(str(perfil_id)):
            self.arvore.selection_set(str(perfil_id))
        self._atualizar_botoes()

    def _ao_selecionar(self, _evento=None) -> None:
        conta = self.selecionada()
        if conta is not None:
            self.inicio.escolher_conta_login(conta.id)
        self._atualizar_botoes()

    def _atualizar_botoes(self) -> None:
        """Com algo em execução, só a seleção funciona (o rodapé explica por quê)."""
        conta = self.selecionada()
        livre = not self.inicio.em_execucao
        estado = lambda ligado: "normal" if ligado else "disabled"  # noqa: E731
        self.botao_adicionar.configure(state=estado(livre))
        self.botao_login.configure(state=estado(conta and livre))
        self.botao_ativar.configure(state=estado(conta and livre),
                                    text=("Desativar" if conta.ativo else "Ativar") if conta else "Ativar/Desativar")
        self.botao_renomear.configure(state=estado(conta and livre))
        self.botao_remover.configure(state=estado(conta and livre and not conta.principal))

    def _travada(self, titulo: str) -> bool:
        """True (e avisa) se as contas não podem mudar agora, porque há um processo em execução."""
        if not self.inicio.em_execucao:
            return False
        messagebox.showinfo(titulo, TRAVADA, parent=self)
        return True

    def _mostrar_estado(self) -> None:
        inicio = self.inicio
        if inicio.em_execucao:
            em_uso = dados.achar_conta(inicio.contas, inicio.perfil_em_uso)
            if inicio.tipo == "login":
                onde = f" na conta {em_uso.nome}" if em_uso else ""
                texto = (f"Login em execução{onde}: faça o login no Chrome e, quando terminar, clique em "
                         "Parar na aba Início. Só um navegador abre por vez; até lá, as contas não podem "
                         "ser adicionadas, renomeadas, ativadas/desativadas nem removidas.")
            else:
                onde = f" (agora na conta {em_uso.nome})" if em_uso else ""
                texto = (f"Automação em execução{onde}. Para adicionar, renomear, ativar/desativar ou "
                         "remover contas, ou fazer login em outra, pare a automação na aba Início.")
            self.rotulo_estado.configure(text=texto, style="Sistema.TLabel")
            return
        total = len(inicio.contas)
        ativas = sum(conta.ativo for conta in inicio.contas)
        texto = f"{total} conta{'s' if total != 1 else ''}, {ativas} ativa{'s' if ativas != 1 else ''}."
        if self._mensagem:
            texto += f" {self._mensagem}"
        self.rotulo_estado.configure(text=texto, style="Suave.TLabel")

    def _informar(self, mensagem: str) -> None:
        self._mensagem = mensagem
        self._mostrar_estado()

    # ------------------------------------------------------------ ações

    def adicionar(self) -> None:
        """Pede o nome, cadastra a conta e oferece fazer o login dela."""
        if self._travada("Adicionar conta"):
            return
        nome = simpledialog.askstring(
            "Adicionar conta", "Nome da nova conta (só para você reconhecer, por exemplo \"Loja\" ou \"Maria\"):",
            parent=self)
        if nome is None:
            return
        try:
            with closing(banco.conectar()) as conn:
                perfil_id = banco.criar_perfil(conn, nome)
                nome = banco.obter_perfil(conn, perfil_id)["nome"]
        except ValueError as erro:
            messagebox.showwarning("Adicionar conta", str(erro), parent=self)
            return
        except sqlite3.Error as erro:
            messagebox.showerror("Adicionar conta", f"Erro no banco de dados:\n{erro}", parent=self)
            return
        self._mensagem = f"Conta {nome} adicionada."
        self.inicio.atualizar_contas()
        self.selecionar(perfil_id)
        if messagebox.askyesno(
                "Adicionar conta",
                f"A conta {nome} foi adicionada.\n\nFazer o login dela agora?\nO Chrome abre com uma aba do "
                "TikTok e uma do Instagram: faça o login nas duas e, quando terminar, clique em Parar "
                "na aba Início.", parent=self):
            self.fazer_login()

    def fazer_login(self) -> None:
        """Abre o navegador na conta selecionada (pela aba Início, que mostra a saída e o Parar)."""
        conta = self.selecionada()
        if conta is None:
            messagebox.showinfo("Fazer login", "Selecione uma conta na lista.", parent=self)
            return
        if self.inicio.em_execucao:
            messagebox.showinfo("Fazer login", "Já há um processo em execução (só um navegador abre por "
                                "vez). Pare-o na aba Início antes.", parent=self)
            return
        if self.inicio.iniciar_login(conta.id):
            self.ir_para("inicio")

    def alternar_ativo(self) -> None:
        conta = self.selecionada()
        if conta is None or self._travada("Ativar/Desativar"):
            return
        try:
            with closing(banco.conectar()) as conn:
                banco.definir_perfil_ativo(conn, conta.id, not conta.ativo)
        except sqlite3.Error as erro:
            messagebox.showerror("Ativar/Desativar", f"Erro no banco de dados:\n{erro}", parent=self)
            return
        if conta.ativo:
            self._mensagem = (f"A conta {conta.nome} foi desativada: a automação vai pulá-la "
                              "(o login continua salvo).")
        else:
            self._mensagem = f"A conta {conta.nome} foi ativada: a automação vai usá-la."
        self.inicio.atualizar_contas()

    def renomear(self) -> None:
        conta = self.selecionada()
        if conta is None or self._travada("Renomear conta"):
            return
        novo = simpledialog.askstring("Renomear conta", "Novo nome da conta:", initialvalue=conta.nome,
                                      parent=self)
        if novo is None:
            return
        try:
            with closing(banco.conectar()) as conn:
                banco.renomear_perfil(conn, conta.id, novo)
                novo = banco.obter_perfil(conn, conta.id)["nome"]
        except ValueError as erro:
            messagebox.showwarning("Renomear conta", str(erro), parent=self)
            return
        except sqlite3.Error as erro:
            messagebox.showerror("Renomear conta", f"Erro no banco de dados:\n{erro}", parent=self)
            return
        self._mensagem = f"Conta {conta.nome} renomeada para {novo}." if novo != conta.nome else ""
        self.inicio.atualizar_contas()

    def remover(self) -> None:
        """Tira a conta da lista e, se o usuário quiser, apaga a pasta com o login salvo."""
        conta = self.selecionada()
        if conta is None:
            return
        if conta.principal:
            messagebox.showinfo("Remover conta", "A conta Principal não pode ser removida.", parent=self)
            return
        if self._travada("Remover conta"):
            return
        if not messagebox.askyesno(
                "Remover conta", f"Remover a conta {conta.nome} da lista?\n\nA automação deixa de usar esta "
                "conta. O histórico do que ela já fez continua no banco.", icon="warning", parent=self):
            return
        try:
            with closing(banco.conectar()) as conn:
                pasta = banco.remover_perfil(conn, conta.id)
        except ValueError as erro:
            messagebox.showwarning("Remover conta", str(erro), parent=self)
            return
        except sqlite3.Error as erro:
            messagebox.showerror("Remover conta", f"Erro no banco de dados:\n{erro}", parent=self)
            return
        self._mensagem = f"Conta {conta.nome} removida."
        self.inicio.atualizar_contas()
        self._oferecer_apagar_pasta(conta.nome, pasta)

    def _oferecer_apagar_pasta(self, nome: str, pasta: Path) -> None:
        """Pergunta (à parte) se apaga a pasta do navegador da conta removida."""
        if not pasta.exists():
            return  # a conta nunca chegou a abrir o navegador
        mostrar = dados.nome_de_pasta_para_mostrar(pasta)
        if not dados.pasta_pode_ser_apagada(pasta) or self.inicio.em_execucao:
            self._informar(f"{self._mensagem} A pasta {mostrar} foi mantida.")
            return
        if not messagebox.askyesno(
                "Apagar o login salvo?",
                f"Apagar também a pasta com o login salvo da conta {nome}?\n\n{mostrar}\n\n"
                "Se apagar, para usar essas contas do Instagram e do TikTok de novo será preciso fazer o "
                "login outra vez. Se não apagar, a pasta continua no computador.",
                icon="warning", default="no", parent=self):
            self._informar(f"{self._mensagem} A pasta {mostrar} foi mantida.")
            return
        try:
            dados.apagar_pasta_da_conta(pasta)
        except (OSError, ValueError) as erro:
            messagebox.showerror("Apagar o login salvo", f"Não consegui apagar a pasta {mostrar}:\n{erro}\n\n"
                                 "Feche o Chrome, se ele estiver aberto, e apague a pasta à mão.", parent=self)
            self._informar(f"{self._mensagem} A pasta {mostrar} foi mantida.")
            return
        self._informar(f"{self._mensagem} A pasta com o login salvo foi apagada.")
