"""Ações em cada rede social e o motor compartilhado por elas.

- comum.py: configuração, ritmo de pessoa, vídeos e carrosséis, e o
  processamento de cada publicação (com o banco evitando repetir ações);
- instagram.py e tiktok.py: seletores e ações de cada rede.

Cada módulo de rede expõe processar_perfil(conn, pg, rodada_id, cfg, perfil,
resumo); o interagir.py escolhe o módulo pelo nome da rede (MODULOS).
"""
