"""Testes da conferência e da preparação da instalação (automacao/instalacao.py).

Nenhum teste instala nada: o conftest proíbe o _executar (que rodaria o uv e o
"playwright install"), e aqui ele é trocado por uma função que só anota o
comando. O log e o marcador ficam em tmp_path.
"""

import json
import sys
from pathlib import Path

import pytest

from automacao import instalacao
from automacao.instalacao import Conferencia
from automacao.instalacao import _executar as executar_de_verdade  # antes do conftest trocar


@pytest.fixture
def requisitos(tmp_path, monkeypatch):
    caminho = tmp_path / "requirements.txt"
    caminho.write_text("playwright>=1.63\nopenpyxl>=3.1\n", encoding="utf-8")
    monkeypatch.setattr(instalacao, "REQUISITOS", caminho)
    return caminho


class Comandos:
    """Substitui o _executar: anota cada comando e devolve o código pedido."""

    def __init__(self, monkeypatch, codigo=0, ao_rodar=None):
        self.rodados = []
        self.codigo = codigo
        self.ao_rodar = ao_rodar
        monkeypatch.setattr(instalacao, "_executar", self)

    def __call__(self, comando, registro):
        self.rodados.append(list(comando))
        if self.ao_rodar:
            self.ao_rodar(comando)
        return self.codigo


@pytest.fixture
def ambiente_pronto(monkeypatch, requisitos):
    """Dentro de um venv, com o Google Chrome e o marcador em dia."""
    monkeypatch.setattr(instalacao, "em_venv", lambda: True)
    monkeypatch.setattr(instalacao, "localizar_chrome", lambda: Path("/opt/google/chrome/chrome"))
    instalacao.gravar_marcador(instalacao.hash_requisitos())


# ------------------------------------------------------------ hash e marcador

def test_hash_muda_com_o_conteudo_e_ignora_o_fim_de_linha(tmp_path):
    unix, windows, outro = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"
    unix.write_bytes(b"playwright>=1.63\nopenpyxl>=3.1\n")
    windows.write_bytes(b"playwright>=1.63\r\nopenpyxl>=3.1\r\n")
    outro.write_bytes(b"playwright>=1.64\nopenpyxl>=3.1\n")
    assert instalacao.hash_requisitos(unix) == instalacao.hash_requisitos(windows)
    assert instalacao.hash_requisitos(unix) != instalacao.hash_requisitos(outro)


def test_hash_muda_com_a_versao_do_preparo(tmp_path, monkeypatch, requisitos):
    antes = instalacao.hash_requisitos()
    monkeypatch.setattr(instalacao, "VERSAO_PREPARO", instalacao.VERSAO_PREPARO + 1)
    assert instalacao.hash_requisitos() != antes


def test_marcador_ida_e_volta(tmp_path):
    caminho = tmp_path / "marcador.json"
    assert instalacao.ler_marcador(caminho) == {}
    instalacao.gravar_marcador("abc", caminho)
    dados = instalacao.ler_marcador(caminho)
    assert dados["hash_requisitos"] == "abc" and dados["python"]
    caminho.write_text("isto não é json", encoding="utf-8")
    assert instalacao.ler_marcador(caminho) == {}
    caminho.write_text("[1, 2]", encoding="utf-8")
    assert instalacao.ler_marcador(caminho) == {}


# ------------------------------------------------------------ dependências

def test_dependencias_so_ficam_em_dia_com_o_marcador_certo(requisitos, tmp_path):
    assert not instalacao.conferir_dependencias().ok          # sem marcador
    instalacao.gravar_marcador(instalacao.hash_requisitos())
    item = instalacao.conferir_dependencias()
    assert item.ok and "playwright" in item.detalhe and "openpyxl" in item.detalhe
    requisitos.write_text("playwright>=2\nopenpyxl>=3.1\n", encoding="utf-8")
    assert not instalacao.conferir_dependencias().ok          # requirements.txt mudou


def test_dependencia_faltando(monkeypatch):
    monkeypatch.setattr(instalacao, "MODULOS_NECESSARIOS", ("modulo_que_nao_existe_xyz",))
    item = instalacao.conferir_dependencias()
    assert not item.ok and "modulo_que_nao_existe_xyz" in item.detalhe


# ------------------------------------------------------------ Tkinter e navegador

def test_tkinter_funciona_neste_ambiente():
    pytest.importorskip("tkinter")
    item = instalacao.conferir_tkinter()
    assert item.ok and item.detalhe.startswith("Tcl/Tk ")


def test_localizar_chrome_no_windows(tmp_path):
    chrome = tmp_path / "Programas" / "Google" / "Chrome" / "Application" / "chrome.exe"
    assert instalacao.localizar_chrome({"PROGRAMFILES": str(tmp_path / "Programas")}, "win32") is None
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    ambiente = {"LOCALAPPDATA": str(tmp_path / "nada"), "PROGRAMFILES(X86)": str(tmp_path / "Programas")}
    assert instalacao.localizar_chrome(ambiente, "win32") == chrome


def test_localizar_chrome_no_linux_procura_onde_o_playwright_procura():
    achado = instalacao.localizar_chrome({}, "linux")
    assert achado in (None, Path("/opt/google/chrome/chrome"))


def test_pasta_dos_navegadores_do_playwright(tmp_path):
    assert instalacao.pasta_navegadores_playwright({"XDG_CACHE_HOME": str(tmp_path)}, "linux") == \
        tmp_path / "ms-playwright"
    assert instalacao.pasta_navegadores_playwright({}, "linux") == Path.home() / ".cache" / "ms-playwright"
    assert instalacao.pasta_navegadores_playwright({"LOCALAPPDATA": str(tmp_path)}, "win32") == \
        tmp_path / "ms-playwright"
    assert instalacao.pasta_navegadores_playwright({"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}, "linux") == tmp_path
    dentro = instalacao.pasta_navegadores_playwright({"PLAYWRIGHT_BROWSERS_PATH": "0"}, "linux")
    assert dentro is not None and dentro.name == ".local-browsers"


def test_chromium_so_conta_se_o_download_terminou(tmp_path):
    assert not instalacao.chromium_do_playwright_instalado(tmp_path / "nao_existe")
    (tmp_path / "chromium_headless_shell-1243").mkdir()
    (tmp_path / "chromium_headless_shell-1243" / "INSTALLATION_COMPLETE").write_bytes(b"")
    pasta = tmp_path / "chromium-1243"
    pasta.mkdir()
    assert not instalacao.chromium_do_playwright_instalado(tmp_path)   # download pela metade
    (pasta / "INSTALLATION_COMPLETE").write_bytes(b"")
    assert instalacao.chromium_do_playwright_instalado(tmp_path)
    assert instalacao.chromium_do_playwright_instalado(tmp_path, "1243")
    assert not instalacao.chromium_do_playwright_instalado(tmp_path, "9999")  # versão de outro Playwright


def test_revisao_do_chromium_vem_do_playwright_instalado():
    revisao = instalacao.revisao_chromium_esperada()
    assert revisao is not None and revisao.isdigit()


# ------------------------------------------------------------ preparar

def test_preparar_com_tudo_pronto_nao_roda_nada(ambiente_pronto):
    # O conftest proíbe o _executar: se a preparação rodasse algum comando, daria erro.
    assert instalacao.preparar(uv=None) == 0
    log = instalacao.LOG_INSTALACAO.read_text(encoding="utf-8")
    assert "Dependências já instaladas" in log and "Tudo pronto." in log


def test_preparar_instala_as_dependencias_quando_o_requirements_muda(ambiente_pronto, requisitos, monkeypatch):
    requisitos.write_text("playwright>=1.70\nopenpyxl>=3.1\n", encoding="utf-8")
    comandos = Comandos(monkeypatch)
    assert instalacao.preparar(uv="/caminho/do/uv") == 0
    assert comandos.rodados == [["/caminho/do/uv", "pip", "install", "--python", sys.executable,
                                 "-r", str(requisitos)]]
    assert instalacao.ler_marcador()["hash_requisitos"] == instalacao.hash_requisitos()


def test_preparar_sem_chrome_baixa_o_chromium_uma_vez(ambiente_pronto, monkeypatch):
    baixado = {"sim": False}
    monkeypatch.setattr(instalacao, "localizar_chrome", lambda: None)
    monkeypatch.setattr(instalacao, "chromium_do_playwright_instalado", lambda *a, **k: baixado["sim"])
    comandos = Comandos(monkeypatch, ao_rodar=lambda comando: baixado.update(sim=True))
    assert instalacao.preparar() == 0
    assert comandos.rodados == [[sys.executable, "-m", "playwright", "install", "chromium"]]

    assert instalacao.preparar() == 0          # já baixado: não baixa de novo
    assert len(comandos.rodados) == 1


def test_preparar_falha_ao_instalar_nao_grava_o_marcador(requisitos, monkeypatch):
    monkeypatch.setattr(instalacao, "em_venv", lambda: True)
    Comandos(monkeypatch, codigo=1)
    assert instalacao.preparar(uv="/caminho/do/uv") == 1
    assert instalacao.ler_marcador() == {}
    assert "falhou" in instalacao.LOG_INSTALACAO.read_text(encoding="utf-8")


def test_preparar_sem_uv_explica(requisitos, monkeypatch, capsys):
    monkeypatch.setattr(instalacao, "em_venv", lambda: True)
    monkeypatch.setattr(instalacao.shutil, "which", lambda nome: None)
    assert instalacao.preparar(uv=None) == 1
    assert "Não encontrei o uv" in capsys.readouterr().out


def test_preparar_fora_de_um_venv_recusa(requisitos, monkeypatch):
    monkeypatch.setattr(instalacao, "em_venv", lambda: False)
    Comandos(monkeypatch)
    assert instalacao.preparar(uv="/caminho/do/uv") == 1


def test_preparar_sem_tkinter_explica(ambiente_pronto, monkeypatch, capsys):
    monkeypatch.setattr(instalacao, "conferir_tkinter",
                        lambda: Conferencia("Tkinter (janela)", False, "não funciona: x", "apague a pasta .venv"))
    assert instalacao.preparar() == 1
    assert "apague a pasta .venv" in capsys.readouterr().out


# ------------------------------------------------------------ execução e log

def test_executar_mostra_e_grava_a_saida(tmp_path, capsys):
    with instalacao.Registro(tmp_path / "logs" / "x.log") as registro:
        codigo = executar_de_verdade([sys.executable, "-X", "utf8", "-c", "print('olá ✔'); raise SystemExit(3)"],
                                     registro)
    assert codigo == 3
    assert "olá ✔" in capsys.readouterr().out
    log = (tmp_path / "logs" / "x.log").read_text(encoding="utf-8")
    assert "olá ✔" in log and "código de saída 3" in log


def test_executar_comando_inexistente(tmp_path):
    with instalacao.Registro(tmp_path / "x.log") as registro:
        assert executar_de_verdade([str(tmp_path / "nao_existe")], registro) == 127


def test_log_grande_e_guardado_a_parte(tmp_path, monkeypatch):
    monkeypatch.setattr(instalacao, "TAMANHO_MAXIMO_LOG", 10)
    log = tmp_path / "instalacao.log"
    log.write_text("x" * 50, encoding="utf-8")
    with instalacao.Registro(log) as registro:
        registro.linha("novo", mostrar=False)
    assert (tmp_path / "instalacao.log.1").read_text(encoding="utf-8") == "x" * 50
    assert log.read_text(encoding="utf-8").endswith("novo\n")


# ------------------------------------------------------------ linha de comando

def test_verificar_pela_linha_de_comando(monkeypatch, capsys):
    tudo_ok = [Conferencia("Python", True, "3.12"), Conferencia("Navegador", True, "Google Chrome")]
    monkeypatch.setattr(instalacao, "verificar", lambda: tudo_ok)
    assert instalacao.main(["--verificar"]) == 0
    assert "Tudo pronto." in capsys.readouterr().out

    falta = tudo_ok + [Conferencia("Dependências", False, "faltam openpyxl", "abra pelo lançador")]
    monkeypatch.setattr(instalacao, "verificar", lambda: falta)
    assert instalacao.main(["--verificar"]) == 1
    saida = capsys.readouterr().out
    assert "✘ Dependências: faltam openpyxl" in saida and "abra pelo lançador" in saida

    assert instalacao.main(["--verificar", "--silencioso"]) == 1
    assert capsys.readouterr().out == ""


def test_verificar_de_verdade_nao_muda_nada(tmp_path):
    antes = sorted(p.name for p in tmp_path.rglob("*"))
    itens = instalacao.verificar()
    assert [item.nome for item in itens][0] == "Python"
    assert {"Dependências", "Tkinter (janela)", "Navegador"} <= {item.nome for item in itens}
    assert sorted(p.name for p in tmp_path.rglob("*")) == antes   # nem marcador nem log


def test_linha_de_comando_exige_um_modo():
    with pytest.raises(SystemExit) as saida:
        instalacao.main([])
    assert saida.value.code == 2


def test_marcador_e_json_legivel(ambiente_pronto):
    dados = json.loads(instalacao.MARCADOR.read_text(encoding="utf-8"))
    assert set(dados) == {"hash_requisitos", "python", "instalado_em"}
