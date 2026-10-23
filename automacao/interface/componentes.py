"""Peças de Tkinter usadas por mais de uma aba.

- rótulos que quebram o texto na largura que ocupam (nada é cortado quando a
  janela fica estreita, e o texto aproveita a largura quando ela cresce);
- uma área com barra de rolagem vertical, para abas mais altas que a janela;
- larguras medidas pela fonte (acompanham o zoom da tela, ao contrário de pixels fixos);
- uma paleta de emojis que põe o emoji no campo de texto, onde está o cursor.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from automacao.caminhos import PASTA_EMOJIS  # imagens dos botões (emojis/gerar.py)

# Widgets que já usam a roda do mouse para si (rolar a lista, mudar o número...).
_USAM_A_RODA = {"Listbox", "Text", "Treeview", "TCombobox", "TSpinbox", "Spinbox", "Scale", "TScale"}

# Emojis da paleta dos comentários, com o nome (mostrado ao passar o mouse: onde a
# fonte da janela não desenha o emoji, ele aparece como um quadradinho, mas vai certo
# no comentário). O Tk 9 guarda qualquer emoji; o Tk 8.6 (que vem com alguns Pythons
# do Windows) erra os que ficam fora do plano básico do Unicode (a maioria dos
# coloridos), então lá a paleta só tem símbolos do plano básico.
EMOJIS = (("🔥", "fogo"), ("👏", "palmas"), ("❤️", "coração"), ("😍", "apaixonado"), ("🙌", "mãos para cima"),
          ("💪", "força"), ("👍", "joinha"), ("😂", "chorando de rir"), ("🤩", "deslumbrado"), ("✨", "brilho"),
          ("💯", "cem"), ("🚀", "foguete"), ("🙏", "mãos juntas"), ("😎", "óculos escuros"), ("🥰", "carinho"),
          ("👀", "olhos"), ("🎉", "festa"), ("💙", "coração azul"), ("⭐", "estrela"), ("✅", "certo"),
          ("🇧🇷", "Brasil"), ("😉", "piscadinha"), ("🤝", "aperto de mãos"), ("👊", "soquinho"))
EMOJIS_BASICOS = (("❤", "coração"), ("♥", "coração (naipe)"), ("✨", "brilho"), ("⭐", "estrela"),
                  ("★", "estrela cheia"), ("✅", "certo"), ("✔", "visto"), ("☀", "sol"), ("⚡", "raio"),
                  ("✌", "paz"), ("☺", "sorriso"), ("☕", "café"), ("⚽", "bola"), ("♪", "nota musical"),
                  ("✿", "flor"), ("☘", "trevo"), ("✈", "avião"), ("☝", "dedo para cima"), ("✍", "escrevendo"),
                  ("❣", "coração exclamação"), ("➡", "seta"), ("☑", "caixa marcada"), ("♛", "coroa"),
                  ("☼", "sol (contorno)"))
EMOJIS_POR_LINHA = 12
DICA_PALETA = ("Clique num emoji para pôr no comentário (passe o mouse para ver o nome). Se no campo ele "
               "aparecer como um quadradinho, é só a fonte desta janela: no Instagram e no TikTok sai certo.")


def arquivo_do_emoji(emoji: str) -> str:
    """Nome da imagem do emoji: os códigos em hexadecimal ("🇧🇷" -> "1f1e7-1f1f7.png")."""
    return "-".join(f"{ord(letra):x}" for letra in emoji) + ".png"


def emojis_da_paleta(versao_tk: float | None = None) -> tuple[tuple[str, str], ...]:
    """(emoji, nome) que a paleta oferece nesta versão do Tk."""
    versao = tk.TkVersion if versao_tk is None else versao_tk
    return EMOJIS if versao >= 9 else EMOJIS_BASICOS


# Folga para alargar a quebra do texto: diferenças menores não requebram. Sem
# ela, um rótulo que muda de altura ao requebrar pode fazer o vizinho (uma
# barra de rolagem, outra coluna) mudar a largura dele, num vaivém sem fim.
FOLGA_QUEBRA = 4


def quebrar_na_largura(rotulo: ttk.Label, margem: int = 6, minimo: int = 120) -> ttk.Label:
    """Faz o texto do rótulo quebrar na largura que ele ocupa na tela.

    O rótulo precisa ser posto com sticky="ew" (ou fill="x"), para ocupar a
    largura disponível; `margem` desconta a borda interna do próprio rótulo.
    Crie-o com um wraplength inicial modesto: ele vira o pedido de largura
    enquanto a janela não aparece.

    Quando o rótulo estreita, o texto requebra na hora (nada fica cortado); quando
    alarga, só requebra se sobrar mais que FOLGA_QUEBRA pixels.
    """
    def ajustar(evento: tk.Event) -> None:
        largura = max(evento.width - margem, minimo)
        try:
            atual = int(str(rotulo.cget("wraplength")) or 0)
        except ValueError:
            atual = 0
        if largura < atual or largura - atual > FOLGA_QUEBRA:
            rotulo.configure(wraplength=largura)

    rotulo.bind("<Configure>", ajustar, add="+")
    return rotulo


def largura_do_texto(widget: tk.Misc, texto: str, folga: int = 16, fonte: str = "TkDefaultFont") -> int:
    """Largura em pixels de `texto` na fonte, mais uma folga (para colunas de tabelas)."""
    return tkfont.nametofont(fonte, root=widget).measure(texto) + folga


class PaletaEmojis(ttk.Frame):
    """Botões pequenos que põem um emoji no campo `alvo` (um Entry), onde está o cursor.

    Se houver texto selecionado no campo, o emoji entra no lugar dele. Os botões
    não pegam o foco: o cursor continua no campo, e dá para pôr vários seguidos.
    Embaixo, uma linha diz o nome do emoji sob o mouse.
    """

    def __init__(self, master: tk.Misc, alvo: tk.Entry | ttk.Entry, versao_tk: float | None = None,
                 **opcoes) -> None:
        super().__init__(master, **opcoes)
        self.alvo = alvo
        ttk.Style(self).configure("Emoji.TButton", padding=(2, 0))
        self.emojis = emojis_da_paleta(versao_tk)
        self.botoes: list[ttk.Button] = []
        self._imagens: list[tk.PhotoImage] = []   # o Tk só mostra a imagem enquanto houver referência
        for indice, (emoji, nome) in enumerate(self.emojis):
            botao = ttk.Button(self, text=emoji, width=3, style="Emoji.TButton", takefocus=False,
                               command=lambda emoji=emoji: self.inserir(emoji))
            imagem = self._imagem(emoji)
            if imagem is not None:  # a imagem aparece colorida mesmo onde a fonte não desenha o emoji
                botao.configure(image=imagem, compound="image", width=0)
            botao.grid(row=indice // EMOJIS_POR_LINHA, column=indice % EMOJIS_POR_LINHA, padx=1, pady=1)
            botao.bind("<Enter>", lambda _e, nome=nome: self.dica.configure(text=nome))
            botao.bind("<Leave>", lambda _e: self.dica.configure(text=DICA_PALETA))
            self.botoes.append(botao)
        linhas = -(-len(self.emojis) // EMOJIS_POR_LINHA)
        self.dica = ttk.Label(self, text=DICA_PALETA, style="Suave.TLabel")
        self.dica.grid(row=linhas, column=0, columnspan=EMOJIS_POR_LINHA, sticky="w", pady=(2, 0))

    def _imagem(self, emoji: str) -> tk.PhotoImage | None:
        caminho = PASTA_EMOJIS / arquivo_do_emoji(emoji)
        try:
            imagem = tk.PhotoImage(master=self, file=str(caminho))
        except (tk.TclError, OSError):
            return None  # sem a imagem, o botão mostra o próprio emoji como texto
        self._imagens.append(imagem)
        return imagem

    def inserir(self, emoji: str) -> None:
        alvo = self.alvo
        posicao = alvo.index("insert")
        try:
            if alvo.selection_present():  # o emoji entra no lugar do que está selecionado
                posicao = alvo.index("sel.first")
                alvo.delete("sel.first", "sel.last")
        except tk.TclError:
            pass  # sem seleção
        alvo.insert(posicao, emoji)
        alvo.icursor(posicao + len(emoji))  # o cursor fica depois do emoji
        alvo.focus_set()


class AreaRolavel(ttk.Frame):
    """Área com barra de rolagem vertical. Ponha o conteúdo em `self.interior`.

    O interior ocupa toda a largura visível e tem a altura que pedir; a roda do
    mouse rola a área quando o ponteiro está sobre ela (menos sobre listas e
    campos que usam a roda para si).
    """

    def __init__(self, master: tk.Misc, **opcoes) -> None:
        super().__init__(master, **opcoes)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        fundo = ttk.Style(self).lookup("TFrame", "background") or None
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, height=120,
                                yscrollincrement=20, **({"background": fundo} if fundo else {}))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.barra = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.barra.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.canvas.configure(yscrollcommand=self.barra.set)
        self.interior = ttk.Frame(self.canvas)
        self._janela = self.canvas.create_window(0, 0, window=self.interior, anchor="nw")
        self.interior.bind("<Configure>", self._ao_mudar_interior)
        self.canvas.bind("<Configure>", self._ao_mudar_canvas)
        for sequencia in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(sequencia, self._rolar, add="+")

    def ajustar_largura_pedida(self) -> None:
        """Pede a largura do conteúdo (chame depois de montar o interior)."""
        self.interior.update_idletasks()
        self.canvas.configure(width=self.interior.winfo_reqwidth())

    def ir_para_o_topo(self) -> None:
        self.canvas.yview_moveto(0)

    def _ao_mudar_interior(self, _evento=None) -> None:
        self.canvas.configure(scrollregion=(0, 0, self.interior.winfo_width(), self.interior.winfo_height()))

    def _ao_mudar_canvas(self, evento: tk.Event) -> None:
        self.canvas.itemconfigure(self._janela, width=evento.width)

    def _dentro(self, widget: object) -> bool:
        if not isinstance(widget, tk.Misc):
            return False
        caminho, meu = str(widget), str(self)
        return caminho == meu or caminho.startswith(meu + ".")

    def _rolar(self, evento: tk.Event) -> str | None:
        widget = evento.widget
        if not self._dentro(widget):
            return None
        try:
            if (not self.winfo_ismapped() or widget.winfo_class() in _USAM_A_RODA
                    or self.interior.winfo_height() <= self.canvas.winfo_height()):
                return None  # escondida, widget que usa a roda, ou tudo cabe
        except tk.TclError:
            return None
        if evento.num == 4:
            passos = -3
        elif evento.num == 5:
            passos = 3
        elif evento.delta:
            passos = -3 if evento.delta > 0 else 3
        else:
            return None
        self.canvas.yview_scroll(passos, "units")
        return "break"
