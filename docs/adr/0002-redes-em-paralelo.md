# ADR-0002: Instagram e TikTok ao mesmo tempo, cada um na sua janela do mesmo Chrome

**Status:** Aceito
**Data:** 23/10/2026
**Decisores:** dono do projeto

## Contexto

Com as duas redes escolhidas, a automação passava por toda a lista do Instagram e só depois pelo
TikTok, numa janela só. Com os tetos por hora, isso deixava uma rede parada esperando enquanto a
outra, que tem tetos próprios, não fazia nada. O dono pediu que as duas rodassem ao mesmo tempo,
"uma aba para cada".

Restrições:

- os logins de cada conta ficam numa pasta de perfil do Chrome (`perfil_navegador/` ou
  `perfis/<conta>/`), que só um Chrome pode abrir por vez. A pasta não pode ser copiada nem
  movida;
- a API síncrona do Playwright não pode ser usada por duas linhas de execução ao mesmo tempo;
- as regras de segurança continuam valendo: nunca clicar num botão já ligado e gravar a ação
  como "não confirmado" antes do clique;
- o Parar da interface tem que parar as duas redes.

## Opções consideradas

| Opção | Por que não (ou sim) |
|---|---|
| Dois processos, um por rede | Os dois precisariam abrir a mesma pasta de perfil: o Chrome não deixa. Copiar a pasta está fora de questão. |
| Duas abas na mesma janela, alternando numa linha de execução só | Cada ação bloqueia (vídeo até o fim, pausas). Alternar exigiria reescrever o motor em passos ou em `asyncio`. E uma aba no fundo fica "escondida" para o site: o vídeo pausa e a grade não carrega. |
| Reescrever tudo com a API assíncrona | Resolve, mas troca o motor inteiro e os ~800 testes por uma mudança de comportamento pequena. |
| **Uma segunda linha de execução com a sua própria conexão ao mesmo Chrome, numa janela própria** | Escolhida. |

## Decisão

1. Com as duas redes escolhidas, o Chrome abre com `--remote-debugging-port=0`. O Chrome sorteia
   a porta, escuta só em 127.0.0.1 e grava a porta no arquivo `DevToolsActivePort` da pasta do
   perfil. Ela é lida por `abrir_redes.endereco_de_controle`, que ignora um arquivo mais antigo
   que a abertura.
2. `interagir.processar_em_paralelo` divide a lista por rede:
   - o Instagram segue na linha principal, na janela de sempre;
   - o TikTok roda numa `threading.Thread` com a sua própria instância do Playwright, conectada ao
     mesmo Chrome por `connect_over_cdp`, e com a sua conexão ao SQLite (WAL, `timeout=30`);
   - a página do TikTok é uma **janela** nova (`Target.createTarget` com `newWindow`), não uma
     aba, para continuar visível para o site.
3. O estado que era global no motor passou a ser de cada linha de execução (`comum._linha`): o
   marcador antes do clique, o último comentário e os textos a evitar. Sem isso, o clique do
   TikTok poderia gravar o marcador da publicação do Instagram.
4. `terminal.SaidaComRotulos` junta o que cada linha de execução imprime até o fim da linha e
   escreve a linha inteira com `[Instagram]` ou `[TikTok]` na frente. A interface continua lendo
   as mesmas mensagens.
5. **Parar:** o Ctrl+C (SIGINT no Linux, arquivo de parada no Windows) chega à linha principal,
   que liga `terminal.PARADA`. As esperas da outra linha (`dormir`, `esperar_ate`, a espera do
   fim do vídeo) acordam com ele e levantam `KeyboardInterrupt`.
6. **Janela fechada:**
   - se só a janela de uma rede fecha e o navegador continua aberto, aquela rede abre outra e
     segue do perfil-alvo em que estava;
   - se o navegador inteiro fecha, as duas param e a conta recomeça com o navegador reaberto, cada
     rede do seu ponto.
7. Fica desligado com `--uma-rede-por-vez` e com `--trace`, porque o trace só grava a conexão
   principal.

## Consequências

- **Mais fácil:** as duas redes andam juntas, e um teto por hora numa rede não segura a outra.
- **Mais difícil:** há duas linhas de execução. Todo estado novo no motor precisa ser por linha
  (`comum._linha`) ou só da rede dele. Esperas novas precisam acordar com `PARADA`.
- **Risco aceito:** enquanto a automação roda com as duas redes, qualquer programa do mesmo
  computador pode se conectar à porta local e controlar esse Chrome logado. Quem roda programas
  no computador do usuário já consegue ler a pasta do perfil, então o risco novo é pequeno. A
  porta fecha com o navegador.
- **Comportamento:** as duas redes agem ao mesmo tempo na mesma conta. Os tetos continuam por
  rede, e cada plataforma só vê a própria atividade.
