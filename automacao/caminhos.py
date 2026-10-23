"""Todos os caminhos de arquivos e pastas do projeto, num lugar só.

Os dados ficam na raiz do projeto (a pasta acima de automacao/), e não junto
do código: assim dá para trocar o código sem encostar nas sessões logadas,
no banco e nas planilhas. Para mudar onde algo fica, mude só aqui.

Os módulos importam o que usam daqui (ex.: banco.CAMINHO_BANCO), e os testes
trocam esses nomes nos próprios módulos por arquivos temporários.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent  # pasta do projeto

# Dados do usuário (nunca versionar nem apagar).
PASTA_PERFIL = RAIZ / "perfil_navegador"       # perfil "Principal" do Chrome, com as sessões logadas
PASTA_PERFIS = RAIZ / "perfis"                 # perfis adicionais (uma pasta por conta extra)
CAMINHO_BANCO = RAIZ / "automacao.db"          # SQLite (com os arquivos -wal e -shm ao lado)
PASTA_DADOS = RAIZ / "data"
PLANILHA_PERFIS = PASTA_DADOS / "para_acessar.xlsx"
PLANILHA_DESTINATARIOS = PASTA_DADOS / "para_compartilhar.xlsx"

# Saídas geradas pela automação.
PASTA_CAPTURAS = RAIZ / "capturas"             # print da tela quando uma ação falha
PASTA_TRACES = RAIZ / "traces"                 # traces do Playwright (opção --trace)
PASTA_LOGS = RAIZ / "logs"
LOG_INSTALACAO = PASTA_LOGS / "instalacao.log"
TRAVA_AUTOMACAO = PASTA_LOGS / "automacao.trava"   # uma automação por vez

# Instalação (lançadores Iniciar.bat e iniciar.sh).
PASTA_VENV = RAIZ / ".venv"
REQUISITOS = RAIZ / "requirements.txt"
