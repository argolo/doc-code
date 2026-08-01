import asyncio
import math
from datetime import datetime


def fibonacci(n: int) -> list[int]:
    if n <= 0:
        return []
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


def calcular_imc(peso: float, altura: float) -> tuple[float, str]:
    if altura <= 0 or peso <= 0:
        raise ValueError("Peso e altura devem ser maiores que zero.")
    imc = peso / (altura**2)
    if imc < 18.5:
        classificacao = "Abaixo do peso"
    elif imc < 25:
        classificacao = "Peso normal"
    elif imc < 30:
        classificacao = "Sobrepeso"
    else:
        classificacao = "Obesidade"
    return round(imc, 2), classificacao


def calcular_metro_quadrado(largura: float, comprimento: float) -> float:
    return round(largura * comprimento, 2)


def fase_da_lua(data: datetime | None = None) -> str:
    if data is None:
        data = datetime.now()
    referencia = datetime(2000, 1, 6)
    dias_decorridos = (data - referencia).total_seconds() / 86400
    ciclo = 29.53058867
    fase_dia = dias_decorridos % ciclo
    if fase_dia < 1.84566:
        return "Lua Nova"
    elif fase_dia < 5.53699:
        return "Lua Crescente"
    elif fase_dia < 9.22831:
        return "Quarto Crescente"
    elif fase_dia < 12.91963:
        return "Gibosa Crescente"
    elif fase_dia < 16.61096:
        return "Lua Cheia"
    elif fase_dia < 20.30228:
        return "Gibosa Minguante"
    elif fase_dia < 23.99361:
        return "Quarto Minguante"
    elif fase_dia < 27.68493:
        return "Lua Minguante"
    else:
        return "Lua Nova"


async def async_eh_primo(numero: int) -> bool:
    await asyncio.sleep(0.05)
    if numero < 2:
        return False
    for i in range(2, int(math.isqrt(numero)) + 1):
        if numero % i == 0:
            return False
    return True


async def async_converter_temperatura(valor: float, de: str = "C", para: str = "F") -> float:
    await asyncio.sleep(0.01)
    de, para = de.upper(), para.upper()
    if de == "C":
        celsius = valor
    elif de == "F":
        celsius = (valor - 32) * 5 / 9
    elif de == "K":
        celsius = valor - 273.15
    else:
        raise ValueError("Unidade de origem inválida.")

    if para == "C":
        return round(celsius, 2)
    elif para == "F":
        return round((celsius * 9 / 5) + 32, 2)
    elif para == "K":
        return round(celsius + 273.15, 2)
    else:
        raise ValueError("Unidade de destino inválida.")


class CalculadoraFinanceira:
    @staticmethod
    def juros_compostos(capital: float, taxa_anual: float, tempo_anos: int) -> float:
        montante = capital * ((1 + (taxa_anual / 100)) ** tempo_anos)
        return round(montante, 2)

    @staticmethod
    async def async_simular_investimento(
        aporte_mensal: float, taxa_mensal: float, meses: int
    ) -> list[float]:
        saldo = 0.0
        historico = []
        for _mes in range(1, meses + 1):
            await asyncio.sleep(0.01)
            saldo = (saldo + aporte_mensal) * (1 + (taxa_mensal / 100))
            historico.append(round(saldo, 2))
        return historico


class UtilitarioTexto:
    def __init__(self, texto: str):
        self.texto = texto

    def estatisticas(self) -> dict[str, int]:
        palavras = len(self.texto.split())
        caracteres = len(self.texto)
        linhas = len(self.texto.splitlines())
        return {"palavras": palavras, "caracteres": caracteres, "linhas": linhas}

    async def async_frequencia_caracteres(self) -> dict[str, int]:
        await asyncio.sleep(0.02)
        frequencia: dict[str, int] = {}
        for char in self.texto.lower():
            if char.isalnum():
                frequencia[char] = frequencia.get(char, 0) + 1
        return dict(sorted(frequencia.items(), key=lambda item: item[1], reverse=True))


async def main():
    print(fibonacci(10))
    print(calcular_imc(75, 1.75))
    print(calcular_metro_quadrado(10.5, 8))
    print(fase_da_lua())
    print(await async_eh_primo(29))
    print(await async_converter_temperatura(25, de="C", para="F"))
    print(CalculadoraFinanceira.juros_compostos(1000, 10, 5))
    print(await CalculadoraFinanceira.async_simular_investimento(500, 0.8, 6))
    util = UtilitarioTexto("Python é uma linguagem incrível.\nSuporta sync e async!")
    print(util.estatisticas())
    print(await util.async_frequencia_caracteres())


if __name__ == "__main__":
    asyncio.run(main())
