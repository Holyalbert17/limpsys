from fpdf import FPDF


def comprobante(factura: dict) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=16)
    pdf.cell(0, 10, "Limsys", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    lineas = [
        "Comprobante interno",
        f"Numero: {factura['numero']}",
        f"Tipo: {factura['tipo']}",
        f"Estado: {factura['estado']}",
        f"Receptor: {factura.get('receptor_nombre') or 'Consumidor final'}",
        f"Documento: {factura.get('receptor_doc') or ''}",
        f"Total Gs: {int(factura['total'])}",
        f"IVA incluido Gs: {int(factura['iva'])}",
        "El CDC de SIFEN se completa cuando el modulo externo confirma la emision.",
    ]
    for linea in lineas:
        pdf.cell(0, 8, linea.encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
