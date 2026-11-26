
import re
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser

# BALNEABILIDADE PE
# Etapa inicial: localizar boletins oficiais da CPRH

BASE = "https://www2.cprh.pe.gov.br"

PAGINAS = [
    BASE + "/monitoramento-ambiental/balneabilidade/",
    BASE + "/monitoramento-ambiental/balneabilidade/informativos-semanais-2026/"
]


class LeitorLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            link = dict(attrs).get("href")
            if link:
                self.links.append(link)


def consultar_boletins():
    boletins = set()

    for pagina in PAGINAS:
        print(f"Consultando: {pagina}")

        requisicao = Request(
            pagina,
            headers={"User-Agent": "BalneabilidadePE/1.0"}
        )

        with urlopen(requisicao, timeout=60) as resposta:
            html = resposta.read().decode(
                "utf-8", errors="replace"
            )

        leitor = LeitorLinks()
        leitor.feed(html)

        for link in leitor.links:
            url = urljoin(pagina, link)
            partes = urlparse(url)
            caminho = partes.path.lower()

            if partes.hostname not in (
                "www2.cprh.pe.gov.br",
                "www.cprh.pe.gov.br",
                "cprh.pe.gov.br"
            ):
                continue

            if (
                caminho.endswith(".pdf")
                and "balneabilidade" in caminho
                and "informativo" in caminho
            ):
                boletins.add(url)

    if not boletins:
        raise RuntimeError(
            "Nenhum boletim localizado. "
            "Verifique a pagina oficial da CPRH."
        )

    print(f"\nBoletins encontrados: {len(boletins)}")

    for boletim in sorted(boletins):
        print(boletim)

    print("\nConsulta finalizada.")
    print("Nenhum dado foi alterado no Supabase.")


if __name__ == "__main__":
    consultar_boletins()


# ETAPA 2 - Teste de leitura do boletim PDF

import subprocess
import tempfile
from pathlib import Path

def testar_leitura_pdf():
    url = (
        BASE + "/wp-content/uploads/2026/11/"
        "informativo-balneabilidade-47_2026-enterolert-1.pdf"
    )

    print("\n=== TESTE DE LEITURA DO PDF ===")

    with tempfile.TemporaryDirectory() as pasta:
        arquivo = Path(pasta) / "boletim.pdf"

        requisicao = Request(
            url, headers={"User-Agent": "Mozilla/5.0"}
        )

        with urlopen(requisicao, timeout=60) as resposta:
            arquivo.write_bytes(resposta.read())

        subprocess.run(
            ["pdftotext", "-layout", str(arquivo), "-"],
            check=True
        )

    print("\nTeste finalizado.")
    print("Nenhum dado foi alterado no Supabase.")

if __name__ == "__main__":
    testar_leitura_pdf()
