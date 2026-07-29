# doc-gub

`doc-gub` gera documentação para arquivos Python, JavaScript e TypeScript usando OpenAI, Gemini ou Ollama.

Por padrão, ele mostra um resumo compacto da geração, sem despejar código no terminal. Para gravar, execute:

```shell
doc-gub --output apply
```

Em automações, use `--yes` junto com `--output apply`. Cada arquivo é gravado assim que sua geração e validação terminam; falhas posteriores não desfazem arquivos já aplicados. O arquivo é comparado com a prévia imediatamente antes da escrita; se tiver sido alterado, o `doc-gub` não o sobrescreve.

## Uso

```shell
doc-gub                         # mudanças Git (staging tem prioridade)
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

Por padrão, `request_scope = "file"` envia o arquivo e todos os símbolos que precisam de geração em uma única requisição. Use `request_scope = "symbol"` (ou `--request-scope symbol`) para enviar cada símbolo em sua própria requisição, com apenas seu escopo de código. Isso reduz o contexto em arquivos grandes, mas pode aumentar latência e o número de chamadas. Símbolos já documentados são ignorados quando `existing_docs = "preserve"`; a documentação de módulo continua usando o arquivo completo.
