# doc-gub

`doc-gub` gera documentação para arquivos Python, JavaScript e TypeScript usando OpenAI, Gemini ou Ollama.

Por padrão, ele mostra um resumo compacto da geração, sem despejar código no terminal. Para gravar, execute:

```shell
doc-gub --output apply
```

Em automações, use `--yes` junto com `--output apply`. Cada arquivo é gravado assim que sua geração e validação terminam; falhas posteriores não desfazem arquivos já aplicados. O arquivo é comparado com a prévia imediatamente antes da escrita; se tiver sido alterado, o `doc-gub` não o sobrescreve.

## Uso

```shell
doc-gub                         # mudanças Git (staging tem prioridade; inclui arquivos não rastreados)
doc-gub src/a.py src/b.ts       # um ou mais arquivos/diretórios específicos
doc-gub --selection repository  # todos os arquivos elegíveis
doc-gub --coverage all --format numpy
doc-gub --language Portuguese
doc-gub --request-scope symbol
doc-gub --check                 # falha no CI se houver símbolos sem documentação
doc-gub config init
doc-gub config show
```

A configuração segue esta precedência: flags, `--config`, variáveis `DOC_GUB_*`, `.doc-gub.toml`, configuração do usuário e valores padrão. Defina `language = "Portuguese"` em `[documentation]`, use `DOC_GUB_LANGUAGE` ou passe `--language Portuguese` para controlar o idioma gerado. Credenciais usam `OPENAI_API_KEY` ou `GEMINI_API_KEY`; o Ollama não requer credencial.

## Configuração (`.doc-gub.toml`)

Crie um arquivo inicial com `doc-gub config init`. As seções `[ai]`, `[documentation]` e `[limits]` são combinadas em uma única configuração; as opções abaixo são todas as chaves aceitas.

### `[ai]`

| Opção | Valores / padrão | Efeito |
| --- | --- | --- |
| `provider` | `openai`, `gemini` ou `ollama` · `ollama` | Seleciona o provedor que gera as descrições. |
| `model` | string · `qwen2.5-coder:14b` | Modelo único usado quando `models` estiver vazio. |
| `models` | lista de 1 a 3 strings · `qwen2.5-coder:14b`, `gemma4:e4b` | Candidatos usados em rotação nas tentativas; tem precedência sobre `model`. |
| `endpoint` | URL ou ausência · endpoint padrão do provedor | Substitui o endpoint HTTP do provedor. |
| `max_input_tokens` | inteiro positivo · `12000` | Limite estimado para o prompt enviado ao modelo. |
| `context_window_tokens` | inteiro positivo · `32768` | Janela total do modelo; deve comportar entrada e saída. |
| `max_output_tokens` | inteiro positivo · `800` | Limite de tokens da resposta gerada. |
| `temperature` | número · `0.2` | Controla a variação da resposta do modelo. |
| `timeout_seconds` | inteiro positivo · `60` | Tempo máximo de cada chamada ao provedor. |

`max_input_tokens + max_output_tokens` não pode exceder `context_window_tokens`.

### `[documentation]`

| Opção | Valores / padrão | Efeito |
| --- | --- | --- |
| `selection` | `changes`, `repository` · `changes` | Define se processa mudanças Git ou todos os arquivos elegíveis. |
| `coverage` | `missing`, `minimal`, `all` · `missing` | `all` torna elegíveis também símbolos já documentados, exceto com `existing_docs = "preserve"`; `missing` e `minimal` consideram apenas os sem docstring. |
| `existing_docs` | `preserve`, `replace` · `preserve` | Com `preserve`, exclui da IA todo símbolo que já tenha docstring; com `replace`, permite sua reescrita. A regra vale para módulos, classes, funções e métodos. |
| `request_scope` | `file`, `symbol` · `file` | Define o contexto por chamada: arquivo completo ou um símbolo. Não muda quais símbolos podem ser gerados. |
| `language` | string não vazia · `English` | Idioma das descrições solicitadas ao modelo. |
| `python_format` | `google`, `numpy`, `sphinx` · `google` | Formato da seção de parâmetros em docstrings Python. |
| `javascript_format` | `jsdoc` · `jsdoc` | Formato das anotações para JavaScript e TypeScript. |
| `output` | `preview`, `apply` · `preview` | Mostra a prévia ou grava as alterações. |
| `confirm` | booleano · `true` | Exige confirmação interativa antes de aplicar alterações. |

### `[limits]`

| Opção | Valores / padrão | Efeito |
| --- | --- | --- |
| `max_files_per_request` | inteiro positivo · `50` | Máximo de arquivos que um escopo pode conter. |
| `max_file_bytes` | inteiro positivo · `100000` | Tamanho máximo de cada arquivo processado. |
| `exclude` | lista de glob patterns | Remove arquivos do escopo. O padrão exclui dependências, artefatos de build, minificados e `package-lock.json`. |
| `include` | lista de glob patterns · lista vazia | Quando preenchida, mantém somente arquivos que correspondam a algum padrão. |

## Padrão de docstrings Python

As docstrings Python geradas seguem o PEP 257: resumos terminam em ponto, docstrings multilinha usam uma linha em branco antes e depois das seções e fecham as aspas em uma linha própria. Docstrings de módulo e classe são separadas da próxima declaração por uma linha em branco. As linhas vazias não recebem espaços de indentação.

O comprimento de linha também segue o projeto-alvo. Para Python, o `doc-gub` usa `tool.ruff.line-length` do `pyproject.toml` mais próximo (ou 88 se não houver configuração). Para JavaScript e TypeScript, usa `max-len` do `eslint.config.js`, `eslint.config.mjs` ou `eslint.config.cjs` mais próximo (ou 100 sem regra). Descrições e linhas JSDoc são quebradas já considerando a indentação e os delimitadores.

Os formatos `google`, `numpy` e `sphinx` controlam somente a seção de parâmetros. Para reescrever docstrings já existentes com esse padrão, use:

```shell
doc-gub --coverage all --existing-docs replace --output apply --yes
```

Para funções e métodos, o `doc-gub` solicita ao modelo uma descrição específica para cada argumento e a insere na seção de parâmetros correspondente. A resposta estruturada deve conter todos os símbolos e todos os argumentos solicitados; respostas incompletas são rejeitadas e geradas novamente. Integrações legadas mantêm um texto de fallback para compatibilidade.

Por padrão, `request_scope = "file"` envia o arquivo e todos os símbolos que precisam de geração em uma única requisição. Use `request_scope = "symbol"` (ou `--request-scope symbol`) para enviar cada símbolo em sua própria requisição, com apenas seu escopo de código. Isso reduz o contexto em arquivos grandes, mas pode aumentar latência e o número de chamadas. Para módulos, ele envia um índice estrutural — docstring inicial, imports, constantes e assinaturas públicas — sem os corpos. Nesse modo, o terminal exibe apenas o progresso e o item ativo (`3/10 arquivo:símbolo`); com `output = "apply"`, cada docstring é gravada imediatamente, preservando as conclusões anteriores se uma geração posterior falhar.

Independentemente de `request_scope`, `existing_docs = "preserve"` impede qualquer chamada à IA para um módulo, classe, função ou método que já tenha docstring. `request_scope` altera somente a unidade de contexto enviada ao modelo; `existing_docs` decide se o símbolo é elegível. Para reescrever a documentação existente, combine `existing_docs = "replace"` com `coverage = "all"`.
