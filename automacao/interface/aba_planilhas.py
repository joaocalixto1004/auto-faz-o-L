"""Aba "Planilhas" da interface: edita data/para_acessar.xlsx e data/para_compartilhar.xlsx.

Cada planilha tem as abas instagram e tiktok, mostradas lado a lado. Dá para
adicionar (vários de uma vez, separados por espaço, vírgula ou linha), remover
e salvar. O arquivo só muda ao clicar em "Salvar planilha".
"""

import re
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from automacao import planilhas
from automacao.caminhos import RAIZ
from automacao.interface import dados
from automacao.interface.componentes import largura_do_texto, quebrar_na_largura


@dataclass(frozen=True)
class TipoPlanilha:
    titulo: str
    caminho: Callable[[], Path]   # lido na hora (os testes trocam o caminho)
    ler: Callable[[Path], dict[str, list[str]]]
    salvar: Callable[[dict[str, list[str]], Path], None]
    coluna: str
    dica: str
    validar_novo: Callable[[str, str, list[str]], tuple[str | None, str | None]]
    observar: Callable[[str, str, list[str]], str]


PERFIS = TipoPlanilha(
    titulo="Perfis a acessar",
    caminho=lambda: planilhas.PLANILHA_PERFIS,
    ler=planilhas.linhas_perfis,
    salvar=planilhas.salvar_perfis,
    coluna="Link do perfil",
    dica="Perfis que a automação visita. Cole o link do perfil, por exemplo "
         "https://www.instagram.com/usuario/ ou https://www.tiktok.com/@usuario.",
    validar_novo=dados.validar_novo_perfil,
    observar=dados.observacao_perfil,
)

DESTINATARIOS = TipoPlanilha(
    titulo="Compartilhar com",
    caminho=lambda: planilhas.PLANILHA_DESTINATARIOS,
    ler=planilhas.linhas_destinatarios,
    salvar=planilhas.salvar_destinatarios,
    coluna="Usuário (sem @)",
    dica="Quem recebe as publicações compartilhadas por mensagem. Digite só o usuário; "
         "o @ é opcional e não é gravado.",
    validar_novo=lambda texto, aba, existentes: dados.validar_novo_destinatario(texto, existentes),
    observar=lambda texto, aba, anteriores: dados.observacao_destinatario(texto, anteriores),
)


def caminho_para_mostrar(caminho: Path) -> str:
    try:
        return str(caminho.resolve().relative_to(RAIZ))
    except ValueError:
        return str(caminho)


class ListaAba(ttk.LabelFrame):
    """Uma aba da planilha (instagram ou tiktok): lista, campo para adicionar e remover."""

    def __init__(self, master: tk.Misc, tipo: TipoPlanilha, aba: str,
                 ao_mudar: Callable[[], None]) -> None:
        self.rede = planilhas.REDES_POR_ABA[aba]
        super().__init__(master, text=self.rede, padding=8)
        self.tipo = tipo
        self.aba = aba
        self.ao_mudar = ao_mudar
        self._valores: list[str] = []
        self.var_novo = tk.StringVar(self)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.arvore = ttk.Treeview(self, columns=("valor", "obs"), show="headings",
                                   selectmode="extended", height=10)
        self.arvore.heading("valor", text=tipo.coluna, anchor="w")
        self.arvore.heading("obs", text="Observação", anchor="w")
        # Larguras iniciais que cabem lado a lado na janela de 900 px; o link estica com a janela.
        self.arvore.column("valor", width=largura_do_texto(self, "https://www.instagram.com/"),
                           minwidth=80, stretch=True)
        self.arvore.column("obs", width=largura_do_texto(self, "inválido: impede o início"), stretch=False)
        self.arvore.tag_configure("aviso", foreground="#9a6700")
        self.arvore.grid(row=0, column=0, columnspan=2, sticky="nsew")
        barra = ttk.Scrollbar(self, orient="vertical", command=self.arvore.yview)
        barra.grid(row=0, column=2, sticky="ns")
        self.arvore.configure(yscrollcommand=barra.set)
        self.arvore.bind("<Delete>", lambda _e: self.remover())

        entrada = ttk.Entry(self, textvariable=self.var_novo)
        entrada.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        entrada.bind("<Return>", lambda _e: self.adicionar())
        ttk.Button(self, text="Adicionar", command=self.adicionar).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=(8, 0))
        self.rotulo_erro = quebrar_na_largura(ttk.Label(self, style="Erro.TLabel", wraplength=300, justify="left"))
        self.rotulo_erro.grid(row=2, column=0, columnspan=3, sticky="ew")
        ttk.Button(self, text="Remover selecionados", command=self.remover).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def valores(self) -> list[str]:
        return list(self._valores)

    def definir(self, valores: list[str]) -> None:
        self._valores = list(valores)
        self.rotulo_erro.configure(text="")
        self._redesenhar()

    def adicionar(self) -> None:
        """Adiciona o que foi digitado (aceita vários, separados por espaço, vírgula ou linha)."""
        partes = [p for p in re.split(r"[\s,;]+", self.var_novo.get()) if p]
        if not partes:
            self.rotulo_erro.configure(text="Digite o que quer adicionar.")
            return
        erros, recusados = [], []
        for parte in partes:
            valor, erro = self.tipo.validar_novo(parte, self.aba, self._valores)
            if erro:
                erros.append(erro)
                recusados.append(parte)
            else:
                self._valores.append(valor)
        self.var_novo.set(" ".join(recusados))  # deixa no campo só o que não entrou
        self.rotulo_erro.configure(text="\n".join(erros))
        if len(recusados) < len(partes):
            self._redesenhar()
            self.arvore.yview_moveto(1.0)
            self.ao_mudar()

    def remover(self) -> None:
        selecionados = {int(item) for item in self.arvore.selection()}
        if not selecionados:
            self.rotulo_erro.configure(text="Selecione na lista o que quer remover.")
            return
        self._valores = [v for i, v in enumerate(self._valores) if i not in selecionados]
        self.rotulo_erro.configure(text="")
        self._redesenhar()
        self.ao_mudar()

    def _redesenhar(self) -> None:
        self.arvore.delete(*self.arvore.get_children())
        for indice, valor in enumerate(self._valores):
            observacao = self.tipo.observar(valor, self.aba, self._valores[:indice])
            self.arvore.insert("", "end", iid=str(indice), values=(valor, observacao),
                               tags=("aviso",) if observacao else ())
        self.configure(text=f"{self.rede} ({len(self._valores)})")


class PainelPlanilha(ttk.Frame):
    """Uma planilha: as abas instagram e tiktok, lado a lado, e os botões de salvar."""

    def __init__(self, master: tk.Misc, tipo: TipoPlanilha,
                 ao_salvar: Callable[[], None] | None = None) -> None:
        super().__init__(master, padding=12)
        self.tipo = tipo
        self.ao_salvar = ao_salvar
        self._salvos: dict[str, list[str]] = {}
        self._modificado_em: float | None = None

        self.columnconfigure((0, 1), weight=1, uniform="abas")
        self.rowconfigure(2, weight=1)
        self.rotulo_arquivo = quebrar_na_largura(ttk.Label(self, style="Forte.TLabel", wraplength=500,
                                                           justify="left"))
        self.rotulo_arquivo.grid(row=0, column=0, columnspan=2, sticky="ew")
        quebrar_na_largura(ttk.Label(self, text=tipo.dica, style="Suave.TLabel", wraplength=500,
                                     justify="left")).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        self.listas = {}
        for coluna, aba in enumerate(planilhas.ABAS):
            lista = ListaAba(self, tipo, aba, ao_mudar=self._ao_mudar)
            lista.grid(row=2, column=coluna, sticky="nsew", padx=(0, 6) if coluna == 0 else (6, 0))
            self.listas[aba] = lista

        rodape = ttk.Frame(self)
        rodape.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        rodape.columnconfigure(0, weight=1)
        self.rotulo_estado = ttk.Label(rodape, style="Suave.TLabel")
        self.rotulo_estado.grid(row=0, column=0, sticky="w")
        ttk.Button(rodape, text="Recarregar do arquivo", command=self.recarregar).grid(row=0, column=1, padx=4)
        ttk.Button(rodape, text="Salvar planilha", style="Destaque.TButton", command=self.salvar).grid(
            row=0, column=2, padx=(4, 0))
        self.carregar()

    @property
    def caminho(self) -> Path:
        return self.tipo.caminho()

    def _data_do_arquivo(self) -> float | None:
        try:
            return self.caminho.stat().st_mtime
        except OSError:
            return None

    def carregar(self) -> None:
        """Lê a planilha do disco (descarta o que não foi salvo)."""
        caminho = self.caminho
        try:
            abas = self.tipo.ler(caminho)
        except Exception as erro:  # noqa: BLE001 - arquivo corrompido, aberto em outro programa...
            messagebox.showerror(self.tipo.titulo, f"Não consegui ler {caminho_para_mostrar(caminho)}:\n{erro}",
                                 parent=self)
            abas = {}
        self._salvos = {aba: list(abas.get(aba, [])) for aba in planilhas.ABAS}
        self._modificado_em = self._data_do_arquivo()
        for aba, lista in self.listas.items():
            lista.definir(self._salvos[aba])
        situacao = "" if caminho.exists() else " — ainda não existe; será criada ao salvar"
        self.rotulo_arquivo.configure(text=f"{self.tipo.titulo}: {caminho_para_mostrar(caminho)}{situacao}")
        self._ao_mudar()

    def recarregar(self) -> None:
        if self.tem_alteracoes() and not messagebox.askyesno(
                self.tipo.titulo, "Descartar as alterações que ainda não foram salvas?", parent=self):
            return
        self.carregar()

    def recarregar_se_mudou_fora(self) -> None:
        """Relê a planilha se ela foi alterada fora do programa e aqui não há nada pendente."""
        if not self.tem_alteracoes() and self._data_do_arquivo() != self._modificado_em:
            self.carregar()

    def dados(self) -> dict[str, list[str]]:
        return {aba: lista.valores() for aba, lista in self.listas.items()}

    def tem_alteracoes(self) -> bool:
        return self.dados() != self._salvos

    def salvar(self) -> bool:
        """Grava as duas abas na planilha. Devolve True se salvou."""
        caminho = self.caminho
        if self._data_do_arquivo() != self._modificado_em and not messagebox.askyesno(
                self.tipo.titulo,
                f"{caminho_para_mostrar(caminho)} foi alterada fora do programa depois que foi carregada.\n"
                "Salvar assim mesmo, substituindo o que está no arquivo?", parent=self):
            return False
        try:
            self.tipo.salvar(self.dados(), caminho)
        except Exception as erro:  # noqa: BLE001 - mostra o problema em vez de fechar
            messagebox.showerror(
                self.tipo.titulo,
                f"Não consegui salvar {caminho_para_mostrar(caminho)}:\n{erro}\n\n"
                "Se a planilha estiver aberta no Excel ou no LibreOffice, feche e tente de novo.",
                parent=self)
            return False
        self.carregar()
        self.rotulo_estado.configure(text=f"Planilha salva às {datetime.now():%H:%M:%S}.", style="Ok.TLabel")
        if self.ao_salvar:
            self.ao_salvar()
        return True

    def _ao_mudar(self) -> None:
        if self.tem_alteracoes():
            self.rotulo_estado.configure(text="Alterações não salvas.", style="Aviso.TLabel")
        else:
            self.rotulo_estado.configure(text="", style="Suave.TLabel")


class AbaPlanilhas(ttk.Frame):
    """As duas planilhas, cada uma numa sub-aba."""

    def __init__(self, master: tk.Misc, ao_salvar: Callable[[], None] | None = None) -> None:
        super().__init__(master, padding=(8, 8, 8, 0))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.cadernos = ttk.Notebook(self)
        self.cadernos.grid(row=0, column=0, sticky="nsew")
        self.perfis = PainelPlanilha(self.cadernos, PERFIS, ao_salvar)
        self.destinatarios = PainelPlanilha(self.cadernos, DESTINATARIOS, ao_salvar)
        self.cadernos.add(self.perfis, text="Perfis a acessar (para_acessar.xlsx)")
        self.cadernos.add(self.destinatarios, text="Compartilhar com (para_compartilhar.xlsx)")

    @property
    def paineis(self) -> tuple[PainelPlanilha, PainelPlanilha]:
        return self.perfis, self.destinatarios

    def recarregar_se_mudou_fora(self) -> None:
        for painel in self.paineis:
            painel.recarregar_se_mudou_fora()

    def tem_alteracoes(self) -> bool:
        return any(painel.tem_alteracoes() for painel in self.paineis)

    def salvar(self) -> bool:
        """Salva as planilhas com alterações. True se todas foram salvas."""
        return all(painel.salvar() for painel in self.paineis if painel.tem_alteracoes())
