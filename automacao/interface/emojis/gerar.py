"""Gera as imagens dos botões da paleta de emojis (rodar à mão, na raiz do projeto).

    .venv/bin/python -m automacao.interface.emojis.gerar

O Tk de alguns Pythons (como o que o uv baixa no Linux) não desenha emojis com
as fontes do sistema: na janela eles viram quadradinhos. Por isso os botões da
paleta usam imagens, desenhadas aqui pelo Chrome com a fonte de emojis do
computador (Noto Color Emoji no Linux, Segoe UI Emoji no Windows). O texto do
comentário continua sendo o emoji de verdade.
"""

import base64

from playwright.sync_api import sync_playwright

from automacao.caminhos import PASTA_EMOJIS
from automacao.interface.componentes import EMOJIS, EMOJIS_BASICOS, arquivo_do_emoji

TAMANHO = 22  # pixels do quadrado de cada imagem

PAGINA = """<!doctype html><meta charset="utf-8"><style>
body {{ margin: 0; background: transparent; }}
div {{ width: {t}px; height: {t}px; display: flex; align-items: center; justify-content: center;
      font: {f}px 'Noto Color Emoji', 'Segoe UI Emoji', 'Apple Color Emoji', sans-serif; line-height: 1; }}
</style><div id="e"></div>"""


def main() -> None:
    pasta = PASTA_EMOJIS
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel="chrome", headless=True)
        pagina = navegador.new_page(device_scale_factor=1)
        pagina.set_content(PAGINA.format(t=TAMANHO, f=TAMANHO - 4))
        for emoji, _nome in dict.fromkeys(EMOJIS + EMOJIS_BASICOS):
            pagina.eval_on_selector("#e", "(e, t) => e.textContent = t", emoji)
            png = pagina.locator("#e").screenshot(omit_background=True)
            (pasta / arquivo_do_emoji(emoji)).write_bytes(png)
            print(f"{emoji}  {arquivo_do_emoji(emoji)}  {len(base64.b64encode(png))} bytes")
        navegador.close()


if __name__ == "__main__":
    main()
