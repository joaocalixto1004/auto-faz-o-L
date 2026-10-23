"""Confere e prepara o que a automação precisa para rodar.

Usado pelos lançadores (Iniciar.bat no Windows, iniciar.sh no Linux), sempre
com o Python do .venv, que eles criam com o uv. Só usa a biblioteca padrão,
porque as dependências podem ainda não estar instaladas.

    python -m automacao.instalacao --verificar          # só confere; não muda nada
    python -m automacao.instalacao --preparar --uv UV   # instala o que faltar

O que é conferido:
- Python 3.11 ou mais novo;
- dependências do requirements.txt. Depois de instaladas, um marcador dentro
  do .venv guarda o hash do requirements.txt: só reinstala quando ele mudar;
- Tkinter, que desenha a janela da interface;
- navegador: o Google Chrome instalado ou, na falta dele, o Chromium do
  Playwright (baixado com "python -m playwright install chromium").

Código de saída: 0 = tudo pronto; 1 = falta algo (--verificar) ou a
preparação falhou. O passo a passo da preparação vai para logs/instalacao.log.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from automacao.caminhos import LOG_INSTALACAO, REQUISITOS
from automacao.terminal import preparar_console

VERSAO_MINIMA = (3, 11)
# Aumente para forçar a reinstalação das dependências em todos os computadores.
VERSAO_PREPARO = 1
MARCADOR = Path(sys.prefix) / "automacao-instalacao.json"   # dentro do .venv
MODULOS_NECESSARIOS = ("playwright", "openpyxl")
TAMANHO_MAXIMO_LOG = 1_000_000  # bytes; acima disso, o log antigo vira instalacao.log.1


@dataclass(frozen=True)
class Conferencia:
    nome: str
    ok: bool
    detalhe: str
    dica: str = ""


# ------------------------------------------------------------ dependências

def hash_requisitos(caminho: Path | None = None) -> str:
    """Hash do requirements.txt (e da versão do Python e deste preparo)."""
    texto = (caminho or REQUISITOS).read_text(encoding="utf-8").replace("\r\n", "\n")
    base = f"{VERSAO_PREPARO}\n{sys.version_info.major}.{sys.version_info.minor}\n{texto}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def ler_marcador(caminho: Path | None = None) -> dict:
    try:
        dados = json.loads((caminho or MARCADOR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def gravar_marcador(hash_atual: str, caminho: Path | None = None) -> None:
    alvo = caminho or MARCADOR
    dados = {"hash_requisitos": hash_atual, "python": platform.python_version(),
             "instalado_em": datetime.now().astimezone().isoformat(timespec="seconds")}
    temporario = alvo.with_name(alvo.name + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporario, alvo)


def modulos_faltando() -> list[str]:
    importlib.invalidate_caches()
    return [nome for nome in MODULOS_NECESSARIOS if importlib.util.find_spec(nome) is None]


def _versao(pacote: str) -> str:
    try:
        return importlib.metadata.version(pacote)
    except importlib.metadata.PackageNotFoundError:
        return "?"


def conferir_dependencias() -> Conferencia:
    faltando = modulos_faltando()
    if faltando:
        return Conferencia("Dependências", False, f"faltam {', '.join(faltando)}",
                           "abra pelo Iniciar.bat ou pelo iniciar.sh para instalar")
    if ler_marcador().get("hash_requisitos") != hash_requisitos():
        return Conferencia("Dependências", False,
                           "o requirements.txt mudou (ou ainda não foi instalado por aqui)",
                           "abra pelo Iniciar.bat ou pelo iniciar.sh para atualizar")
    versoes = ", ".join(f"{nome} {_versao(nome)}" for nome in MODULOS_NECESSARIOS)
    return Conferencia("Dependências", True, versoes)


# ------------------------------------------------------------ Python e Tkinter

def em_venv() -> bool:
    return sys.prefix != sys.base_prefix


def conferir_python() -> Conferencia:
    versao = platform.python_version()
    if sys.version_info < VERSAO_MINIMA:
        minima = ".".join(map(str, VERSAO_MINIMA))
        return Conferencia("Python", False, f"{versao} (é preciso o {minima} ou mais novo)",
                           "apague a pasta .venv e abra pelo lançador de novo")
    return Conferencia("Python", True, f"{versao} ({sys.executable})")


def conferir_tkinter() -> Conferencia:
    """O Tkinter importa e o Tcl inicia (não abre janela nem precisa de tela)."""
    try:
        import tkinter
        versao = tkinter.Tcl().eval("info patchlevel")
    except Exception as erro:  # noqa: BLE001 - ImportError, TclError (init.tcl ausente)...
        if sys.platform == "win32":
            dica = "apague a pasta .venv e abra o Iniciar.bat de novo"
        else:
            dica = ("apague a pasta .venv e abra o iniciar.sh de novo (ele usa um Python que já traz "
                    "o Tkinter); com o Python do sistema, instale o pacote python3-tk")
        return Conferencia("Tkinter (janela)", False, f"não funciona: {erro}", dica)
    return Conferencia("Tkinter (janela)", True, f"Tcl/Tk {versao}")


# ------------------------------------------------------------ navegador

def localizar_chrome(ambiente: Mapping[str, str] | None = None,
                     plataforma: str | None = None) -> Path | None:
    """O Google Chrome, nos mesmos lugares em que o Playwright procura (channel="chrome")."""
    ambiente = os.environ if ambiente is None else ambiente
    plataforma = sys.platform if plataforma is None else plataforma
    if plataforma == "win32":
        prefixos = [ambiente.get(nome) for nome in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
        if ambiente.get("HOMEDRIVE"):
            prefixos += [ambiente["HOMEDRIVE"] + "\\Program Files", ambiente["HOMEDRIVE"] + "\\Program Files (x86)"]
        candidatos = [Path(p) / "Google" / "Chrome" / "Application" / "chrome.exe" for p in prefixos if p]
    elif plataforma == "darwin":
        candidatos = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        candidatos = [Path("/opt/google/chrome/chrome")]
    return next((c for c in candidatos if c.is_file()), None)


def _pasta_do_playwright() -> Path | None:
    especificacao = importlib.util.find_spec("playwright")
    if especificacao is None or not especificacao.submodule_search_locations:
        return None
    return Path(list(especificacao.submodule_search_locations)[0])


def pasta_navegadores_playwright(ambiente: Mapping[str, str] | None = None,
                                 plataforma: str | None = None) -> Path | None:
    """Onde o Playwright guarda os navegadores que ele baixa (mesma regra dele)."""
    ambiente = os.environ if ambiente is None else ambiente
    plataforma = sys.platform if plataforma is None else plataforma
    personalizada = ambiente.get("PLAYWRIGHT_BROWSERS_PATH")
    if personalizada == "0":  # dentro do próprio pacote
        pacote = _pasta_do_playwright()
        return pacote / "driver" / "package" / ".local-browsers" if pacote else None
    if personalizada:
        return Path(personalizada)
    if plataforma == "win32":
        base = Path(ambiente.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif plataforma == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(ambiente.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "ms-playwright"


def revisao_chromium_esperada() -> str | None:
    """A versão (revisão) do Chromium que o Playwright instalado usa, lida do browsers.json dele."""
    pacote = _pasta_do_playwright()
    if pacote is None:
        return None
    try:
        dados = json.loads((pacote / "driver" / "package" / "browsers.json").read_text(encoding="utf-8"))
        return next(str(b["revision"]) for b in dados["browsers"] if b.get("name") == "chromium")
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


def chromium_do_playwright_instalado(pasta: Path | None = None, revisao: str | None = None) -> bool:
    """O Chromium do Playwright foi baixado por completo?

    Com a revisão, exige exatamente a pasta chromium-<revisão>; sem ela, qualquer uma.
    """
    pasta = pasta or pasta_navegadores_playwright()
    if pasta is None or not pasta.is_dir():
        return False
    candidatas = [pasta / f"chromium-{revisao}"] if revisao else list(pasta.glob("chromium-*"))
    return any((candidata / "INSTALLATION_COMPLETE").is_file() for candidata in candidatas)


def conferir_navegador() -> Conferencia:
    chrome = localizar_chrome()
    if chrome:
        return Conferencia("Navegador", True, f"Google Chrome ({chrome})")
    if chromium_do_playwright_instalado(revisao=revisao_chromium_esperada()):
        return Conferencia("Navegador", True, "Chromium do Playwright (o Google Chrome não foi encontrado)")
    return Conferencia("Navegador", False, "nem o Google Chrome nem o Chromium do Playwright foram encontrados",
                       "abra pelo lançador para baixar o Chromium, ou instale o Google Chrome")


# ------------------------------------------------------------ conferência

def verificar() -> list[Conferencia]:
    """Confere tudo, sem instalar nem mudar nada."""
    itens = [conferir_python()]
    if not em_venv():
        itens.append(Conferencia("Ambiente", False, f"este Python não é de um ambiente virtual ({sys.prefix})",
                                 "use o Python do .venv (o lançador cria a pasta .venv)"))
    return itens + [conferir_dependencias(), conferir_tkinter(), conferir_navegador()]


def mostrar(itens: Sequence[Conferencia]) -> None:
    for item in itens:
        print(f"{'✔' if item.ok else '✘'} {item.nome}: {item.detalhe}")
        if not item.ok and item.dica:
            print(f"    → {item.dica}")
    if all(item.ok for item in itens):
        print("Tudo pronto.")
    else:
        print("Falta preparar a instalação.")


# ------------------------------------------------------------ preparação

class Registro:
    """Mostra as mensagens no terminal e grava tudo em logs/instalacao.log."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        self._arquivo = None
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            if caminho.exists() and caminho.stat().st_size > TAMANHO_MAXIMO_LOG:
                os.replace(caminho, caminho.with_name(caminho.name + ".1"))
            self._arquivo = open(caminho, "a", encoding="utf-8")  # noqa: SIM115 - fechado em fechar()
        except OSError:
            self._arquivo = None  # sem log; a preparação continua

    def linha(self, texto: str, mostrar: bool = True) -> None:
        if mostrar:
            print(texto, flush=True)
        if self._arquivo:
            self._arquivo.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {texto}\n")
            self._arquivo.flush()

    def fechar(self) -> None:
        if self._arquivo:
            self._arquivo.close()
            self._arquivo = None

    def __enter__(self) -> "Registro":
        return self

    def __exit__(self, *_erro) -> None:
        self.fechar()


def _executar(comando: Sequence[str], registro: Registro) -> int:
    """Roda o comando mostrando a saída e gravando no log. Devolve o código de saída."""
    registro.linha("$ " + subprocess.list2cmdline(list(comando)), mostrar=False)
    try:
        processo = subprocess.Popen(list(comando), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError as erro:
        registro.linha(f"Não consegui rodar {comando[0]}: {erro}")
        return 127
    with processo:
        for linha in processo.stdout:
            registro.linha("    " + linha.rstrip())
    registro.linha(f"(código de saída {processo.returncode})", mostrar=False)
    return processo.returncode


def preparar(uv: str | None = None, log: Path | None = None) -> int:
    """Instala o que faltar. Rápido quando já está tudo pronto. Devolve o código de saída."""
    with Registro(log or LOG_INSTALACAO) as registro:
        registro.linha(f"Preparando a instalação (Python {platform.python_version()}, {platform.platform()}).",
                       mostrar=False)

        def falhar(mensagem: str) -> int:
            registro.linha(f"✘ {mensagem}")
            registro.linha(f"Detalhes em: {registro.caminho}")
            return 1

        python = conferir_python()
        if not python.ok:
            return falhar(f"Python {python.detalhe}. Solução: {python.dica}.")
        if not em_venv():
            return falhar("Rode com o Python do .venv (o lançador Iniciar.bat ou iniciar.sh cria essa pasta).")

        # 1. Dependências (só quando o requirements.txt mudou ou falta algum pacote).
        instalou = False
        dependencias = conferir_dependencias()
        if dependencias.ok:
            registro.linha(f"✔ Dependências já instaladas ({dependencias.detalhe}).")
        else:
            uv = uv or shutil.which("uv")
            if not uv:
                return falhar("Não encontrei o uv, que instala as dependências. Abra pelo Iniciar.bat ou "
                              "pelo iniciar.sh, que instalam o uv sozinhos.")
            registro.linha("Instalando as dependências (na primeira vez, pode levar alguns minutos)...")
            codigo = _executar([uv, "pip", "install", "--python", sys.executable, "-r", str(REQUISITOS)], registro)
            if codigo != 0:
                return falhar("A instalação das dependências falhou. Confira a conexão com a internet "
                              "e abra de novo.")
            faltando = modulos_faltando()
            if faltando:
                return falhar(f"Depois de instalar, ainda faltam: {', '.join(faltando)}.")
            gravar_marcador(hash_requisitos())
            instalou = True
            registro.linha("✔ Dependências instaladas.")

        # 2. Tkinter (a janela).
        janela = conferir_tkinter()
        if not janela.ok:
            return falhar(f"A interface gráfica não vai abrir: o Tkinter {janela.detalhe}. "
                          f"Solução: {janela.dica}.")
        registro.linha(f"✔ Tkinter ({janela.detalhe}).")

        # 3. Navegador: o Google Chrome ou, na falta dele, o Chromium do Playwright.
        chrome = localizar_chrome()
        if chrome:
            registro.linha(f"✔ Google Chrome encontrado ({chrome}).")
        elif instalou or not chromium_do_playwright_instalado(revisao=revisao_chromium_esperada()):
            registro.linha("O Google Chrome não foi encontrado. Baixando o Chromium do Playwright "
                           "(cerca de 150 MB; só desta vez)...")
            codigo = _executar([sys.executable, "-m", "playwright", "install", "chromium"], registro)
            if codigo != 0 or not chromium_do_playwright_instalado(revisao=revisao_chromium_esperada()):
                return falhar("Não consegui baixar o Chromium. Confira a internet e abra de novo, "
                              "ou instale o Google Chrome.")
            registro.linha("✔ Chromium do Playwright instalado.")
        else:
            registro.linha("✔ Chromium do Playwright já instalado (o Google Chrome não foi encontrado).")

        registro.linha("Tudo pronto.")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    preparar_console()
    parser = argparse.ArgumentParser(description="Confere e prepara a instalação da automação.")
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument("--verificar", action="store_true",
                      help="só confere o que falta; não instala nem muda nada")
    modo.add_argument("--preparar", action="store_true",
                      help="instala o que faltar (dependências e, se preciso, o Chromium)")
    parser.add_argument("--uv", help="caminho do uv, usado para instalar as dependências")
    parser.add_argument("--silencioso", action="store_true",
                        help="com --verificar, não mostra nada (só o código de saída)")
    args = parser.parse_args(argv)

    if args.verificar:
        itens = verificar()
        if not args.silencioso:
            mostrar(itens)
        return 0 if all(item.ok for item in itens) else 1
    return preparar(args.uv)


if __name__ == "__main__":
    sys.exit(main())
