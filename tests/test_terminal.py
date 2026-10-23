"""Testes do aviso de parada por arquivo (terminal.vigiar_pedido_de_parada)."""

import os
import subprocess
import sys
import time
from pathlib import Path

from automacao import terminal

RAIZ = Path(__file__).resolve().parent.parent

SCRIPT = """
import sys, time
sys.path.insert(0, sys.argv[1])
from automacao.terminal import vigiar_pedido_de_parada
vigiar_pedido_de_parada(intervalo=0.1)
print("pronto", flush=True)
try:
    while True:
        time.sleep(0.2)
except KeyboardInterrupt:
    print("parou direito", flush=True)
"""


def test_arquivo_de_parada_vira_ctrl_c(tmp_path):
    arquivo = tmp_path / "parar"
    ambiente = dict(os.environ, **{terminal.VARIAVEL_ARQUIVO_PARADA: str(arquivo)}, PYTHONUNBUFFERED="1")
    processo = subprocess.Popen([sys.executable, "-c", SCRIPT, str(RAIZ)], env=ambiente,
                                stdout=subprocess.PIPE, text=True)
    try:
        assert processo.stdout.readline().strip() == "pronto"
        arquivo.touch()
        saida, _ = processo.communicate(timeout=10)
    finally:
        processo.kill()
    assert "parou direito" in saida
    assert processo.returncode == 0
    assert not arquivo.exists()  # o script apaga o aviso ao atender


def test_sem_variavel_nao_vigia(monkeypatch):
    monkeypatch.delenv(terminal.VARIAVEL_ARQUIVO_PARADA, raising=False)
    antes = {t.name for t in __import__("threading").enumerate()}
    terminal.vigiar_pedido_de_parada()
    time.sleep(0.05)
    depois = {t.name for t in __import__("threading").enumerate()}
    assert "pedido-de-parada" not in depois - antes
