"""Interface gráfica (Tkinter): janela principal, abas e execução dos scripts.

- janela.py: janela principal e aba Início (python -m automacao.interface);
- aba_configuracoes.py e aba_planilhas.py: as outras abas;
- dados.py: o que as abas mostram, sem Tkinter (testável com pytest);
- processo.py: roda o login e a automação em segundo plano.

Este pacote não importa o Tkinter ao ser carregado: dados.py e processo.py
funcionam sem ele.
"""
