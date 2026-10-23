#!/usr/bin/env python3
"""Banco de dados SQLite da aplicação.

O SQLite não roda como servidor: o banco é o arquivo automacao.db, aberto
direto pelo Python (módulo sqlite3 da biblioteca padrão), sem processo à parte.

Rodar este módulo mostra o histórico:
    python -m automacao.banco [--limite N]                # execuções do login (abrir_redes)
    python -m automacao.banco --publicacoes [--limite N]  # publicações da automação (interagir)
"""

import argparse
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from automacao.caminhos import CAMINHO_BANCO, PASTA_PERFIL, PASTA_PERFIS, RAIZ
from automacao.terminal import preparar_console

# Status de uma ação numa publicação (coluna acoes.status). Os três primeiros
# contam como concluídos: a ação nunca é repetida naquela publicação.
#   feito            o script fez a ação e confirmou na tela
#   ja_estava_feito  a ação já estava feita antes (ex.: post já curtido)
#   nao_confirmado   o script clicou, mas não conseguiu confirmar o resultado
#   falhou           a ação não chegou a ser feita; será tentada de novo
STATUS_CONCLUIDOS = ("feito", "ja_estava_feito", "nao_confirmado")

# Valores usados enquanto a página de configuração não tiver salvo nada.
CONFIG_PADRAO = {
    "modo": "quantidade",          # "quantidade" (N primeiras) ou "tempo" (últimas X semanas)
    "quantidade": "3",             # publicações por aba no modo quantidade (o máximo, com faixa)
    "quantidade_min": "",          # mínimo da faixa: cada conta sorteia de quantidade_min a quantidade; vazio = fixo
    "semanas": "2",                # janela de tempo no modo tempo
    "comentario": "Boa!!!",
    "compartilhar_com": "",        # Instagram, reserva para quando a planilha não tiver ninguém (vazio: sem reserva)
    "limite_diario": "40",         # máximo de publicações trabalhadas por conta, por rede, por dia
    "redes": "ambas",              # em quais redes a automação age: "instagram", "tiktok" ou "ambas"
    "anti_bloqueio": "nao",        # "sim" liga o modo anti-bloqueio (bem mais lento e cauteloso)
    "boost": "nao",                # "sim" ignora os tetos por hora/dia e o limite diário (mais risco)
    "tetos": "",                   # tetos por ação em JSON (protecao.validar_tetos); vazio: as sugestões
    "tetos_zerados_em": "",        # momento (ISO) do "zerar contagem": tetos e limite diário contam só depois dele
    "seguir": "nao",               # "sim": ao visitar um perfil da lista, segue se ainda não segue
    "ver_stories": "nao",          # "sim": assiste e curte os stories ainda não vistos do perfil
    "repetir": "nao",              # "sim": ao terminar a lista, recomeça sozinho depois de um intervalo
    "repetir_minutos": "20",       # intervalo entre o fim da lista e o recomeço
    "continuar_sem_login": "sim",  # "sim": contas sem login são puladas; "nao": para no início
}
REDES_ESCOLHA = {"instagram": ("Instagram",), "tiktok": ("TikTok",), "ambas": ("Instagram", "TikTok")}
PERFIL_PRINCIPAL = 1  # o perfil que já existia (pasta perfil_navegador): não pode ser removido

# Cada item é uma versão do esquema. Para mudar o banco, acrescente um novo
# item no fim da lista; nunca altere um item que já foi aplicado.
MIGRACOES = [
    """
    CREATE TABLE execucoes (
        id            INTEGER PRIMARY KEY,
        iniciada_em   TEXT NOT NULL,
        finalizada_em TEXT,
        motivo_fim    TEXT CHECK (motivo_fim IN ('enter', 'janela_fechada', 'interrompido', 'erro'))
    );

    CREATE TABLE logins (
        id            INTEGER PRIMARY KEY,
        execucao_id   INTEGER NOT NULL REFERENCES execucoes (id) ON DELETE CASCADE,
        rede          TEXT NOT NULL,
        status        TEXT NOT NULL CHECK (status IN ('ja_logado', 'nao_logado', 'login_detectado')),
        registrado_em TEXT NOT NULL,
        UNIQUE (execucao_id, rede)
    );

    CREATE INDEX idx_logins_rede ON logins (rede, registrado_em);
    """,
    """
    CREATE TABLE rodadas (
        id            INTEGER PRIMARY KEY,
        iniciada_em   TEXT NOT NULL,
        finalizada_em TEXT,
        status        TEXT CHECK (status IN ('concluida', 'interrompida', 'janela_fechada', 'erro'))
    );

    CREATE TABLE publicacoes (
        id             INTEGER PRIMARY KEY,
        rede           TEXT NOT NULL,
        codigo         TEXT NOT NULL,   -- código do post (Instagram) ou id do vídeo (TikTok)
        perfil         TEXT NOT NULL,   -- perfil onde a publicação foi encontrada
        aba            TEXT NOT NULL,   -- posts, reposts, videos ou republicacoes
        posicao        INTEGER NOT NULL,
        url            TEXT NOT NULL,
        link           TEXT,            -- link copiado pelo botão "Copiar link"
        encontrada_em  TEXT NOT NULL,
        atualizada_em  TEXT NOT NULL,
        UNIQUE (rede, codigo)
    );

    CREATE TABLE acoes (
        id             INTEGER PRIMARY KEY,
        publicacao_id  INTEGER NOT NULL REFERENCES publicacoes (id) ON DELETE CASCADE,
        rodada_id      INTEGER REFERENCES rodadas (id),
        acao           TEXT NOT NULL,
        status         TEXT NOT NULL CHECK (status IN ('feito', 'ja_estava_feito', 'nao_confirmado', 'falhou')),
        detalhe        TEXT,
        registrado_em  TEXT NOT NULL
    );

    CREATE INDEX idx_acoes_publicacao ON acoes (publicacao_id, acao, status);
    """,
    """
    ALTER TABLE publicacoes ADD COLUMN publicada_em TEXT;

    CREATE TABLE configuracoes (
        chave         TEXT PRIMARY KEY,
        valor         TEXT NOT NULL,
        atualizada_em TEXT NOT NULL
    );
    """,
    # O compartilhamento passou a ser registrado por destinatário
    # ("compartilhar:usuario"). Os registros antigos foram todos para a conta
    # configurada na época.
    """
    UPDATE acoes
    SET acao = 'compartilhar:' || coalesce(
        (SELECT valor FROM configuracoes WHERE chave = 'compartilhar_com'), '')
    WHERE acao = 'compartilhar';
    """,
    # Conta logada em cada rede ("@usuario"), lida da
    # página pelo abrir_redes. Fica vazia quando não deu para ler.
    """
    ALTER TABLE logins ADD COLUMN conta TEXT;
    """,
    # Destinatários passam a ser gravados em minúsculas.
    """
    UPDATE acoes SET acao = lower(acao) WHERE acao LIKE 'compartilhar:%';
    """,
    # Várias contas: cada perfil do navegador (pasta própria) guarda um login de
    # cada rede. O perfil que já existia vira o "Principal". As ações passam a
    # registrar a conta que as fez; as antigas recebem a conta lida no login.
    """
    CREATE TABLE perfis (
        id         INTEGER PRIMARY KEY,
        nome       TEXT NOT NULL UNIQUE,
        pasta      TEXT NOT NULL UNIQUE,   -- relativa à raiz do projeto
        ativo      INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
        criado_em  TEXT NOT NULL
    );
    INSERT INTO perfis (id, nome, pasta, ativo, criado_em)
    VALUES (1, 'Principal', 'perfil_navegador', 1, strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'));

    ALTER TABLE execucoes ADD COLUMN perfil_id INTEGER REFERENCES perfis (id);
    UPDATE execucoes SET perfil_id = 1;

    ALTER TABLE acoes ADD COLUMN conta TEXT;
    UPDATE acoes SET conta = (
        SELECT l.conta FROM logins l JOIN publicacoes p ON p.id = acoes.publicacao_id
        WHERE l.rede = p.rede AND l.conta IS NOT NULL
        ORDER BY l.execucao_id DESC, l.id DESC LIMIT 1);
    CREATE INDEX idx_acoes_conta ON acoes (conta, registrado_em);
    """,
    # Pausas por aviso da rede ("Tente novamente mais tarde", captcha...): a conta
    # fica parada até `fim`, inclusive em execuções futuras. rede/conta "*" = todas.
    """
    CREATE TABLE pausas (
        id      INTEGER PRIMARY KEY,
        rede    TEXT NOT NULL,
        conta   TEXT NOT NULL,
        motivo  TEXT,
        inicio  TEXT NOT NULL,
        fim     TEXT NOT NULL
    );
    CREATE INDEX idx_pausas_fim ON pausas (fim);
    """,
    # Contas sempre em minúsculas (as redes não diferenciam) e, em cada ação, o
    # perfil-alvo que estava sendo visitado (uma publicação pode aparecer em
    # vários perfis; os limites por alvo valem para o perfil da visita).
    """
    UPDATE acoes SET conta = lower(conta) WHERE conta IS NOT NULL;
    UPDATE logins SET conta = lower(conta) WHERE conta IS NOT NULL;
    UPDATE pausas SET conta = lower(conta) WHERE conta != '*';
    ALTER TABLE acoes ADD COLUMN alvo TEXT;
    UPDATE acoes SET alvo = (SELECT p.perfil FROM publicacoes p WHERE p.id = acoes.publicacao_id);
    """,
]


def agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def conectar(caminho: Path | None = None) -> sqlite3.Connection:
    """Abre o banco (criando o arquivo se preciso) e aplica migrações pendentes."""
    # Espera até 30 s se outro processo (interface x automação) estiver gravando;
    # no Windows, antivírus e travas de arquivo deixam essas esperas mais comuns.
    conn = sqlite3.connect(caminho or CAMINHO_BANCO, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrar(conn)
    return conn


def migrar(conn: sqlite3.Connection) -> None:
    """Aplica as migrações pendentes, uma por vez.

    Se outra conexão migrar o mesmo banco ao mesmo tempo (duas abas da página
    de configuração, por exemplo), a segunda percebe e segue sem erro.
    """
    while True:
        versao = conn.execute("PRAGMA user_version").fetchone()[0]
        if versao >= len(MIGRACOES):
            return
        numero = versao + 1
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{MIGRACOES[versao]}\n"
                               f"PRAGMA user_version = {numero};\nCOMMIT;")
        except sqlite3.OperationalError:
            if conn.in_transaction:
                conn.rollback()
            if conn.execute("PRAGMA user_version").fetchone()[0] >= numero:
                continue  # outra conexão aplicou esta migração primeiro
            raise


def iniciar_execucao(conn: sqlite3.Connection, perfil_id: int | None = PERFIL_PRINCIPAL) -> int:
    with conn:
        cursor = conn.execute("INSERT INTO execucoes (iniciada_em, perfil_id) VALUES (?, ?)",
                              (agora(), perfil_id))
    return cursor.lastrowid


def registrar_login(conn: sqlite3.Connection, execucao_id: int, rede: str, status: str) -> None:
    """Grava (ou atualiza) o status de login de uma rede nesta execução."""
    with conn:
        conn.execute(
            """
            INSERT INTO logins (execucao_id, rede, status, registrado_em)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (execucao_id, rede)
            DO UPDATE SET status = excluded.status, registrado_em = excluded.registrado_em
            """,
            (execucao_id, rede, status, agora()),
        )


def finalizar_execucao(conn: sqlite3.Connection, execucao_id: int, motivo: str) -> None:
    with conn:
        conn.execute(
            "UPDATE execucoes SET finalizada_em = ?, motivo_fim = ? WHERE id = ?",
            (agora(), motivo, execucao_id),
        )


def iniciar_rodada(conn: sqlite3.Connection) -> int:
    with conn:
        cursor = conn.execute("INSERT INTO rodadas (iniciada_em) VALUES (?)", (agora(),))
    return cursor.lastrowid


def finalizar_rodada(conn: sqlite3.Connection, rodada_id: int, status: str) -> None:
    with conn:
        conn.execute("UPDATE rodadas SET finalizada_em = ?, status = ? WHERE id = ?",
                     (agora(), status, rodada_id))


def registrar_publicacao(conn: sqlite3.Connection, rede: str, codigo: str, perfil: str,
                         aba: str, posicao: int, url: str, publicada_em: str | None = None) -> int:
    """Grava a publicação (se ainda não existir) e devolve o id dela."""
    momento = agora()
    with conn:
        conn.execute(
            """
            INSERT INTO publicacoes (rede, codigo, perfil, aba, posicao, url, publicada_em,
                                     encontrada_em, atualizada_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (rede, codigo) DO UPDATE SET
                atualizada_em = excluded.atualizada_em,
                publicada_em = coalesce(publicacoes.publicada_em, excluded.publicada_em)
            """,
            (rede, codigo, perfil, aba, posicao, url, publicada_em, momento, momento),
        )
    return conn.execute("SELECT id FROM publicacoes WHERE rede = ? AND codigo = ?",
                        (rede, codigo)).fetchone()[0]


def salvar_link(conn: sqlite3.Connection, publicacao_id: int, link: str) -> None:
    with conn:
        conn.execute("UPDATE publicacoes SET link = ?, atualizada_em = ? WHERE id = ?",
                     (link, agora(), publicacao_id))


def registrar_acao(conn: sqlite3.Connection, publicacao_id: int, rodada_id: int | None,
                   acao: str, status: str, detalhe: str | None = None,
                   conta: str | None = None, alvo: str | None = None) -> None:
    """Grava uma ação. `conta` é a conta ("@usuario") que agiu; `alvo`, o perfil visitado."""
    with conn:
        conn.execute(
            """
            INSERT INTO acoes (publicacao_id, rodada_id, acao, status, detalhe, registrado_em, conta, alvo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (publicacao_id, rodada_id, acao, status, detalhe, agora(), conta.lower() if conta else None,
             alvo),
        )


def acoes_concluidas(conn: sqlite3.Connection, rede: str, codigo: str,
                     conta: str | None = None) -> set[str]:
    """Ações já concluídas nesta publicação, em qualquer execução.

    Com `conta`, só as feitas por essa conta (cada conta curte e comenta por si).
    Ações antigas sem conta registrada valem para todas, por segurança.
    """
    marcadores = ", ".join("?" for _ in STATUS_CONCLUIDOS)
    filtro_conta, parametros = "", [rede, codigo, *STATUS_CONCLUIDOS]
    if conta:
        filtro_conta = "AND (a.conta = ? OR a.conta IS NULL)"
        parametros.append(conta.lower())
    linhas = conn.execute(
        f"""
        SELECT DISTINCT a.acao
        FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
        WHERE p.rede = ? AND p.codigo = ? AND a.status IN ({marcadores}) {filtro_conta}
        """,
        parametros,
    ).fetchall()
    return {linha[0] for linha in linhas}


def contagem_zerada_em(conn: sqlite3.Connection) -> str:
    """Momento (ISO) em que a contagem dos tetos foi zerada; "" se nunca foi."""
    linha = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'tetos_zerados_em'").fetchone()
    return (linha[0] if linha else "") or ""


def zerar_contagem_dos_tetos(conn: sqlite3.Connection) -> str:
    """Faz os tetos por hora/dia e o limite diário recomeçarem a contar de agora.

    O histórico continua inteiro: é ele que impede repetir uma ação numa
    publicação. Devolve o momento gravado.
    """
    momento = agora()
    salvar_configuracoes(conn, {"tetos_zerados_em": momento})
    return momento


def publicacoes_trabalhadas_hoje(conn: sqlite3.Connection, rede: str,
                                conta: str | None = None) -> int:
    """Quantas publicações da rede tiveram alguma ação feita pelo script hoje.

    Com `conta`, conta só as da conta (o limite diário é por conta). Depois de
    zerar a contagem, só o que veio depois conta.
    """
    filtro_conta, parametros = "", [rede, contagem_zerada_em(conn)]
    if conta:
        filtro_conta = "AND a.conta = ?"
        parametros.append(conta.lower())
    return conn.execute(
        f"""
        SELECT count(DISTINCT a.publicacao_id)
        FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
        WHERE p.rede = ? AND a.registrado_em >= ? AND a.status IN ('feito', 'nao_confirmado') {filtro_conta}
          AND p.aba NOT IN ('perfil', 'stories')
          AND substr(a.registrado_em, 1, 10) = date('now', 'localtime')
        """,
        parametros,
    ).fetchone()[0]


def ler_configuracoes(conn: sqlite3.Connection) -> dict[str, str]:
    """Configurações salvas pela página, completadas com os valores padrão."""
    valores = dict(CONFIG_PADRAO)
    for chave, valor in conn.execute("SELECT chave, valor FROM configuracoes"):
        valores[chave] = valor
    return valores


def salvar_configuracoes(conn: sqlite3.Connection, valores: dict[str, str]) -> None:
    momento = agora()
    with conn:
        conn.executemany(
            """
            INSERT INTO configuracoes (chave, valor, atualizada_em) VALUES (?, ?, ?)
            ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor, atualizada_em = excluded.atualizada_em
            """,
            [(chave, str(valor), momento) for chave, valor in valores.items()],
        )


def publicacoes_recentes(conn: sqlite3.Connection, limite: int = 10) -> list[sqlite3.Row]:
    """Publicações mais recentes, com o último status de cada ação."""
    return conn.execute(
        """
        SELECT p.id, p.rede, p.perfil, p.aba, p.posicao, p.codigo, p.link, p.atualizada_em,
               p.publicada_em,
               (SELECT group_concat(acao || ': ' || status, ', ')
                FROM (SELECT a.acao, a.status FROM acoes a
                      WHERE a.publicacao_id = p.id
                        AND a.id = (SELECT max(a2.id) FROM acoes a2
                                    WHERE a2.publicacao_id = p.id AND a2.acao = a.acao)
                      ORDER BY a.id)) AS acoes
        FROM publicacoes p
        ORDER BY p.atualizada_em DESC, p.id DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()


def historico(conn: sqlite3.Connection, limite: int = 10) -> list[sqlite3.Row]:
    """Últimas execuções, com o status de cada rede agrupado em uma coluna."""
    return conn.execute(
        """
        SELECT e.id, e.iniciada_em, e.finalizada_em, e.motivo_fim,
               group_concat(l.rede || ': ' || l.status, ', ') AS logins
        FROM execucoes e
        LEFT JOIN logins l ON l.execucao_id = e.id
        GROUP BY e.id
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()


def registrar_conta(conn: sqlite3.Connection, execucao_id: int, rede: str, conta: str) -> None:
    """Anota a conta logada no registro de login da rede nesta execução.

    O registro precisa existir (registrar_login vem antes); senão, nada muda.
    """
    with conn:
        conn.execute("UPDATE logins SET conta = ? WHERE execucao_id = ? AND rede = ?",
                     (conta, execucao_id, rede))


def ultimo_status_logins(conn: sqlite3.Connection, perfil_id: int | None = None) -> dict[str, dict]:
    """Último registro de login de cada rede (num perfil, se `perfil_id` for dado).

    {rede: {"status", "conta", "registrado_em", "ultima_conta"}}, em que
    `ultima_conta` é a conta lida mais recentemente naquela rede, em qualquer
    execução (serve de pista quando a última não conseguiu ler a conta).
    """
    filtro, parametros = "", []
    if perfil_id is not None:
        filtro = "AND {t}.execucao_id IN (SELECT id FROM execucoes WHERE perfil_id = ?)"
        parametros = [perfil_id, perfil_id]
    linhas = conn.execute(
        f"""
        SELECT l.rede, l.status, l.conta, l.registrado_em,
               (SELECT l2.conta FROM logins l2
                WHERE l2.rede = l.rede AND l2.conta IS NOT NULL {filtro.format(t="l2")}
                ORDER BY l2.execucao_id DESC, l2.id DESC LIMIT 1) AS ultima_conta
        FROM logins l
        WHERE l.id = (SELECT l3.id FROM logins l3 WHERE l3.rede = l.rede {filtro.format(t="l3")}
                      ORDER BY l3.execucao_id DESC, l3.id DESC LIMIT 1)
        """,
        parametros,
    ).fetchall()
    return {linha["rede"]: dict(linha) for linha in linhas}


# ------------------------------------------------------------------ perfis
# Cada perfil é uma pasta de navegador com um login de cada rede (várias contas).

def pasta_do_perfil(perfil: sqlite3.Row | dict) -> Path:
    """Pasta do navegador do perfil (o Principal é a perfil_navegador de sempre)."""
    if perfil["id"] == PERFIL_PRINCIPAL:
        return PASTA_PERFIL
    return RAIZ / perfil["pasta"]


def listar_perfis(conn: sqlite3.Connection, somente_ativos: bool = False) -> list[sqlite3.Row]:
    filtro = "WHERE ativo = 1" if somente_ativos else ""
    return conn.execute(f"SELECT id, nome, pasta, ativo, criado_em FROM perfis {filtro} ORDER BY id").fetchall()


def obter_perfil(conn: sqlite3.Connection, referencia: int | str) -> sqlite3.Row | None:
    """Perfil pelo id (número) ou pelo nome (sem diferenciar maiúsculas, inclusive acentuadas)."""
    texto = str(referencia).strip()
    if texto.isascii() and texto.isdecimal():
        return conn.execute("SELECT id, nome, pasta, ativo, criado_em FROM perfis WHERE id = ?",
                            (int(texto),)).fetchone()
    chave = " ".join(texto.split()).casefold()
    for perfil in conn.execute("SELECT id, nome, pasta, ativo, criado_em FROM perfis"):
        if perfil["nome"].casefold() == chave:
            return perfil
    return None


def _validar_nome_perfil(nome: str) -> str:
    nome = " ".join(nome.split())
    if not nome or len(nome) > 40:
        raise ValueError("O nome do perfil precisa ter de 1 a 40 caracteres.")
    if nome.isdecimal():
        # Só números se confundiria com o número (id) do perfil na linha de comando.
        raise ValueError("Use pelo menos uma letra no nome do perfil.")
    return nome


def _nome_de_pasta(nome: str) -> str:
    """'Conta da Maria' -> 'conta-da-maria' (só letras sem acento, números e hífens)."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-") or "perfil"


def criar_perfil(conn: sqlite3.Connection, nome: str) -> int:
    """Cadastra um perfil novo (pasta própria em perfis/). Devolve o id.

    Levanta ValueError se o nome estiver vazio, for longo demais ou já existir.
    A pasta é criada pelo navegador no primeiro login.
    """
    nome = _validar_nome_perfil(nome)
    if obter_perfil(conn, nome) is not None:
        raise ValueError(f"Já existe um perfil chamado {nome!r}.")
    base = PASTA_PERFIS.relative_to(RAIZ) / _nome_de_pasta(nome)
    pasta, sufixo = base, 2
    while (conn.execute("SELECT 1 FROM perfis WHERE pasta = ?", (pasta.as_posix(),)).fetchone()
           or (RAIZ / pasta).exists()):
        pasta, sufixo = base.with_name(f"{base.name}-{sufixo}"), sufixo + 1
    # O SQLite reaproveitaria o número de um perfil apagado; um processo em
    # andamento ainda o conheceria pelo nome antigo. Sempre usa um número novo.
    ultimo = int(conn.execute("SELECT valor FROM configuracoes WHERE chave = 'ultimo_perfil_id'").fetchone()[0]
                 if conn.execute("SELECT 1 FROM configuracoes WHERE chave = 'ultimo_perfil_id'").fetchone() else 0)
    novo_id = max(ultimo, conn.execute("SELECT coalesce(max(id), 0) FROM perfis").fetchone()[0]) + 1
    with conn:
        conn.execute("INSERT INTO perfis (id, nome, pasta, ativo, criado_em) VALUES (?, ?, ?, 1, ?)",
                     (novo_id, nome, pasta.as_posix(), agora()))
        conn.execute("""INSERT INTO configuracoes (chave, valor, atualizada_em) VALUES ('ultimo_perfil_id', ?, ?)
                        ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor""", (str(novo_id), agora()))
    return novo_id


def renomear_perfil(conn: sqlite3.Connection, perfil_id: int, nome: str) -> None:
    nome = _validar_nome_perfil(nome)
    existente = obter_perfil(conn, nome)
    if existente is not None and existente["id"] != perfil_id:
        raise ValueError(f"Já existe um perfil chamado {nome!r}.")
    with conn:
        conn.execute("UPDATE perfis SET nome = ? WHERE id = ?", (nome, perfil_id))


def definir_perfil_ativo(conn: sqlite3.Connection, perfil_id: int, ativo: bool) -> None:
    """Liga ou desliga o perfil na automação (o login continua guardado)."""
    with conn:
        conn.execute("UPDATE perfis SET ativo = ? WHERE id = ?", (1 if ativo else 0, perfil_id))


def remover_perfil(conn: sqlite3.Connection, perfil_id: int) -> Path:
    """Tira o perfil do cadastro e devolve a pasta dele (quem chama decide se apaga).

    O histórico de ações continua no banco. O Principal não pode ser removido.
    """
    if perfil_id == PERFIL_PRINCIPAL:
        raise ValueError("O perfil Principal não pode ser removido.")
    perfil = obter_perfil(conn, perfil_id)
    if perfil is None:
        raise ValueError("Perfil não encontrado.")
    with conn:
        conn.execute("UPDATE execucoes SET perfil_id = NULL WHERE perfil_id = ?", (perfil_id,))
        conn.execute("DELETE FROM perfis WHERE id = ?", (perfil_id,))
    return pasta_do_perfil(perfil)


# ------------------------------------------------------ ritmo e pausas por aviso

def acoes_recentes(conn: sqlite3.Connection, rede: str, conta: str | None, desde: str) -> list[tuple[str, str]]:
    """[(acao, momento)] das ações que clicaram desde `desde` (ISO), uma por publicação e ação.

    A marca "nao_confirmado" gravada antes do clique e o "feito" depois dele
    contam como uma ação só (vale o primeiro momento). Nada antes do momento
    em que a contagem foi zerada entra.
    """
    filtro_conta, parametros = "", [rede, max(desde, contagem_zerada_em(conn))]
    if conta:
        filtro_conta = "AND a.conta = ?"
        parametros.append(conta.lower())
    return [(linha[0], linha[1]) for linha in conn.execute(
        f"""
        SELECT a.acao, min(a.registrado_em)
        FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
        WHERE p.rede = ? AND a.status IN ('feito', 'nao_confirmado') AND a.registrado_em >= ? {filtro_conta}
        GROUP BY a.publicacao_id, a.acao
        ORDER BY 2
        """,
        parametros,
    )]


def publicacoes_do_alvo_hoje(conn: sqlite3.Connection, rede: str, perfil_alvo: str, conta: str | None) -> int:
    """Publicações do perfil-alvo em que a conta já agiu hoje (depois do último "zerar contagem")."""
    filtro_conta, parametros = "", [rede, perfil_alvo, contagem_zerada_em(conn)]
    if conta:
        filtro_conta = "AND a.conta = ?"
        parametros.append(conta.lower())
    return conn.execute(
        f"""
        SELECT count(DISTINCT a.publicacao_id)
        FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
        WHERE p.rede = ? AND lower(coalesce(a.alvo, p.perfil)) = lower(?) AND a.registrado_em >= ?
          AND a.status IN ('feito', 'nao_confirmado') {filtro_conta}
          AND p.aba NOT IN ('perfil', 'stories')
          AND substr(a.registrado_em, 1, 10) = date('now', 'localtime')
        """,
        parametros,
    ).fetchone()[0]


def _limite_dias(dias: float) -> str:
    return (datetime.now().astimezone() - timedelta(days=dias)).isoformat(timespec="seconds")


def comentou_no_alvo(conn: sqlite3.Connection, rede: str, perfil_alvo: str, conta: str | None, dias: float) -> bool:
    """A conta comentou em alguma publicação do perfil-alvo nos últimos `dias`?"""
    filtro_conta, parametros = "", [rede, perfil_alvo, _limite_dias(dias)]
    if conta:
        filtro_conta = "AND a.conta = ?"
        parametros.append(conta.lower())
    return conn.execute(
        f"""
        SELECT 1 FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
        WHERE p.rede = ? AND lower(coalesce(a.alvo, p.perfil)) = lower(?) AND a.acao = 'comentar'
          AND a.status IN ('feito', 'nao_confirmado') AND a.registrado_em >= ? {filtro_conta}
        LIMIT 1
        """,
        parametros,
    ).fetchone() is not None


def textos_comentados(conn: sqlite3.Connection, conta: str | None, dias: float,
                      rede: str | None = None) -> set[str]:
    """Textos que a conta comentou nos últimos `dias` numa rede (o detalhe começa pelo texto)."""
    filtros, parametros = "", [_limite_dias(dias)]
    if conta:
        filtros += " AND a.conta = ?"
        parametros.append(conta.lower())
    if rede:
        filtros += " AND p.rede = ?"
        parametros.append(rede)
    textos = set()
    for (detalhe,) in conn.execute(
            f"""SELECT a.detalhe FROM acoes a JOIN publicacoes p ON p.id = a.publicacao_id
                WHERE a.acao = 'comentar' AND a.status = 'feito'
                AND a.registrado_em >= ? AND a.detalhe IS NOT NULL {filtros}""", parametros):
        textos.add(detalhe.split(" (nenhum comentário seu", 1)[0])
    return textos


def registrar_pausa(conn: sqlite3.Connection, rede: str, conta: str, motivo: str, horas: float) -> str:
    """Põe a conta em pausa por `horas`. Devolve o fim (ISO)."""
    inicio = datetime.now().astimezone()
    fim = (inicio + timedelta(hours=horas)).isoformat(timespec="seconds")
    with conn:
        conn.execute("INSERT INTO pausas (rede, conta, motivo, inicio, fim) VALUES (?, ?, ?, ?, ?)",
                     (rede, conta if conta == "*" else conta.lower(), motivo,
                      inicio.isoformat(timespec="seconds"), fim))
    return fim


def _em_vigor(linhas: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Pausas cujo fim ainda não passou, comparando instantes (não texto: o fuso pode variar)."""
    agora_ = datetime.now().astimezone()
    vigentes = []
    for linha in linhas:
        try:
            if datetime.fromisoformat(linha["fim"]) > agora_:
                vigentes.append(linha)
        except (TypeError, ValueError):
            continue
    return sorted(vigentes, key=lambda linha: datetime.fromisoformat(linha["fim"]))


def pausa_ativa(conn: sqlite3.Connection, rede: str, conta: str | None) -> sqlite3.Row | None:
    """A pausa em vigor para a conta (ou a pausa geral de todas), se houver."""
    linhas = conn.execute(
        """
        SELECT rede, conta, motivo, inicio, fim FROM pausas
        WHERE (rede = ? AND conta = ?) OR (rede = '*' AND conta = '*')
        """,
        (rede, (conta or "?").lower()),
    ).fetchall()
    vigentes = _em_vigor(linhas)
    return vigentes[-1] if vigentes else None


def contas_com_aviso_hoje(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """(rede, conta) que receberam aviso hoje (sem contar a pausa geral)."""
    return {(linha[0], linha[1]) for linha in conn.execute(
        """SELECT DISTINCT rede, conta FROM pausas
           WHERE rede != '*' AND substr(inicio, 1, 10) = date('now', 'localtime')""")}


def pausas_ativas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Todas as pausas em vigor (para mostrar na interface)."""
    return _em_vigor(conn.execute("SELECT rede, conta, motivo, inicio, fim FROM pausas").fetchall())


def redes_escolhidas(valores: dict[str, str]) -> tuple[str, ...]:
    """Redes em que a automação age, pela configuração "redes"."""
    return REDES_ESCOLHA.get(valores.get("redes", "ambas"), REDES_ESCOLHA["ambas"])


def main() -> None:
    preparar_console()
    parser = argparse.ArgumentParser(description="Mostra o histórico gravado no banco.")
    parser.add_argument("--limite", type=int, default=10, help="quantos itens mostrar")
    parser.add_argument("--publicacoes", action="store_true",
                        help="mostra as publicações e ações da automação (interagir)")
    args = parser.parse_args()

    if not CAMINHO_BANCO.exists():
        print("Nenhum banco ainda. Ele é criado na primeira execução do login ou da automação.")
        return

    if args.publicacoes:
        with closing(conectar()) as conn:
            linhas = publicacoes_recentes(conn, args.limite)
        if not linhas:
            print("Nenhuma publicação registrada.")
        for linha in linhas:
            print(f"#{linha['id']}  {linha['rede']} @{linha['perfil']}  {linha['aba']} #{linha['posicao']}"
                  f"  ({linha['codigo']})  publicada em {(linha['publicada_em'] or '?')[:10]}"
                  f"  atualizada {linha['atualizada_em']}")
            print(f"    link: {linha['link'] or '-'}")
            print(f"    {linha['acoes'] or 'sem ações registradas'}")
        return

    with closing(conectar()) as conn:
        linhas = historico(conn, args.limite)
    if not linhas:
        print("Nenhuma execução registrada.")
    for linha in linhas:
        print(f"#{linha['id']}  {linha['iniciada_em']} → {linha['finalizada_em'] or 'em andamento'}"
              f"  ({linha['motivo_fim'] or '-'})")
        print(f"    {linha['logins'] or 'sem registros de login'}")


if __name__ == "__main__":
    main()
