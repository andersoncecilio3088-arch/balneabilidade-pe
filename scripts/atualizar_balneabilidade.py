import os
import re
import json
import time
import subprocess
import tempfile

from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from urllib.error import URLError, HTTPError
from zoneinfo import ZoneInfo


# ==========================================
# CONFIGURACOES
# ==========================================

BASE = "https://www2.cprh.pe.gov.br"

ANO = datetime.now(
    ZoneInfo("America/Recife")
).year

PAGINAS = [
    BASE + "/monitoramento-ambiental/balneabilidade/",
    BASE + f"/monitoramento-ambiental/balneabilidade/informativos-semanais-{ANO}/"
]

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", ""
).rstrip("/")

SUPABASE_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY", ""
)

TENTATIVAS = 5


# ==========================================
# LEITURA DOS LINKS
# ==========================================

class LeitorLinks(HTMLParser):

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            link = dict(attrs).get("href")

            if link:
                self.links.append(link)


# ==========================================
# DOWNLOAD COM RECONEXAO
# ==========================================

def baixar(url):

    for tentativa in range(1, TENTATIVAS + 1):

        try:
            requisicao = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urlopen(
                requisicao,
                timeout=30
            ) as resposta:

                return resposta.read()

        except HTTPError as erro:

            if erro.code not in (
                408, 429, 500, 502, 503, 504
            ):
                raise

            print(
                f"Tentativa {tentativa}: {erro}"
            )

            if tentativa == TENTATIVAS:
                raise

        except (URLError, OSError) as erro:

            print(
                f"Tentativa {tentativa}: {erro}"
            )

            if tentativa == TENTATIVAS:
                raise

        espera = tentativa * 5

        print(
            f"Nova tentativa em {espera} segundos."
        )

        time.sleep(espera)


# ==========================================
# LOCALIZAR BOLETIM MAIS RECENTE
# ==========================================

def localizar_boletim():

    boletins = set()

    for pagina in PAGINAS:

        print("Consultando:", pagina)

        leitor = LeitorLinks()

        leitor.feed(
            baixar(pagina).decode(
                "utf-8",
                errors="replace"
            )
        )

        for link in leitor.links:

            url = urljoin(pagina, link)
            caminho = urlparse(url).path

            if urlparse(url).hostname != urlparse(BASE).hostname:
                continue

            if not caminho.lower().endswith(".pdf"):
                continue

            dados = re.search(
                r"balneabilidade[-_\s]+(\d+)[-_\s]+(\d{4})",
                caminho,
                re.IGNORECASE
            )

            if dados:

                numero = int(dados.group(1))
                ano = int(dados.group(2))

                boletins.add(
                    (ano, numero, url)
                )

    if not boletins:
        raise RuntimeError(
            "Nenhum boletim localizado."
        )

    ano, numero = max(
        (a, n) for a, n, u in boletins
    )

    urls = sorted(
        u for a, n, u in boletins
        if (a, n) == (ano, numero)
    )

    if len(urls) != 1:
        raise RuntimeError(
            "Mais de um PDF para o boletim mais recente."
        )

    url = urls[0]

    print("Boletim mais recente:", numero, "/", ano)
    print("PDF:", url)

    return ano, numero, url


# ==========================================
# LEITURA DO PDF
# ==========================================

def ler_pdf(url):

    with tempfile.TemporaryDirectory() as pasta:

        arquivo = Path(pasta) / "boletim.pdf"

        conteudo = baixar(url)

        if not conteudo.startswith(b"%PDF-"):
            raise RuntimeError(
                "O arquivo recebido nao e um PDF valido."
            )

        arquivo.write_bytes(conteudo)

        resultado = subprocess.run(
            [
                "pdftotext",
                "-layout",
                str(arquivo),
                "-"
            ],
            capture_output=True,
            text=True,
            check=True
        )

        if not resultado.stdout.strip():
            raise RuntimeError(
                "PDF sem texto extraivel."
            )

        return resultado.stdout


# ==========================================
# CONVERSAO DAS DATAS
# ==========================================

def converter_data(data):

    return datetime.strptime(
        data,
        "%d/%m/%Y"
    ).strftime("%Y-%m-%d")


# ==========================================
# EXTRACAO DOS DADOS
# ==========================================

def extrair_dados(texto):

    periodo = re.search(
        r"PER[ÍI]ODO:\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})",
        texto,
        re.IGNORECASE
    )

    coleta = re.search(
        r"DATA DA COLETA:\s*(\d{2}/\d{2}/\d{4})",
        texto,
        re.IGNORECASE
    )

    publicacao = re.search(
        r"^\s*DATA:\s*(\d{2}/\d{2}/\d{4})",
        texto,
        re.MULTILINE
    )

    if not periodo or not coleta or not publicacao:

        raise RuntimeError(
            "Nao foi possivel identificar todas as datas."
        )

    pontos = re.findall(
        r"^[ \t]*([A-Z]{3}-\d{2,3})[ \t]+.*?[ \t]+(Imprópria|Própria)[ \t]*$",
        texto,
        re.MULTILINE
    )

    resultados = []

    for codigo, classificacao in pontos:

        resultados.append({
            "codigo": codigo,
            "classificacao": (
                "IMPROPRIA"
                if classificacao == "Imprópria"
                else "PROPRIA"
            )
        })

    dados = {
        "data_publicacao": converter_data(
            publicacao.group(1)
        ),
        "inicio_validade": converter_data(
            periodo.group(1)
        ),
        "fim_validade": converter_data(
            periodo.group(2)
        ),
        "data_coleta": converter_data(
            coleta.group(1)
        ),
        "resultados": resultados
    }

    if not (
        dados["data_coleta"]
        <= dados["data_publicacao"]
        <= dados["inicio_validade"]
        <= dados["fim_validade"]
    ):
        raise RuntimeError(
            "Datas do boletim inconsistentes."
        )

    return dados


# ==========================================
# COMUNICACAO COM SUPABASE
# ==========================================

def supabase(
    tabela,
    consulta="",
    metodo="GET",
    dados=None,
    prefer=None
):

    if not SUPABASE_URL or not SUPABASE_KEY:

        raise RuntimeError(
            "Credenciais do Supabase ausentes."
        )

    url = f"{SUPABASE_URL}/rest/v1/{tabela}"

    if consulta:
        url += "?" + consulta

    headers = {
        "apikey": SUPABASE_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    if SUPABASE_KEY.startswith("eyJ"):

        headers["Authorization"] = (
            f"Bearer {SUPABASE_KEY}"
        )

    if prefer:
        headers["Prefer"] = prefer

    corpo = None

    if dados is not None:

        corpo = json.dumps(
            dados,
            ensure_ascii=False
        ).encode("utf-8")

    requisicao = Request(
        url,
        data=corpo,
        headers=headers,
        method=metodo
    )

    try:

        with urlopen(
            requisicao,
            timeout=60
        ) as resposta:

            conteudo = resposta.read()

            if conteudo:
                return json.loads(conteudo)

            return None

    except HTTPError as erro:

        detalhe = erro.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Erro Supabase {erro.code}: {detalhe}"
        ) from erro


# ==========================================
# CONSULTAR SUPABASE
# ==========================================

def consultar_supabase(tabela, consulta):

    return supabase(
        tabela,
        consulta=consulta
    )


# ==========================================
# CADASTRAR BOLETIM
# ==========================================

def cadastrar_boletim(ano, numero, url, dados):

    registro = {
        "numero": numero,
        "ano": ano,
        "data_publicacao": dados["data_publicacao"],
        "inicio_validade": dados["inicio_validade"],
        "fim_validade": dados["fim_validade"],
        "url_pdf": url,
        "publicado": False
    }

    supabase(
        "boletins",
        consulta="on_conflict=numero,ano",
        metodo="POST",
        dados=registro,
        prefer=(
            "resolution=ignore-duplicates,"
            "return=minimal"
        )
    )

    boletins = consultar_supabase(
        "boletins",
        f"select=id,publicado&numero=eq.{numero}&ano=eq.{ano}"
    )

    if len(boletins) != 1:

        raise RuntimeError(
            "Falha ao localizar o boletim cadastrado."
        )

    return boletins[0]


# ==========================================
# CADASTRAR RESULTADOS
# ==========================================

def cadastrar_resultados(
    boletim_id,
    resultados,
    mapa,
    data_coleta
):

    registros = []

    for resultado in resultados:

        registros.append({
            "boletim_id": boletim_id,
            "ponto_id": mapa[resultado["codigo"]],
            "classificacao": resultado["classificacao"],
            "data_coleta": data_coleta
        })

    supabase(
        "resultados",
        consulta="on_conflict=boletim_id,ponto_id",
        metodo="POST",
        dados=registros,
        prefer=(
            "resolution=ignore-duplicates,"
            "return=minimal"
        )
    )

    print(
        "Resultados enviados:",
        len(registros)
    )


# ==========================================
# VALIDAR RESULTADOS GRAVADOS
# ==========================================

def validar_resultados(
    boletim_id,
    resultados,
    mapa,
    data_coleta
):

    gravados = consultar_supabase(
        "resultados",
        f"select=ponto_id,classificacao,data_coleta&boletim_id=eq.{boletim_id}"
    )

    esperados = {
        (
            mapa[r["codigo"]],
            r["classificacao"],
            data_coleta
        )
        for r in resultados
    }

    encontrados = {
        (
            r["ponto_id"],
            r["classificacao"],
            r["data_coleta"]
        )
        for r in gravados
    }

    if (
        encontrados != esperados
        or len(gravados) != len(resultados)
    ):

        raise RuntimeError(
            "Divergencia nos resultados gravados. "
            "Publicacao interrompida."
        )

    print(
        "Resultados confirmados:",
        len(gravados)
    )


# ==========================================
# PUBLICAR BOLETIM
# ==========================================

def publicar_boletim(boletim_id):

    supabase(
        "boletins",
        consulta=f"id=eq.{boletim_id}",
        metodo="PATCH",
        dados={
            "publicado": True
        },
        prefer="return=minimal"
    )

    confirmacao = consultar_supabase(
        "boletins",
        f"select=publicado&id=eq.{boletim_id}"
    )

    if (
        len(confirmacao) != 1
        or not confirmacao[0]["publicado"]
    ):
        raise RuntimeError(
            "Nao foi possivel confirmar a publicacao."
        )

    print("Boletim publicado com sucesso!")


# ==========================================
# EXECUCAO PRINCIPAL
# ==========================================

def main():

    print("=== BALNEABILIDADE PE ===")

    ano, numero, url = localizar_boletim()

    texto = ler_pdf(url)

    dados = extrair_dados(texto)

    resultados = dados["resultados"]

    print("\n=== DADOS DO BOLETIM ===")

    print("Numero:", numero)
    print("Ano:", ano)
    print("Publicacao:", dados["data_publicacao"])
    print("Inicio:", dados["inicio_validade"])
    print("Fim:", dados["fim_validade"])
    print("Coleta:", dados["data_coleta"])
    print("Total de pontos:", len(resultados))

    pontos = consultar_supabase(
        "pontos_coleta",
        "select=id,codigo&ativo=eq.true"
    )

    mapa = {
        ponto["codigo"]: ponto["id"]
        for ponto in pontos
    }

    codigos = [
        r["codigo"]
        for r in resultados
    ]

    if len(codigos) != len(set(codigos)):

        raise RuntimeError(
            "Codigos duplicados no PDF."
        )

    if not codigos or set(codigos) != set(mapa):

        print(
            "Pontos ausentes no PDF:",
            sorted(set(mapa) - set(codigos))
        )

        print(
            "Pontos nao cadastrados:",
            sorted(set(codigos) - set(mapa))
        )

        raise RuntimeError(
            "Divergencia entre PDF e pontos cadastrados."
        )

    print("\n=== VALIDACAO DOS PONTOS ===")

    for resultado in resultados:

        codigo = resultado["codigo"]

        print(
            codigo,
            "| ID:", mapa[codigo],
            "|", resultado["classificacao"]
        )

    boletins = consultar_supabase(
        "boletins",
        f"select=*&numero=eq.{numero}&ano=eq.{ano}"
    )

    print("\n=== SITUACAO DO BANCO ===")

    if boletins:

        boletim = boletins[0]

        boletim_id = boletim["id"]

        print("Boletim ja cadastrado.")
        print("ID:", boletim_id)

        for campo in (
            "data_publicacao",
            "inicio_validade",
            "fim_validade"
        ):

            if boletim[campo] != dados[campo]:

                raise RuntimeError(
                    f"Divergencia no campo {campo}."
                )

        if boletim["publicado"]:

            validar_resultados(
                boletim_id,
                resultados,
                mapa,
                dados["data_coleta"]
            )

            print("Boletim ja publicado.")
            print("Nenhum registro duplicado.")
            print("Nenhum dado foi alterado.")
            print("\nProcesso concluido com sucesso.")

            return

        print("Retomando cadastro incompleto.")

    else:

        print("Novo boletim identificado.")

        boletim = cadastrar_boletim(
            ano,
            numero,
            url,
            dados
        )

        boletim_id = boletim["id"]

        print("ID do boletim:", boletim_id)

    print("\n=== GRAVACAO DOS RESULTADOS ===")

    cadastrar_resultados(
        boletim_id,
        resultados,
        mapa,
        dados["data_coleta"]
    )

    validar_resultados(
        boletim_id,
        resultados,
        mapa,
        dados["data_coleta"]
    )

    print("\n=== PUBLICACAO ===")

    publicar_boletim(boletim_id)

    proprios = sum(
        r["classificacao"] == "PROPRIA"
        for r in resultados
    )

    improprios = sum(
        r["classificacao"] == "IMPROPRIA"
        for r in resultados
    )

    print("\n=== RESUMO FINAL ===")

    print("Boletim:", numero, "/", ano)
    print("Total de pontos:", len(resultados))
    print("Proprios:", proprios)
    print("Improprios:", improprios)

    print("\nProcesso concluido com sucesso.")


if __name__ == "__main__":
    main()
