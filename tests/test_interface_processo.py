"""Testes do executor de processos da interface (interface/processo.py), com scripts falsos.

Nenhum teste roda o login (abrir_redes) ou a automação (interagir): os scripts
e módulos são arquivos Python temporários que só imprimem linhas e esperam.
"""

import importlib.util
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from automacao.interface import processo as ip
from automacao.interface.processo import Processo

RAIZ = Path(__file__).resolve().parent.parent
so_linux = pytest.mark.skipif(sys.platform == "win32", reason="sinais POSIX")


def script(tmp_path: Path, codigo: str, nome: str = "falso.py") -> Path:
    caminho = tmp_path / nome
    caminho.write_text(textwrap.dedent(codigo), encoding="utf-8")
    return caminho


def coletar(processo: Processo, timeout: float = 15) -> tuple[list[str], int | None]:
    """Lê a fila até o "fim" (ou até o prazo) e devolve (linhas, código)."""
    linhas, prazo = [], time.monotonic() + timeout
    while time.monotonic() < prazo:
        try:
            tipo, valor = processo.saida.get(timeout=0.1)
        except Exception:  # noqa: BLE001 - queue.Empty
            continue
        if tipo == "fim":
            return linhas, valor
        linhas.append(valor)
    return linhas, None


def vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/status")
    try:
        return "zombie" not in status.read_text()
    except OSError:
        return False


def esperar_morrer(pid: int, timeout: float = 5) -> bool:
    prazo = time.monotonic() + timeout
    while time.monotonic() < prazo:
        if not vivo(pid):
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------ comando e ambiente

def test_comando_no_linux_usa_o_mesmo_python_e_caminho_absoluto(tmp_path):
    comando = ip.montar_comando(tmp_path / "x.py", ["--simular"], windows=False)
    assert comando == [sys.executable, str((tmp_path / "x.py").resolve()), "--simular"]


def test_comando_no_windows_passa_pelo_preambulo(tmp_path):
    comando = ip.montar_comando(tmp_path / "x.py", ["--trace"], windows=True)
    assert comando[:3] == [sys.executable, "-c", ip.PREAMBULO_WINDOWS]
    assert comando[3:] == [str((tmp_path / "x.py").resolve()), "--trace"]


def test_modulos_padrao_sao_os_do_projeto():
    assert (ip.MODULO_LOGIN, ip.MODULO_AUTOMACAO) == ("automacao.abrir_redes", "automacao.interagir")
    for modulo in (ip.MODULO_LOGIN, ip.MODULO_AUTOMACAO):
        especificacao = importlib.util.find_spec(modulo)
        assert especificacao is not None and Path(especificacao.origin).is_relative_to(RAIZ / "automacao")


def test_comando_de_modulo_no_linux_usa_menos_m():
    assert ip.montar_comando("automacao.interagir", ["--simular"], windows=False) == [
        sys.executable, "-m", "automacao.interagir", "--simular"]


def test_comando_de_modulo_no_windows_passa_pelo_preambulo():
    comando = ip.montar_comando("automacao.abrir_redes", ["--timeout", "5"], windows=True)
    assert comando == [sys.executable, "-c", ip.PREAMBULO_WINDOWS_MODULO, "automacao.abrir_redes",
                       "--timeout", "5"]


def test_nome_do_alvo(tmp_path):
    assert ip.nome_do_alvo("automacao.interagir") == "automacao.interagir"
    assert ip.nome_do_alvo(tmp_path / "falso.py") == "falso.py"


def modulo_falso(tmp_path: Path) -> str:
    """Um pacote temporário com um módulo que mostra como foi rodado."""
    pacote = tmp_path / "pacote_falso"
    pacote.mkdir()
    (pacote / "__init__.py").write_text("", encoding="utf-8")
    (pacote / "modulo.py").write_text(textwrap.dedent("""
        import sys
        print("nome", __name__, "args", sys.argv[1:], "arquivo", sys.argv[0].endswith("modulo.py"))
        sys.exit(4)
    """), encoding="utf-8")
    return "pacote_falso.modulo"


@pytest.mark.parametrize("windows", [False, True])
def test_modulo_roda_como_principal_a_partir_da_raiz(tmp_path, windows):
    """Com -m (Linux) ou pelo preâmbulo do Windows, o módulo roda como __main__ e recebe os argumentos."""
    modulo = modulo_falso(tmp_path)
    processo = Processo(ip.montar_comando(modulo, ["--simular"], windows=windows), cwd=tmp_path)
    processo.iniciar()
    linhas, codigo = coletar(processo)
    assert linhas == ["nome __main__ args ['--simular'] arquivo True"]
    assert codigo == 4


@pytest.mark.parametrize(("simular", "trace", "esperado"), [
    (False, False, []), (True, False, ["--simular"]), (False, True, ["--trace"]),
    (True, True, ["--simular", "--trace"]),
])
def test_argumentos_automacao(simular, trace, esperado):
    assert ip.argumentos_automacao(simular, trace) == esperado


def test_ambiente_sem_buffer_e_em_utf8():
    ambiente = ip.ambiente_filho({"PATH": "/bin", "PYTHONIOENCODING": "latin-1"})
    assert ambiente == {"PATH": "/bin", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}


def test_preambulo_roda_o_script_como_principal(tmp_path):
    """O preâmbulo do Windows também funciona aqui (sem SIGBREAK, só roda o script)."""
    falso = script(tmp_path, """
        import sys
        print("nome", __name__, "args", sys.argv[1:], "arquivo", __file__.endswith("falso.py"))
        sys.exit(3)
    """)
    processo = Processo(ip.montar_comando(falso, ["--simular"], windows=True), cwd=tmp_path)
    processo.iniciar()
    linhas, codigo = coletar(processo)
    assert linhas == ["nome __main__ args ['--simular'] arquivo True"]
    assert codigo == 3


# ------------------------------------------------------------ execução

def test_saida_linha_a_linha_com_acentos_e_stderr(tmp_path):
    falso = script(tmp_path, """
        import sys
        print("[TikTok] Já logado ✔")
        print("[Instagram] Não logado — faça login")
        print("erro no stderr", file=sys.stderr)
        print("✔ TikTok  ✘ Instagram")
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path)
    processo.iniciar()
    linhas, codigo = coletar(processo)
    assert codigo == 0 and processo.codigo == 0 and not processo.rodando
    assert linhas == ["[TikTok] Já logado ✔", "[Instagram] Não logado — faça login",
                      "erro no stderr", "✔ TikTok  ✘ Instagram"]


def test_linhas_chegam_enquanto_o_processo_roda(tmp_path):
    falso = script(tmp_path, """
        import time
        print("primeira")
        time.sleep(30)
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path, espera_interromper=2, espera_terminar=2)
    processo.iniciar()
    try:
        tipo, valor = processo.saida.get(timeout=10)
        assert (tipo, valor) == ("linha", "primeira")
        assert processo.rodando
    finally:
        processo.matar()


def test_stdin_e_vazio_e_cwd_e_o_pedido(tmp_path):
    falso = script(tmp_path, """
        import os, sys
        print(sys.stdin.isatty(), repr(sys.stdin.read()), os.getcwd())
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path)
    processo.iniciar()
    linhas, _ = coletar(processo)
    assert linhas == [f"False '' {tmp_path}"]


def test_codigo_de_erro(tmp_path):
    falso = script(tmp_path, "raise SystemExit(2)\n")
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path)
    processo.iniciar()
    assert coletar(processo)[1] == 2


def test_nao_inicia_duas_vezes(tmp_path):
    processo = Processo(ip.montar_comando(script(tmp_path, "pass\n")), cwd=tmp_path)
    processo.iniciar()
    with pytest.raises(RuntimeError):
        processo.iniciar()
    coletar(processo)


def test_parar_processo_que_ja_terminou_nao_faz_nada(tmp_path):
    processo = Processo(ip.montar_comando(script(tmp_path, "pass\n")), cwd=tmp_path)
    processo.parar()  # nem iniciou
    processo.iniciar()
    coletar(processo)
    processo.parar()
    processo.matar()
    assert processo.codigo == 0


# ------------------------------------------------------------ parada

@so_linux
def test_parar_e_como_ctrl_c(tmp_path):
    """Primeiro sinal é um SIGINT: o script trata o KeyboardInterrupt e sai sozinho."""
    falso = script(tmp_path, """
        import time
        print("rodando", flush=True)
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            print("Interrompido.")
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path, espera_interromper=5, espera_terminar=2)
    processo.iniciar()
    assert processo.saida.get(timeout=10) == ("linha", "rodando")
    inicio = time.monotonic()
    processo.parar()
    linhas, codigo = coletar(processo)
    assert linhas == ["Interrompido."]
    assert codigo == 0 and processo.parada_pedida
    assert time.monotonic() - inicio < 4


@so_linux
def test_parar_escala_e_mata_o_grupo_todo(tmp_path):
    """Script que ignora SIGINT e SIGTERM e abriu um processo filho (como o Chrome):
    os dois morrem, sem sobrar órfão."""
    falso = script(tmp_path, """
        import signal, subprocess, sys, time
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        filho = subprocess.Popen([sys.executable, "-c",
            "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
        print(filho.pid, flush=True)
        time.sleep(60)
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path, espera_interromper=0.5, espera_terminar=0.5)
    processo.iniciar()
    tipo, pid_filho = processo.saida.get(timeout=10)
    pid_filho = int(pid_filho)
    try:
        processo.parar()
        _, codigo = coletar(processo)
        assert codigo == -9  # SIGKILL
        assert esperar_morrer(pid_filho)
    finally:
        if vivo(pid_filho):
            os.kill(pid_filho, 9)


@so_linux
def test_segundo_pedido_forca_na_hora(tmp_path):
    falso = script(tmp_path, """
        import signal, time
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        print("pronto", flush=True)
        time.sleep(60)
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path, espera_interromper=30, espera_terminar=30)
    processo.iniciar()
    processo.saida.get(timeout=10)
    processo.parar()
    time.sleep(0.3)
    assert processo.rodando  # ignorou o "Ctrl+C" e a espera é longa
    inicio = time.monotonic()
    processo.parar(forcar=True)
    _, codigo = coletar(processo)
    assert codigo == -9 and time.monotonic() - inicio < 5


@so_linux
def test_a_interface_nao_recebe_os_sinais(tmp_path):
    """O filho fica em outro grupo: o SIGINT mandado a ele não chega ao pytest."""
    falso = script(tmp_path, "import time; print('ok', flush=True); time.sleep(30)\n")
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path, espera_interromper=5)
    processo.iniciar()
    processo.saida.get(timeout=10)
    assert os.getpgid(processo.pid) == processo.pid != os.getpgid(0)
    processo.parar()
    assert coletar(processo)[1] is not None


def test_saida_segurada_por_neto_nao_trava_o_fim(tmp_path):
    """Se um neto herda a saída e continua vivo, o fim do processo é avisado mesmo assim."""
    falso = script(tmp_path, """
        import subprocess, sys
        filho = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(8)"])
        print(filho.pid, flush=True)
    """)
    processo = Processo(ip.montar_comando(falso), cwd=tmp_path)
    processo.iniciar()
    linhas, codigo = coletar(processo, timeout=6)
    try:
        assert codigo == 0
    finally:
        if linhas and sys.platform != "win32" and vivo(int(linhas[0])):
            os.kill(int(linhas[0]), 9)


def test_argumentos_automacao_modo_teste_de_comentarios():
    assert ip.argumentos_automacao(verificar_comentarios=True) == ["--verificar-comentarios"]
    assert ip.argumentos_automacao(True, True, True) == ["--simular", "--trace", "--verificar-comentarios"]
