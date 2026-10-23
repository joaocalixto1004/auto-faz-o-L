"""Aba "Configurações" da interface: os mesmos valores da página do configuracao.py.

A validação é a mesma da página (configuracao.validar) e tudo é salvo no
SQLite com banco.salvar_configuracoes, de onde o interagir.py lê. Os
comentários são uma lista: a automação sorteia um para cada publicação.

O formulário fica numa área com rolagem (é mais alto que a janela no tamanho
mínimo); os botões de salvar ficam sempre visíveis, embaixo.
"""

import re
import sqlite3
import tkinter as tk
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

from automacao import banco, configuracao
from automacao.interface import dados
from automacao.interface.componentes import AreaRolavel, quebrar_na_largura
from automacao.redes import comum

# Opções sim/não, guardadas como "sim" ou "nao" (como o configuracao.validar devolve).
OPCOES_SIM_NAO = ("anti_bloqueio", "seguir", "ver_stories", "repetir", "continuar_sem_login")
CAMPOS = ("modo", "quantidade", "semanas", "compartilhar_com", "limite_diario", "redes",
          "repetir_minutos", *OPCOES_SIM_NAO)
ESCOLHAS_REDES = (("instagram", "Só Instagram"), ("tiktok", "Só TikTok"), ("ambas", "As duas"))
MINUTOS_REPETIR = (1, 1440)

AJUDA_ANTI_BLOQUEIO = (
    "Deixa a automação bem mais lenta e cautelosa: pausas de minutos entre ações, limites menores por "
    "hora e por dia, atividade só das 9h às 22h, pausas longas, no máximo 2 publicações por perfil-alvo "
    "por dia e parada de 48 h ao primeiro aviso da rede. Recomendado para contas novas ou que já "
    "receberam aviso. Nenhuma regra garante que a rede não bloqueie.")


class AbaConfiguracoes(ttk.Frame):
    def __init__(self, master: tk.Misc, ao_salvar: Callable[[], None] | None = None) -> None:
        super().__init__(master, padding=(16, 16, 16, 12))
        self.ao_salvar = ao_salvar
        self.vars = {campo: tk.StringVar(self) for campo in CAMPOS}
        self.var_novo_comentario = tk.StringVar(self)
        self._comentarios: list[str] = []
        self._salvos: dict[str, str] = {}
        self._carregando = False
        self._montar()
        self.carregar()
        for var in self.vars.values():
            var.trace_add("write", self._ao_editar)
        self.var_novo_comentario.trace_add("write", self._contar_letras)

    # ------------------------------------------------------------ montagem

    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text="Configuração das interações", style="Titulo.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(self, text="Vale para todas as contas ativas, nas próximas execuções da automação.",
                  style="Suave.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.area = AreaRolavel(self)
        self.area.grid(row=2, column=0, sticky="nsew")
        interior = self.area.interior
        interior.columnconfigure(0, weight=1)

        self.rotulo_erros = quebrar_na_largura(ttk.Label(interior, style="Erro.TLabel", justify="left",
                                                         wraplength=500))
        self.rotulo_erros.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.rotulo_erros.grid_remove()

        self._apenas_digitos = (self.register(_apenas_digitos), "%P")
        self._ate_4_digitos = (self.register(lambda texto: _apenas_digitos(texto, 4)), "%P")
        partes = (self._montar_redes, self._montar_modo, self._montar_comentarios,
                  self._montar_compartilhamento, self._montar_extras, self._montar_ritmo)
        for linha, montar in enumerate(partes, start=1):
            montar(interior).grid(row=linha, column=0, sticky="ew", pady=(0, 8), padx=(0, 4))
        self.area.ajustar_largura_pedida()

        rodape = ttk.Frame(self)
        rodape.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        rodape.columnconfigure(0, weight=1)
        self.rotulo_estado = ttk.Label(rodape, style="Suave.TLabel")
        self.rotulo_estado.grid(row=0, column=0, sticky="w")
        ttk.Button(rodape, text="Restaurar padrões", command=self.restaurar_padroes).grid(row=0, column=1, padx=4)
        ttk.Button(rodape, text="Desfazer alterações", command=self.carregar).grid(row=0, column=2, padx=4)
        ttk.Button(rodape, text="Salvar", style="Destaque.TButton", command=self.salvar).grid(
            row=0, column=3, padx=(4, 0))

    def _dica(self, master: tk.Misc, texto: str) -> ttk.Label:
        return quebrar_na_largura(ttk.Label(master, text=texto, style="Suave.TLabel", wraplength=480,
                                            justify="left"))

    def _montar_redes(self, master: tk.Misc) -> ttk.LabelFrame:
        quadro = ttk.LabelFrame(master, text="Em quais redes agir", padding=10)
        quadro.columnconfigure(len(ESCOLHAS_REDES), weight=1)
        self.radios_redes = {}
        for coluna, (valor, texto) in enumerate(ESCOLHAS_REDES):
            radio = ttk.Radiobutton(quadro, text=texto, value=valor, variable=self.vars["redes"])
            radio.grid(row=0, column=coluna, sticky="w", padx=(0, 18))
            self.radios_redes[valor] = radio
        self._dica(quadro, "Os logins das contas continuam guardados nas duas redes: isto só muda onde a "
                           "automação age. Os perfis da planilha das outras redes são pulados.").grid(
            row=1, column=0, columnspan=len(ESCOLHAS_REDES) + 1, sticky="ew", pady=(6, 0))
        return quadro

    def _montar_modo(self, master: tk.Misc) -> ttk.LabelFrame:
        modo = ttk.LabelFrame(master, text="Quais publicações trabalhar em cada aba", padding=10)
        modo.columnconfigure(3, weight=1)
        ttk.Radiobutton(modo, text="Por número de publicações", value="quantidade",
                        variable=self.vars["modo"], command=self._atualizar_modo).grid(row=0, column=0, sticky="w")
        ttk.Label(modo, text="Publicações por aba (1 a 50):").grid(row=0, column=1, sticky="e", padx=(24, 6))
        self.spin_quantidade = ttk.Spinbox(modo, from_=1, to=50, width=6, textvariable=self.vars["quantidade"],
                                           validate="key", validatecommand=self._apenas_digitos)
        self.spin_quantidade.grid(row=0, column=2, sticky="w")
        ttk.Radiobutton(modo, text="Por tempo", value="tempo",
                        variable=self.vars["modo"], command=self._atualizar_modo).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(modo, text="Últimas semanas (1 a 52):").grid(row=1, column=1, sticky="e", padx=(24, 6), pady=(6, 0))
        self.spin_semanas = ttk.Spinbox(modo, from_=1, to=52, width=6, textvariable=self.vars["semanas"],
                                        validate="key", validatecommand=self._apenas_digitos)
        self.spin_semanas.grid(row=1, column=2, sticky="w", pady=(6, 0))
        self._dica(modo, "No modo por tempo, conta a data em que a publicação foi postada. "
                         "Posts fixados antigos ficam de fora.").grid(
            row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        return modo

    def _montar_comentarios(self, master: tk.Misc) -> ttk.LabelFrame:
        quadro = ttk.LabelFrame(master, text="Comentários", padding=10)
        quadro.columnconfigure(0, weight=1)
        quadro.rowconfigure(1, weight=1)
        self._dica(quadro, f"A cada publicação, a automação sorteia um destes comentários, sem repetir o da "
                           f"publicação anterior. Até {configuracao.MAXIMO_COMENTARIOS} comentários, de até "
                           f"{dados.MAXIMO_LETRAS_COMENTARIO} caracteres cada.").grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        self.lista_comentarios = tk.Listbox(quadro, height=5, selectmode="extended", activestyle="none",
                                            exportselection=False, relief="flat", borderwidth=1,
                                            highlightthickness=1, highlightbackground="#d9dde3")
        self.lista_comentarios.grid(row=1, column=0, columnspan=2, sticky="nsew")
        barra = ttk.Scrollbar(quadro, orient="vertical", command=self.lista_comentarios.yview)
        barra.grid(row=1, column=2, sticky="ns")
        self.lista_comentarios.configure(yscrollcommand=barra.set)
        self.lista_comentarios.bind("<Double-Button-1>", lambda _e: self.editar_comentario())
        self.lista_comentarios.bind("<Delete>", lambda _e: self.remover_comentarios())

        entrada = ttk.Entry(quadro, textvariable=self.var_novo_comentario)
        entrada.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        entrada.bind("<Return>", lambda _e: self.adicionar_comentario())
        self.rotulo_contador = ttk.Label(quadro, style="Suave.TLabel", width=8, anchor="e")
        self.rotulo_contador.grid(row=2, column=1, columnspan=2, sticky="e", pady=(8, 0))

        botoes = ttk.Frame(quadro)
        botoes.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(botoes, text="Adicionar", command=self.adicionar_comentario).pack(side="left")
        ttk.Button(botoes, text="Editar…", command=self.editar_comentario).pack(side="left", padx=6)
        ttk.Button(botoes, text="Remover selecionados", command=self.remover_comentarios).pack(side="left")
        self.rotulo_quantos = ttk.Label(botoes, style="Suave.TLabel")
        self.rotulo_quantos.pack(side="left", padx=(12, 0))
        self.rotulo_erro_comentario = quebrar_na_largura(ttk.Label(quadro, style="Erro.TLabel", wraplength=480,
                                                                   justify="left"))
        self.rotulo_erro_comentario.grid(row=4, column=0, columnspan=3, sticky="ew")
        return quadro

    def _montar_compartilhamento(self, master: tk.Misc) -> ttk.LabelFrame:
        quadro = ttk.LabelFrame(master, text="Compartilhamento", padding=10)
        quadro.columnconfigure(1, weight=1)
        ttk.Label(quadro, text="Compartilhar com:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(quadro, textvariable=self.vars["compartilhar_com"], width=30).grid(row=0, column=1, sticky="w")
        self._dica(quadro, "Usuário do Instagram que recebe o compartilhamento quando a planilha "
                           "para_compartilhar.xlsx não tiver ninguém na aba instagram.").grid(
            row=1, column=1, sticky="ew", pady=(2, 0))
        return quadro

    def _opcao(self, master: tk.Misc, linha: int, campo: str, texto: str, ajuda: str,
               comando: Callable[[], None] | None = None) -> ttk.Checkbutton:
        """Uma caixa de marcar sim/não com a explicação embaixo."""
        caixa = ttk.Checkbutton(master, text=texto, variable=self.vars[campo], onvalue="sim", offvalue="nao",
                                command=comando)
        caixa.grid(row=linha, column=0, sticky="w", pady=(6 if linha else 0, 0))
        self._dica(master, ajuda).grid(row=linha + 1, column=0, columnspan=3, sticky="ew", padx=(24, 0))
        return caixa

    def _montar_extras(self, master: tk.Misc) -> ttk.LabelFrame:
        quadro = ttk.LabelFrame(master, text="Ações extras e repetição", padding=10)
        quadro.columnconfigure(2, weight=1)
        self.check_seguir = self._opcao(
            quadro, 0, "seguir", "Seguir os perfis da lista (se ainda não segue)",
            "Ao visitar um perfil da lista, começa a seguir se a conta ainda não segue. Nunca deixa de seguir.")
        self.check_stories = self._opcao(
            quadro, 2, "ver_stories", "Ver stories",
            "Assiste e curte os stories ainda não vistos de cada perfil da lista.")
        self.check_repetir = self._opcao(
            quadro, 4, "repetir", "Recomeçar a lista automaticamente",
            "Ao terminar todos os perfis, espera esse tempo e começa de novo. Só para pelo botão Parar.",
            comando=self._ao_marcar_repetir)
        minutos = ttk.Frame(quadro)
        minutos.grid(row=4, column=1, sticky="w", padx=(12, 0), pady=(6, 0))
        self.spin_repetir = ttk.Spinbox(minutos, from_=MINUTOS_REPETIR[0], to=MINUTOS_REPETIR[1], width=6,
                                        textvariable=self.vars["repetir_minutos"], validate="key",
                                        validatecommand=self._ate_4_digitos)
        self.spin_repetir.pack(side="left")
        ttk.Label(minutos, text="minutos depois de terminar").pack(side="left", padx=(6, 0))
        self.check_sem_login = self._opcao(
            quadro, 6, "continuar_sem_login", "Continuar mesmo se alguma conta não estiver logada",
            "Contas sem login (ou que não abrirem) são puladas com um aviso, em vez de impedir o início.")
        return quadro

    def _montar_ritmo(self, master: tk.Misc) -> ttk.LabelFrame:
        quadro = ttk.LabelFrame(master, text="Limite diário e proteção anti-bloqueio", padding=10)
        quadro.columnconfigure(2, weight=1)
        ttk.Label(quadro, text="Limite diário:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Spinbox(quadro, from_=1, to=500, width=6, textvariable=self.vars["limite_diario"],
                    validate="key", validatecommand=self._apenas_digitos).grid(row=0, column=1, sticky="w")
        self._dica(quadro, "publicações por conta e rede, por dia (1 a 500). Volume baixo evita "
                           "bloqueios.").grid(row=0, column=2, sticky="ew", padx=(8, 0))
        self.check_anti_bloqueio = ttk.Checkbutton(quadro, text="Proteção anti-bloqueio",
                                                   variable=self.vars["anti_bloqueio"], onvalue="sim",
                                                   offvalue="nao")
        self.check_anti_bloqueio.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._dica(quadro, AJUDA_ANTI_BLOQUEIO).grid(row=2, column=0, columnspan=3, sticky="ew", padx=(24, 0))
        return quadro

    # ------------------------------------------------------------ dados

    def comentarios(self) -> list[str]:
        return list(self._comentarios)

    def formulario(self) -> dict[str, str]:
        """Os campos como o configuracao.validar espera (comentários um por linha)."""
        campos = {campo: var.get() for campo, var in self.vars.items()}
        campos["comentarios"] = "\n".join(self._comentarios)
        return campos

    def _preencher(self, valores: dict[str, str]) -> None:
        self._carregando = True
        try:
            for campo, var in self.vars.items():
                valor = str(valores.get(campo, ""))
                if campo in OPCOES_SIM_NAO:
                    valor = "sim" if dados.ligado(valor) else "nao"
                elif campo == "redes" and valor not in banco.REDES_ESCOLHA:
                    valor = "ambas"  # o que a automação faz com um valor desconhecido
                var.set(valor)
            self._definir_comentarios(comum.comentarios_da_config(valores))
            self.var_novo_comentario.set("")
            self.rotulo_erro_comentario.configure(text="")
        finally:
            self._carregando = False
        self._atualizar_modo()
        self._atualizar_repetir()
        self._ao_editar()

    def carregar(self) -> None:
        """Lê do banco a configuração salva (descarta o que não foi salvo)."""
        try:
            with closing(banco.conectar()) as conn:
                valores = banco.ler_configuracoes(conn)
        except sqlite3.Error as erro:
            messagebox.showerror("Configurações", f"Não consegui ler o banco de dados:\n{erro}", parent=self)
            valores = dict(banco.CONFIG_PADRAO)
        self._preencher(valores)
        self._salvos = self._normalizado()
        self._mostrar_erros([])
        self._ao_editar()

    def restaurar_padroes(self) -> None:
        """Põe os valores padrão no formulário (só valem depois de salvar)."""
        self._preencher(banco.CONFIG_PADRAO)

    def _normalizado(self) -> dict[str, str]:
        return {campo: valor.strip() for campo, valor in self.formulario().items()}

    def tem_alteracoes(self) -> bool:
        return self._normalizado() != self._salvos

    def salvar(self) -> bool:
        """Valida e grava. Devolve True se salvou."""
        try:
            valores, erros = configuracao.validar(self.formulario())
        except ValueError:  # por garantia: um número que passou na digitação mas não converte
            valores, erros = {}, ["Use só os algarismos de 0 a 9 nos campos numéricos."]
        if erros:
            self._mostrar_erros(erros)
            messagebox.showwarning("Configuração não salva", "\n".join(erros), parent=self)
            return False
        try:
            with closing(banco.conectar()) as conn:
                banco.salvar_configuracoes(conn, valores)
        except sqlite3.Error as erro:
            messagebox.showerror("Configuração não salva", f"Erro no banco de dados:\n{erro}", parent=self)
            return False
        self.carregar()
        self.rotulo_estado.configure(text=f"Configuração salva às {datetime.now():%H:%M:%S}.", style="Ok.TLabel")
        if self.ao_salvar:
            self.ao_salvar()
        return True

    # ------------------------------------------------------------ comentários

    def _definir_comentarios(self, comentarios: list[str]) -> None:
        self._comentarios = list(comentarios)
        self.lista_comentarios.delete(0, "end")
        for comentario in self._comentarios:
            self.lista_comentarios.insert("end", comentario)
        quantos = len(self._comentarios)
        self.rotulo_quantos.configure(text=f"{quantos} comentário{'s' if quantos != 1 else ''}")

    def _mudar_comentarios(self, comentarios: list[str]) -> None:
        self._definir_comentarios(comentarios)
        self._ao_editar()

    def adicionar_comentario(self) -> None:
        """Adiciona o texto do campo (cada linha colada vira um comentário)."""
        linhas = [linha for linha in re.split(r"[\r\n]+", self.var_novo_comentario.get()) if linha.strip()]
        if not linhas:
            self.rotulo_erro_comentario.configure(text="Digite o comentário no campo abaixo da lista.")
            return
        comentarios, erros, recusados = self.comentarios(), [], []
        for linha in linhas:
            valor, erro = dados.validar_comentario(linha, comentarios)
            if erro:
                erros.append(erro)
                recusados.append(linha)
            else:
                comentarios.append(valor)
        self.var_novo_comentario.set("\n".join(recusados))
        self.rotulo_erro_comentario.configure(text="\n".join(erros))
        if len(recusados) < len(linhas):
            self._mudar_comentarios(comentarios)
            self.lista_comentarios.see("end")

    def editar_comentario(self) -> None:
        selecionados = self.lista_comentarios.curselection()
        if len(selecionados) != 1:
            self.rotulo_erro_comentario.configure(text="Selecione um comentário na lista para editar.")
            return
        indice = selecionados[0]
        novo = simpledialog.askstring("Editar comentário", "Comentário:", parent=self,
                                      initialvalue=self._comentarios[indice])
        if novo is None:
            return
        outros = self._comentarios[:indice] + self._comentarios[indice + 1:]
        valor, erro = dados.validar_comentario(novo, outros, editando=True)
        self.rotulo_erro_comentario.configure(text=erro or "")
        if erro:
            return
        comentarios = self.comentarios()
        comentarios[indice] = valor
        self._mudar_comentarios(comentarios)
        self.lista_comentarios.selection_set(indice)

    def remover_comentarios(self) -> None:
        selecionados = set(self.lista_comentarios.curselection())
        if not selecionados:
            self.rotulo_erro_comentario.configure(text="Selecione na lista o que quer remover.")
            return
        self.rotulo_erro_comentario.configure(text="")
        self._mudar_comentarios([c for i, c in enumerate(self._comentarios) if i not in selecionados])

    # ------------------------------------------------------------ reações

    def _mostrar_erros(self, erros: list[str]) -> None:
        if erros:
            self.rotulo_erros.configure(text="\n".join(f"• {erro}" for erro in erros))
            self.rotulo_erros.grid()
            self.area.ir_para_o_topo()
        else:
            self.rotulo_erros.grid_remove()

    def _atualizar_modo(self) -> None:
        por_tempo = self.vars["modo"].get() == "tempo"
        self.spin_quantidade.configure(state="disabled" if por_tempo else "normal")
        self.spin_semanas.configure(state="normal" if por_tempo else "disabled")

    def _atualizar_repetir(self) -> None:
        """O intervalo só vale (e só dá para editar) com "recomeçar a lista" marcado."""
        ligado = self.vars["repetir"].get() == "sim"
        self.spin_repetir.configure(state="normal" if ligado else "disabled")

    def _ao_marcar_repetir(self) -> None:
        # Desmarcado com um intervalo inválido no campo (que fica travado): volta ao último válido.
        minutos = self.vars["repetir_minutos"]
        if self.vars["repetir"].get() != "sim" and not _minutos_validos(minutos.get()):
            salvo = self._salvos.get("repetir_minutos", "")
            minutos.set(salvo if _minutos_validos(salvo) else banco.CONFIG_PADRAO["repetir_minutos"])
        self._atualizar_repetir()

    def _contar_letras(self, *_args) -> None:
        tamanho = len(self.var_novo_comentario.get().strip())
        demais = tamanho > dados.MAXIMO_LETRAS_COMENTARIO
        self.rotulo_contador.configure(text=f"{tamanho}/{dados.MAXIMO_LETRAS_COMENTARIO}",
                                       style="Erro.TLabel" if demais else "Suave.TLabel")

    def _ao_editar(self, *_args) -> None:
        self._contar_letras()
        if self._carregando:
            return
        if self.tem_alteracoes():
            self.rotulo_estado.configure(text="Alterações não salvas.", style="Aviso.TLabel")
        else:
            self.rotulo_estado.configure(text="", style="Suave.TLabel")


def _apenas_digitos(texto: str, maximo: int = 3) -> bool:
    """Só deixa digitar algarismos de 0 a 9 (até `maximo`) nos campos numéricos."""
    return texto == "" or (len(texto) <= maximo and texto.isascii() and texto.isdigit())


def _minutos_validos(texto: str) -> bool:
    texto = texto.strip()
    return texto.isascii() and texto.isdecimal() and MINUTOS_REPETIR[0] <= int(texto) <= MINUTOS_REPETIR[1]
