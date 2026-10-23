"""Leitura das planilhas da pasta data/.

- para_acessar.xlsx: perfis a visitar. Uma aba por rede (instagram, tiktok),
  com os links dos perfis na primeira coluna.
- para_compartilhar.xlsx: com quem compartilhar. Uma aba por rede, com o
  usuário (só o @) na primeira coluna.

A primeira linha de cada aba pode ser um cabeçalho; linhas que não parecem um
perfil ou um usuário são ignoradas e avisadas.
"""

import os
import re
from pathlib import Path

import openpyxl

from automacao.caminhos import PLANILHA_DESTINATARIOS, PLANILHA_PERFIS

REDES_POR_ABA = {"instagram": "Instagram", "tiktok": "TikTok"}
RE_USUARIO = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")
CABECALHOS_CONHECIDOS = {"url", "link", "perfil", "perfis", "usuario", "usuário", "usuarios", "usuários",
                         "conta", "contas", "username", "user", "destinatario", "destinatário"}


def _primeira_coluna(caminho: Path) -> dict[str, list[tuple[int, str]]]:
    """{nome da aba: [(número da linha, texto), ...]} sem células vazias."""
    livro = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    try:
        abas = {}
        for aba in livro.worksheets:
            linhas = []
            for numero, (valor, *_) in enumerate(aba.iter_rows(values_only=True), start=1):
                if valor is not None and str(valor).strip():
                    linhas.append((numero, str(valor).strip()))
            abas[aba.title] = linhas
        return abas
    finally:
        livro.close()


def ler_perfis(caminho: Path = PLANILHA_PERFIS) -> list[str]:
    """Links de perfis de todas as abas, na ordem da planilha."""
    perfis = []
    for aba, linhas in _primeira_coluna(caminho).items():
        for numero, texto in linhas:
            if re.search(r"(instagram|tiktok)\.com[:/]", texto, re.I):
                perfis.append(texto)
            elif numero > 1:  # a linha 1 costuma ser o cabeçalho
                print(f"Aviso: {caminho.name}, aba {aba}, linha {numero}: {texto!r} não é um link de perfil.")
    return perfis


def ler_destinatarios(caminho: Path = PLANILHA_DESTINATARIOS) -> dict[str, list[str]]:
    """{rede: [usuários]} para o compartilhamento, sem @ e sem repetição."""
    destinatarios: dict[str, list[str]] = {}
    for aba, linhas in _primeira_coluna(caminho).items():
        rede = REDES_POR_ABA.get(aba.strip().lower())
        if rede is None:
            print(f"Aviso: {caminho.name}: aba {aba!r} ignorada (use instagram ou tiktok).")
            continue
        lista = destinatarios.setdefault(rede, [])
        for numero, texto in linhas:
            if numero == 1 and texto.strip().lower() in CABECALHOS_CONHECIDOS:
                continue  # cabeçalho que parece um usuário ("usuario", "perfil"...)
            if RE_USUARIO.match(texto):
                # Usuários do Instagram e do TikTok não diferenciam maiúsculas.
                usuario = texto.lstrip("@").lower()
                if usuario not in lista:
                    lista.append(usuario)
            elif numero > 1:
                print(f"Aviso: {caminho.name}, aba {aba}, linha {numero}: {texto!r} não é um usuário.")
    return destinatarios


# ------------------------------------------------------- edição (interface)
# Funções usadas pela interface gráfica para mostrar e alterar as planilhas.
# Só a primeira coluna é usada; a linha 1 de cada aba é o cabeçalho.

ABAS = ("instagram", "tiktok")
CABECALHO_PERFIS = "url"
CABECALHO_DESTINATARIOS = "perfil (somente o @)"


def eh_link_de_perfil(texto: str) -> bool:
    """Mesmo critério do ler_perfis para aproveitar uma linha."""
    return bool(re.search(r"(instagram|tiktok)\.com/", texto, re.I))


def eh_usuario(texto: str) -> bool:
    """Mesmo critério do ler_destinatarios para aproveitar uma linha."""
    return bool(RE_USUARIO.match(texto))


def _eh_cabecalho(texto: str, cabecalho: str, eh_valor) -> bool:
    return texto.strip().lower() == cabecalho.lower() or not eh_valor(texto.strip())


def _ler_para_editar(caminho: Path, cabecalho: str, eh_valor) -> dict[str, list[str]]:
    """{aba: [textos]} das abas instagram e tiktok, sem o cabeçalho.

    As duas abas sempre aparecem (vazias se a planilha ou a aba não existir).
    Linhas inválidas também vêm, para que dê para vê-las e removê-las.
    """
    abas: dict[str, list[str]] = {aba: [] for aba in ABAS}
    if not caminho.exists():
        return abas
    for nome, linhas in _primeira_coluna(caminho).items():
        aba = nome.strip().lower()
        if aba not in abas:
            continue
        for numero, texto in linhas:
            if numero == 1 and _eh_cabecalho(texto, cabecalho, eh_valor):
                continue
            abas[aba].append(texto)
    return abas


def linhas_perfis(caminho: Path | None = None) -> dict[str, list[str]]:
    """{aba: [links]} de para_acessar.xlsx, para mostrar e editar."""
    return _ler_para_editar(caminho or PLANILHA_PERFIS, CABECALHO_PERFIS, eh_link_de_perfil)


def linhas_destinatarios(caminho: Path | None = None) -> dict[str, list[str]]:
    """{aba: [usuários]} de para_compartilhar.xlsx, para mostrar e editar."""
    return _ler_para_editar(caminho or PLANILHA_DESTINATARIOS, CABECALHO_DESTINATARIOS, eh_usuario)


def _chave_linha(texto: str) -> str:
    """Chave para reconhecer o mesmo perfil escrito de jeitos diferentes.

    'instagram.com/Fulano', 'https://www.instagram.com/fulano/' e '@Fulano'
    viram a mesma chave.
    """
    chave = texto.strip().casefold()
    for prefixo in ("https://", "http://"):
        chave = chave.removeprefix(prefixo)
    for prefixo in ("www.", "m."):
        chave = chave.removeprefix(prefixo)
    return chave.split("?", 1)[0].rstrip("/").lstrip("@")


def _salvar(caminho: Path, abas: dict[str, list[str]], cabecalho: str, eh_valor) -> None:
    """Regrava a primeira coluna das abas pedidas, mantendo o cabeçalho e as outras abas.

    Cria a planilha (e as abas instagram e tiktok, com cabeçalho) se não existirem.
    Grava num arquivo temporário e troca no fim, para não corromper a planilha
    se algo der errado no meio.
    """
    caminho = Path(caminho)
    if caminho.exists():
        livro = openpyxl.load_workbook(caminho)
    else:
        livro = openpyxl.Workbook()
        livro.remove(livro.active)
    pedidas = {aba.strip().lower(): valores for aba, valores in abas.items()}
    por_nome = {folha.title.strip().lower(): folha for folha in livro.worksheets}
    for aba in (*ABAS, *(a for a in pedidas if a not in ABAS)):
        folha = por_nome.get(aba)
        if folha is None:
            folha = por_nome[aba] = livro.create_sheet(aba)
        if aba in pedidas:
            valores = [str(v).strip() for v in pedidas[aba] if str(v).strip()]
        else:
            valores = None  # aba que não foi pedida: só garante que existe

        primeira = folha.cell(row=1, column=1).value
        texto = "" if primeira is None else str(primeira).strip()
        if not texto:
            folha.cell(row=1, column=1, value=cabecalho)
        elif not _eh_cabecalho(texto, cabecalho, eh_valor):
            folha.insert_rows(1)  # a aba não tinha cabeçalho
            folha.cell(row=1, column=1, value=cabecalho)

        if valores is None:
            continue
        # Outras colunas (anotações do usuário) acompanham a linha do seu perfil:
        # guarda cada linha antiga pela chave do valor da primeira coluna.
        antigas: dict[str, list] = {}
        for linha in folha.iter_rows(min_row=2, values_only=True):
            if linha and linha[0] is not None and str(linha[0]).strip():
                antigas.setdefault(_chave_linha(str(linha[0])), list(linha))
        for linha in folha.iter_rows(min_row=2, max_row=max(folha.max_row, 2)):
            for celula in linha:
                celula.value = None  # limpa o conteúdo e mantém a formatação
        for numero, valor in enumerate(valores, start=2):
            folha.cell(row=numero, column=1, value=valor)
            for coluna, conteudo in enumerate(antigas.get(_chave_linha(valor), [])[1:], start=2):
                folha.cell(row=numero, column=coluna, value=conteudo)

    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.stem}.salvando{caminho.suffix}")
    try:
        livro.save(temporario)
        livro.close()  # no Windows, o arquivo não pode estar aberto ao ser substituído
        os.replace(temporario, caminho)
    finally:
        livro.close()
        temporario.unlink(missing_ok=True)


def salvar_perfis(abas: dict[str, list[str]], caminho: Path | None = None) -> None:
    """Grava {aba: [links]} em para_acessar.xlsx (cabeçalho "url")."""
    _salvar(caminho or PLANILHA_PERFIS, abas, CABECALHO_PERFIS, eh_link_de_perfil)


def salvar_destinatarios(abas: dict[str, list[str]], caminho: Path | None = None) -> None:
    """Grava {aba: [usuários]} em para_compartilhar.xlsx, sem o @."""
    limpas = {aba: [str(u).strip().lstrip("@") for u in usuarios] for aba, usuarios in abas.items()}
    _salvar(caminho or PLANILHA_DESTINATARIOS, limpas, CABECALHO_DESTINATARIOS, eh_usuario)
