"""Configuração comum dos testes.

A raiz do projeto entra no sys.path pelo pyproject.toml (pythonpath = ["."]).
Cada teste é isolado por segurança:
- o banco padrão (banco.CAMINHO_BANCO) aponta para um arquivo temporário,
  então nada encosta no automacao.db real, nem por engano;
- as pastas de capturas, traces e dos perfis do navegador (perfil_navegador/
  e perfis/), as planilhas de data/ e o log e o marcador da instalação
  apontam para tmp_path;
- qualquer tentativa de abrir o Playwright ou o navegador falha na hora, e a
  instalação não roda comandos de verdade (uv, playwright install).
"""

import pytest

from automacao import abrir_redes, banco, instalacao, interagir, planilhas
from automacao.redes import comum


def _proibido(*args, **kwargs):
    raise RuntimeError("os testes não podem abrir o navegador nem iniciar o Playwright")


def _comando_proibido(*args, **kwargs):
    raise RuntimeError("os testes não podem rodar comandos de instalação de verdade")


@pytest.fixture(autouse=True)
def isolamento(tmp_path, monkeypatch):
    """Rede de segurança aplicada a todos os testes."""
    monkeypatch.setattr(banco, "CAMINHO_BANCO", tmp_path / "padrao.db")
    monkeypatch.setattr(comum, "PASTA_CAPTURAS", tmp_path / "capturas")
    monkeypatch.setattr(interagir, "PASTA_TRACES", tmp_path / "traces")
    monkeypatch.setattr(interagir, "TRAVA_AUTOMACAO", tmp_path / "automacao.trava")
    monkeypatch.setattr(abrir_redes, "PASTA_PERFIL", tmp_path / "perfil_falso")
    # Pastas das contas (perfis do navegador): a interface pode apagar a de uma
    # conta removida, então a raiz e perfis/ também apontam para tmp_path.
    monkeypatch.setattr(banco, "PASTA_PERFIL", tmp_path / "perfil_falso")
    monkeypatch.setattr(banco, "RAIZ", tmp_path / "raiz")
    monkeypatch.setattr(banco, "PASTA_PERFIS", tmp_path / "raiz" / "perfis")
    monkeypatch.setattr(planilhas, "PLANILHA_PERFIS", tmp_path / "sem_planilha_perfis.xlsx")
    monkeypatch.setattr(planilhas, "PLANILHA_DESTINATARIOS", tmp_path / "sem_planilha_destinatarios.xlsx")
    monkeypatch.setattr(instalacao, "LOG_INSTALACAO", tmp_path / "logs" / "instalacao.log")
    monkeypatch.setattr(instalacao, "MARCADOR", tmp_path / "automacao-instalacao.json")
    monkeypatch.setattr(instalacao, "_executar", _comando_proibido)
    monkeypatch.setattr(abrir_redes, "abrir_navegador", _proibido)
    monkeypatch.setattr(abrir_redes, "sync_playwright", _proibido)
    monkeypatch.setattr(abrir_redes, "abrir_com_insistencia", _proibido)
    monkeypatch.setattr(interagir, "abrir_com_insistencia", _proibido)
    monkeypatch.setattr(interagir, "sync_playwright", _proibido)


@pytest.fixture
def conn(tmp_path):
    """Conexão com um banco novo, num arquivo temporário."""
    conexao = banco.conectar(tmp_path / "t.db")
    yield conexao
    conexao.close()
