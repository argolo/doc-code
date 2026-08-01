"""Módulo contendo funções e classes utilitárias."""

import asyncio
import math
from datetime import datetime


def fibonacci(n: int) -> list[int]:
    """Calcula a sequência de Fibonacci até o termo n.

    Args:
        n: O número de termos da sequência de Fibonacci a serem calculados.

    """
    if n <= 0:
        return []
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


def calcular_imc(peso: float, altura: float) -> tuple[float, str]:
    """Calcula o Índice de Massa Corporal (IMC) e retorna a classificação.

    Args:
        peso: O peso da pessoa em quilogramas (kg).
        altura: A altura da pessoa em metros (m).

    """
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
    """Calcula a área em metros quadrados de um retângulo.

    Args:
        largura: A largura do retângulo (em metros).
        comprimento: O comprimento do retângulo (em metros).

    """
    return round(largura * comprimento, 2)


def fase_da_lua(data: datetime | None = None) -> str:
    """Calcula a fase atual da lua em uma determinada data.

    Args:
        data: A data para calcular a fase da lua (opcional). Se não fornecida, usa a data e hora
        atuais.

    """
    if data is None:
        data = datetime.now()
    referencia = datetime(2000, 1, 6)
    dias_decorridos = (data - referencia).total_seconds() / 86400
    ciclo = 29.53058867
    fase_dia = dias_decorridos % ciclo
    fases = (
        (1.84566, "Lua Nova"),
        (5.53699, "Lua Crescente"),
        (9.22831, "Quarto Crescente"),
        (12.91963, "Gibosa Crescente"),
        (16.61096, "Lua Cheia"),
        (20.30228, "Gibosa Minguante"),
        (23.99361, "Quarto Minguante"),
        (27.68493, "Lua Minguante"),
        (ciclo, "Lua Nova"),
    )
    return next(nome for limite, nome in fases if fase_dia < limite)


async def async_eh_primo(numero: int) -> bool:
    """Verifica se um número inteiro é primo de forma assíncrona.

    Args:
        numero: O número inteiro a ser verificado.

    """
    await asyncio.sleep(0.05)
    if numero < 2:
        return False
    for i in range(2, int(math.isqrt(numero)) + 1):
        if numero % i == 0:
            return False
    return True


async def async_converter_temperatura(valor: float, de: str = "C", para: str = "F") -> float:
    """Converte um valor de temperatura entre diferentes escalas (Celsius, Fahrenheit ou Kelvin).

    Args:
        valor: O valor numérico da temperatura a ser convertido.
        de: A unidade de origem da temperatura ('C', 'F' ou 'K'). Padrão: 'C'.
        para: A unidade de destino desejada para o valor ('C', 'F' ou 'K'). Padrão: 'F'.

    """
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
    """Classe que contém métodos estáticos para realizar cálculos financeiros."""

    @staticmethod
    def juros_compostos(capital: float, taxa_anual: float, tempo_anos: int) -> float:
        """Calcula o montante final de um investimento utilizando juros compostos.

        Args:
            capital: O valor inicial investido (principal).
            taxa_anual: A taxa de juros anual em porcentagem (ex: 10 para 10%).
            tempo_anos: O período de tempo do investimento em anos.

        """
        montante = capital * ((1 + (taxa_anual / 100)) ** tempo_anos)
        return round(montante, 2)

    @staticmethod
    async def async_simular_investimento(
        aporte_mensal: float, taxa_mensal: float, meses: int
    ) -> list[float]:
        """Simula o crescimento de um investimento ao longo do tempo, retornando o saldo acumulado
        em cada mês.

        Args:
            aporte_mensal: O valor fixo aportado mensalmente no investimento (em reais).
            taxa_mensal: A taxa de juros mensal esperada para o investimento (em porcentagem, ex:
            1.5 para 1.5%).
            meses: O número total de meses que a simulação deve cobrir.

        """
        saldo = 0.0
        historico = []
        for _mes in range(1, meses + 1):
            await asyncio.sleep(0.01)
            saldo = (saldo + aporte_mensal) * (1 + (taxa_mensal / 100))
            historico.append(round(saldo, 2))
        return historico


class UtilitarioTexto:
    """Uma classe que fornece utilidades para manipulação e análise de texto."""

    def __init__(self, texto: str):
        """Inicializa uma instância de UtilitarioTexto com o texto fornecido.

        Args:
            texto: O texto inicial para a instância.

        """
        self.texto = texto

    def estatisticas(self) -> dict[str, int]:
        """Retorna um dicionário contendo estatísticas básicas do texto, como número de palavras,
        caracteres e linhas.

        """
        palavras = len(self.texto.split())
        caracteres = len(self.texto)
        linhas = len(self.texto.splitlines())
        return {"palavras": palavras, "caracteres": caracteres, "linhas": linhas}

    async def async_frequencia_caracteres(self) -> dict[str, int]:
        """Calcula e retorna um dicionário com a frequência de ocorrência de cada caractere
        alfanumérico no texto, ordenado do mais frequente para o menos frequente.

        """
        await asyncio.sleep(0.02)
        frequencia: dict[str, int] = {}
        for char in self.texto.lower():
            if char.isalnum():
                frequencia[char] = frequencia.get(char, 0) + 1
        return dict(sorted(frequencia.items(), key=lambda item: item[1], reverse=True))


async def main():
    """Ponto de entrada principal que demonstra o uso de várias funções e métodos."""
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
