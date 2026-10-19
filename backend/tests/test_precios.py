from decimal import Decimal

from app.domain.geo import punto_en_poligono
from app.domain.precios import DescuentoPedido, calcular_precio


def _base(**cambios):
    datos = dict(
        horas_base=Decimal("4"),
        factor=Decimal("1"),
        tarifa_hora=Decimal("45000"),
        elementos=[],
        traslado=Decimal("15000"),
        multiplicador=Decimal("1"),
        descuentos=[],
        costo_hora=Decimal("20000"),
        cargas_pct=Decimal("0.10"),
        insumos_hora=Decimal("2000"),
        margen_minimo=Decimal("0.35"),
        tope_descuento=Decimal("0.30"),
        minimo_horas=Decimal("2"),
        iva_divisor=Decimal("11"),
        anticipo_pct=Decimal("0.30"),
    )
    datos.update(cambios)
    return calcular_precio(**datos)


def test_formula_sin_extras():
    resultado = _base()
    assert resultado.horas_est == Decimal("4.00")
    assert resultado.subtotal == Decimal("180000")
    assert resultado.total == Decimal("195000")
    assert resultado.iva == Decimal("17727")
    assert resultado.anticipo == Decimal("58500")


def test_elemento_suma_tiempo_y_adicional():
    resultado = _base(elementos=[(Decimal("25000"), 30, 1)])
    assert resultado.horas_est == Decimal("4.50")
    assert resultado.subtotal == Decimal("227500")


def test_minimo_de_dos_horas():
    resultado = _base(horas_base=Decimal("1"), factor=Decimal("1"))
    assert resultado.horas_est == Decimal("2.00")


def test_recorta_descuento_para_cuidar_margen():
    resultado = _base(descuentos=[DescuentoPedido("cupon", Decimal("90000"), True)])
    assert resultado.recortado is True
    assert resultado.margen_pct >= Decimal("0.35")
    assert resultado.descuentos < Decimal("90000")


def test_punto_dentro_y_fuera_de_asuncion():
    centro = [[[-57.67, -25.30], [-57.60, -25.30], [-57.60, -25.26], [-57.67, -25.26], [-57.67, -25.30]]]
    assert punto_en_poligono(-25.28, -57.63, centro) is True
    assert punto_en_poligono(-25.50, -57.20, centro) is False
