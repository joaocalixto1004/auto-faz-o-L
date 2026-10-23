# ADR-0001: Código num pacote `automacao/`, dados na raiz e lançadores com instalação automática

**Status:** Aceito
**Data:** 23/10/2026
**Decisores:** dono do projeto

## Contexto

Os 14 módulos ficavam soltos na raiz, misturados com os dados do usuário (`perfil_navegador/`,
`automacao.db*`, `data/*.xlsx`). Cada módulo calculava os próprios caminhos a partir de
`__file__`, e a interface rodava os scripts pelo caminho do arquivo. Quem vai usar é leigo, no
Windows e no Linux, e não deve precisar instalar Python nem digitar comandos.

Restrições: os dados do usuário não podem mudar de lugar; o comportamento da automação e as
regras de segurança não podem mudar; os 347 testes continuam valendo.

## Decisão

1. **Um pacote `automacao/`** com dois subpacotes:
   - `redes/`: o motor compartilhado (`comum.py`) e um módulo por rede (`instagram.py`,
     `tiktok.py`). O motor fica junto das redes porque só elas o usam, e acrescentar uma rede
     passa a ser "um arquivo novo em `redes/`";
   - `interface/`: os módulos do Tkinter (`janela.py`, `aba_*.py`) e os dois sem Tkinter
     (`dados.py`, `processo.py`). O `__init__` não importa o Tkinter.
   Os demais (`abrir_redes`, `interagir`, `banco`, `planilhas`, `terminal`, `configuracao`,
   `instalacao`) ficam no primeiro nível: são pontos de entrada ou serviços usados por todos.
   Os nomes dos módulos foram mantidos, para o histórico e a documentação continuarem valendo.
2. **`automacao/caminhos.py`** é o único lugar que calcula caminhos. Ele aponta para a raiz do
   projeto (a pasta acima do pacote), onde os dados já estão. Os módulos importam o que usam
   (ex.: `banco.CAMINHO_BANCO`), então os testes continuam trocando esses nomes.
3. **Pontos de entrada com `python -m`** a partir da raiz (`python -m automacao.interface`,
   `automacao.abrir_redes`, `automacao.interagir`...). A interface roda os filhos do mesmo jeito
   (no Windows, por um preâmbulo com `runpy.run_module` que mantém o Ctrl+Break virando Ctrl+C).
4. **`pyproject.toml`** com metadados, dependências e a configuração do pytest; `[tool.uv]
   package = false`, porque é um aplicativo que roda da pasta, não uma biblioteca. O
   `requirements.txt` continua (os lançadores usam) e um teste garante que os dois batem.
5. **Lançadores finos** (`Iniciar.bat`, `iniciar.sh` e `instalar_atalho.sh` para o ícone no
   Linux) que só acham ou instalam o **uv**, criam o `.venv` com o Python do próprio uv e chamam
   `automacao/instalacao.py`. Toda a lógica fica nesse módulo em Python (testável): marcador com
   o hash do `requirements.txt` dentro do `.venv`, conferência do Tkinter e do navegador, log em
   `logs/instalacao.log`.

## Opções consideradas

### A: Pacote único com subpacotes, dados na raiz (escolhida)

| Dimensão | Avaliação |
|----------|-----------|
| Complexidade | Baixa: mover arquivos e trocar imports |
| Risco para os dados | Nenhum: nada muda de lugar |
| Familiaridade | Alta: mesmos nomes de módulos |

**Prós:** raiz limpa; caminhos num lugar só; testes e monkeypatch quase iguais.
**Contras:** os comandos ficam mais longos (`python -m automacao.interagir`); é preciso rodar da
raiz (os lançadores e a interface já fazem isso).

### B: Layout `src/` com o projeto instalado no venv (`pip install -e .`)

| Dimensão | Avaliação |
|----------|-----------|
| Complexidade | Média: build backend, instalação editável |
| Risco para os dados | Alto: uma instalação não editável levaria `caminhos.py` para o `site-packages`, e o banco e o perfil passariam a ser procurados lá |
| Familiaridade | Média |

**Prós:** comandos curtos (`[project.scripts]`), padrão de bibliotecas.
**Contras:** mais uma etapa na instalação do leigo, e o risco acima.

### C: Manter tudo na raiz, só com `caminhos.py` e os lançadores

**Prós:** mudança mínima. **Contras:** a raiz continua confusa, com código e dados misturados.

## Trade-offs

A opção A troca comandos um pouco mais longos por zero risco aos dados e uma instalação sem
etapa de build. O uv foi escolhido para os lançadores porque baixa o próprio Python (o leigo não
precisa ter Python instalado), é rápido e tem instaladores oficiais de uma linha nos dois
sistemas. Usar o Python do uv (`--managed-python`) garante o Tkinter, que falta em alguns Python
de sistema no Linux. No Windows, a interface abre com `python.exe` num console minimizado, e não
com `pythonw`, para o Parar continuar parando com calma.

## Consequências

- Mais fácil: achar o código de cada rede; mudar onde ficam os dados (só `caminhos.py`);
  instalar em outro computador (dois cliques).
- Mais difícil: rodar um módulo pelo caminho do arquivo (`python automacao/interagir.py` não
  funciona; use `-m`).
- A revisitar: se o projeto for distribuído como pacote instalável, os dados precisarão de uma
  pasta do usuário (ex.: variável de ambiente lida em `caminhos.py`) em vez da raiz do projeto.

## Itens de ação

1. [x] Mover os módulos, centralizar os caminhos e trocar a interface para `python -m`.
2. [x] `pyproject.toml`, lançadores, `instalacao.py` e testes da estrutura.
3. [ ] Testar o `Iniciar.bat` num Windows de verdade (caminho com espaço e acento, sem Python e
       sem Chrome).
