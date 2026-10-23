#!/usr/bin/env python3
"""Página de configuração das interações, servida só neste computador.

Abre no navegador uma página para escolher se o limite é por número de
publicações ou por tempo (últimas X semanas), além do comentário e do limite
diário. Também mostra o que há nas planilhas data/para_acessar.xlsx (perfis) e
data/para_compartilhar.xlsx (com quem compartilhar). Tudo é salvo no SQLite e
usado pelo interagir.py. A aba Configurações da interface faz o mesmo (e usa a
validação daqui); esta página continua funcionando.

Uso (na raiz do projeto, com o Python do .venv):
    python -m automacao.configuracao          # abre http://127.0.0.1:8765
    python -m automacao.configuracao --porta 9000 --sem-abrir
"""

import argparse
import html
import json
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from automacao import banco, planilhas
from automacao.redes import protecao
from automacao.terminal import preparar_console

LIMITES = {"quantidade": (1, 50), "semanas": (1, 52), "limite_diario": (1, 500)}
MAXIMO_COMENTARIOS = 50
# Opções que só a interface gráfica edita; a página web as preserva ao salvar.
CAMPOS_SO_NA_INTERFACE = ("redes", "anti_bloqueio", "boost", "tetos", "quantidade_min", "seguir", "ver_stories", "repetir",
                          "repetir_minutos", "continuar_sem_login")
# 50 comentários de 300 caracteres codificados no formulário (acentos viram %XX%XX).
TAMANHO_MAXIMO_FORMULARIO = 300_000


def validar(formulario: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Confere os campos enviados. Devolve (valores, erros)."""
    erros, valores = [], {}
    modo = formulario.get("modo", "")
    if modo not in ("quantidade", "tempo"):
        erros.append("Escolha se o limite é por número de publicações ou por tempo.")
    valores["modo"] = modo
    # Só o limite do modo escolhido é obrigatório. O do outro modo é gravado se
    # estiver certo; se estiver vazio ou errado, fica de fora e o valor salvo antes continua.
    em_uso = {"quantidade": modo != "tempo", "semanas": modo != "quantidade", "limite_diario": True}
    for campo, (minimo, maximo) in LIMITES.items():
        texto = formulario.get(campo, "").strip()
        if not (texto.isascii() and texto.isdecimal()) or not minimo <= int(texto) <= maximo:
            if em_uso[campo]:
                erros.append(f"O campo {ROTULOS[campo].lower()} precisa ser um número de {minimo} a {maximo}.")
                valores[campo] = texto
            continue
        valores[campo] = texto
    # Mínimo da faixa do modo quantidade (cada conta sorteia de mínimo a máximo). Vazio:
    # sem faixa (sempre o máximo). Só é cobrado no modo quantidade.
    minimo = formulario.get("quantidade_min", "").strip()
    if minimo:
        maximo = valores.get("quantidade", "")
        certo = minimo.isascii() and minimo.isdecimal() and 1 <= int(minimo) <= LIMITES["quantidade"][1]
        if certo and maximo.isascii() and maximo.isdecimal() and int(minimo) > int(maximo):
            certo = False
            if modo != "tempo":
                erros.append(f"O mínimo de publicações por aba ({minimo}) não pode ser maior que o máximo ({maximo}).")
        elif not certo and modo != "tempo":
            erros.append(f"O mínimo de publicações por aba precisa ser um número de 1 a {LIMITES['quantidade'][1]}.")
        if certo:
            valores["quantidade_min"] = minimo
    else:
        valores["quantidade_min"] = ""
    # Lista de comentários, um por linha (o campo antigo "comentario" ainda vale).
    texto = formulario.get("comentarios", formulario.get("comentario", ""))
    comentarios = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    if not comentarios:
        erros.append("Informe pelo menos um comentário.")
    elif len(comentarios) > MAXIMO_COMENTARIOS:
        erros.append(f"Use no máximo {MAXIMO_COMENTARIOS} comentários.")
    longos = [c for c in comentarios if len(c) > 300]
    if longos:
        erros.append(f"Cada comentário pode ter até 300 caracteres (passou: {longos[0][:40]!r}...).")
    valores["comentarios"] = json.dumps(comentarios, ensure_ascii=False)
    valores["comentario"] = comentarios[0] if comentarios else ""
    # Em quais redes agir. Formulários antigos (sem o campo) continuam valendo "ambas".
    redes = formulario.get("redes", "ambas").strip().lower() or "ambas"
    if redes not in banco.REDES_ESCOLHA:
        erros.append("Escolha as redes: só Instagram, só TikTok ou as duas.")
    valores["redes"] = redes
    # Modo anti-bloqueio: "sim" ou "nao" (aceita também 1/0, true/false, on/off).
    for chave, padrao in (("anti_bloqueio", "nao"), ("boost", "nao"), ("seguir", "nao"), ("ver_stories", "nao"),
                          ("repetir", "nao"), ("continuar_sem_login", "sim")):
        valor = formulario.get(chave, padrao).strip().lower()
        valores[chave] = "sim" if valor in ("sim", "s", "1", "true", "on", "yes") else "nao"
    minutos = formulario.get("repetir_minutos", "20").strip()
    if not (minutos.isascii() and minutos.isdecimal()) or not 1 <= int(minutos) <= 1440:
        erros.append("O intervalo para recomeçar a lista precisa ser um número de 1 a 1440 minutos.")
    valores["repetir_minutos"] = minutos
    # Tetos por ação (JSON). Iguais às sugestões, ficam vazios: assim valem as
    # sugestões de versões futuras.
    tetos, erros_tetos = protecao.validar_tetos(formulario.get("tetos", ""))
    erros.extend(erros_tetos)
    valores["tetos"] = "" if tetos == protecao.tetos_sugeridos() else json.dumps(tetos, ensure_ascii=False)
    # Conta reserva: só é usada quando a planilha não tem ninguém para o Instagram.
    # Vazio vale (sem reserva); preenchido precisa ser um usuário válido.
    conta = formulario.get("compartilhar_com", "").strip().lstrip("@")
    if conta and not planilhas.RE_USUARIO.match(conta):
        erros.append("Informe o usuário do Instagram que recebe o compartilhamento: só o nome "
                     "(letras, números, ponto e _), sem espaços nem link.")
    valores["compartilhar_com"] = conta
    return valores, erros


ROTULOS = {
    "quantidade": "Publicações por aba",
    "semanas": "Semanas",
    "limite_diario": "Limite diário por rede",
}


def resumo_planilhas() -> str:
    """HTML com o que as planilhas da pasta data/ contêm."""
    itens = []
    for titulo, caminho, ler in (
        ("Perfis a acessar", planilhas.PLANILHA_PERFIS, lambda c: {"": planilhas.ler_perfis(c)}),
        ("Compartilhar com", planilhas.PLANILHA_DESTINATARIOS, planilhas.ler_destinatarios),
    ):
        if not caminho.exists():
            itens.append(f"<li><strong>{titulo}:</strong> data/{caminho.name} não encontrada</li>")
            continue
        try:
            dados = ler(caminho)
        except Exception as erro:  # noqa: BLE001 - mostra o problema na página
            itens.append(f"<li><strong>{titulo}:</strong> erro ao ler data/{caminho.name}: "
                         f"{html.escape(str(erro))}</li>")
            continue
        partes = []
        for rede, valores in dados.items():
            lista = ", ".join(html.escape(v) for v in valores) or "ninguém"
            partes.append(f"{html.escape(rede)}: {lista}" if rede else lista)
        itens.append(f"<li><strong>{titulo}</strong> (data/{caminho.name}): {'; '.join(partes)}</li>")
    return "<ul class=\"planilhas\">" + "".join(itens) + "</ul>"


def _comentarios_em_linhas(valores: dict[str, str]) -> str:
    """Os comentários salvos (lista em JSON) como texto, um por linha."""
    try:
        lista = json.loads(valores.get("comentarios") or "[]")
    except json.JSONDecodeError:
        lista = []
    if not isinstance(lista, list) or not lista:
        lista = [valores.get("comentario", "")]
    return "\n".join(str(c) for c in lista)


def pagina(valores: dict[str, str], aviso: str = "", erros: list[str] | None = None) -> str:
    v = {chave: html.escape(valor, quote=True) for chave, valor in valores.items()}
    linhas_comentarios = html.escape(_comentarios_em_linhas(valores))
    marcado = lambda modo: "checked" if valores.get("modo") == modo else ""  # noqa: E731
    if erros:
        faixa = '<div class="faixa erro" role="alert"><ul>' + "".join(
            f"<li>{html.escape(e)}</li>" for e in erros) + "</ul></div>"
    elif aviso:
        faixa = f'<div class="faixa ok" role="status">{html.escape(aviso)}</div>'
    else:
        faixa = ""
    if valores.get("modo") == "tempo":
        resumo = f"publicações das últimas {v.get('semanas')} semana(s) de cada aba"
    else:
        resumo = f"as {v.get('quantidade')} primeiras publicações de cada aba"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Configuração das interações</title>
<style>
  :root {{
    --fundo: #f6f7f9; --cartao: #ffffff; --texto: #1d2129; --suave: #5b6472;
    --borda: #d9dde3; --destaque: #2f5bea; --destaque-texto: #ffffff;
    --ok-fundo: #e7f6ec; --ok-texto: #1d6b3a; --erro-fundo: #fdecec; --erro-texto: #9b1c1c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --fundo: #14161a; --cartao: #1d2026; --texto: #e8eaed; --suave: #a3abb8;
      --borda: #343a44; --destaque: #6d8cff; --destaque-texto: #0d1020;
      --ok-fundo: #173323; --ok-texto: #8fe0ad; --erro-fundo: #3a1a1a; --erro-texto: #ffaaaa;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--fundo); color: var(--texto);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width: 640px; margin: 0 auto; padding: 32px 16px 48px; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .sub {{ color: var(--suave); margin: 0 0 24px; }}
  form {{ background: var(--cartao); border: 1px solid var(--borda); border-radius: 12px; padding: 24px; }}
  fieldset {{ border: 0; padding: 0; margin: 0 0 24px; }}
  legend {{ font-weight: 600; margin-bottom: 12px; padding: 0; }}
  .opcoes {{ display: grid; gap: 12px; grid-template-columns: 1fr 1fr; }}
  @media (max-width: 520px) {{ .opcoes {{ grid-template-columns: 1fr; }} }}
  .opcao {{ border: 1px solid var(--borda); border-radius: 10px; padding: 14px; cursor: pointer; display: block; }}
  .opcao:has(input:checked) {{ border-color: var(--destaque); box-shadow: 0 0 0 1px var(--destaque); }}
  .opcao .titulo {{ display: flex; gap: 8px; align-items: center; font-weight: 600; }}
  .opcao .campo {{ display: block; margin-top: 12px; }}
  .opcao:not(:has(input[type=radio]:checked)) .campo {{ opacity: .45; }}
  .rotulo {{ display: block; font-size: .9rem; color: var(--suave); margin-bottom: 4px; }}
  input[type=number], input[type=text], textarea {{ width: 100%; resize: vertical; padding: 10px 12px; font: inherit; color: var(--texto);
         background: var(--fundo); border: 1px solid var(--borda); border-radius: 8px; }}
  input:focus-visible {{ outline: 2px solid var(--destaque); outline-offset: 1px; }}
  .linha {{ display: grid; gap: 16px; grid-template-columns: 1fr 1fr; margin-bottom: 16px; }}
  @media (max-width: 520px) {{ .linha {{ grid-template-columns: 1fr; }} }}
  .dica {{ font-size: .85rem; color: var(--suave); margin: 4px 0 0; }}
  button {{ font: inherit; font-weight: 600; padding: 12px 20px; border: 0; border-radius: 8px;
           background: var(--destaque); color: var(--destaque-texto); cursor: pointer; }}
  .rodape {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-top: 8px; }}
  .resumo {{ color: var(--suave); font-size: .9rem; margin: 0; }}
  .faixa {{ border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; }}
  .faixa ul {{ margin: 0; padding-left: 18px; }}
  .ok {{ background: var(--ok-fundo); color: var(--ok-texto); }}
  .erro {{ background: var(--erro-fundo); color: var(--erro-texto); }}
  .planilhas {{ background: var(--cartao); border: 1px solid var(--borda); border-radius: 12px;
               padding: 16px 16px 16px 36px; margin: 0 0 16px; color: var(--suave); font-size: .9rem; }}
  .planilhas strong {{ color: var(--texto); }}
  .planilhas li + li {{ margin-top: 6px; }}
</style>
</head>
<body>
<main>
  <h1>Configuração das interações</h1>
  <p class="sub">Vale para o Instagram e o TikTok, nas próximas execuções da automação.</p>
  <p class="dica">As redes, a proteção (anti-bloqueio ou boost), os tetos por ação e as ações extras
    ficam na aba Configurações da janela do programa; esta página não muda essas opções.</p>
  {faixa}
  {resumo_planilhas()}
  <form method="post" action="/">
    <fieldset>
      <legend>Quais publicações trabalhar em cada aba</legend>
      <div class="opcoes">
        <label class="opcao">
          <span class="titulo"><input type="radio" name="modo" value="quantidade" {marcado("quantidade")}> Por número de publicações</span>
          <span class="campo">
            <span class="rotulo">Publicações por aba</span>
            <input type="number" name="quantidade" min="1" max="50" value="{v.get('quantidade', '')}" aria-label="Publicações por aba">
          </span>
        </label>
        <label class="opcao">
          <span class="titulo"><input type="radio" name="modo" value="tempo" {marcado("tempo")}> Por tempo</span>
          <span class="campo">
            <span class="rotulo">Últimas semanas</span>
            <input type="number" name="semanas" min="1" max="52" value="{v.get('semanas', '')}" aria-label="Semanas">
          </span>
        </label>
      </div>
      <p class="dica">No modo por tempo, conta a data em que a publicação foi postada. Posts fixados antigos ficam de fora.</p>
    </fieldset>
    <div class="linha">
      <div>
        <label class="rotulo" for="comentarios">Comentários (um por linha)</label>
        <textarea id="comentarios" name="comentarios" rows="5">{linhas_comentarios}</textarea>
        <p class="dica">A cada publicação, o sistema sorteia um deles, sem repetir o anterior.</p>
      </div>
      <div>
        <label class="rotulo" for="compartilhar_com">Compartilhar com, se a planilha não tiver ninguém (Instagram)</label>
        <input type="text" id="compartilhar_com" name="compartilhar_com" value="{v.get('compartilhar_com', '')}">
      </div>
    </div>
    <div class="linha">
      <div>
        <label class="rotulo" for="limite_diario">Limite diário por rede</label>
        <input type="number" id="limite_diario" name="limite_diario" min="1" max="500" value="{v.get('limite_diario', '')}">
        <p class="dica">Máximo de publicações trabalhadas por dia em cada rede. Volume baixo evita bloqueios.</p>
      </div>
    </div>
    <div class="rodape">
      <p class="resumo">Configuração atual: {resumo}.</p>
      <button type="submit">Salvar</button>
    </div>
  </form>
</main>
</body>
</html>"""


class Manipulador(BaseHTTPRequestHandler):
    def _responder(self, corpo: str, status: int = 200) -> None:
        dados = corpo.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:  # noqa: N802 - nome exigido pelo http.server
        if urlparse(self.path).path != "/":
            self._responder("Não encontrado", 404)
            return
        with closing(banco.conectar()) as conn:
            self._responder(pagina(banco.ler_configuracoes(conn)))

    def do_POST(self) -> None:  # noqa: N802
        # Só aceita envios da própria página (evita que outro site mude a configuração).
        origem = self.headers.get("Origin")
        if origem and urlparse(origem).hostname not in ("127.0.0.1", "localhost"):
            self._responder("Origem não permitida", 403)
            return
        try:
            tamanho = max(int(self.headers.get("Content-Length", 0)), 0)
            if tamanho > TAMANHO_MAXIMO_FORMULARIO:
                self._responder("Formulário grande demais", 413)
                return
            corpo = self.rfile.read(tamanho).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            self._responder("Requisição inválida", 400)
            return
        campos = {k: v[0] for k, v in parse_qs(corpo).items()}
        # Esta página não tem todos os campos (redes, anti-bloqueio, seguir...):
        # os ausentes ficam como estão, em vez de voltar ao padrão.
        with closing(banco.conectar()) as conn:
            atuais = banco.ler_configuracoes(conn)
        for chave in CAMPOS_SO_NA_INTERFACE:
            if chave not in campos and chave in atuais:
                campos[chave] = atuais[chave]
        valores, erros = validar(campos)
        if erros:
            self._responder(pagina(valores, erros=erros), 400)
            return
        with closing(banco.conectar()) as conn:
            banco.salvar_configuracoes(conn, valores)
            atuais = banco.ler_configuracoes(conn)
        self._responder(pagina(atuais, aviso="Configuração salva."))

    def log_message(self, formato: str, *args) -> None:
        pass  # sem log de cada requisição no terminal


def main() -> None:
    preparar_console()
    parser = argparse.ArgumentParser(description="Página de configuração das interações.")
    parser.add_argument("--porta", type=int, default=8765)
    parser.add_argument("--sem-abrir", action="store_true", help="não abre o navegador sozinho")
    args = parser.parse_args()

    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Manipulador)
    endereco = f"http://127.0.0.1:{args.porta}/"
    print(f"Página de configuração em {endereco}  (Ctrl+C para encerrar)")
    if not args.sem_abrir:
        webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
