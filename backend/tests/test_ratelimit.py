"""Tests del limitador de ventana deslizante, con un reloj falso (sin dormir)."""

from app.ratelimit import LimitadorVentana


class RelojFalso:
    """Reloj que avanza solo cuando el test lo dice."""

    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora


def test_deja_pasar_hasta_el_maximo_y_corta_el_siguiente() -> None:
    limitador = LimitadorVentana(maximo=3, ventana_segundos=60, reloj=RelojFalso())

    resultados = [limitador.intentar("ip") for _ in range(4)]

    assert resultados[:3] == [None, None, None]
    assert resultados[3] is not None


def test_informa_cuanto_falta_para_reintentar() -> None:
    reloj = RelojFalso()
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    limitador.intentar("ip")

    reloj.ahora += 20

    assert limitador.intentar("ip") == 40  # el primer pedido caduca a los 60 s


def test_vuelve_a_dejar_pasar_cuando_el_pedido_viejo_sale_de_la_ventana() -> None:
    reloj = RelojFalso()
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    limitador.intentar("ip")
    assert limitador.intentar("ip") is not None

    reloj.ahora += 60

    assert limitador.intentar("ip") is None


def test_cada_clave_tiene_su_propio_cupo() -> None:
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=RelojFalso())
    limitador.intentar("ip-a")

    assert limitador.intentar("ip-a") is not None
    assert limitador.intentar("ip-b") is None


def test_un_pedido_rechazado_no_consume_cupo() -> None:
    # Insistir contra el límite no alarga el bloqueo: solo cuentan los pedidos aceptados.
    reloj = RelojFalso()
    limitador = LimitadorVentana(maximo=1, ventana_segundos=60, reloj=reloj)
    limitador.intentar("ip")

    for _ in range(10):
        reloj.ahora += 1
        limitador.intentar("ip")

    reloj.ahora += 50  # 60 s desde el único pedido aceptado

    assert limitador.intentar("ip") is None
