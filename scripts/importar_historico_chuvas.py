import os
import json
import time
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SECRET_KEY"]

API_CHUVAS = "https://archive-api.open-meteo.com/v1/archive"

ANO = 2026
INICIO = date(ANO, 1, 1) - timedelta(days=3)
FIM = min(
    date.today() - timedelta(days=7),
    date(ANO, 12, 31)
)

FONTE = "Open-Meteo"
MODELO = "ERA5"


def requisitar(url, headers=None, dados=None, metodo="GET"):
    for tentativa in range(4):
        try:
            req = Request(
                url,
                headers=headers or {},
                data=dados,
                method=metodo
            )

            with urlopen(req, timeout=90) as resposta:
                conteudo = resposta.read()

                if conteudo:
                    return json.loads(conteudo)

                return None

        except Exception as erro:
            if tentativa == 3:
                raise

            print("Nova tentativa:", str(erro))
            time.sleep(5 * (tentativa + 1))


def headers_supabase():
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json"
    }

    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"

    return headers


def consultar_pontos():
    url = (
        SUPABASE_URL
        + "/rest/v1/pontos_coleta"
        + "?select=id,codigo,latitude,longitude"
        + "&ativo=eq.true"
        + "&order=id"
    )

    return requisitar(
        url,
        headers=headers_supabase()
    )


def consultar_chuvas(ponto):
    parametros = {
        "latitude": ponto["latitude"],
        "longitude": ponto["longitude"],
        "start_date": INICIO.isoformat(),
        "end_date": FIM.isoformat(),
        "daily": "precipitation_sum",
        "models": "era5",
        "timezone": "America/Recife",
        "cell_selection": "land"
    }

    url = API_CHUVAS + "?" + urlencode(parametros)

    dados = requisitar(url)

    diario = dados["daily"]

    datas = diario["time"]
    chuvas = diario["precipitation_sum"]

    if len(datas) != len(chuvas):
        raise RuntimeError("Serie meteorologica inconsistente.")

    registros = []

    for dia, chuva in zip(datas, chuvas):
        if chuva is None:
            continue

        if chuva < 0:
            raise RuntimeError("Precipitacao negativa.")

        registros.append({
            "ponto_id": ponto["id"],
            "data_referencia": dia,
            "chuva_mm": chuva,
            "latitude": ponto["latitude"],
            "longitude": ponto["longitude"],
            "fonte": FONTE,
            "modelo": MODELO
        })

    return registros


def salvar_registros(registros):
    if not registros:
        return

    url = (
        SUPABASE_URL
        + "/rest/v1/historico_chuvas"
        + "?on_conflict=ponto_id,data_referencia,fonte,modelo"
    )

    headers = headers_supabase()

    headers["Prefer"] = (
        "resolution=merge-duplicates,return=minimal"
    )

    requisitar(
        url,
        headers=headers,
        dados=json.dumps(registros).encode("utf-8"),
        metodo="POST"
    )


def main():
    print("=== HISTORICO DE CHUVAS ===")
    print("Inicio:", INICIO)
    print("Fim:", FIM)

    pontos = consultar_pontos()

    print("Pontos cadastrados:", len(pontos))

    if not pontos:
        raise RuntimeError("Nenhum ponto localizado.")

    total = 0
    erros = []

    for ponto in pontos:
        codigo = ponto["codigo"]

        try:
            if (
                ponto["latitude"] is None
                or ponto["longitude"] is None
            ):
                raise RuntimeError("Coordenadas ausentes.")

            print("\nConsultando:", codigo)

            registros = consultar_chuvas(ponto)

            if not registros:
                raise RuntimeError("Nenhum dado de chuva.")

            salvar_registros(registros)

            total += len(registros)

            print("Registros processados:", len(registros))

        except Exception as erro:
            erros.append(codigo)
            print("ERRO:", codigo, str(erro))

        time.sleep(2)

    print("\n=== RESUMO FINAL ===")
    print("Pontos:", len(pontos))
    print("Registros processados:", total)
    print("Pontos com erro:", len(erros))

    if erros:
        print("Pendencias:", ", ".join(erros))
        raise SystemExit(1)

    print("Importacao concluida com sucesso!")


if __name__ == "__main__":
    main()
