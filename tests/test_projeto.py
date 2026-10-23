"""Testes da estrutura do projeto: caminhos, dependências, pontos de entrada e lançadores.

Os lançadores (Iniciar.bat e iniciar.sh) não são executados aqui: os testes
só conferem o formato dos arquivos e o que eles chamam. Os pontos de entrada
rodam só com --help, que termina antes de abrir banco ou navegador.
"""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from automacao import caminhos

RAIZ = Path(__file__).resolve().parent.parent
PACOTE = RAIZ / "automacao"


# ------------------------------------------------------------ caminhos

def test_dados_do_usuario_ficam_na_raiz_com_os_nomes_de_sempre():
    assert caminhos.RAIZ == RAIZ
    assert caminhos.PASTA_PERFIL == RAIZ / "perfil_navegador"
    assert caminhos.CAMINHO_BANCO == RAIZ / "automacao.db"
    assert caminhos.PLANILHA_PERFIS == RAIZ / "data" / "para_acessar.xlsx"
    assert caminhos.PLANILHA_DESTINATARIOS == RAIZ / "data" / "para_compartilhar.xlsx"
    assert caminhos.PASTA_CAPTURAS == RAIZ / "capturas"
    assert caminhos.PASTA_TRACES == RAIZ / "traces"
    assert caminhos.LOG_INSTALACAO == RAIZ / "logs" / "instalacao.log"
    assert caminhos.PASTA_VENV == RAIZ / ".venv"
    assert caminhos.REQUISITOS == RAIZ / "requirements.txt"


def test_so_o_caminhos_py_calcula_caminhos_a_partir_do_codigo():
    """Todo caminho de dados sai do caminhos.py, para dar para mudar num lugar só."""
    usam = sorted(str(p.relative_to(PACOTE)) for p in PACOTE.rglob("*.py")
                  if "__file__" in p.read_text(encoding="utf-8"))
    assert usam == ["caminhos.py"]


def test_nao_sobrou_modulo_na_raiz():
    assert sorted(p.name for p in RAIZ.glob("*.py")) == []
    assert not (RAIZ / "pytest.ini").exists()  # a configuração do pytest está no pyproject.toml


# ------------------------------------------------------------ dependências

def _linhas(caminho: Path) -> list[str]:
    linhas = (linha.split("#", 1)[0].strip() for linha in caminho.read_text(encoding="utf-8").splitlines())
    return [linha for linha in linhas if linha]


def test_requirements_iguais_ao_pyproject():
    projeto = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert projeto["requires-python"] == ">=3.11"
    assert _linhas(RAIZ / "requirements.txt") == projeto["dependencies"]
    assert _linhas(RAIZ / "requirements-dev.txt") == ["-r requirements.txt", *projeto["optional-dependencies"]["dev"]]


# ------------------------------------------------------------ pontos de entrada

@pytest.mark.parametrize("modulo", ["automacao.abrir_redes", "automacao.interagir", "automacao.banco",
                                    "automacao.configuracao", "automacao.instalacao"])
def test_pontos_de_entrada_respondem_ao_help(modulo):
    ambiente = {k: v for k, v in os.environ.items() if k != "AUTOMACAO_ARQUIVO_PARADA"}
    saida = subprocess.run([sys.executable, "-m", modulo, "--help"], cwd=RAIZ, env=ambiente,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert saida.returncode == 0, saida.stderr
    assert saida.stdout.startswith("usage:")


def test_modulos_principais_da_interface_importam_sem_abrir_janela():
    import importlib
    for nome in ("automacao.__main__", "automacao.interface.__main__"):
        modulo = importlib.import_module(nome)
        assert callable(modulo.main)


# ------------------------------------------------------------ lançadores

def _rotulos_e_saltos(texto: str) -> tuple[list[str], set[str]]:
    rotulos = re.findall(r"(?im)^:([a-z_][\w]*)\s*$", texto)
    saltos = set(re.findall(r"(?i)\bgoto\s+:?([a-z_]\w*)", texto))
    saltos |= set(re.findall(r"(?i)\bcall\s+:([a-z_]\w*)", texto))
    return rotulos, saltos


def test_iniciar_bat_tem_o_formato_que_o_cmd_exige():
    bruto = (RAIZ / "Iniciar.bat").read_bytes()
    assert not bruto.startswith(b"\xef\xbb\xbf"), "BOM quebra a primeira linha no cmd"
    assert b"\n" not in bruto.replace(b"\r\n", b""), "o cmd precisa de CRLF em todas as linhas"
    texto = bruto.decode("utf-8")
    linhas = texto.split("\r\n")
    assert linhas[0] == "@echo off"
    antes_do_chcp = linhas[:next(i for i, linha in enumerate(linhas) if linha.startswith("chcp 65001"))]
    assert all(linha.isascii() for linha in antes_do_chcp), "antes do chcp 65001, só ASCII"

    rotulos, saltos = _rotulos_e_saltos(texto)
    assert len(rotulos) == len(set(rotulos)), "rótulo repetido"
    assert saltos <= set(rotulos), f"goto/call para rótulo inexistente: {saltos - set(rotulos)}"


def test_iniciar_bat_faz_o_que_o_leigo_precisa():
    texto = (RAIZ / "Iniciar.bat").read_text(encoding="utf-8")
    assert 'pushd "%~dp0"' in texto
    assert "irm https://astral.sh/uv/install.ps1 | iex" in texto and "-ExecutionPolicy ByPass" in texto
    assert '"%UV%" venv --python 3.12' in texto
    assert '"%PYTHON%" -m automacao.instalacao --preparar --uv "%UV%"' in texto
    assert '/min "%PYTHON%" -m automacao.interface' in texto and texto.count("start ") == 1
    comandos = [linha for linha in texto.splitlines() if not linha.lstrip().lower().startswith("rem")]
    assert not any("pythonw" in linha for linha in comandos)  # com console, o Parar funciona com calma
    assert "pause" in comandos


@pytest.mark.parametrize("nome", ["iniciar.sh", "instalar_atalho.sh"])
def test_scripts_do_linux(nome):
    caminho = RAIZ / nome
    bruto = caminho.read_bytes()
    assert bruto.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r" not in bruto, "script do Linux com CRLF não roda"
    if sys.platform != "win32":
        assert os.access(caminho, os.X_OK), "falta o bit de execução (chmod +x)"
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", str(caminho)], capture_output=True).returncode == 0


def test_iniciar_sh_chama_o_preparo_e_abre_a_interface():
    texto = (RAIZ / "iniciar.sh").read_text(encoding="utf-8")
    assert "curl -LsSf" in texto and "https://astral.sh/uv/install.sh" in texto
    assert '-m automacao.instalacao --verificar --silencioso' in texto
    assert '-m automacao.instalacao --preparar --uv "$UV"' in texto
    assert texto.count("-m automacao.interface") == 2  # com setsid ou, na falta dele, nohup
