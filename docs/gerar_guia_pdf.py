"""Gera o "COMO USAR NO WINDOWS.pdf" a partir do .txt (rodar à mão, na raiz do projeto).

    .venv/bin/python docs/gerar_guia_pdf.py

O .txt é a fonte: mude o texto lá e rode de novo. O PDF é impresso pelo Chrome
do Playwright (sem janela). O formato que o .txt usa:
- primeira linha: título, sublinhado com "===";
- cada seção: uma linha sublinhada com "---" ("PASSO N - ..." vira um passo);
- "1. " começa um item numerado e "- " um item de lista; as linhas recuadas
  continuam o item (um "- " recuado é um subitem; um link recuado, um exemplo);
- parágrafos que começam com "IMPORTANTE:", "Dica:" ou "CUIDADO:" viram quadros.
"""

import html
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
ORIGEM = RAIZ / "COMO USAR NO WINDOWS.txt"
DESTINO = RAIZ / "COMO USAR NO WINDOWS.pdf"

NOMES_PROPRIOS = {"windows": "Windows", "instagram": "Instagram", "tiktok": "TikTok", "chrome": "Chrome",
                  "google": "Google", "excel": "Excel", "zip": "zip"}

CSS = """
@page { size: A4; margin: 18mm 17mm 20mm 17mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", Arial, sans-serif; font-size: 11.5pt;
       line-height: 1.5; color: #1f2933; margin: 0; }
.capa { border-radius: 14px; padding: 22px 26px; margin-bottom: 18px;
        background: linear-gradient(135deg, #0f766e, #155e75); color: #fff; }
.capa h1 { margin: 0 0 6px; font-size: 23pt; line-height: 1.15; }
.capa p { margin: 0; font-size: 12pt; opacity: .95; }
section { margin: 0 0 14px; break-inside: auto; }
h2 { display: flex; align-items: center; gap: 10px; font-size: 15pt; color: #0f766e; margin: 18px 0 8px;
     padding-bottom: 4px; border-bottom: 2px solid #ccebe7; break-after: avoid; }
h2 .passo { background: #0f766e; color: #fff; border-radius: 999px; padding: 2px 11px; font-size: 10.5pt;
            letter-spacing: .5px; white-space: nowrap; }
p { margin: 6px 0; }
ol.passos { list-style: none; counter-reset: passo; padding: 0; margin: 6px 0; }
ol.passos > li { counter-increment: passo; position: relative; padding: 4px 0 4px 40px; margin: 2px 0;
                 break-inside: avoid; }
ol.passos > li::before { content: counter(passo); position: absolute; left: 0; top: 4px; width: 27px; height: 27px;
                         border-radius: 50%; background: #e6f4f1; color: #0f766e; font-weight: 700;
                         display: flex; align-items: center; justify-content: center; font-size: 11pt; }
ul { margin: 6px 0; padding-left: 22px; }
ul li { margin: 3px 0; break-inside: avoid; }
ul li::marker { color: #0f766e; }
li ul { margin: 4px 0 2px; }
.exemplo { display: block; font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 10.5pt;
           background: #f1f5f4; border-radius: 6px; padding: 3px 9px; margin: 3px 0; color: #134e4a; }
.ui { font-weight: 700; color: #0b4f4a; }
strong { color: #111827; }
.quadro { border-radius: 10px; padding: 9px 13px; margin: 10px 0; break-inside: avoid; border-left: 6px solid; }
.quadro.importante { background: #fdecec; border-color: #c62828; }
.quadro.dica { background: #e8f1fd; border-color: #1565c0; }
.quadro.cuidado { background: #fff4e0; border-color: #e08a00; }
.quadro b.rotulo { display: block; font-size: 10pt; letter-spacing: .6px; margin-bottom: 2px; }
"""


def titulo_de_frase(texto: str) -> str:
    """'TIRAR O PROGRAMA DO ZIP' -> 'Tirar o programa do zip' (nomes próprios mantidos)."""
    palavras = texto.lower().split()
    palavras = [NOMES_PROPRIOS.get(p.strip("()?,."), None) and p.replace(p.strip("()?,."),
                NOMES_PROPRIOS[p.strip("()?,.")]) or p for p in palavras]
    frase = " ".join(palavras)
    return frase[:1].upper() + frase[1:]


def realcar(texto: str) -> str:
    """Escapa o texto e destaca: "rótulos da tela" e PALAVRAS EM MAIÚSCULAS."""
    texto = html.escape(texto, quote=False)
    texto = re.sub(r'"([^"]+)"', r'<span class="ui">“\1”</span>', texto)
    maiusculas = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]"
    return re.sub(rf"(?<![\w@/])({maiusculas}{{2,}}(?:\s+{maiusculas}{{2,}})*)(?![\w/])",
                  lambda m: m.group(1) if m.group(1) in ("OK", "SMS") else f"<strong>{m.group(1)}</strong>", texto)


def secoes(texto: str) -> tuple[str, list[tuple[str, list[str]]]]:
    linhas = texto.lstrip("﻿").splitlines()
    titulo = linhas[0].strip()
    resultado: list[tuple[str, list[str]]] = [("", [])]
    i = 2
    while i < len(linhas):
        if i + 1 < len(linhas) and re.fullmatch(r"-{3,}", linhas[i + 1].strip()) and linhas[i].strip():
            resultado.append((linhas[i].strip(), []))
            i += 2
            continue
        resultado[-1][1].append(linhas[i])
        i += 1
    return titulo, [s for s in resultado if s[0] or any(l.strip() for l in s[1])]


def blocos(linhas: list[str]) -> list[list[str]]:
    grupos, atual = [], []
    for linha in linhas:
        if linha.strip():
            atual.append(linha.rstrip())
        elif atual:
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)
    return grupos


def lista(bloco: list[str], numerada: bool) -> str:
    """Itens na ordem do texto: trechos, exemplos (links recuados) e subitens ("- " recuado)."""
    marcador = re.compile(r"^(\d+)\. (.*)$" if numerada else r"^- (.*)$")
    itens: list[list[tuple[str, object]]] = []
    for linha in bloco:
        recuo = len(linha) - len(linha.lstrip())
        conteudo = linha.strip()
        achado = marcador.match(linha) if recuo == 0 else None
        if achado:
            itens.append([("texto", achado.groups()[-1])])
        elif not itens:
            continue
        elif re.match(r"^- ", conteudo):
            itens[-1].append(("sub", {"texto": [conteudo[2:]], "recuo": recuo}))
        elif conteudo.startswith(("http://", "https://")):
            itens[-1].append(("exemplo", conteudo))
        elif itens[-1][-1][0] == "sub" and recuo > itens[-1][-1][1]["recuo"]:
            itens[-1][-1][1]["texto"].append(conteudo)
        else:
            itens[-1].append(("texto", conteudo))
    partes = []
    for item in itens:
        corpo, textos, subs = [], [], []

        def descarregar_texto() -> None:
            if textos:
                corpo.append(realcar(" ".join(textos)))
                textos.clear()

        def descarregar_subs() -> None:
            if subs:
                corpo.append("<ul>" + "".join(f"<li>{realcar(' '.join(s['texto']))}</li>" for s in subs) + "</ul>")
                subs.clear()

        for tipo, valor in item:
            if tipo == "texto":
                descarregar_subs()
                textos.append(valor)
            elif tipo == "exemplo":
                descarregar_texto()
                descarregar_subs()
                corpo.append(f'<span class="exemplo">{html.escape(valor)}</span>')
            else:
                descarregar_texto()
                subs.append(valor)
        descarregar_texto()
        descarregar_subs()
        partes.append(f"<li>{''.join(corpo)}</li>")
    return (f'<ol class="passos">{"".join(partes)}</ol>' if numerada else f'<ul>{"".join(partes)}</ul>')


def comeca_lista(linha: str) -> bool:
    return bool(re.match(r"^(\d+\. |- )", linha))


def bloco_html(bloco: list[str]) -> str:
    # Um parágrafo seguido de uma lista sem linha em branco entre eles: separa.
    if not comeca_lista(bloco[0]):
        for i, linha in enumerate(bloco):
            if comeca_lista(linha):
                return bloco_html(bloco[:i]) + bloco_html(bloco[i:])
    if re.match(r"^\d+\. ", bloco[0]):
        return lista(bloco, numerada=True)
    if bloco[0].startswith("- "):
        return lista(bloco, numerada=False)
    texto = " ".join(linha.strip() for linha in bloco)
    for prefixo, classe, rotulo in (("IMPORTANTE:", "importante", "IMPORTANTE"), ("Dica:", "dica", "DICA"),
                                    ("CUIDADO:", "cuidado", "CUIDADO")):
        if texto.startswith(prefixo):
            resto = texto[len(prefixo):].strip()
            resto = resto[:1].upper() + resto[1:]
            return f'<div class="quadro {classe}"><b class="rotulo">{rotulo}</b>{realcar(resto)}</div>'
    return f"<p>{realcar(texto)}</p>"


def montar_html(texto: str) -> str:
    titulo, partes = secoes(texto)
    corpo = []
    for cabecalho, linhas in partes:
        grupos = blocos(linhas)
        if not cabecalho:   # a introdução vai na capa
            intro = "".join(bloco_html(g) for g in grupos)
            corpo.append(f'<div class="capa"><h1>{html.escape(titulo_de_frase(titulo))}</h1>{intro}</div>')
            continue
        passo = re.match(r"^PASSO (\d+) - (.*)$", cabecalho)
        if passo:
            h2 = f'<h2><span class="passo">PASSO {passo.group(1)}</span>{html.escape(titulo_de_frase(passo.group(2)))}</h2>'
        else:
            h2 = f"<h2>{html.escape(titulo_de_frase(cabecalho))}</h2>"
        corpo.append(f"<section>{h2}{''.join(bloco_html(g) for g in grupos)}</section>")
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>{html.escape(titulo)}</title>'
            f"<style>{CSS}</style></head><body>{''.join(corpo)}</body></html>")


def main() -> int:
    pagina_html = montar_html(ORIGEM.read_text(encoding="utf-8-sig"))
    rodape = ('<div style="width:100%;font-size:8pt;color:#6b7280;text-align:center;font-family:sans-serif;">'
              'Automação das redes sociais — como usar no Windows · página <span class="pageNumber"></span> de '
              '<span class="totalPages"></span></div>')
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel="chrome", headless=True)
        pagina = navegador.new_page()
        pagina.set_content(pagina_html, wait_until="load")
        pagina.pdf(path=str(DESTINO), format="A4", print_background=True, display_header_footer=True,
                   header_template="<span></span>", footer_template=rodape,
                   margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"})
        navegador.close()
    print(f"PDF gerado: {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
