"""Ajustes do terminal e do processo para Linux e Windows."""

import _thread
import os
import sys
import threading
import time
from pathlib import Path

# A interface passa por esta variável o caminho de um arquivo que, quando
# aparece, significa "pare". Funciona igual no Linux e no Windows, com ou sem
# console (no Windows, sem console, não dá para mandar Ctrl+C ao processo).
VARIAVEL_ARQUIVO_PARADA = "AUTOMACAO_ARQUIVO_PARADA"

# Ligado quando a interface pede para parar. As esperas longas usam dormir(),
# que acorda na hora; o time.sleep só atenderia o pedido no fim da espera.
PARADA = threading.Event()


def dormir(segundos: float) -> None:
    """Espera `segundos`, mas acorda assim que a parada for pedida (vira Ctrl+C)."""
    if segundos <= 0:
        return
    if PARADA.wait(segundos):
        # O vigia já pediu o Ctrl+C (interrupt_main); ele chega aqui em instantes.
        # Se por algum motivo não chegar, levanta para não seguir trabalhando.
        time.sleep(0.5)
        raise KeyboardInterrupt


def obter_trava(caminho: Path):
    """Trava exclusiva de arquivo; devolve o arquivo aberto (guarde-o) ou None se já travado.

    O sistema libera a trava quando o processo termina, mesmo se ele cair.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arquivo = open(caminho, "a+", encoding="utf-8")  # noqa: SIM115 - fica aberto até o fim do processo
    try:
        if sys.platform == "win32":
            import msvcrt
            arquivo.seek(0)
            msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        arquivo.close()
        return None
    return arquivo


def preparar_console() -> None:
    """Força UTF-8 na entrada e na saída para os símbolos não quebrarem no Windows.

    O Prompt de Comando do Windows costuma usar cp1252/cp850, que não têm ✔
    nem ✘: sem isto, um print com esses símbolos derruba o programa.
    """
    for fluxo in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # fluxo ausente (pythonw) ou que não é um arquivo de texto


def vigiar_pedido_de_parada(intervalo: float = 0.5) -> None:
    """Se a interface pedir para parar (criando o arquivo), age como um Ctrl+C.

    O KeyboardInterrupt cai no tratamento normal do script, que fecha o
    navegador direito (gravando os logins) e registra o fim no banco.
    """
    caminho = os.environ.get(VARIAVEL_ARQUIVO_PARADA)
    if not caminho:
        return
    alvo = Path(caminho)

    def vigiar() -> None:
        while True:
            if alvo.exists():
                try:
                    alvo.unlink()
                except OSError:
                    pass
                PARADA.set()            # acorda quem estiver numa espera longa
                _thread.interrupt_main()
                return
            time.sleep(intervalo)

    threading.Thread(target=vigiar, name="pedido-de-parada", daemon=True).start()


# ------------------------------------------- saída de várias linhas de execução

_rotulo = threading.local()


def definir_rotulo(rotulo: str | None) -> None:
    """Rótulo das linhas que esta linha de execução imprime (ex.: "TikTok"); None tira."""
    _rotulo.valor = rotulo


class SaidaComRotulos:
    """Envolve a saída para duas redes imprimirem ao mesmo tempo sem misturar.

    Cada linha de execução junta o que escreve até o fim da linha e só então
    a manda inteira, com o rótulo dela na frente ("[TikTok]     curtir  feito").
    Linhas que já começam com "[" (ex.: "[TikTok] Conta: @x") ficam como estão.
    """

    def __init__(self, fluxo) -> None:
        self.fluxo = fluxo
        self._trava = threading.Lock()
        self._pendente = threading.local()

    def write(self, texto: str) -> int:
        juntado = getattr(self._pendente, "texto", "") + texto
        *linhas, resto = juntado.split("\n")
        self._pendente.texto = resto
        if linhas:
            rotulo = getattr(_rotulo, "valor", None)
            if rotulo:
                linhas = [f"[{rotulo}] {linha}" if linha and not linha.startswith("[") else linha
                          for linha in linhas]
            with self._trava:
                self.fluxo.write("\n".join(linhas) + "\n")
        return len(texto)

    def flush(self) -> None:
        with self._trava:
            self.fluxo.flush()

    def descarregar(self) -> None:
        """Manda o pedaço sem fim de linha que esta linha de execução ainda guarda."""
        resto = getattr(self._pendente, "texto", "")
        self._pendente.texto = ""
        if resto:
            with self._trava:
                self.fluxo.write(resto)
                self.fluxo.flush()

    def __getattr__(self, nome: str):
        return getattr(self.fluxo, nome)
