"""Testes da data de publicação embutida no código do post (Instagram) e no id do vídeo (TikTok)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from automacao.redes import instagram, tiktok


# --------------------------------------------------------------- Instagram

def test_instagram_codigo_conhecido_de_outubro():
    data = instagram.data_publicacao("DemwcD8lKAk")
    assert data.tzinfo == timezone.utc
    assert data.date() == date(2026, 10, 17)
    assert abs(data - datetime(2026, 10, 17, 18, 30, 55, tzinfo=timezone.utc)) < timedelta(minutes=1)


def test_instagram_codigo_conhecido_de_setembro():
    data = instagram.data_publicacao("DdkWKr6FKFb")
    assert data.tzinfo == timezone.utc
    assert data.date() == date(2026, 9, 21)


def test_instagram_codigo_mais_novo_tem_data_maior():
    assert instagram.data_publicacao("DemwcD8lKAk") > instagram.data_publicacao("DdkWKr6FKFb")


@pytest.mark.parametrize("codigo", [
    "",
    "DemwcD8lKA",      # 10 caracteres
    "DemwcD8lKAkk",    # 12 caracteres
    "DemwcD8lKA!",
    "DemwcD8lKA.",
    "DemwcD8lKA ",
    "DemwcD8lKAç",
])
def test_instagram_codigo_invalido_devolve_none(codigo):
    assert instagram.data_publicacao(codigo) is None


# ------------------------------------------------------------------ TikTok

def test_tiktok_id_conhecido_de_outubro():
    data = tiktok.data_publicacao("7699507358707305746")
    assert data == datetime(2026, 10, 22, 15, 0, 54, tzinfo=timezone.utc)


def test_tiktok_id_conhecido_de_novembro():
    data = tiktok.data_publicacao("7573525231633976588")
    assert data.tzinfo == timezone.utc
    assert data.date() == date(2025, 11, 17)
    assert (data.hour, data.minute) == (3, 5)


@pytest.mark.parametrize("codigo", [
    "",
    "abc",
    "7699507358707305746a",
    "12.5",
    "9" * 40,           # grande demais para virar data
])
def test_tiktok_id_invalido_devolve_none(codigo):
    assert tiktok.data_publicacao(codigo) is None
