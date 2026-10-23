"""Dados que a interface mostra, sem nada de Tkinter (para dar para testar com pytest).

- interpretar as linhas que o login (abrir_redes) e a automação (interagir)
  imprimem sobre as contas, inclusive qual conta (perfil do navegador) está em uso;
- montar a situação de cada conta (do banco) e o resumo da configuração, das
  planilhas e das pausas por aviso da rede;
- conferir (e separar) o que o usuário digita ou cola para as planilhas;
- apagar, com segurança, a pasta de uma conta removida.

Na interface, "conta" é um perfil do navegador (banco.listar_perfis): uma pasta
do Chrome com um login do Instagram e um do TikTok. "Perfis a acessar" são os
perfis-alvo da planilha, outra coisa.
"""

import io
import re
import shutil
import sqlite3
from contextlib import redirect_stdout
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from automacao import banco, caminhos, configuracao, planilhas
from automacao.abrir_redes import REDES
from automacao.interagir import interpretar_perfil
from automacao.redes import comum, protecao

NOMES_REDES = tuple(rede.nome for rede in REDES)
STATUS_LOGADO = ("ja_logado", "login_detectado")
REDES_AUTOMACAO = ("Instagram", "TikTok")
MAXIMO_LETRAS_COMENTARIO = 300  # o mesmo limite do configuracao.validar
# O que o configuracao.validar entende como "ligado" nas opções sim/não.
VALORES_SIM = ("sim", "s", "1", "true", "on", "yes")

# ------------------------------------------------- linhas do abrir_redes.py

RE_STATUS = re.compile(r"^\[(?P<rede>[^\]]+)\] (?P<texto>Já logado|Login detectado|Não logado)\b")
RE_CONTA = re.compile(r"^\[(?P<rede>[^\]]+)\] Conta: (?P<conta>\S+)$")
RE_RESUMO = re.compile(r"^(?:[✔✘] \S+\s*)+$")
RE_ITEM_RESUMO = re.compile(r"([✔✘]) (\S+)")
STATUS_DO_TEXTO = {"Já logado": "ja_logado", "Login detectado": "login_detectado",
                   "Não logado": "nao_logado"}


@dataclass(frozen=True)
class EventoLogin:
    """O que uma linha da saída diz sobre o login de uma rede."""
    rede: str
    logado: bool | None = None   # None: a linha não fala da situação do login
    status: str | None = None    # código do banco (ja_logado...), quando a linha diz
    conta: str | None = None


def interpretar_linha(linha: str) -> list[EventoLogin]:
    """Eventos de login de uma linha da saída (lista vazia se ela não fala disso).

    Entende "[Rede] Já logado ✔", "[Rede] Não logado — ...", "[Rede] Login
    detectado ✔", "[Rede] Conta: @usuario" e o resumo "✔ TikTok  ✘ X ...".
    """
    linha = linha.strip()
    if achado := RE_CONTA.match(linha):
        if achado["rede"] in NOMES_REDES:
            return [EventoLogin(achado["rede"], conta=achado["conta"])]
        return []
    if achado := RE_STATUS.match(linha):
        if achado["rede"] in NOMES_REDES:
            status = STATUS_DO_TEXTO[achado["texto"]]
            return [EventoLogin(achado["rede"], logado=status in STATUS_LOGADO, status=status)]
        return []
    if RE_RESUMO.match(linha):
        itens = RE_ITEM_RESUMO.findall(linha)
        if itens and all(nome in NOMES_REDES for _, nome in itens):
            return [EventoLogin(nome, logado=marca == "✔") for marca, nome in itens]
    return []


# "Perfil: <nome>" é a primeira linha do login; "===== Perfil <nome> =====" vem
# antes de cada conta na automação. O nome vai até o " =====" do fim, então pode
# ter "=", espaços e acentos (o fim exige um espaço seguido só de "=").
RE_PERFIL = re.compile(r"^(?:Perfil: (?P<login>.+)|=+ Perfil (?P<automacao>.+?) =+)$")
# A automação com "recomeçar a lista" espera entre uma volta e outra.
INICIO_ESPERA_RECOMECO = "Lista concluída. Recomeçando em"


def perfil_da_linha(linha: str) -> str | None:
    """Nome da conta (perfil do navegador) que o processo passou a usar, ou None."""
    achado = RE_PERFIL.match(linha.strip())
    if not achado:
        return None
    nome = " ".join((achado["login"] or achado["automacao"]).split())
    return nome or None


def espera_recomeco(linha: str) -> str | None:
    """O texto da linha, se ela avisa que a lista acabou e a automação vai recomeçar; senão None."""
    linha = linha.strip()
    return linha if linha.startswith(INICIO_ESPERA_RECOMECO) else None


def estilo_da_linha(linha: str) -> str | None:
    """Cor da linha no registro da saída: "ok", "erro", "aviso" ou None."""
    if "Traceback" in linha or re.search(r"\b(Erro|Error|Exception)\b", linha):
        return "erro"
    if "✘" in linha or "Aviso" in linha or "Não logado" in linha or "Não está logado" in linha:
        return "aviso"
    if "✔" in linha or "Conta:" in linha:
        return "ok"
    return None


# ------------------------------------------------------------ logins

@dataclass
class SituacaoLogin:
    rede: str
    status: str | None = None          # ja_logado, login_detectado, nao_logado; None: nunca verificado
    conta: str | None = None
    ultima_conta: str | None = None    # última conta lida em qualquer execução
    verificado_em: str | None = None   # data e hora ISO do último registro
    verificando: bool = False          # o abrir_redes.py está rodando agora

    @property
    def logado(self) -> bool:
        return self.status in STATUS_LOGADO


def situacao_logins(conn: sqlite3.Connection, perfil_id: int | None = None) -> dict[str, SituacaoLogin]:
    """Situação de cada rede segundo o banco, na ordem do abrir_redes.py.

    Com `perfil_id`, só os logins daquela conta (perfil do navegador).
    """
    ultimos = banco.ultimo_status_logins(conn, perfil_id)
    situacoes = {}
    for nome in NOMES_REDES:
        dados = ultimos.get(nome, {})
        situacoes[nome] = SituacaoLogin(nome, dados.get("status"), dados.get("conta"),
                                        dados.get("ultima_conta"), dados.get("registrado_em"))
    return situacoes


def aplicar_evento(situacoes: dict[str, SituacaoLogin], evento: EventoLogin,
                   momento: str | None = None) -> None:
    """Atualiza a situação da rede com o que a linha da saída disse."""
    situacao = situacoes.setdefault(evento.rede, SituacaoLogin(evento.rede))
    if evento.conta:
        situacao.conta = situacao.ultima_conta = evento.conta
        if evento.logado is None and not situacao.logado:
            # A conta só é lida com a rede logada: a automação imprime "[Rede] Conta: @x"
            # sem um "Já logado" antes, e o banco pode ter um "não logado" antigo.
            evento = replace(evento, logado=True, status="ja_logado")
    if evento.logado is not None:
        if evento.status:
            situacao.status = evento.status
        elif situacao.status is None or situacao.logado != evento.logado:
            situacao.status = "login_detectado" if evento.logado else "nao_logado"
        situacao.verificado_em = momento or banco.agora()
        situacao.verificando = False


def texto_situacao(situacao: SituacaoLogin) -> tuple[str, str]:
    """(texto, estilo) da situação do login. Estilo: "ok", "erro" ou "suave"."""
    if situacao.verificando:
        return "Verificando…", "suave"
    if situacao.status is None:
        return "Nunca verificado", "suave"
    if situacao.logado:
        return "Logado", "ok"
    return "Não logado", "erro"


def texto_conta(situacao: SituacaoLogin) -> str:
    if situacao.conta:
        return situacao.conta
    if situacao.ultima_conta:
        return f"{situacao.ultima_conta} (última vista)"
    return "—"


def texto_login_curto(situacao: SituacaoLogin) -> str:
    """O login de uma rede numa célula das tabelas de contas: a conta, "não logado"..."""
    if situacao.verificando:
        return "verificando…"
    if situacao.status is None:
        return situacao.conta or "nunca verificado"  # a automação leu a conta, sem verificar o login
    if not situacao.logado:
        return "não logado"
    if situacao.conta:
        return situacao.conta
    if situacao.ultima_conta:
        return f"{situacao.ultima_conta} (última vista)"
    return "logado"


# ------------------------------------------- contas (perfis do navegador)

@dataclass
class SituacaoConta:
    """Uma conta da aba Contas: um perfil do navegador, com um login de cada rede."""
    id: int
    nome: str
    ativo: bool = True
    logins: dict[str, SituacaoLogin] = field(default_factory=dict)

    @property
    def principal(self) -> bool:
        return self.id == banco.PERFIL_PRINCIPAL

    def login(self, rede: str) -> SituacaoLogin:
        return self.logins.setdefault(rede, SituacaoLogin(rede))

    @property
    def ultimo_login(self) -> str | None:
        """Data e hora ISO da verificação de login mais recente, em qualquer rede."""
        momentos = [s.verificado_em for s in self.logins.values() if s.verificado_em]
        return max(momentos, key=_instante, default=None)


def _instante(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, OverflowError, OSError):
        return float("-inf")


def situacao_contas(conn: sqlite3.Connection) -> list[SituacaoConta]:
    """Todas as contas cadastradas (a Principal primeiro), com os logins de cada uma."""
    return [SituacaoConta(perfil["id"], perfil["nome"], bool(perfil["ativo"]),
                          situacao_logins(conn, perfil["id"]))
            for perfil in banco.listar_perfis(conn)]


def chave_nome(nome: str) -> str:
    """Nome da conta para comparar (o banco não diferencia maiúsculas)."""
    return " ".join(nome.split()).casefold()


def achar_conta(contas: list[SituacaoConta], perfil_id: int | None) -> SituacaoConta | None:
    return next((conta for conta in contas if conta.id == perfil_id), None)


def marcar_verificando(conta: SituacaoConta) -> None:
    """O login da conta começou: as redes dela ficam "verificando…" até a saída dizer."""
    for rede in NOMES_REDES:
        conta.login(rede).verificando = True


def tratar_linha_de_contas(contas: list[SituacaoConta], em_uso: int | None, linha: str,
                           ids_por_nome: dict[str, int] | None = None,
                           momento: str | None = None) -> tuple[int | None, bool]:
    """Aplica uma linha da saída do processo às contas. Devolve (conta em uso, se algo mudou).

    "Perfil: X" (login) e "===== Perfil X =====" (automação) trocam a conta em
    uso; as linhas de login ("[Rede] Já logado ✔", "[Rede] Conta: @x"...) valem
    para a conta em uso. `ids_por_nome` ({chave_nome: id}) é a foto dos nomes de
    quando o processo começou: se a conta for renomeada no meio, o nome que o
    processo imprime continua valendo. Conta desconhecida: as linhas são ignoradas.
    """
    nome = perfil_da_linha(linha)
    if nome is not None:
        chave = chave_nome(nome)
        novo = (ids_por_nome or {}).get(chave)
        if novo is None:
            novo = next((conta.id for conta in contas if chave_nome(conta.nome) == chave), None)
        return novo, novo != em_uso
    eventos = interpretar_linha(linha)
    conta = achar_conta(contas, em_uso)
    if not eventos or conta is None:
        return em_uso, False
    for evento in eventos:
        aplicar_evento(conta.logins, evento, momento)
    return em_uso, True


def resumo_contas_ativas(contas: list[SituacaoConta], mostrar: int = 4) -> tuple[str, str | None]:
    """(texto, estilo) de quais contas a automação vai usar (uma depois da outra). Estilo "erro" se nenhuma."""
    ativas = [conta.nome for conta in contas if conta.ativo]
    if not ativas:
        return "nenhuma: a automação não inicia (ative uma conta na aba Contas)", "erro"
    nomes = ", ".join(ativas[:mostrar])
    if len(ativas) > mostrar:
        nomes += f" e mais {len(ativas) - mostrar}"
    if len(contas) == 1:
        return nomes, None
    return f"{len(ativas)} de {len(contas)}: {nomes}", None


def nome_de_pasta_para_mostrar(pasta: Path) -> str:
    try:
        return str(pasta.resolve().relative_to(caminhos.RAIZ.resolve()))
    except (ValueError, OSError):
        return str(pasta)


def pasta_pode_ser_apagada(pasta: Path) -> bool:
    """True só para a pasta de uma conta extra, dentro de perfis/ (nunca a perfil_navegador)."""
    try:
        if pasta.is_symlink() or not pasta.is_dir():
            return False
        alvo = pasta.resolve()
        base = banco.PASTA_PERFIS.resolve()
        protegidas = {base, banco.PASTA_PERFIL.resolve(), caminhos.PASTA_PERFIL.resolve(),
                      caminhos.RAIZ.resolve(), banco.RAIZ.resolve()}
    except OSError:
        return False
    if any(protegida == alvo or protegida.is_relative_to(alvo) for protegida in protegidas):
        return False  # a própria pasta protegida ou uma pasta que a contém
    return alvo.is_relative_to(base)


def apagar_pasta_da_conta(pasta: Path) -> None:
    """Apaga a pasta do navegador de uma conta removida. ValueError se não for uma pasta de conta extra."""
    if not pasta_pode_ser_apagada(pasta):
        raise ValueError(f"Por segurança, o programa não apaga a pasta {pasta}.")
    shutil.rmtree(pasta)


def formatar_momento(iso: str | None, agora: datetime | None = None) -> str:
    """'hoje às 14:05', 'ontem às 09:30' ou '21/10/2026 às 18:00'."""
    if not iso:
        return "—"
    try:
        momento = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if momento.tzinfo is not None:
        momento = momento.astimezone()  # horário local
    agora = agora or datetime.now()
    hora = momento.strftime("%H:%M")
    if momento.date() == agora.date():
        return f"hoje às {hora}"
    if momento.date() == agora.date() - timedelta(days=1):
        return f"ontem às {hora}"
    return f"{momento:%d/%m/%Y} às {hora}"


# ------------------------------------------------- pausas por aviso da rede

def texto_pausas(pausas, agora: datetime | None = None, mostrar: int = 2) -> str:
    """'Em pausa por aviso da rede: @conta no Instagram até 24/10 15:30' ("" se não há pausa).

    `pausas` são as linhas de banco.pausas_ativas (rede, conta, motivo, inicio,
    fim); rede ou conta "*" valem para todas. As que já acabaram ficam de fora.
    """
    agora = agora or datetime.now().astimezone()
    if agora.tzinfo is None:
        agora = agora.astimezone()
    itens = []
    for pausa in pausas:
        try:
            fim = datetime.fromisoformat(pausa["fim"]).astimezone()  # sem fuso: hora local
        except (TypeError, ValueError, KeyError, IndexError, OverflowError):
            continue
        if fim <= agora:
            continue
        conta, rede = str(pausa["conta"] or "?"), str(pausa["rede"] or "*")
        if conta == "*":
            quem = "todas as contas"
        elif conta == "?":
            quem = "uma conta não identificada"
        else:
            quem = conta if conta.startswith("@") else f"@{conta}"
        onde = "em todas as redes" if rede == "*" else f"no {rede}"
        itens.append((fim, f"{quem} {onde} até {fim:%d/%m %H:%M}"))
    if not itens:
        return ""
    itens.sort(key=lambda item: item[0])
    texto = "; ".join(descricao for _, descricao in itens[:mostrar])
    if len(itens) > mostrar:
        texto += f" e mais {len(itens) - mostrar}"
    return f"Em pausa por aviso da rede: {texto}"


# ------------------------------------------------------ configuração

def ligado(valor: object) -> bool:
    """Uma opção sim/não da configuração, entendida como o configuracao.validar entende."""
    return str(valor or "").strip().lower() in VALORES_SIM


def texto_redes(valores: dict[str, str]) -> str:
    """Em quais redes a automação age, como o interagir entende (banco.redes_escolhidas)."""
    redes = banco.redes_escolhidas(valores)
    return f"só {redes[0]}" if len(redes) == 1 else "as duas (" + " e ".join(redes) + ")"


def resumo_extras(valores: dict[str, str]) -> str:
    """'Seguir: sim · Stories: não · Recomeçar: 20 min após terminar · Conta sem login: pula'."""
    sim_nao = lambda chave: "sim" if ligado(valores.get(chave)) else "não"  # noqa: E731
    recomecar = (f"{valores.get('repetir_minutos')} min após terminar" if ligado(valores.get("repetir"))
                 else "não")
    sem_login = "pula" if ligado(valores.get("continuar_sem_login")) else "não inicia"
    return (f"Seguir: {sim_nao('seguir')} · Stories: {sim_nao('ver_stories')} · "
            f"Recomeçar: {recomecar} · Conta sem login: {sem_login}")


def modo_protecao(valores: dict[str, str]) -> str:
    """"normal", "anti_bloqueio" ou "boost", como a automação decide (com os dois, vale o anti-bloqueio)."""
    return protecao.protecao_da_config(valores).nome


def texto_protecao(valores: dict[str, str]) -> str:
    """O modo de proteção para o resumo da aba Início."""
    modo = modo_protecao(valores)
    if modo == "anti_bloqueio":
        texto = "anti-bloqueio (lento e cauteloso)"
        if valores.get("boost") == "sim":
            texto += "; o boost marcado fica sem efeito"
        return texto
    if modo == "boost":
        return "boost: sem tetos por hora e por dia nem limite diário ⚠ mais risco de bloqueio"
    return "normal"


def texto_tetos(valores: dict[str, str]) -> str:
    """Os tetos por ação que valem, para o resumo da aba Início (o total por hora e por dia)."""
    modo = modo_protecao(valores)
    if modo == "boost":
        return "ignorados (boost)"
    escolhidos = protecao.ler_tetos(valores.get("tetos"))
    origem = "sugeridos" if escolhidos == protecao.tetos_sugeridos() else "personalizados"
    if modo == "anti_bloqueio":
        origem += ", no limite do anti-bloqueio"
    efetivos = protecao.protecao_da_config(valores)
    return (f"{origem}: até {efetivos.por_hora['total']} ações/h e {efetivos.por_dia['total']}/dia no total, "
            "por conta e rede")


def resumo_configuracao(valores: dict[str, str]) -> list[tuple[str, str]]:
    """Linhas (rótulo, texto) com a configuração salva."""
    if valores.get("modo") == "tempo":
        publicacoes = f"as das últimas {valores.get('semanas')} semana(s), em cada aba (modo tempo)"
    else:
        publicacoes = f"as {valores.get('quantidade')} primeiras de cada aba (modo quantidade)"
    limite = f"{valores.get('limite_diario')} publicações por conta, em cada rede"
    if modo_protecao(valores) == "boost":
        limite = f"ignorado no modo boost (salvo: {valores.get('limite_diario')} por conta, em cada rede)"
    return [
        ("Redes", texto_redes(valores)),
        ("Publicações", publicacoes),
        ("Comentários", resumo_comentarios(comum.comentarios_da_config(valores), mostrar=2, tamanho=30)),
        ("Ações extras", resumo_extras(valores)),
        ("Limite diário", limite),
        ("Tetos", texto_tetos(valores)),
        ("Proteção", texto_protecao(valores)),
    ]


def encurtar(texto: str, tamanho: int) -> str:
    return texto if len(texto) <= tamanho else texto[:tamanho - 1].rstrip() + "…"


def resumo_comentarios(comentarios: list[str], mostrar: int = 3, tamanho: int = 40) -> str:
    """Quantos comentários há e uma prévia dos primeiros."""
    if not comentarios:
        return "nenhum"
    previa = ", ".join(f"“{encurtar(c, tamanho)}”" for c in comentarios[:mostrar])
    if len(comentarios) > mostrar:
        previa += f" e mais {len(comentarios) - mostrar}"
    if len(comentarios) == 1:
        return f"1 comentário: {previa}"
    return f"{len(comentarios)} comentários, sorteados a cada publicação: {previa}"


def validar_comentario(texto: str, existentes: list[str], editando: bool = False) -> tuple[str | None, str | None]:
    """(comentário a gravar, erro) para um comentário digitado na lista.

    `existentes` são os outros comentários da lista (sem o que está sendo editado).
    """
    texto = " ".join(texto.split())  # sem quebras de linha nem espaços repetidos
    if not texto:
        return None, "Digite o comentário."
    if len(texto) > MAXIMO_LETRAS_COMENTARIO:
        return None, (f"O comentário pode ter até {MAXIMO_LETRAS_COMENTARIO} caracteres "
                      f"(este tem {len(texto)}): {encurtar(texto, 40)!r}.")
    if texto.casefold() in {e.casefold() for e in existentes}:
        return None, f"O comentário {encurtar(texto, 40)!r} já está na lista."
    if not editando and len(existentes) >= configuracao.MAXIMO_COMENTARIOS:
        return None, f"Use no máximo {configuracao.MAXIMO_COMENTARIOS} comentários."
    return texto, None


# ------------------------------------------------------------ planilhas

@dataclass
class ResumoPerfis:
    existe: bool
    por_rede: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REDES_AUTOMACAO, 0))
    invalidos: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.por_rede.values())

    def nas_redes(self, redes: tuple[str, ...]) -> int:
        """Quantos perfis-alvo há nas redes escolhidas (os das outras redes são pulados)."""
        return sum(self.por_rede.get(rede, 0) for rede in redes)

    def texto(self, redes: tuple[str, ...]) -> str:
        """'Instagram: 2 · TikTok: 1', marcando as redes fora da escolha."""
        return " · ".join(f"{rede}: {quantos}" + ("" if rede in redes else " (rede não escolhida)")
                          for rede, quantos in self.por_rede.items())


def resumo_perfis(caminho: Path | None = None) -> ResumoPerfis:
    """Quantos perfis por rede o interagir.py vai visitar, lidos como ele lê."""
    caminho = caminho or planilhas.PLANILHA_PERFIS
    resumo = ResumoPerfis(existe=caminho.exists())
    if not resumo.existe:
        return resumo
    with redirect_stdout(io.StringIO()):  # os avisos de linhas ignoradas iriam para o terminal
        textos = planilhas.ler_perfis(caminho)
    vistos = set()
    for texto in textos:
        try:
            perfil = interpretar_perfil(texto)
        except ValueError:
            resumo.invalidos.append(texto)
            continue
        if perfil not in vistos:  # mesmo critério do interagir.ler_perfis
            vistos.add(perfil)
            resumo.por_rede[perfil[0]] = resumo.por_rede.get(perfil[0], 0) + 1
    return resumo


def resumo_destinatarios(compartilhar_com: str,
                         caminho: Path | None = None) -> dict[str, tuple[list[str], bool]]:
    """{rede: (usuários, veio_da_configuracao)}, com o mesmo critério do interagir.montar_config.

    Se a planilha não tiver ninguém para o Instagram, vale a conta da configuração.
    """
    caminho = caminho or planilhas.PLANILHA_DESTINATARIOS
    destinatarios: dict[str, list[str]] = {rede: [] for rede in REDES_AUTOMACAO}
    if caminho.exists():
        with redirect_stdout(io.StringIO()):
            destinatarios.update(planilhas.ler_destinatarios(caminho))
    resumo = {rede: (usuarios, False) for rede, usuarios in destinatarios.items()}
    conta = compartilhar_com.strip().lstrip("@").lower()
    if conta and not destinatarios["Instagram"]:
        resumo["Instagram"] = ([conta], True)
    return resumo


def texto_links_invalidos(invalidos: list[str], mostrar: int = 3, tamanho: int = 45) -> str:
    """'2 links inválidos serão ignorados: a, b' (a automação pula essas linhas e segue)."""
    quantos = len(invalidos)
    inicio = "1 link inválido será ignorado" if quantos == 1 else f"{quantos} links inválidos serão ignorados"
    lista = ", ".join(encurtar(texto, tamanho) for texto in invalidos[:mostrar])
    if quantos > mostrar:
        lista += f" e mais {quantos - mostrar}"
    return f"{inicio}: {lista}"


# Um link novo pode vir colado ao anterior por vírgula ou ponto e vírgula
# ("link1,link2"); as vírgulas de dentro de um link (?x=1,2) ficam.
RE_VIRGULA_ANTES_DE_LINK = re.compile(r"[,;]+(?=(?:https?://|www\.|m\.)?(?:instagram|tiktok)\.com\b|https?://)",
                                      re.IGNORECASE)


def separar_links(texto: str) -> list[str]:
    """Os links de perfil colados no campo: separados por espaço ou linha.

    Vírgula e ponto e vírgula só separam quando vêm logo antes de outro link;
    no meio de um link (parte depois do "?"), fazem parte dele.
    """
    partes = []
    for bloco in texto.split():
        for parte in RE_VIRGULA_ANTES_DE_LINK.split(bloco):
            parte = parte.strip(",;")
            if parte:
                partes.append(parte)
    return partes


def separar_usuarios(texto: str) -> list[str]:
    """Os usuários colados no campo: separados por espaço, linha, vírgula ou ponto e vírgula."""
    return [parte for parte in re.split(r"[\s,;]+", texto) if parte]


def link_do_perfil(rede: str, perfil: str) -> str:
    """Link padrão do perfil, como fica gravado na planilha."""
    if rede == "TikTok":
        return f"https://www.tiktok.com/@{perfil}"
    return f"https://www.instagram.com/{perfil}/"


def chave_perfil(texto: str) -> tuple[str, str]:
    """Identifica o perfil para achar repetidos (o Instagram e o TikTok ignoram maiúsculas)."""
    try:
        rede, perfil = interpretar_perfil(texto)
    except ValueError:
        return "?", texto.strip().lower()
    return rede, perfil.lower()


def validar_novo_perfil(texto: str, aba: str, existentes: list[str]) -> tuple[str | None, str | None]:
    """(link a gravar, erro) para um perfil digitado na aba da planilha para_acessar."""
    texto = texto.strip()
    if not texto:
        return None, "Digite o link do perfil."
    try:
        rede, perfil = interpretar_perfil(texto)
    except ValueError:
        return None, f"{texto!r} não é um link de perfil do Instagram ou do TikTok."
    esperada = planilhas.REDES_POR_ABA[aba]
    if rede != esperada:
        return None, f"{texto!r} é um perfil do {rede}; esta aba é do {esperada}."
    if (rede, perfil.lower()) in {chave_perfil(e) for e in existentes}:
        return None, f"@{perfil} já está na lista."
    return link_do_perfil(rede, perfil), None


OBSERVACAO_INVALIDO = "inválido: será ignorado"


def observacao_perfil(texto: str, aba: str, anteriores: list[str]) -> str:
    """Aviso para mostrar ao lado de um link já gravado na planilha ("" se está tudo certo)."""
    if not planilhas.eh_link_de_perfil(texto):
        return "não é link (ignorado)"
    try:
        rede, _ = interpretar_perfil(texto)
    except ValueError:
        return OBSERVACAO_INVALIDO
    if chave_perfil(texto) in {chave_perfil(a) for a in anteriores}:
        return "repetido"
    if rede != planilhas.REDES_POR_ABA.get(aba):
        return f"perfil do {rede}"
    return ""


def validar_novo_destinatario(texto: str, existentes: list[str]) -> tuple[str | None, str | None]:
    """(usuário sem @, erro) para quem recebe o compartilhamento."""
    texto = texto.strip()
    if not texto:
        return None, "Digite o usuário."
    if not planilhas.RE_USUARIO.match(texto):
        return None, (f"{texto!r} não é um usuário: use só o @, com letras, números, "
                      "ponto ou _ (até 30).")
    usuario = texto.lstrip("@")
    if usuario.lower() in {e.strip().lstrip("@").lower() for e in existentes}:
        return None, f"@{usuario} já está na lista."
    return usuario, None


def observacao_destinatario(texto: str, anteriores: list[str]) -> str:
    if not planilhas.eh_usuario(texto):
        return "inválido (ignorado)"
    if texto.lstrip("@").lower() in {a.lstrip("@").lower() for a in anteriores}:
        return "repetido"
    return ""
