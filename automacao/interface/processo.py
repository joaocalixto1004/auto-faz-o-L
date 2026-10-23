"""Execução do login e da automação em segundo plano, para a interface.

O login (automacao.abrir_redes) e a automação (automacao.interagir) rodam num
processo filho, com "python -m", a partir da raiz do projeto. Uma thread lê a saída (stdout e stderr
juntos) linha a linha e põe numa fila (queue.Queue), que a interface consulta
com after(), sem travar a janela.

Para parar, primeiro manda o equivalente a um Ctrl+C, para o script gravar o
fim da execução no banco e fechar o navegador direito. Se ele não sair em
alguns segundos, encerra à força o processo e tudo o que ele abriu (o driver
do Playwright e o Chrome), para não sobrar processo órfão.

Quando o script termina por qualquer motivo (parada, erro, queda), o que ele
deixou para trás e não fechou sozinho em alguns segundos também é encerrado:
um driver ou um Chrome órfão seguraria o perfil do navegador. Se ele terminou
sem deixar ninguém, nada é morto.

Também fica aqui a trava de "uma janela por vez" (travar_interface).
"""

import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

from automacao.caminhos import PASTA_LOGS, RAIZ
from automacao.terminal import VARIAVEL_ARQUIVO_PARADA, obter_trava

# O que a interface roda: módulos do projeto, com "python -m" (a pasta de
# trabalho do processo filho é a raiz do projeto, onde fica o pacote).
MODULO_LOGIN = "automacao.abrir_redes"
MODULO_AUTOMACAO = "automacao.interagir"

NO_WINDOWS = sys.platform == "win32"

# Uma janela da interface por vez: duas janelas abririam o login ou a automação
# no mesmo perfil do Chrome. O sistema solta a trava quando a janela fecha.
TRAVA_INTERFACE = PASTA_LOGS / "interface.trava"

# No Windows, o único sinal que dá para mandar só para o grupo do processo filho
# é o Ctrl+Break, e ele encerra o Python na hora, sem passar pelos finally que
# gravam o fim da execução e fecham o navegador. Estes preâmbulos fazem o
# Ctrl+Break virar KeyboardInterrupt (como um Ctrl+C) e depois rodam o módulo
# (como o "python -m") ou o script normalmente.
_TRATAR_CTRL_BREAK = (
    "import runpy, signal, sys; "
    "hasattr(signal, 'SIGBREAK') and signal.signal(signal.SIGBREAK, signal.default_int_handler); "
    "sys.argv = sys.argv[1:]; "
)
PREAMBULO_WINDOWS_MODULO = _TRATAR_CTRL_BREAK + "runpy.run_module(sys.argv[0], run_name='__main__', alter_sys=True)"
PREAMBULO_WINDOWS = _TRATAR_CTRL_BREAK + "runpy.run_path(sys.argv[0], run_name='__main__')"

ESPERA_INTERROMPER = 10.0  # segundos para o script sair sozinho depois do "Ctrl+C"
ESPERA_TERMINAR = 5.0      # segundos depois do pedido de encerramento (SIGTERM)
ESPERA_MATAR = 3.0         # segundos depois de matar à força
ESPERA_RESTOS = 3.0        # segundos para o que o script abriu sair sozinho depois que ele saiu


def travar_interface():
    """Trava de "uma janela por vez": o arquivo aberto (guarde-o até o fim) ou None se outra janela já tem."""
    return obter_trava(TRAVA_INTERFACE)


def montar_comando(alvo: str | Path, argumentos: Sequence[str] = (),
                   windows: bool = NO_WINDOWS) -> list[str]:
    """Linha de comando para rodar o alvo com o mesmo Python da interface.

    `alvo` é o nome de um módulo do projeto (str, ex.: MODULO_LOGIN), rodado
    como "python -m", ou o caminho de um script .py (Path; os testes usam
    scripts falsos).
    """
    if isinstance(alvo, Path):
        caminho = str(alvo.resolve())
        if windows:
            return [sys.executable, "-c", PREAMBULO_WINDOWS, caminho, *argumentos]
        return [sys.executable, caminho, *argumentos]
    if windows:
        return [sys.executable, "-c", PREAMBULO_WINDOWS_MODULO, alvo, *argumentos]
    return [sys.executable, "-m", alvo, *argumentos]


def nome_do_alvo(alvo: str | Path) -> str:
    """Como o alvo aparece para o usuário: o nome do módulo ou do arquivo."""
    return alvo.name if isinstance(alvo, Path) else alvo


def argumentos_login(perfil_id: int | None = None) -> list[str]:
    """Opções do login (abrir_redes): a conta (perfil do navegador) em que logar."""
    return [] if perfil_id is None else ["--perfil", str(perfil_id)]


def argumentos_automacao(simular: bool = False, trace: bool = False,
                         verificar_comentarios: bool = False) -> list[str]:
    """Opções da automação (interagir) marcadas na interface (o resto vem do banco e das planilhas)."""
    argumentos = []
    if simular:
        argumentos.append("--simular")
    if trace:
        argumentos.append("--trace")
    if verificar_comentarios:
        argumentos.append("--verificar-comentarios")
    return argumentos


def ambiente_filho(base: dict[str, str] | None = None) -> dict[str, str]:
    """Variáveis de ambiente do processo filho.

    Sem buffer na saída (as linhas chegam na hora) e sempre em UTF-8: no Windows,
    o console usaria outra codificação e o "✔" dos scripts daria erro.
    """
    ambiente = dict(os.environ if base is None else base)
    ambiente["PYTHONUNBUFFERED"] = "1"
    ambiente["PYTHONIOENCODING"] = "utf-8"
    return ambiente


def _matar_arvore_windows(pid: int) -> None:
    """Mata o processo e os filhos dele (driver do Playwright, Chrome) no Windows."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        pass  # taskkill ausente ou travado: não há mais o que fazer


def _filhos_windows(pid: int) -> list[int]:
    """PIDs dos processos cujo pai é `pid`, no Windows.

    Lá os filhos continuam apontando para o pai mesmo depois que ele sai (o
    taskkill /T no pai que já saiu não acha mais ninguém). Enquanto a interface
    guarda o processo (Popen), o Windows não reaproveita o número dele.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class Entrada(ctypes.Structure):  # PROCESSENTRY32W
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entrada)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entrada)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        foto = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not foto or foto == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
            return []
        filhos = []
        try:
            entrada = Entrada()
            entrada.dwSize = ctypes.sizeof(Entrada)
            tem = kernel32.Process32FirstW(foto, ctypes.byref(entrada))
            while tem:
                if entrada.th32ParentProcessID == pid and entrada.th32ProcessID not in (pid, 0):
                    filhos.append(int(entrada.th32ProcessID))
                tem = kernel32.Process32NextW(foto, ctypes.byref(entrada))
        finally:
            kernel32.CloseHandle(foto)
        return filhos
    except (OSError, AttributeError, ValueError):
        return []  # sem a API (não é Windows) ou ela falhou: não mata ninguém


def _grupo_tem_processos(grupo: int) -> bool:
    """No Linux: ainda há algum processo no grupo (o script, o driver...)?"""
    try:
        os.killpg(grupo, 0)
    except OSError:  # o grupo acabou (ou não é mais nosso)
        return False
    return True


class Processo:
    """Um script rodando em segundo plano, com a saída numa fila.

    A fila `saida` recebe ("linha", texto) para cada linha que o script
    imprime e, no fim, ("fim", código de saída). Linhas que chegarem depois do
    "fim" (de um processo neto que ainda segurava a saída) também são entregues.
    """

    def __init__(self, comando: Sequence[str], cwd: Path = RAIZ,
                 ambiente: dict[str, str] | None = None,
                 espera_interromper: float = ESPERA_INTERROMPER,
                 espera_terminar: float = ESPERA_TERMINAR) -> None:
        self.comando = list(comando)
        self.cwd = Path(cwd)
        self.ambiente = dict(ambiente_filho() if ambiente is None else ambiente)
        # Arquivo de "pare" vigiado pelo script (terminal.vigiar_pedido_de_parada).
        self.arquivo_parada = Path(tempfile.gettempdir()) / f"automacao-parar-{uuid.uuid4().hex}"
        self.ambiente[VARIAVEL_ARQUIVO_PARADA] = str(self.arquivo_parada)
        self.espera_interromper = espera_interromper
        self.espera_terminar = espera_terminar
        self.saida: queue.Queue[tuple[str, object]] = queue.Queue()
        self.parada_pedida = False
        self.iniciado_em: float | None = None
        self._proc: subprocess.Popen | None = None
        self._leitor: threading.Thread | None = None
        self._terminou = threading.Event()
        self._limpo = threading.Event()  # terminou e os restos (Chrome, driver) já foram encerrados
        self._trava = threading.Lock()

    # ------------------------------------------------------------ estado

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def rodando(self) -> bool:
        return self._proc is not None and not self._terminou.is_set()

    @property
    def codigo(self) -> int | None:
        """Código de saída, depois que o processo terminou."""
        return self._proc.returncode if self._terminou.is_set() else None

    def esperar(self, timeout: float | None = None) -> bool:
        """Espera o processo terminar. True se terminou dentro do prazo."""
        return self._terminou.wait(timeout)

    def esperar_limpeza(self, timeout: float | None = None) -> bool:
        """Espera o processo terminar E os restos (Chrome, driver) serem encerrados."""
        if self._proc is None:
            return True  # nem chegou a iniciar
        return self._limpo.wait(timeout)

    # ------------------------------------------------------------ início

    def iniciar(self) -> None:
        """Inicia o processo. Levanta OSError se não der para iniciar."""
        if self._proc is not None:
            raise RuntimeError("este processo já foi iniciado")
        opcoes: dict = {}
        if NO_WINDOWS:
            # Grupo próprio: o Ctrl+Break vai só para ele, não para a interface.
            opcoes["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Sessão própria: dá para mandar sinais ao grupo todo (script,
            # driver do Playwright e Chrome) sem atingir a interface.
            opcoes["start_new_session"] = True
        self._proc = subprocess.Popen(
            self.comando, cwd=str(self.cwd), env=self.ambiente,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", **opcoes,
        )
        self.iniciado_em = time.monotonic()
        self._leitor = threading.Thread(target=self._ler_saida, name="leitor-saida", daemon=True)
        self._leitor.start()
        threading.Thread(target=self._esperar_fim, name="espera-processo", daemon=True).start()

    def _ler_saida(self) -> None:
        try:
            for linha in self._proc.stdout:
                self.saida.put(("linha", linha.rstrip("\r\n")))
        except (OSError, ValueError):
            pass  # saída fechada no meio da leitura
        finally:
            try:
                self._proc.stdout.close()
            except OSError:
                pass

    def _esperar_fim(self) -> None:
        codigo = self._proc.wait()
        try:
            # Qualquer que seja o motivo do fim (parada, erro, queda), o driver e o
            # Chrome que sobraram seguram o perfil: são encerrados antes de avisar o
            # fim, para a interface não iniciar outro processo no mesmo perfil.
            self._limpar_restos()
        finally:
            # Dá um tempo para chegar o resto da saída. Se um processo neto ainda
            # segurar a saída aberta, o fim é avisado mesmo assim.
            self._leitor.join(timeout=2)
            try:
                self.arquivo_parada.unlink(missing_ok=True)
            except OSError:
                pass
            self._terminou.set()
            self._limpo.set()
            self.saida.put(("fim", codigo))

    # ------------------------------------------------------------ parada

    def parar(self, forcar: bool = False) -> None:
        """Pede para o processo parar, sem travar quem chamou.

        Primeiro como um Ctrl+C; se não sair em `espera_interromper` segundos,
        pede para encerrar; por fim, mata o grupo todo. Com `forcar`, mata na hora.
        """
        if not self.rodando:
            return
        with self._trava:
            primeira_vez = not self.parada_pedida
            self.parada_pedida = True
        if forcar:
            threading.Thread(target=self.matar, name="matar-processo", daemon=True).start()
        elif primeira_vez:
            threading.Thread(target=self._parar_em_etapas, name="parar-processo", daemon=True).start()

    def matar(self, espera: float = ESPERA_MATAR) -> None:
        """Mata o processo e o que ele abriu, sem esperar que gravem nada. Bloqueia até `espera` s.

        O que sobrar depois que o script sair é encerrado pela thread que espera o fim.
        """
        self.parada_pedida = True
        if self.rodando:
            self._sinalizar("matar")
            self._terminou.wait(espera)

    def _parar_em_etapas(self) -> None:
        for etapa, espera in (("interromper", self.espera_interromper),
                              ("terminar", self.espera_terminar)):
            if not self.rodando:
                return
            if self._sinalizar(etapa) and self._terminou.wait(espera):
                return
        self.matar()

    def _sinalizar(self, etapa: str) -> bool:
        """Manda o sinal da etapa ("interromper", "terminar" ou "matar"). False se não deu."""
        if self._proc is None:
            return False
        pid = self._proc.pid
        try:
            if etapa == "interromper":
                if self._proc.returncode is not None:
                    return False  # o script já saiu; os restos são encerrados pela espera do fim
                # Pede ao script para parar como um Ctrl+C, sem atingir o Chrome e o
                # driver: o script fecha o navegador direito e os logins ficam gravados.
                if NO_WINDOWS:
                    self.arquivo_parada.touch()  # funciona mesmo sem console (pythonw)
                else:
                    os.kill(pid, signal.SIGINT)  # só o script, não o grupo
            elif NO_WINDOWS:
                # Com o script já fora, o taskkill /T nele não acha os filhos: vai em cada um.
                for alvo in ([pid] if self._proc.returncode is None else _filhos_windows(pid)):
                    _matar_arvore_windows(alvo)
                if etapa == "matar":
                    self._proc.kill()
            else:
                sinal = {"terminar": signal.SIGTERM, "matar": signal.SIGKILL}[etapa]
                os.killpg(pid, sinal)  # o grupo todo: script, driver e navegador
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _limpar_restos(self) -> None:
        """Encerra o que o script deixou para trás depois que ele saiu (driver do Playwright, Chrome).

        Vale para qualquer fim (parada, erro, queda). Os restos têm ESPERA_RESTOS
        segundos para sair sozinhos (o Chrome fechando com calma); depois, são
        mortos: no Linux, o grupo do script; no Windows, a árvore de cada filho
        (taskkill /T). Se o script não deixou ninguém, nada é morto.
        """
        if self._proc is None:
            return
        pid = self._proc.pid
        prazo = time.monotonic() + ESPERA_RESTOS
        while True:
            restos = _filhos_windows(pid) if NO_WINDOWS else _grupo_tem_processos(pid)
            if not restos:
                return  # não sobrou ninguém
            if time.monotonic() >= prazo:
                break
            time.sleep(0.1)
        if NO_WINDOWS:
            for filho in restos:
                _matar_arvore_windows(filho)
            return
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
