# Automação das redes sociais

Programa de janela que visita perfis do **Instagram** e do **TikTok** num Google Chrome visível e,
em cada publicação, curte, reposta ou favorita, comenta, compartilha por mensagem e copia o link,
no ritmo de uma pessoa. Também pode seguir os perfis da lista, ver os stories e recomeçar a lista
sozinho de tempos em tempos. Funciona com várias contas, uma depois da outra. Tudo o que ele faz
fica gravado num banco local, para nunca repetir as próprias ações.

- [Para quem só quer usar](#para-quem-só-quer-usar)
- [Para quem desenvolve](#para-quem-desenvolve)

---

## Para quem só quer usar

### 1. Abrir o programa

Não é preciso instalar nada antes: na primeira vez, o próprio lançador baixa o que falta (o
Python, as bibliotecas e, se o Google Chrome não estiver instalado, um navegador Chromium). Isso
precisa de internet e pode levar alguns minutos. Nas outras vezes, a janela abre em segundos.

**Windows**

1. Abra a pasta do projeto e dê **dois cliques em `Iniciar.bat`**.
2. Se aparecer "O Windows protegeu o computador", clique em **Mais informações** e depois em
   **Executar assim mesmo**.
3. Uma janela preta mostra o andamento e se fecha quando a janela do programa abre. Fica uma
   outra janela preta **minimizada** na barra de tarefas: não a feche enquanto usa o programa,
   ela é o próprio programa rodando.

**Linux**

1. Na primeira vez, abra a pasta do projeto, clique com o botão direito em **`iniciar.sh`** e
   escolha **Executar como programa** (ou, num terminal aberto na pasta, rode `./iniciar.sh`).
   Ele instala o que faltar, cria o ícone **"Automação das redes sociais"** no menu de
   aplicativos e na Área de Trabalho e abre a janela.
2. Das próximas vezes, é só clicar no ícone (ou no `iniciar.sh` de novo: se já estiver tudo
   instalado, ele só abre a janela). Um terminal mostra o andamento e se fecha sozinho quando
   a janela do programa abre.
3. Se o ícone da Área de Trabalho não abrir, clique nele com o botão direito e escolha
   **Permitir execução**.

Se mudar a pasta do projeto de lugar, abra pelo `iniciar.sh` uma vez: ele refaz o ícone. Para
recriar um ícone apagado da Área de Trabalho, rode `./instalar_atalho.sh`.

Se algo der errado, a mensagem fica na tela até você apertar uma tecla, e os detalhes ficam em
`logs/instalacao.log`. Quase sempre é falta de internet: confira a conexão e abra de novo.

### 2. Primeiro login

1. Na aba **Início**, escolha a conta em **Conta:** (na primeira vez, só existe a **Principal**)
   e clique em **Fazer login / verificar contas**. O Chrome abre com uma aba do TikTok e uma do
   Instagram.
2. Faça o login em cada aba, como faria normalmente (código por SMS e captcha inclusos).
3. A tabela **Contas** mostra cada rede como **Logado** e o nome da conta (`@usuario`).
4. Quando terminar, clique em **Parar**. O navegador fecha e o login fica guardado.

Os logins ficam salvos: nas próximas vezes, as redes já abrem logadas, sem digitar senha. A conta
Principal fica na pasta `perfil_navegador/` e as outras em `perfis/`. Essas pastas são as suas
sessões: não as apague, não as envie para ninguém e não abra esses perfis em outro Chrome
enquanto o programa estiver usando.

### 3. Várias contas

Cada conta do programa é um perfil separado do Chrome, com **um login do Instagram e um do
TikTok**. Para usar mais contas, vá na aba **Contas**:

- **Adicionar conta…**: cria uma conta nova com o nome que você escolher (por exemplo, "Loja").
  Depois, selecione-a e clique em **Fazer login** para logar nas duas redes.
- **Ativar/Desativar**: a automação usa só as contas ativas, uma depois da outra, com uma pausa
  entre elas. As inativas são puladas, mas o login continua guardado.
- **Renomear…** e **Remover…**: remover tira a conta da lista e depois pergunta se apaga também a
  pasta com o login salvo. A Principal não pode ser removida.

Não precisa logar nas duas redes: uma conta só com Instagram é usada só no Instagram.

### 4. As abas da janela

**Início**

- **Contas**: se cada rede está logada, qual conta e quando foi verificada.
- **Configuração atual**: o resumo do que a automação vai fazer, com atalhos para editar.
- **Conta** e **Fazer login / verificar contas**: abre o navegador da conta escolhida para logar
  ou só conferir.
- **Iniciar automação**: começa a visitar os perfis da planilha com todas as contas ativas. Se
  houver alterações não salvas nas outras abas, a janela pergunta antes.
  - **Simular (não clica em nada)**: abre as publicações e confere os botões, mas não clica,
    não comenta e não grava nada no banco. Bom para testar se está tudo certo.
  - **Gravar trace**: grava um "filme" da execução em `traces/` para alguém rever depois, passo a
    passo. Os arquivos são **grandes** (dezenas a centenas de MB): use só quando for investigar
    um problema e apague os antigos.
  - **Modo teste: não comentar onde já comentei**: antes de comentar, procura um comentário
    seu na publicação e, se achar, não comenta. É mais lento e serve para testes. Normalmente
    o programa comenta direto (o banco já impede que ele comente duas vezes na mesma publicação).
- **Parar**: para com calma, como um Ctrl+C: grava o fim da execução e fecha o navegador. Se
  demorar, o botão vira **Forçar parada**, que encerra na hora.
- **Saída do processo**: o que o programa está fazendo, em tempo real.

Só uma coisa roda por vez (login ou automação), porque as duas usam os mesmos perfis do Chrome.

**Se a janela do navegador for fechada, ela reabre sozinha** em alguns segundos, e a automação
continua de onde parou. Para encerrar de verdade, use sempre o **Parar**. Fechar a janela do
programa com algo rodando pede confirmação e também para tudo.

**Contas**: veja [Várias contas](#3-várias-contas).

**Configurações** (valem para todas as contas ativas)

- **Em quais redes agir**: só Instagram, só TikTok ou as duas. Os logins continuam guardados; os
  perfis da planilha das outras redes são pulados.
- **Quais publicações**: as N primeiras de cada aba do perfil (1 a 50) ou as das últimas X
  semanas (1 a 52; posts fixados antigos ficam de fora). Só o número do modo escolhido precisa
  estar preenchido.
- **Comentários**: uma lista (até 50, de até 300 letras). A cada publicação o programa sorteia
  um, sem repetir o da publicação anterior.
- **Compartilhar com**: uma conta do Instagram de reserva, usada só quando a planilha de
  compartilhamento não tem ninguém na aba do Instagram. Pode ficar vazia.
- **Ações extras e repetição** (todas começam desligadas, menos a última):
  - **Seguir os perfis da lista**: segue o perfil visitado se a conta ainda não segue. Nunca
    deixa de seguir.
  - **Ver stories**: assiste e curte os stories ainda não vistos de cada perfil da lista.
  - **Recomeçar a lista automaticamente**: ao terminar todos os perfis, espera o tempo escolhido
    (20 minutos por padrão) e começa de novo. Só para pelo botão **Parar**. Configurações e
    planilhas são relidas a cada volta.
  - **Continuar mesmo se alguma conta não estiver logada**: contas sem login (ou que não abrirem)
    são puladas com um aviso, em vez de impedir o início. Vem ligada.
- **Limite diário**: o máximo de publicações trabalhadas **por conta e por rede**, por dia (1 a
  500; o padrão é 40). Volume baixo é o que mais ajuda a evitar bloqueios. Quando o limite chega,
  aquela conta para de agir naquela rede até o dia seguinte.
- **Proteção anti-bloqueio**: deixa a automação bem mais lenta e cautelosa (veja a tabela abaixo).
  Recomendada para contas novas ou que já receberam aviso.

Nada muda até clicar em **Salvar**. **Restaurar padrões** só preenche o formulário.

**Planilhas**

As duas planilhas da pasta `data/`, com uma aba para o Instagram e outra para o TikTok:

- **Perfis a acessar** (`para_acessar.xlsx`): cole o link do perfil, por exemplo
  `https://www.instagram.com/usuario/` ou `https://www.tiktok.com/@usuario`.
- **Compartilhar com** (`para_compartilhar.xlsx`): quem recebe as publicações por mensagem.
  Digite só o usuário (o @ é opcional).

Dá para colar vários de uma vez, separados por espaço, vírgula ou linha. Repetidos e links da
rede errada são recusados. O arquivo só muda ao clicar em **Salvar planilha**; feche a planilha
no Excel ou no LibreOffice antes de salvar. Anotações que você puser em outras colunas continuam
na linha do seu perfil.

### 5. O que o programa faz (e o que nunca faz)

| Rede | Abas do perfil | Ações em cada publicação | Extras (se ligados) |
|------|----------------|--------------------------|---------------------|
| Instagram | Posts e Reposts | curtir, repostar, comentar, compartilhar com cada conta da planilha, copiar o link | seguir, ver e curtir stories |
| TikTok | Vídeos e Republicações | curtir, comentar, adicionar aos favoritos, compartilhar com cada conta da planilha, copiar o link | seguir |

Regras de segurança:

- **Curtir, repostar, favoritar e seguir só são clicados quando a página mostra que ainda não
  foram feitos.** Esses botões ligam e desligam; clicar num que já está ligado desfaria a ação
  (inclusive uma que você fez à mão). Se o programa não tiver certeza do estado, ele não clica.
- **Compartilhar e comentar de novo é aceitável.** Se você já compartilhou ou comentou uma
  publicação por conta própria, o programa pode fazer isso mais uma vez.
- **O programa nunca repete o que ele mesmo fez.** Cada ação vai para o banco (`automacao.db`),
  separada por conta, antes do clique; se ele não conseguir confirmar o resultado na tela, grava
  "não confirmado" e segue em frente, sem tentar de novo. Só uma ação que nem chegou a acontecer
  é tentada de novo numa próxima vez.
- **Aviso, bloqueio ou captcha**: se a rede mostrar um aviso ("tente mais tarde", ação
  bloqueada), aquela conta para de agir naquela rede e fica **em pausa por 24 horas** (48 horas
  com a proteção anti-bloqueio), mesmo que você inicie a automação de novo. No TikTok, se
  aparecer um captcha, ele espera até 5 minutos você resolver na janela.
- **Ritmo de pessoa**: o mouse anda até cada botão, o texto é digitado letra a letra, há pausas
  aleatórias, os vídeos são assistidos até o fim, os carrosséis são passados até a última foto e
  as fotos são olhadas com calma. Isso reduz o risco, mas **não garante** que a rede não bloqueie
  a conta: os termos de uso do Instagram e do TikTok proíbem automação em qualquer ritmo.

Os limites de cada modo, por conta e por rede:

| | Normal | Anti-bloqueio |
|---|---|---|
| Ações por hora (total) | 30 | 15 |
| Ações por dia (total) | 200 | 80 |
| Comentários por hora / por dia | 5 / 30 | 2 / 8 |
| Intervalo mínimo entre comentários | 3 min | 15 min |
| Pausa longa | 10 a 20 min a cada 20 a 30 ações | 30 a 60 min a cada 10 a 15 ações |
| Horário | qualquer | só das 9h às 22h, com descanso a cada 25 min |
| Mesmo perfil-alvo | sem limite | até 2 publicações por dia, 1 comentário por semana |
| Depois de um aviso da rede | pausa de 24 h | pausa de 48 h; aviso em 2 contas no dia para todas por 24 h |

Quando uma ação falha, um print da tela vai para `capturas/`, para ajudar a entender o erro.

---

## Para quem desenvolve

### Estrutura

```
.
├── Iniciar.bat            lançador do Windows (dois cliques)
├── iniciar.sh             lançador do Linux
├── instalar_atalho.sh     cria o ícone .desktop no Linux (o iniciar.sh chama na primeira vez)
├── pyproject.toml         metadados, dependências e configuração do pytest
├── requirements.txt       dependências (usadas pelos lançadores; iguais às do pyproject)
├── requirements-dev.txt   + pytest
├── data/                  planilhas modelo (abas e cabeçalhos, sem perfis)
├── automacao/
│   ├── caminhos.py        TODOS os caminhos de dados e saídas (um lugar só)
│   ├── abrir_redes.py     sessão de login (ponto de entrada)
│   ├── interagir.py       automação pela linha de comando (ponto de entrada)
│   ├── banco.py           SQLite, migrações, contas (perfis) e pausas; também mostra o histórico
│   ├── planilhas.py       leitura e gravação das planilhas .xlsx
│   ├── terminal.py        console em UTF-8, pedido de parada, espera interrompível e trava
│   ├── configuracao.py    validação da configuração e página antiga, no navegador
│   ├── instalacao.py      conferência e preparo usados pelos lançadores
│   ├── redes/
│   │   ├── comum.py       motor: Config, ritmo de pessoa, vídeos, processar_publicacao
│   │   ├── protecao.py    tetos por hora e por dia, pausas, horário, avisos (modos normal e anti-bloqueio)
│   │   ├── instagram.py
│   │   └── tiktok.py
│   └── interface/
│       ├── janela.py      janela principal e aba Início
│       ├── aba_contas.py
│       ├── aba_configuracoes.py
│       ├── aba_planilhas.py
│       ├── componentes.py peças de Tkinter usadas por mais de uma aba
│       ├── dados.py       o que as abas mostram, sem Tkinter
│       └── processo.py    roda o login e a automação em segundo plano
├── tests/
└── docs/
    ├── adr/0001-estrutura-em-pacote.md
    └── exemplos/perfis.txt   exemplo para a opção --arquivo
```

Ficam na raiz e não são versionados: `.venv/`, `perfil_navegador/` e `perfis/` (sessões
logadas), `automacao.db` (com `-wal` e `-shm`), `capturas/`, `traces/` e `logs/`. As planilhas de
`data/` vão para o repositório **vazias** (só abas e cabeçalhos); na sua cópia, marque-as com
`git update-index --skip-worktree data/*.xlsx` para que os seus perfis não entrem num commit. Os
caminhos vêm todos de `automacao/caminhos.py`. O motivo do layout está em
[docs/adr/0001-estrutura-em-pacote.md](docs/adr/0001-estrutura-em-pacote.md).

### Ambiente

```bash
./iniciar.sh --preparar                          # cria o .venv e instala as dependências
uv pip install --python .venv/bin/python -r requirements-dev.txt   # + pytest
```

Ou à mão: `uv venv --python 3.12 --managed-python` e `uv pip install -r requirements-dev.txt`.
O `--managed-python` usa um Python do próprio uv, que já traz o Tkinter. No Windows, troque
`.venv/bin/python` por `.venv\Scripts\python`.

### Comandos

Sempre na raiz do projeto, com o Python do `.venv`:

| Comando | O que faz |
|---------|-----------|
| `python -m automacao.interface` (ou `python -m automacao`) | abre a janela |
| `python -m automacao.abrir_redes [--perfil CONTA] [--timeout MIN] [--sem-reabrir]` | sessão de login |
| `python -m automacao.interagir [links...] [--simular] [--trace] [--verificar-comentarios]` | automação; sem links, usa `data/para_acessar.xlsx` |
| `python -m automacao.interagir --arquivo docs/exemplos/perfis.txt --quantidade 2` | perfis de um arquivo de texto |
| `python -m automacao.banco [--publicacoes] [--limite N]` | histórico do banco |
| `python -m automacao.configuracao [--porta 8765] [--sem-abrir]` | página de configuração antiga |
| `python -m automacao.instalacao --verificar` | confere a instalação sem mudar nada |
| `python -m playwright show-trace traces/<arquivo>.zip` | revê um trace |
| `./iniciar.sh --verificar` / `Iniciar.bat --verificar` | o mesmo pelo lançador (não abre a janela) |

As opções `--quantidade`, `--semanas`, `--comentario` (repetível), `--compartilhar-com`,
`--limite-diario`, `--planilha`, `--planilha-compartilhar`, `--redes`, `--perfil` (repetível),
`--anti-bloqueio`, `--seguir`, `--ver-stories` e `--repetir MINUTOS` / `--sem-repetir` do
`interagir` têm prioridade sobre o que está salvo no banco. Veja `--help` de cada módulo.

### Testes

```bash
.venv/bin/python -m pytest -q
```

Os testes não abrem o navegador nem rodam o login ou a automação: o `tests/conftest.py` aponta o
banco, as planilhas, as capturas, os traces, os perfis, a trava e o log da instalação para pastas
temporárias, e troca por uma função que falha tudo o que abriria o Playwright ou rodaria o uv. Os
testes da janela usam uma janela escondida e scripts falsos (e são pulados sem tela). O
`tests/test_projeto.py` confere o formato dos lançadores (CRLF e rótulos do `.bat`, LF e bit de
execução dos `.sh`) e se o `requirements.txt` está igual ao `pyproject.toml`.

### Como funciona

- **Processos.** A janela roda o login e a automação como processos filhos
  (`python -m automacao.abrir_redes` / `automacao.interagir`, em `interface/processo.py`), com a
  saída lida linha a linha. O **Parar** pede ao script para parar como um Ctrl+C: no Linux,
  com um SIGINT só para o script; no Windows, criando o arquivo indicado em
  `AUTOMACAO_ARQUIVO_PARADA`, que o `terminal.vigiar_pedido_de_parada` transforma num
  `KeyboardInterrupt`. Todas as esperas usam `terminal.dormir`, que acorda na hora com o pedido.
  O script fecha o navegador e grava o fim no banco. Se ele não sair em 10 s, o grupo de
  processos é encerrado (no Windows, com `taskkill /T`). Uma trava em `logs/automacao.trava`
  impede duas automações ao mesmo tempo.
- **Contas.** Cada conta é uma linha da tabela `perfis` e uma pasta do Chrome
  (`banco.pasta_do_perfil`): a Principal (id 1) usa `perfil_navegador/`, as outras uma pasta
  em `perfis/` com o nome da conta. O `interagir` passa pelas contas ativas, uma depois da outra, descobre o
  `@usuario` logado em cada rede e grava cada ação com a conta (`acoes.conta`), então a
  deduplicação e os limites são por conta.
- **Navegador.** `abrir_redes.abrir_navegador` abre o Chrome (`channel="chrome"`) com o perfil
  persistente; sem Chrome, cai para o Chromium do Playwright. O login é detectado pelo cookie
  `sessionid`. Se a janela fecha, os dois scripts reabrem o navegador (a não ser com `--sem-reabrir`).
- **Motor.** `interagir` lê os perfis e a configuração e chama `processar_perfil` do módulo da
  rede (`MODULOS`). Cada rede lê a grade do perfil (`comum.coletar_da_grade`) e passa cada
  publicação a `comum.processar_publicacao`, que pula as ações já concluídas no banco, respeita o
  limite diário e chama as ações. Uma ação recebe `(página, config, publicação)` e devolve um
  `Resultado` (`feito`, `ja_estava_feito` ou `simulado`), ou levanta `NaoConfirmado` ou `Bloqueio`.
- **Proteção.** Antes de cada clique, `protecao.controlar_ritmo` aplica o horário, os tetos por
  hora e por dia (contados no banco), o intervalo entre comentários e as pausas longas; se não
  der para agir, levanta `PularAcao` (a ação fica para outra vez) ou `LimiteDiario`. Um aviso da
  rede vira uma linha na tabela `pausas`, conferida no início de cada perfil.
- **Banco.** SQLite em modo WAL. Para mudar o esquema, **acrescente** um item no fim de
  `banco.MIGRACOES`; nunca altere nem reordene os existentes (o banco aplica as que faltam pelo
  `PRAGMA user_version`).
- **Instalação.** Os lançadores só acham ou instalam o uv, criam o `.venv` e chamam
  `automacao/instalacao.py`, que instala as dependências quando o hash do `requirements.txt`
  muda (marcador em `.venv/automacao-instalacao.json`), confere o Tkinter e, sem Chrome, baixa o
  Chromium. Para forçar uma reinstalação em todo lugar, aumente `instalacao.VERSAO_PREPARO`.

### Como acrescentar uma rede

1. Crie `automacao/redes/<rede>.py` seguindo o `instagram.py` ou o `tiktok.py`: `REDE`,
   `data_publicacao(codigo)`, a leitura da grade, `acoes(cfg)` e
   `processar_perfil(conn, pg, rodada_id, cfg, perfil, resumo)`.
2. Em cada ação que muda algo na rede: confira o estado na página antes (botões de liga/desliga
   só são clicados quando estão desligados, de preferência com `conferir_antes` no `clicar`),
   chame `antes_de_clicar()` imediatamente antes do clique e levante `NaoConfirmado` se não der
   para confirmar o resultado. Nunca tente de novo um clique que já foi enviado.
3. Registre a rede em `interagir.MODULOS` e em `interagir.interpretar_perfil` (links de perfil),
   em `abrir_redes.REDES` (página de login e cookie de sessão), em `planilhas.REDES_POR_ABA` e
   `planilhas.ABAS` (aba das planilhas), em `interface/dados.REDES_AUTOMACAO`, em
   `banco.REDES_ESCOLHA` e nos destinatários de `interagir.montar_config`.
4. Escreva testes com páginas falsas, como em `tests/test_comum.py`.
