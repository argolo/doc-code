# Doc Code

`doc-code` gera documentação para arquivos Python, JavaScript e TypeScript usando OpenAI, Gemini ou Ollama.

Por padrão, ele mostra um diff revisável da documentação gerada. Use `--no-show-diff` para manter somente o resumo compacto. Para gravar, execute:

```shell
doc-code --output apply
```

Em automações, use `--yes` junto com `--output apply`. Cada arquivo é gravado assim que sua geração e validação terminam; falhas posteriores não desfazem arquivos já aplicados. O arquivo é comparado com a prévia imediatamente antes da escrita; se tiver sido alterado, o `doc-code` não o sobrescreve. Com `confirm = true`, o Doc Code mostra e confirma cada docstring formatada antes de aplicá-la; com `confirm = false`, revisa o diff completo do arquivo antes de aplicar.

## Instalação e requisitos

O `doc-code` requer Python 3.11 ou superior e Git no `PATH`. Para processar JavaScript, instale Node.js; para TypeScript/TSX, instale também o compilador `tsc` (por exemplo, `npm install --global typescript`). Esses executáveis são usados para validar a sintaxe gerada antes de qualquer arquivo ser alterado.

Para desenvolvimento, use `uv sync --locked --all-extras`; para instalar o pacote publicado, use `pip install doc-code`.

## Uso

```shell
doc-code                         # mudanças Git (staged, unstaged e arquivos não rastreados)
doc-code src/a.py src/b.ts       # um ou mais arquivos/diretórios específicos
doc-code --selection repository  # todos os arquivos elegíveis
doc-code --coverage all --format numpy
doc-code --language Portuguese
doc-code --request-scope symbol
doc-code --no-show-diff          # prévia compacta, sem o diff unificado
doc-code --check                 # falha no CI se houver símbolos sem documentação
doc-code --continue-on-error     # aceita status 0 mesmo quando algum arquivo for ignorado
doc-code config init
doc-code config show
```

A configuração segue esta precedência: flags, `--config`, variáveis `DOC_CODE_*`, `.doc-code.toml`, configuração do usuário e valores padrão. Defina `language = "Portuguese"` em `[documentation]`, use `DOC_CODE_LANGUAGE` ou passe `--language Portuguese` para controlar o idioma gerado. Credenciais usam `OPENAI_API_KEY` ou `GEMINI_API_KEY`; o Ollama não requer credencial.

## Configuração (`.doc-code.toml`)

Crie um arquivo inicial com `doc-code config init`. As seções `[ai]`, `[documentation]` e `[limits]` são combinadas em uma única configuração; as opções abaixo são todas as chaves aceitas.

### `[ai]`

| Opção | Valores / padrão | Efeito |
| --- | --- | --- |
| `provider` | `openai`, `gemini` ou `ollama` · `ollama` | Seleciona o provedor que gera as descrições. |
| `model` | string · depende do provedor | Modelo único usado quando `models` estiver vazio: `qwen2.5-coder:14b`, `gpt-5.6-sol` ou `gemini-3.6-flash`. |
| `models` | lista de até 3 strings · `qwen2.5-coder:14b`, `gemma4:e4b` | Candidatos usados em rotação nas tentativas; tem precedência sobre `model`. Uma lista vazia usa somente `model`. |
| `endpoint` | URL ou ausência · endpoint padrão do provedor | Substitui o endpoint do provedor. Provedores autenticados exigem HTTPS fora de loopback. |
| `max_input_tokens` | inteiro positivo · `12000` | Limite estimado para o prompt enviado ao modelo. |
| `context_window_tokens` | inteiro positivo · `32768` | Janela total do modelo; deve comportar entrada e saída. |
| `max_output_tokens` | inteiro positivo · `800` | Limite de tokens da resposta gerada. |
| `temperature` | número · `0.2` | Controla a variação da resposta; deve ser não negativa e fica entre 0 e 2 para OpenAI e Gemini. |
| `timeout_seconds` | inteiro positivo · `60` | Tempo máximo de cada chamada ao provedor. |

`max_input_tokens + max_output_tokens` não pode exceder `context_window_tokens`.

### `[documentation]`

| Opção | Valores / padrão | Efeito |
| --- | --- | --- |
| `selection` | `changes`, `repository` · `changes` | Define se processa mudanças Git ou todos os arquivos elegíveis. |
| `coverage` | `missing`, `minimal`, `all` · `missing` | `missing` inclui todos os símbolos sem docstring; `minimal` inclui somente módulo e API pública de primeiro nível sem docstring; `all` gera ou substitui a documentação de todos os símbolos. |
| `request_scope` | `file`, `symbol` · `file` | Define o contexto por chamada: arquivo completo ou um símbolo. Não muda quais símbolos podem ser gerados. |
| `language` | string não vazia · `English` | Idioma das descrições solicitadas ao modelo. |
| `python_format` | `google`, `numpy`, `sphinx` · `google` | Formato da seção de parâmetros em docstrings Python. |
| `javascript_format` | `jsdoc` · `jsdoc` | Formato das anotações para JavaScript e TypeScript. |
| `output` | `preview`, `apply` · `preview` | Mostra a prévia ou grava as alterações. |
| `confirm` | booleano · `true` | Com `true`, mostra e confirma cada docstring formatada antes de aplicá-la; com `false`, mostra o diff gerado do arquivo e pede confirmação antes de aplicá-lo. |

### `[limits]`

| Opção | Valores / padrão | Efeito |
| --- | --- | --- |
| `max_files_per_request` | inteiro positivo · `50` | Máximo de arquivos que um escopo pode conter. |
| `max_file_bytes` | inteiro positivo · `100000` | Tamanho máximo de cada arquivo processado. |
| `exclude` | lista de glob patterns | Remove arquivos do escopo. O padrão exclui dependências, artefatos de build, minificados e `package-lock.json`. |
| `include` | lista de glob patterns · lista vazia | Quando preenchida, mantém somente arquivos que correspondam a algum padrão. |

## Padrão de docstrings Python

As docstrings Python geradas seguem o PEP 257: resumos terminam em ponto, docstrings multilinha usam uma linha em branco antes e depois das seções e fecham as aspas em uma linha própria. Docstrings de módulo e classe são separadas da próxima declaração por uma linha em branco. As linhas vazias não recebem espaços de indentação.

O comprimento de linha também segue o projeto-alvo. Para Python, o `doc-code` usa `tool.ruff.line-length` e `tool.ruff.lint.pydocstyle.convention` do `pyproject.toml` mais próximo (ou 88 e o formato configurado no `doc-code` quando ausentes). Resumos longos são separados da descrição por uma linha em branco, conforme D205. Para JavaScript e TypeScript, usa `max-len` do `eslint.config.js`, `eslint.config.mjs` ou `eslint.config.cjs` mais próximo (ou 100 sem regra). Descrições e linhas JSDoc são quebradas já considerando a indentação e os delimitadores.

Os formatos `google`, `numpy` e `sphinx` controlam somente a seção de parâmetros. Para reescrever docstrings já existentes com esse padrão, use:

```shell
doc-code --coverage all --output apply --yes
```

Para funções e métodos, o `doc-code` solicita ao modelo uma descrição específica para cada argumento e a insere na seção de parâmetros correspondente. A resposta estruturada deve conter todos os símbolos e todos os argumentos solicitados; respostas incompletas são rejeitadas e geradas novamente. Integrações legadas mantêm um texto de fallback para compatibilidade.

Por padrão, `request_scope = "file"` envia o arquivo e todos os símbolos que precisam de geração em uma única requisição. Use `request_scope = "symbol"` (ou `--request-scope symbol`) para enviar cada símbolo em sua própria requisição, com apenas seu escopo de código. Isso reduz o contexto em arquivos grandes, mas pode aumentar latência e o número de chamadas. Para módulos, ele envia um índice estrutural — docstring inicial, imports, constantes e assinaturas públicas — sem os corpos. Com `output = "apply"` e `confirm = true`, cada docstring formatada é revisada e confirmada individualmente; uma recusa é sinalizada e ignorada, enquanto os demais símbolos continuam. `--yes` aplica sem perguntas. Com `confirm = false`, o diff completo do arquivo é revisado antes de qualquer alteração ser gravada.

Independentemente de `request_scope`, `coverage` decide quais símbolos são elegíveis e `request_scope` altera somente a unidade de contexto enviada ao modelo. Use `coverage = "all"` para reescrever a documentação existente.

Falhas parciais retornam status 1 depois que os demais arquivos elegíveis são processados. Use `--continue-on-error` somente quando a automação aceitar explicitamente resultados parciais.
