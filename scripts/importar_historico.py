import os
import re

from urllib.parse import urljoin, urlparse, unquote

from atualizar_balneabilidade import (
    BASE,
    LeitorLinks,
    baixar,
    ler_pdf,
    extrair_dados,
    consultar_supabase,
    cadastrar_boletim,
    cadastrar_resultados,
    validar_resultados,
    publicar_boletim
)

ANO = 2026

SIMULACAO = os.environ.get(
    "IMPORTAR_HISTORICO", "false"
).lower() != "true"


def localizar_historico():

    pagina = (
        BASE
        + "/monitoramento-ambiental/balneabilidade/"
        + f"informativos-semanais-{ANO}/"
    )

    print("Consultando:", pagina)

    leitor = LeitorLinks()

    leitor.feed(
        baixar(pagina).decode(
            "utf-8",
            errors="replace"
        )
    )

    boletins = {}

    for link in leitor.links:

        url = urljoin(pagina, link)
        caminho = unquote(urlparse(url).path)

        if urlparse(url).hostname != urlparse(BASE).hostname:
            continue

        if not caminho.lower().endswith(".pdf"):
            continue

        dados = re.search(
            r"balneabilidade[-_\s]+(\d+)[-_\s]+(\d{4})",
            caminho,
            re.IGNORECASE
        )

        if not dados:
            continue

        numero = int(dados.group(1))
        ano = int(dados.group(2))

        if ano != ANO:
            continue

        boletins.setdefault(numero, set()).add(url)

    if not boletins:
        raise RuntimeError(
            "Nenhum boletim historico localizado."
        )

    return boletins


def processar_boletim(numero, url, mapa):

    texto = ler_pdf(url)
    dados = extrair_dados(texto)

    resultados = dados["resultados"]

    codigos = [
        r["codigo"]
        for r in resultados
    ]

    if not codigos:
        raise RuntimeError(
            "Nenhum resultado identificado."
        )

    if len(codigos) != len(set(codigos)):
        raise RuntimeError(
            "Codigos duplicados no PDF."
        )

    desconhecidos = set(codigos) - set(mapa)

    if desconhecidos:
        raise RuntimeError(
            f"Pontos nao cadastrados: {sorted(desconhecidos)}"
        )

    if set(codigos) != set(mapa):
        raise RuntimeError(
            "Quantidade ou conjunto de pontos divergente. "
            "Necessaria conferencia manual."
        )

    proprios = sum(
        r["classificacao"] == "PROPRIA"
        for r in resultados
    )

    improprios = sum(
        r["classificacao"] == "IMPROPRIA"
        for r in resultados
    )

    print("Publicacao:", dados["data_publicacao"])
    print("Coleta:", dados["data_coleta"])
    print("Total de pontos:", len(resultados))
    print("Proprios:", proprios)
    print("Improprios:", improprios)

    existentes = consultar_supabase(
        "boletins",
        f"select=*&numero=eq.{numero}&ano=eq.{ANO}"
    )

    if existentes:

        boletim = existentes[0]
        boletim_id = boletim["id"]

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

            print("Boletim ja cadastrado e validado.")

            return "existente"

    if SIMULACAO:

        print("Simulacao: nenhum dado alterado.")

        return "validado"

    if existentes:

        boletim_id = existentes[0]["id"]

    else:

        boletim = cadastrar_boletim(
            ANO,
            numero,
            url,
            dados
        )

        boletim_id = boletim["id"]

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

    publicar_boletim(boletim_id)

    return "importado"


def main():

    print("=== HISTORICO BALNEABILIDADE PE ===")
    print("Ano:", ANO)
    print("Simulacao:", SIMULACAO)

    boletins = localizar_historico()

    print("Boletins localizados:", len(boletins))
    print("Numeros:", sorted(boletins))

    pontos = consultar_supabase(
        "pontos_coleta",
        "select=id,codigo&ativo=eq.true"
    )

    mapa = {
        ponto["codigo"]: ponto["id"]
        for ponto in pontos
    }

    resumo = {
        "existente": 0,
        "validado": 0,
        "importado": 0,
        "erro": 0
    }

    erros = []

    for numero in sorted(boletins):

        print("\n" + "=" * 45)
        print(f"BOLETIM {numero}/{ANO}")
        print("=" * 45)

        try:

            urls = boletins[numero]

            if len(urls) != 1:
                raise RuntimeError(
                    "Mais de um PDF para este boletim."
                )

            url = next(iter(urls))

            situacao = processar_boletim(
                numero,
                url,
                mapa
            )

            resumo[situacao] += 1

        except Exception as erro:

            resumo["erro"] += 1

            erros.append(
                f"{numero}/{ANO}: {erro}"
            )

            print("ERRO:", erro)

    print("\n=== RESUMO FINAL ===")

    print("Localizados:", len(boletins))
    print("Ja cadastrados:", resumo["existente"])
    print("Validados:", resumo["validado"])
    print("Importados:", resumo["importado"])
    print("Com erro:", resumo["erro"])

    if erros:

        print("\n=== PENDENCIAS ===")

        for erro in erros:
            print(erro)

    if SIMULACAO:
        print("\nNenhum dado foi alterado.")

    if erros:
        raise SystemExit(1)

    print("\nProcesso concluido com sucesso.")


if __name__ == "__main__":
    main()
