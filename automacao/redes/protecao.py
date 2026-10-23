"""Ritmo e limites da automação: modo normal e modo anti-bloqueio.

As regras vêm de pesquisa em fontes públicas (Termos e Padrões da Comunidade
do Instagram/Meta e do TikTok, e estimativas de terceiros, já que as
plataformas não publicam limites numéricos). Elas reduzem o risco de bloqueio
agindo menos como spam: volume baixo, ritmo variável, horário de atividade,
pausas longas e parada ao primeiro aviso.

Nenhuma regra garante que a rede não bloqueie, e automatizar interações
continua contra os termos de uso das duas redes, em qualquer ritmo.

Os limites são por conta e por rede, contados no banco (ações que chegaram a
clicar: "feito" e "nao_confirmado") em janelas de 1 hora e do dia.
"""

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from automacao import banco
from automacao.terminal import dormir as dormir_interrompivel

TIPOS = ("curtir", "comentar", "compartilhar", "repostar", "favoritar", "seguir")
# Ações com nome próprio que contam como outro tipo nos tetos.
SINONIMOS = {"curtir_story": "curtir"}


class Bloqueio(Exception):
    """A rede bloqueou as ações (aviso de limite, captcha não resolvido...)."""


class LimiteDiario(Bloqueio):
    """O limite do dia da conta foi atingido (não é aviso da rede)."""


class PularAcao(Exception):
    """A ação não deve ser feita agora (fica para outro dia); nada é gravado."""


def tipo_da_acao(nome: str) -> str | None:
    """'compartilhar:fulano' -> 'compartilhar'; ações que não clicam (copiar_link) -> None."""
    base = nome.split(":", 1)[0]
    base = SINONIMOS.get(base, base)
    return base if base in TIPOS else None


@dataclass(frozen=True)
class Protecao:
    nome: str
    # Pausas aleatórias, em segundos (mínimo, máximo).
    pausa_leitura: tuple[float, float]           # depois de abrir um vídeo
    pausa_foto: tuple[float, float]              # olhando uma foto antes das ações
    pausa_item_carrossel: tuple[float, float]
    pausa_entre_acoes: tuple[float, float]
    pausa_entre_publicacoes: tuple[float, float]
    pausa_entre_perfis: tuple[float, float]      # entre perfis-alvo
    pausa_entre_contas: tuple[float, float]      # ao trocar de perfil do navegador
    # Tetos por conta e rede (tipos de TIPOS e "total").
    por_hora: dict[str, int] = field(default_factory=dict)
    por_dia: dict[str, int] = field(default_factory=dict)
    intervalo_min_comentarios_s: float = 0
    pausa_longa_a_cada: tuple[int, int] = (20, 30)       # nº de ações
    pausa_longa: tuple[float, float] = (600, 1200)       # segundos
    sessao_max_min: float | None = None                  # minutos de atividade antes de descansar
    descanso_sessao: tuple[float, float] = (0, 0)        # segundos
    janela_horario: tuple[int, int] | None = None        # horas locais [início, fim)
    max_publicacoes_por_alvo_dia: int | None = None
    comentario_por_alvo_dias: int | None = None          # no máximo 1 comentário por alvo nesse período
    comentario_repetido_dias: int | None = None          # o mesmo texto não volta nesse período
    pausa_apos_aviso_h: float = 24                       # conta em pausa depois de um aviso da rede
    pausa_geral_dois_avisos_h: float | None = None       # avisos em 2 contas no dia: todas param
    # Boost: sem tetos por hora/dia, sem limite diário, sem intervalo entre comentários
    # e sem pausas longas. O resto continua (vídeo até o fim, pausas curtas, avisos da rede).
    sem_tetos: bool = False

    @property
    def descricao(self) -> str:
        texto = {"anti_bloqueio": "anti-bloqueio (lento e cauteloso)",
                 "boost": "boost (sem tetos por hora e por dia)"}.get(self.nome, "normal")
        if not self.sem_tetos:
            texto += f"; tetos: {self.por_hora.get('total')} ações por hora e {self.por_dia.get('total')} por dia"
        return texto


# Até quanto vale esperar o teto por hora de um tipo de ação; acima disso, pula a ação.
ESPERA_MAXIMA_TETO_DO_TIPO_S = 180

PROTECAO_NORMAL = Protecao(
    nome="normal",
    pausa_leitura=(3.0, 8.0),
    pausa_foto=(5.0, 12.0),
    pausa_item_carrossel=(2.5, 6.0),
    pausa_entre_acoes=(4.0, 12.0),
    pausa_entre_publicacoes=(10.0, 30.0),      # o teto por hora é que segura o volume
    pausa_entre_perfis=(20.0, 60.0),
    pausa_entre_contas=(300.0, 600.0),
    por_hora={"curtir": 15, "comentar": 5, "compartilhar": 5, "repostar": 4, "favoritar": 10, "seguir": 8,
              "total": 30},
    por_dia={"curtir": 120, "comentar": 30, "compartilhar": 20, "repostar": 15, "favoritar": 60, "seguir": 40,
             "total": 200},
    intervalo_min_comentarios_s=180,
    pausa_longa_a_cada=(20, 30),
    pausa_longa=(600, 1200),
    pausa_apos_aviso_h=24,
)

PROTECAO_ANTI_BLOQUEIO = Protecao(
    nome="anti_bloqueio",
    pausa_leitura=(15.0, 40.0),
    pausa_foto=(15.0, 45.0),
    pausa_item_carrossel=(4.0, 9.0),
    pausa_entre_acoes=(30.0, 90.0),
    pausa_entre_publicacoes=(120.0, 360.0),
    pausa_entre_perfis=(180.0, 480.0),
    pausa_entre_contas=(2700.0, 5400.0),
    por_hora={"curtir": 8, "comentar": 2, "compartilhar": 2, "repostar": 1, "favoritar": 4, "seguir": 3,
              "total": 15},
    por_dia={"curtir": 50, "comentar": 8, "compartilhar": 5, "repostar": 5, "favoritar": 25, "seguir": 12,
             "total": 80},
    intervalo_min_comentarios_s=900,
    pausa_longa_a_cada=(10, 15),
    pausa_longa=(1800, 3600),
    sessao_max_min=25,
    descanso_sessao=(1800, 3600),
    janela_horario=(9, 22),
    max_publicacoes_por_alvo_dia=2,
    comentario_por_alvo_dias=7,
    comentario_repetido_dias=7,
    pausa_apos_aviso_h=48,
    pausa_geral_dois_avisos_h=24,
)


PROTECAO_BOOST = replace(PROTECAO_NORMAL, nome="boost", sem_tetos=True, intervalo_min_comentarios_s=0)

# Tetos que a pessoa pode mudar na interface (config "tetos", em JSON). Vazio:
# valem as sugestões, que são os tetos do modo normal.
CHAVES_TETO = (*TIPOS, "total")
PERIODOS_TETO = ("por_hora", "por_dia")
TETO_MAXIMO = 10_000


def tetos_sugeridos(protecao: Protecao = PROTECAO_NORMAL) -> dict[str, dict[str, int]]:
    """{"por_hora": {tipo: n}, "por_dia": {tipo: n}} de um modo (cópia, pode mudar à vontade)."""
    return {"por_hora": dict(protecao.por_hora), "por_dia": dict(protecao.por_dia)}


def validar_tetos(texto: str | None) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Confere os tetos enviados (JSON). Devolve (tetos completos, erros).

    O que não vier fica com a sugestão. Cada teto é um número inteiro de 1 a
    TETO_MAXIMO, e o da hora não pode passar do dia.
    """
    tetos, erros = tetos_sugeridos(), []
    if not (texto or "").strip():
        return tetos, erros
    try:
        enviados = json.loads(texto)
    except json.JSONDecodeError:
        return tetos, ["Os tetos por ação vieram num formato inválido; restaure as sugestões."]
    if not isinstance(enviados, dict):
        return tetos, ["Os tetos por ação vieram num formato inválido; restaure as sugestões."]
    for periodo in PERIODOS_TETO:
        valores = enviados.get(periodo) or {}
        if not isinstance(valores, dict):
            erros.append("Os tetos por ação vieram num formato inválido; restaure as sugestões.")
            continue
        for chave in CHAVES_TETO:
            if chave not in valores:
                continue
            valor = valores[chave]
            if isinstance(valor, str) and valor.strip().isascii() and valor.strip().isdecimal():
                valor = int(valor.strip())
            if isinstance(valor, bool) or not isinstance(valor, int) or not 1 <= valor <= TETO_MAXIMO:
                erros.append(f"O teto {periodo.replace('_', ' ')} de {chave} precisa ser um número de 1 a "
                             f"{TETO_MAXIMO}.")
                continue
            tetos[periodo][chave] = valor
    for chave in CHAVES_TETO:
        if tetos["por_hora"][chave] > tetos["por_dia"][chave]:
            erros.append(f"O teto por hora de {chave} ({tetos['por_hora'][chave]}) não pode ser maior que o "
                         f"do dia ({tetos['por_dia'][chave]}).")
    return tetos, erros


def ler_tetos(texto: str | None) -> dict[str, dict[str, int]]:
    """Os tetos salvos; se algo estiver estragado, valem as sugestões."""
    tetos, erros = validar_tetos(texto)
    return tetos_sugeridos() if erros else tetos


def protecao_da_config(salvas: dict[str, str], forcar_anti: bool = False, forcar_boost: bool = False) -> Protecao:
    """O modo de proteção escolhido, com os tetos da interface.

    Com anti-bloqueio e boost ligados, vale o anti-bloqueio (o mais cauteloso).
    No normal valem os tetos escolhidos; no anti-bloqueio, o menor entre eles e
    os do modo; no boost, nenhum.
    """
    tetos = ler_tetos(salvas.get("tetos"))
    if forcar_anti or salvas.get("anti_bloqueio") == "sim":
        base = PROTECAO_ANTI_BLOQUEIO
        tetos = {periodo: {chave: min(teto, tetos[periodo][chave]) for chave, teto in getattr(base, periodo).items()}
                 for periodo in PERIODOS_TETO}
    elif forcar_boost or salvas.get("boost") == "sim":
        return PROTECAO_BOOST
    else:
        base = PROTECAO_NORMAL
    if tetos == tetos_sugeridos(base):
        return base
    return replace(base, **tetos)


# --------------------------------------------------------- estado da execução

@dataclass
class _Estado:
    """Ritmo de uma conta numa rede, nesta execução."""
    acoes_desde_pausa: int = 0
    proxima_pausa: int = 0
    inicio_sessao: float = field(default_factory=time.monotonic)
    avisados: set[str] = field(default_factory=set)


_estados: dict[tuple[str, str | None], _Estado] = {}


def _estado(rede: str, conta: str | None, protecao: Protecao) -> _Estado:
    estado = _estados.get((rede, conta))
    if estado is None:
        estado = _estados[(rede, conta)] = _Estado(proxima_pausa=random.randint(*protecao.pausa_longa_a_cada))
    return estado


def reiniciar_estado() -> None:
    """Esquece o ritmo acumulado (usado nos testes)."""
    _estados.clear()


def _minutos(segundos: float) -> str:
    return f"{segundos / 60:.0f} min" if segundos >= 90 else f"{segundos:.0f} s"


def _esperar(segundos: float, motivo: str, dormir: Callable[[float], None]) -> None:
    if segundos <= 0:
        return
    print(f"    {'ritmo':<13} {motivo}: esperando {_minutos(segundos)}", flush=True)
    dormir(segundos)


def esperar_horario(protecao: Protecao, dormir: Callable[[float], None] = dormir_interrompivel,
                    relogio: Callable[[], datetime] = datetime.now) -> None:
    """Fora do horário de atividade, espera até o próximo início."""
    if not protecao.janela_horario:
        return
    inicio, fim = protecao.janela_horario
    agora = relogio()
    if inicio <= agora.hour < fim:
        return
    proximo = agora.replace(hour=inicio, minute=0, second=0, microsecond=0)
    if agora.hour >= fim:
        proximo += timedelta(days=1)
    # Começa num minuto aleatório da primeira hora, para não ter horário fixo.
    proximo += timedelta(minutes=random.randint(0, 45))
    _esperar((proximo - agora).total_seconds(),
             f"fora do horário de atividade ({inicio}h às {fim}h)", dormir)


def controlar_ritmo(conn, protecao: Protecao, rede: str, conta: str | None, nome_acao: str,
                    dormir: Callable[[float], None] = dormir_interrompivel,
                    relogio: Callable[[], datetime] = datetime.now, pode_esperar: bool = True) -> None:
    """Antes de uma ação que clica: aplica horário, tetos, intervalos e pausas.

    Espera o que for preciso (teto por hora, intervalo entre comentários,
    pausa longa, descanso de sessão). Levanta PularAcao se o teto do dia
    daquele tipo de ação foi atingido (a ação fica para outro dia) e
    LimiteDiario se o total do dia da conta foi atingido. Com
    `pode_esperar=False` (dentro dos stories, que andam sozinhos), em vez de
    esperar levanta PularAcao.
    """
    if not pode_esperar:
        def dormir(segundos: float, _original=dormir) -> None:  # noqa: ARG001
            raise PularAcao("teto por hora ou pausa de ritmo: só assistindo, sem curtir")
    tipo = tipo_da_acao(nome_acao)
    if tipo is None or protecao.sem_tetos:
        return
    esperar_horario(protecao, dormir, relogio)

    agora = relogio().astimezone()
    feitas = [(t, datetime.fromisoformat(momento)) for t, momento in
              banco.acoes_recentes(conn, rede, conta, (agora - timedelta(hours=24)).isoformat(timespec="seconds"))]
    tipos = [tipo_da_acao(t) for t, _ in feitas]
    hoje = [(tp, m) for tp, (_, m) in zip(tipos, feitas) if tp and m.astimezone().date() == agora.date()]
    quem = f" de {conta}" if conta else ""
    if len(hoje) >= protecao.por_dia["total"]:
        raise LimiteDiario(f"teto do dia{quem} atingido ({protecao.por_dia['total']} ações)")
    if sum(1 for tp, _ in hoje if tp == tipo) >= protecao.por_dia[tipo]:
        raise PularAcao(f"teto do dia para {tipo}{quem} atingido ({protecao.por_dia[tipo]}); fica para amanhã")

    # Teto por hora: a ação mais antiga da janela precisa sair dela.
    hora = [(tp, m) for tp, (_, m) in zip(tipos, feitas) if tp and m > agora - timedelta(hours=1)]
    do_tipo = sorted(m for tp, m in hora if tp == tipo)
    todas = sorted(m for _, m in hora)
    espera_tipo = espera_total = 0.0
    if len(do_tipo) >= protecao.por_hora[tipo]:
        espera_tipo = (do_tipo[len(do_tipo) - protecao.por_hora[tipo]] + timedelta(hours=1) - agora).total_seconds()
    if len(todas) >= protecao.por_hora["total"]:
        espera_total = (todas[len(todas) - protecao.por_hora["total"]] + timedelta(hours=1) - agora).total_seconds()
    # Só este tipo está no teto e falta muito: pula a ação (não grava nada; ela é
    # feita numa próxima volta) em vez de parar a rede inteira, com o vídeo em
    # loop na tela, até a hora liberar. O teto total, esse sim, faz esperar.
    if espera_tipo > ESPERA_MAXIMA_TETO_DO_TIPO_S and espera_tipo > espera_total:
        raise PularAcao(f"teto por hora de {tipo}{quem} atingido ({protecao.por_hora[tipo]}); "
                        "fica para a próxima volta")
    espera = max(espera_tipo, espera_total)
    if espera > 0:
        _esperar(espera + random.uniform(30, 120), f"teto por hora atingido ({tipo})", dormir)

    if tipo == "comentar" and protecao.intervalo_min_comentarios_s:
        comentarios = [m for tp, m in zip(tipos, (m for _, m in feitas)) if tp == "comentar"]
        if comentarios:
            falta = protecao.intervalo_min_comentarios_s - (relogio().astimezone() - max(comentarios)).total_seconds()
            if falta > 0:
                _esperar(falta + random.uniform(10, 60), "intervalo mínimo entre comentários", dormir)

    estado = _estado(rede, conta, protecao)
    if estado.acoes_desde_pausa >= estado.proxima_pausa:
        _esperar(random.uniform(*protecao.pausa_longa), "pausa longa", dormir)
        estado.acoes_desde_pausa = 0
        estado.proxima_pausa = random.randint(*protecao.pausa_longa_a_cada)
        estado.inicio_sessao = time.monotonic()
    if protecao.sessao_max_min and (time.monotonic() - estado.inicio_sessao) / 60 >= protecao.sessao_max_min:
        _esperar(random.uniform(*protecao.descanso_sessao), "descanso entre sessões", dormir)
        estado.inicio_sessao = time.monotonic()
    # As esperas acima podem ter passado do fim do horário de atividade.
    esperar_horario(protecao, dormir, relogio)
    estado.acoes_desde_pausa += 1


def registrar_aviso(conn, protecao: Protecao, rede: str, conta: str | None, motivo: str) -> str:
    """Põe a conta em pausa depois de um aviso da rede. Devolve a mensagem para o log.

    No modo anti-bloqueio, avisos em duas contas no mesmo dia pausam todas.
    """
    conta = conta or "?"
    fim = banco.registrar_pausa(conn, rede, conta, motivo, protecao.pausa_apos_aviso_h)
    mensagem = f"{conta} em pausa no {rede} até {fim[:16].replace('T', ' ')} ({protecao.pausa_apos_aviso_h:g} h)"
    if protecao.pausa_geral_dois_avisos_h and len(banco.contas_com_aviso_hoje(conn)) >= 2:
        fim_geral = banco.registrar_pausa(conn, "*", "*", "avisos em duas contas no mesmo dia",
                                          protecao.pausa_geral_dois_avisos_h)
        mensagem += f"; avisos em duas contas hoje: todas em pausa até {fim_geral[:16].replace('T', ' ')}"
    return mensagem
