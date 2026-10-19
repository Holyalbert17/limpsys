from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from app.core.util import decimal


def gs(valor) -> Decimal:
    return decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def horas_q(valor) -> Decimal:
    return decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class DescuentoPedido:
    origen: str
    monto: Decimal
    acumulable: bool
    meta: dict = field(default_factory=dict)


@dataclass
class ResultadoPrecio:
    horas_est: Decimal
    subtotal: Decimal
    descuentos: Decimal
    detalle_descuentos: list[dict]
    multiplicador: Decimal
    recargos: Decimal
    traslado: Decimal
    total: Decimal
    iva: Decimal
    anticipo: Decimal
    costo_mano_obra: Decimal
    cargas: Decimal
    insumos: Decimal
    costo_total: Decimal
    margen: Decimal
    margen_pct: Decimal
    recortado: bool


def _aplicar_lista(subtotal: Decimal, pedidos: list[DescuentoPedido], tope: Decimal) -> tuple[list[dict], Decimal, bool]:
    positivos = [pedido for pedido in pedidos if pedido.monto > 0]
    elegidos: list[DescuentoPedido] = []
    for pedido in positivos:
        if not elegidos:
            elegidos.append(pedido)
            continue
        if pedido.acumulable and all(item.acumulable for item in elegidos):
            elegidos.append(pedido)
    suma = sum((item.monto for item in elegidos), Decimal(0))
    mejor = max(positivos, key=lambda item: item.monto, default=None)
    if mejor is not None and mejor.monto > suma:
        elegidos = [mejor]
        suma = mejor.monto

    recortado = False
    if suma > tope:
        recortado = True
        exceso = suma - tope
        for item in reversed(elegidos):
            corte = min(item.monto, exceso)
            item.monto -= corte
            exceso -= corte
            if exceso <= 0:
                break
        suma = sum((item.monto for item in elegidos), Decimal(0))

    detalle = [
        {
            "origen": item.origen,
            "monto": gs(item.monto),
            "acumulable": item.acumulable,
            **item.meta,
        }
        for item in elegidos
        if item.monto > 0
    ]
    return detalle, gs(suma), recortado


def calcular_precio(
    *,
    horas_base: Decimal,
    factor: Decimal,
    tarifa_hora: Decimal,
    elementos: list[tuple[Decimal, int, int]],
    traslado: Decimal,
    multiplicador: Decimal,
    descuentos: list[DescuentoPedido],
    costo_hora: Decimal,
    cargas_pct: Decimal,
    insumos_hora: Decimal,
    margen_minimo: Decimal,
    tope_descuento: Decimal,
    minimo_horas: Decimal,
    iva_divisor: Decimal,
    anticipo_pct: Decimal,
) -> ResultadoPrecio:
    minutos = sum(cantidad * minutos_item for _, minutos_item, cantidad in elementos)
    horas_est = horas_q(decimal(horas_base) * decimal(factor) + Decimal(minutos) / Decimal(60))
    if horas_est < decimal(minimo_horas):
        horas_est = horas_q(minimo_horas)

    adicionales = sum((decimal(adicional) * cantidad for adicional, _, cantidad in elementos), Decimal(0))
    subtotal = gs(decimal(tarifa_hora) * horas_est + adicionales)
    tope = gs(subtotal * decimal(tope_descuento))
    detalle, descuento, recortado = _aplicar_lista(subtotal, descuentos, tope)

    costo_mano = gs(decimal(costo_hora) * horas_est)
    cargas = gs(costo_mano * decimal(cargas_pct))
    insumos = gs(decimal(insumos_hora) * horas_est)
    costo_total = gs(costo_mano + cargas + decimal(traslado) + insumos)
    multi = decimal(multiplicador)
    if multi <= 0:
        multi = Decimal(1)

    def total_con(desc: Decimal) -> Decimal:
        base = subtotal - desc
        if base < 0:
            base = Decimal(0)
        return gs(base * multi + decimal(traslado))

    if margen_minimo < 1 and costo_total > 0:
        minimo_total = gs(costo_total / (Decimal(1) - decimal(margen_minimo)))
        # (subtotal - desc) * multi + traslado >= minimo_total
        if multi > 0:
            max_base = (decimal(minimo_total) - decimal(traslado)) / multi
            max_desc = gs(subtotal - max_base)
            if max_desc < 0:
                max_desc = Decimal(0)
            if descuento > max_desc:
                recortado = True
                exceso = descuento - max_desc
                for item in reversed(detalle):
                    monto = decimal(item["monto"])
                    corte = min(monto, exceso)
                    item["monto"] = gs(monto - corte)
                    item["recortado"] = True
                    exceso -= corte
                    if exceso <= 0:
                        break
                detalle = [item for item in detalle if decimal(item["monto"]) > 0]
                descuento = gs(sum((decimal(item["monto"]) for item in detalle), Decimal(0)))

    total = total_con(descuento)
    recargos = gs(total - decimal(traslado) - (subtotal - descuento))
    if recargos < 0:
        recargos = Decimal(0)
    iva = gs(total / decimal(iva_divisor)) if total else Decimal(0)
    anticipo = gs(total * decimal(anticipo_pct))
    margen = gs(total - costo_total)
    margen_pct = (margen / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if total else Decimal(0)

    return ResultadoPrecio(
        horas_est=horas_est,
        subtotal=subtotal,
        descuentos=descuento,
        detalle_descuentos=detalle,
        multiplicador=multi,
        recargos=recargos,
        traslado=gs(traslado),
        total=total,
        iva=iva,
        anticipo=anticipo,
        costo_mano_obra=costo_mano,
        cargas=cargas,
        insumos=insumos,
        costo_total=costo_total,
        margen=margen,
        margen_pct=margen_pct,
        recortado=recortado,
    )
